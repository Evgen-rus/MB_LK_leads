from __future__ import annotations

import json
import math
import sqlite3
import shutil
import threading
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import crud, models, schemas
from . import db
from .config import RUNS_DIR
from .excel_reader import list_sheets, read_excel_sheet
from .export_history import (
    ExportMetadata,
    ExportPeriod,
    analysis_report_path,
    delete_analysis_export,
    list_exports,
)
from .sheets_reader import parse_spreadsheet_url, read_spreadsheet_url
from .matcher import match_files
from .models import ColumnMapping, StatusRule
from .pipeline import analyze_file
from .status_classifier import ALL_GROUPS, is_missing_status, unknown_statuses
from .structure_detector import detect_match_mapping, prepare_analyze_mapping
from .source_utils import safe_filename

PREVIEW_ROWS = 25
JOB_WORKER_LOCK = threading.Lock()


class MappingPayload(BaseModel):
    sheet_name: str
    date_column: str | None = None
    phone_column: str | None = None
    channel_column: str | None = None
    source_column: str | None = None
    status_column: str | None = None
    comment_column: str | None = None
    lkid_column: str | None = None
    project_column: str | None = None


class MatchPayload(BaseModel):
    lk_mapping: MappingPayload
    client_mapping: MappingPayload


class AnalyzeSetupPayload(BaseModel):
    mapping: MappingPayload | None = None


class AnalysisPeriodPayload(BaseModel):
    period_start: date
    period_end: date


class AnalyzePayload(BaseModel):
    mapping: MappingPayload
    status_rules: dict[str, str] = {}
    periods: list[AnalysisPeriodPayload]
    analysis_date: date | None = None
    source_file_name: str | None = None


class GroupPayload(BaseModel):
    name: str
    project_ids: list[int]
    spreadsheet_url: str | None = None


class ExportPeriodRecord(BaseModel):
    period_start: str
    period_end: str
    total_count: int
    missed_count: int
    missed_rate: float
    quality_count: int
    quality_rate: float
    demand_count: int
    demand_rate: float


class ExportRecord(BaseModel):
    id: int
    export_number: int
    report_available: bool
    period_start: str
    period_end: str
    analysis_date: str
    source_file_name: str
    total_count: int
    missed_count: int
    missed_rate: float
    quality_count: int
    quality_rate: float
    demand_count: int
    demand_rate: float
    periods: list[ExportPeriodRecord]


class SheetPreview(BaseModel):
    name: str
    columns: list[str]
    rows: list[dict[str, Any]]


class FileInspect(BaseModel):
    filename: str
    detected: MappingPayload
    sheets: list[SheetPreview]


class UploadResponse(BaseModel):
    run_id: str
    project: str
    lk: FileInspect
    client: FileInspect
    lk_row_count: int


class WorkbookPreview(BaseModel):
    filename: str
    sheets: list[SheetPreview]


class MatchResponse(BaseModel):
    filename: str
    preview: WorkbookPreview


class AnalyzeSetupResponse(BaseModel):
    filename: str
    mapping: MappingPayload
    sheets: list[SheetPreview]
    unknown_statuses: list[str]
    unknown_status_counts: dict[str, int]
    status_groups: list[str]


class JobResponse(BaseModel):
    id: int
    run_id: str
    kind: Literal["match", "analyze"]
    status: Literal["queued", "running", "completed", "failed"]
    phase: str
    processed_rows: int
    total_rows: int
    error_text: str | None = None
    output_file_name: str | None = None
    export_id: int | None = None


class StatusRuleResponse(BaseModel):
    id: int | None = None
    pattern: str
    match_type: str
    group_name: str
    priority: int
    source: Literal["project", "global", "default"]


class StatusRulesResponse(BaseModel):
    project_rules: list[StatusRuleResponse]
    system_rules: list[StatusRuleResponse]
    status_groups: list[str]


class StatusRuleUpdatePayload(BaseModel):
    group_name: str


def _run_dir(group_id: int, run_id: str) -> Path:
    if not run_id or any(part in run_id for part in ("..", "/", "\\")):
        raise HTTPException(status_code=400, detail="Некорректный run_id")
    run = db.get_run(run_id)
    if run is None or int(run["group_id"]) != group_id:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    path = RUNS_DIR / run_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Запуск не найден")
    return path


def _input_path(group_id: int, run_id: str, role: Literal["lk", "client"]) -> Path:
    return _run_dir(group_id, run_id) / "input" / f"{role}.xlsx"


def _output_dir(group_id: int, run_id: str) -> Path:
    path = _run_dir(group_id, run_id) / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stored_output(group_id: int, run_id: str, filename: str | None) -> Path:
    if not filename or Path(filename).name != filename or not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=404, detail="Файл еще не создан")
    path = _run_dir(group_id, run_id) / "output" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл еще не создан")
    return path


def _group(group_id: int, *, active: bool = False) -> dict[str, object]:
    value = db.get_group(group_id)
    if value is None or (active and value["archived"]):
        raise HTTPException(status_code=404, detail="Группа аналитики не найдена")
    return value


def _run(group_id: int, run_id: str) -> dict[str, object]:
    _run_dir(group_id, run_id)
    value = db.get_run(run_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    return value


def _clean_optional(value: str | None) -> str | None:
    value = value.strip() if value else ""
    return value or None


def _sheet_url(value: str | None) -> str | None:
    url = _clean_optional(value)
    if url:
        try:
            parse_spreadsheet_url(url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return url


def _to_mapping(payload: MappingPayload) -> ColumnMapping:
    return ColumnMapping(
        sheet_name=payload.sheet_name,
        date_column=_clean_optional(payload.date_column),
        phone_column=_clean_optional(payload.phone_column),
        channel_column=_clean_optional(payload.channel_column),
        source_column=_clean_optional(payload.source_column),
        status_column=_clean_optional(payload.status_column),
        comment_column=_clean_optional(payload.comment_column),
        lkid_column=_clean_optional(payload.lkid_column),
        project_column=_clean_optional(payload.project_column),
    )


def _from_mapping(mapping: ColumnMapping) -> MappingPayload:
    return MappingPayload(
        sheet_name=mapping.sheet_name,
        date_column=mapping.date_column,
        phone_column=mapping.phone_column,
        channel_column=mapping.channel_column,
        source_column=mapping.source_column,
        status_column=mapping.status_column,
        comment_column=mapping.comment_column,
        lkid_column=mapping.lkid_column,
        project_column=mapping.project_column,
    )


def _json_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value):
        return None
    return value.isoformat() if isinstance(value, (datetime, date, pd.Timestamp)) else value


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {str(key): _json_value(value) for key, value in item.items()}
        for item in df.head(PREVIEW_ROWS).to_dict(orient="records")
    ]


def _inspect_workbook(path: Path) -> list[SheetPreview]:
    previews = []
    for sheet in list_sheets(path):
        df = read_excel_sheet(path, sheet).head(PREVIEW_ROWS)
        previews.append(SheetPreview(name=sheet, columns=[str(column) for column in df.columns], rows=_records(df)))
    return previews


def _inspect_upload(
    path: Path,
    filename: str,
    role: Literal["lk", "client"],
    preferred_sheet: str | None = None,
    saved_mapping: ColumnMapping | None = None,
) -> FileInspect:
    available_sheets = set(list_sheets(path))
    saved_sheet = saved_mapping.sheet_name if saved_mapping and saved_mapping.sheet_name in available_sheets else None
    detected = detect_match_mapping(path, role, saved_sheet or preferred_sheet)
    if saved_mapping and saved_mapping.sheet_name == detected.sheet_name:
        columns = set(read_excel_sheet(path, detected.sheet_name).columns)
        fields = ("date_column", "phone_column", "channel_column", "source_column", "status_column",
                  "comment_column", "lkid_column", "project_column")
        if all(getattr(saved_mapping, field) is None or getattr(saved_mapping, field) in columns for field in fields):
            detected = saved_mapping
    return FileInspect(
        filename=filename,
        detected=_from_mapping(detected),
        sheets=_inspect_workbook(path),
    )


def _restore_saved_analyze_mapping(path: Path, mapping: ColumnMapping | None) -> ColumnMapping:
    detected = prepare_analyze_mapping(path)
    if mapping is None or mapping.sheet_name != detected.sheet_name or mapping.sheet_name not in list_sheets(path):
        return detected
    columns = set(read_excel_sheet(path, mapping.sheet_name).columns)
    fields = ("date_column", "phone_column", "channel_column", "source_column", "status_column", "comment_column")
    if all(getattr(mapping, field) is None or getattr(mapping, field) in columns for field in fields):
        return mapping
    return detected


def _preview(path: Path) -> WorkbookPreview:
    return WorkbookPreview(filename=path.name, sheets=_inspect_workbook(path))


def _validate_excel(file: UploadFile) -> None:
    name = file.filename or ""
    if not name.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail=f"Файл {name} должен быть .xlsx")


def _validate_periods(periods: list[AnalysisPeriodPayload]) -> None:
    if not periods:
        raise HTTPException(status_code=400, detail="Добавьте хотя бы один период")
    if any(item.period_start > item.period_end for item in periods):
        raise HTTPException(status_code=400, detail="Дата начала периода не может быть позже даты конца")
    if len(set((item.period_start, item.period_end) for item in periods)) != len(periods):
        raise HTTPException(status_code=400, detail="Одинаковые периоды нельзя добавлять дважды")


def _validate_unknown_status_rules(unknown: list[str], status_rules: dict[str, str]) -> None:
    if any(not status_rules.get(status, "").strip() for status in unknown):
        raise HTTPException(status_code=400, detail="Выберите группу для каждого неизвестного статуса перед аналитикой")
    if any(group.strip() not in ALL_GROUPS for group in status_rules.values() if group.strip()):
        raise HTTPException(status_code=400, detail="Неизвестная группа статуса")


def _unknown_status_counts(values: list[object], unknown: list[str]) -> dict[str, int]:
    counts = Counter(str(value).strip() for value in values if not is_missing_status(value))
    return {status: counts[status] for status in unknown}


def _job_response(job: dict[str, object]) -> JobResponse:
    return JobResponse(
        id=int(job["id"]), run_id=str(job["run_id"]), kind=str(job["kind"]), status=str(job["status"]),
        phase=str(job["phase"]), processed_rows=int(job["processed_rows"]), total_rows=int(job["total_rows"]),
        error_text=str(job["error_text"]) if job["error_text"] else None,
        output_file_name=str(job["output_file_name"]) if job["output_file_name"] else None,
        export_id=int(job["export_id"]) if job.get("export_id") is not None else None,
    )


def _run_job(job: dict[str, object]) -> None:
    job_id, run_id, group_id, payload = int(job["id"]), str(job["run_id"]), int(job["group_id"]), job["payload"]
    try:
        if not isinstance(payload, dict):
            raise ValueError("Некорректные данные задания")
        last: tuple[str, int, int] | None = None

        def report(phase: str, current: int, total: int) -> None:
            nonlocal last
            if last is None or phase != last[0] or current == total or current - last[1] >= max(1, total // 100):
                db.update_processing_job(job_id, phase=phase, processed_rows=current, total_rows=total)
                last = (phase, current, total)

        if job["kind"] == "match":
            report("Чтение и подготовка файлов", 0, 0)
            output = match_files(
                str(payload["project"]), _input_path(group_id, run_id, "lk"), _input_path(group_id, run_id, "client"),
                _to_mapping(MappingPayload(**payload["lk_mapping"])),
                _to_mapping(MappingPayload(**payload["client_mapping"])), _output_dir(group_id, run_id), progress=report,
            )
        elif job["kind"] == "analyze":
            report("Подготовка аналитики", 0, 0)
            output = analyze_file(
                str(payload["project"]), _stored_output(group_id, run_id, str(payload["match_output"])),
                _to_mapping(MappingPayload(**payload["mapping"])), _output_dir(group_id, run_id),
                ExportMetadata(
                    export_number=None,
                    periods=[ExportPeriod(str(period["period_start"]), str(period["period_end"])) for period in payload["periods"]],
                    analysis_date=payload.get("analysis_date"), source_file_name=str(payload["source_file_name"]),
                ), progress=report, storage_key=str(payload["storage_key"]),
                rule_snapshot=[StatusRule(**item) for item in payload["rule_snapshot"]], run_id=run_id,
                settings_snapshot=dict(payload["settings_snapshot"]),
            )
        else:
            raise ValueError("Неизвестный тип задания")
        saved_output = output.with_name(f"job-{job_id}_{output.name}")
        shutil.copy2(output, saved_output)
        if job["kind"] == "analyze":
            export_id = db.get_analysis_export_id(str(payload["storage_key"]), run_id)
            if export_id is None:
                raise RuntimeError("Готовый отчёт аналитики не зарегистрирован")
            db.update_run_status(run_id, "completed")
        else:
            export_id = None
            db.update_run_status(run_id, "matched")
        db.update_processing_job(job_id, status="completed", phase="Готово", output_file_name=saved_output.name,
                                 export_id=export_id)
    except Exception as exc:
        db.update_processing_job(job_id, status="failed", phase="Ошибка", error_text=str(exc))
        db.update_run_status(run_id, "failed")


def _worker() -> None:
    try:
        while job := db.claim_next_processing_job():
            _run_job(job)
    finally:
        JOB_WORKER_LOCK.release()
    if db.has_queued_processing_jobs():
        start_worker()


def start_worker() -> None:
    if not JOB_WORKER_LOCK.acquire(blocking=False):
        return
    threading.Thread(target=_worker, daemon=True, name="lead-analytics-jobs").start()


def _export_lk_snapshot(
    session: Any,
    client_id: int,
    project_ids: list[int],
    start: datetime,
    end_exclusive: datetime,
    path: Path,
    source_code_for_display: Callable[[str | None], str],
    source_text_for_display: Callable[[str | None], str],
    project_name_for_display: Callable[[str | None], str],
) -> int:
    if not project_ids:
        raise HTTPException(status_code=400, detail="Добавьте в группу хотя бы один проект")
    base = select(models.ProviderLead).where(
        models.ProviderLead.project_id.in_(project_ids),
        models.ProviderLead.imported_at >= start,
        models.ProviderLead.imported_at < end_exclusive,
    )
    count = int(session.execute(select(func.count()).select_from(base.subquery())).scalar_one())
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="leads")
    written = 0
    try:
        ws.append(["Дата", "Телефон", "Канал", "Источники", "Проект", "lk id", "ext_id", "Клиент"])
        user_info = crud._get_user_info(session, client_id)
        for row in crud.iter_provider_leads_for_export(
            session, start_local=start, end_local=end_exclusive, max_rows=count,
            project_ids=project_ids, user_info=user_info, expose_internal_names=True,
        ):
            ws.append([
                row["imported_at"], row["phone"], source_code_for_display(row["source"]),
                source_text_for_display(row["utm_campaign"]), project_name_for_display(row["project_name"]),
                row["lk_id"], row["ext_id"], row["user_name"],
            ])
            written += 1
        wb.save(path)
    except Exception:
        for sheet in wb.worksheets:
            writer = getattr(sheet, "_writer", None)
            if writer is None:
                continue
            try:
                if not sheet.closed:
                    sheet.close()
            except Exception:
                pass
            try:
                temp_path = Path(writer.out)
                if temp_path.exists():
                    writer.cleanup()
            except Exception:
                pass
        try:
            wb.close()
        except Exception:
            pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if written != count:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Изменилось число идентификаций во время подготовки: было {count}, прочитано {written}")
    after_count = int(session.execute(select(func.count()).select_from(base.subquery())).scalar_one())
    if after_count != count:
        path.unlink(missing_ok=True)
        raise RuntimeError("Идентификации изменились во время подготовки; запустите подготовку повторно")
    return written


def build_router(
    current_admin: Callable[..., Any],
    get_db: Callable[..., Any],
    source_code_for_display: Callable[[str | None], str],
    source_text_for_display: Callable[[str | None], str],
    project_name_for_display: Callable[[str | None], str],
) -> APIRouter:
    router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"], dependencies=[Depends(current_admin)])

    def db_session(session: Any = Depends(get_db)) -> Any:
        return session

    @router.get("/clients")
    def clients(session: Any = Depends(db_session)) -> list[dict[str, object]]:
        rows = session.execute(
            select(models.User, models.ClientProfile)
            .outerjoin(models.ClientProfile, models.ClientProfile.user_id == models.User.id)
            .where(models.User.role == "client")
            .where(models.User.id != 1)
            .order_by(models.ClientProfile.name, models.User.login)
        ).all()
        return [
            {"id": int(user.id), "name": str((profile.name.strip() if profile and profile.name and profile.name.strip() else None) or user.display_name or user.login),
             "table_url": profile.table_url if profile else None,
             "pixel_table_url": profile.pixel_table_url if profile else None}
            for user, profile in rows
        ]

    @router.get("/clients/{client_id}/projects")
    def projects(client_id: int, q: str | None = None, session: Any = Depends(db_session)) -> list[dict[str, object]]:
        client = session.get(models.User, client_id)
        if not client or client.id == 1 or client.role != "client":
            raise HTTPException(status_code=404, detail="Клиент не найден")
        stmt = select(models.Project).where(models.Project.user_id == client_id)
        if q and q.strip():
            stmt = stmt.where(models.Project.name.ilike(f"%{q.strip()}%"))
        rows = session.execute(stmt.order_by(models.Project.name, models.Project.id)).scalars().all()
        return [
            {"id": int(item.id), "name": str(item.name), "status": str(item.status),
             "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
             "collection_source": str(item.collection_source)}
            for item in rows
        ]

    @router.get("/clients/{client_id}/groups")
    def groups(client_id: int, session: Any = Depends(db_session)) -> list[dict[str, object]]:
        client = session.get(models.User, client_id)
        if not client or client.id == 1 or client.role != "client":
            raise HTTPException(status_code=404, detail="Клиент не найден")
        return [
            {"id": item["id"], "client_id": item["client_id"], "name": item["name"],
             "project_ids": item["project_ids"], "spreadsheet_url": item["spreadsheet_url"],
             "archived": item["archived"]}
            for item in db.list_groups(client_id)
        ]

    def validate_project_ids(session: Any, client_id: int, project_ids: list[int]) -> list[int]:
        ids = sorted(set(int(value) for value in project_ids))
        if any(value <= 0 for value in ids):
            raise HTTPException(status_code=400, detail="Некорректный идентификатор проекта")
        if ids:
            rows = session.execute(select(models.Project.id).where(
                models.Project.user_id == client_id, models.Project.id.in_(ids),
            )).all()
            if {int(row[0]) for row in rows} != set(ids):
                raise HTTPException(status_code=400, detail="Все проекты группы должны принадлежать выбранному клиенту")
        return ids

    @router.post("/clients/{client_id}/groups")
    def create_group(client_id: int, payload: GroupPayload, session: Any = Depends(db_session)) -> dict[str, object]:
        client = session.get(models.User, client_id)
        if not client or client.role != "client":
            raise HTTPException(status_code=404, detail="Клиент не найден")
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Укажите название группы")
        group_id = db.create_group(client_id, name, validate_project_ids(session, client_id, payload.project_ids), _sheet_url(payload.spreadsheet_url))
        return db.get_group(group_id) or {}

    @router.put("/groups/{group_id}")
    def put_group(group_id: int, payload: GroupPayload, session: Any = Depends(db_session)) -> dict[str, object]:
        item = _group(group_id, active=True)
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Укажите название группы")
        project_ids = validate_project_ids(session, int(item["client_id"]), payload.project_ids)
        if not db.update_group(group_id, name, project_ids, _sheet_url(payload.spreadsheet_url)):
            raise HTTPException(status_code=404, detail="Группа аналитики не найдена")
        return db.get_group(group_id) or {}

    @router.delete("/groups/{group_id}")
    def archive_group(group_id: int) -> dict[str, bool]:
        _group(group_id, active=True)
        db.archive_group(group_id)
        return {"archived": True}

    @router.get("/groups/{group_id}/status-rules", response_model=StatusRulesResponse)
    def status_rules(group_id: int) -> StatusRulesResponse:
        _group(group_id)
        key = db.group_key(group_id)
        rules = [StatusRuleResponse(
            id=int(row["id"]), pattern=str(row["pattern"]), match_type=str(row["match_type"]),
            group_name=str(row["group_name"]), priority=int(row["priority"]), source="project",
        ) for row in db.list_project_status_rules(key)]
        return StatusRulesResponse(project_rules=rules, system_rules=[], status_groups=ALL_GROUPS)

    @router.put("/groups/{group_id}/status-rules/{rule_id}", response_model=StatusRuleResponse)
    def update_status_rule(group_id: int, rule_id: int, payload: StatusRuleUpdatePayload) -> StatusRuleResponse:
        _group(group_id)
        key, group_name = db.group_key(group_id), payload.group_name.strip()
        if group_name not in ALL_GROUPS:
            raise HTTPException(status_code=400, detail="Неизвестная группа статуса")
        if not db.update_project_status_rule_group(rule_id, key, group_name):
            raise HTTPException(status_code=404, detail="Правило статуса не найдено")
        row = next((item for item in db.list_project_status_rules(key) if int(item["id"]) == rule_id), None)
        assert row is not None
        return StatusRuleResponse(id=rule_id, pattern=str(row["pattern"]), match_type=str(row["match_type"]),
                                  group_name=group_name, priority=int(row["priority"]), source="project")

    @router.delete("/groups/{group_id}/status-rules/{rule_id}")
    def delete_status_rule(group_id: int, rule_id: int) -> dict[str, bool]:
        _group(group_id)
        if not db.delete_project_status_rule(rule_id, db.group_key(group_id)):
            raise HTTPException(status_code=404, detail="Правило статуса не найдено")
        return {"deleted": True}

    @router.get("/groups/{group_id}/exports", response_model=list[ExportRecord])
    def saved_exports(group_id: int) -> list[ExportRecord]:
        _group(group_id)
        return [ExportRecord(**item, report_available=bool(
            (path := analysis_report_path(item.get("report_file_name"))) and path.is_file()
        )) for item in list_exports(db.group_key(group_id))]

    @router.get("/groups/{group_id}/exports/{export_id}/download")
    def download_export(group_id: int, export_id: int) -> FileResponse:
        group = _group(group_id)
        item = next((record for record in list_exports(db.group_key(group_id)) if int(record["id"]) == export_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Выгрузка не найдена")
        path = analysis_report_path(item.get("report_file_name"))
        if not path or not path.is_file():
            raise HTTPException(status_code=404, detail="Файл отчёта для этой выгрузки недоступен")
        run = db.get_run(str(item.get("run_id"))) if item.get("run_id") else None
        report_group_name = run["group_name"] if run else group["name"]
        return FileResponse(path, filename=f"{safe_filename(str(report_group_name))}_выгрузка_{item['export_number']}_аналитика.xlsx",
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @router.delete("/groups/{group_id}/exports/{export_id}")
    def delete_export(group_id: int, export_id: int) -> dict[str, bool]:
        _group(group_id)
        item = next((record for record in list_exports(db.group_key(group_id)) if int(record["id"]) == export_id), None)
        if item is None or not delete_analysis_export(db.group_key(group_id), int(item["export_number"])):
            raise HTTPException(status_code=404, detail="Выгрузка не найдена")
        return {"deleted": True}

    @router.post("/groups/{group_id}/runs", response_model=UploadResponse)
    def create_run(
        group_id: int,
        periods: str = Form(...),
        client_file: UploadFile | None = File(default=None),
        client_url: str | None = Form(default=None),
        session: Any = Depends(db_session),
    ) -> UploadResponse:
        group = _group(group_id, active=True)
        project_ids = [int(value) for value in group["project_ids"]]
        if not project_ids:
            raise HTTPException(status_code=400, detail="В группу не добавлены проекты")
        projects = session.execute(select(models.Project).where(models.Project.id.in_(project_ids))).scalars().all()
        if {int(project.id) for project in projects} != set(project_ids) or any(project.user_id != int(group["client_id"]) for project in projects):
            raise HTTPException(status_code=409, detail="Состав группы изменился; проверьте проекты перед новым запуском")
        project_names = {int(project.id): str(project.name) for project in projects}
        try:
            periods_value = [AnalysisPeriodPayload(**item) for item in json.loads(periods)]
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный список периодов") from exc
        _validate_periods(periods_value)
        url = None if client_file else (_sheet_url(client_url) or _sheet_url(group["spreadsheet_url"]))
        if (client_file is None and not url) or (client_file is not None and client_url and client_url.strip()):
            raise HTTPException(status_code=400, detail="Выберите .xlsx файл клиента или ссылку Google Таблицы")
        if client_file:
            _validate_excel(client_file)
            client_content = client_file.file.read()
            client_name = client_file.filename or "client.xlsx"
            preferred_sheet = None
        else:
            try:
                client_content, client_name, preferred_sheet = read_spreadsheet_url(str(url))
            except Exception as exc:
                raise HTTPException(status_code=502, detail="Не удалось прочитать Google Таблицу. Проверьте ссылку и доступ.") from exc

        start_date = min(item.period_start for item in periods_value)
        end_date = max(item.period_end for item in periods_value)
        start_local = datetime.combine(start_date, datetime.min.time())
        end_exclusive = datetime.max if end_date == date.max else datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        run_id = uuid.uuid4().hex
        run_path = RUNS_DIR / run_id
        input_path = run_path / "input"
        input_path.mkdir(parents=True, exist_ok=False)
        lk_path, client_path = input_path / "lk.xlsx", input_path / "client.xlsx"
        try:
            row_count = _export_lk_snapshot(
                session, int(group["client_id"]), project_ids, start_local, end_exclusive, lk_path,
                source_code_for_display, source_text_for_display, project_name_for_display,
            )
            client_path.write_bytes(client_content)
            lk_inspect = _inspect_upload(lk_path, "Идентификации ЛК.xlsx", "lk", saved_mapping=db.get_match_mapping(db.group_key(group_id), "lk"))
            try:
                client_inspect = _inspect_upload(client_path, client_name, "client", preferred_sheet,
                                                 db.get_match_mapping(db.group_key(group_id), "client"))
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Файл клиента не является корректной книгой XLSX") from exc
            if url:
                db.update_group_spreadsheet_url(group_id, url)
            db.create_run({
                "id": run_id, "group_id": group_id, "client_id": group["client_id"],
                "group_name": group["name"], "project_ids": project_ids,
                "project_names": {str(pid): project_names.get(pid, f"Проект {pid}") for pid in project_ids},
                "periods": [{"period_start": item.period_start.isoformat(), "period_end": item.period_end.isoformat()} for item in periods_value],
                "settings": {"spreadsheet_url": url, "client_file_name": client_name},
                "lk_snapshot": str(lk_path.relative_to(RUNS_DIR)),
                "client_snapshot": str(client_path.relative_to(RUNS_DIR)), "source_file_name": client_name,
            })
        except Exception:
            shutil.rmtree(run_path, ignore_errors=True)
            raise
        return UploadResponse(
            run_id=run_id, project=str(group["name"]), lk=lk_inspect,
            client=client_inspect, lk_row_count=row_count,
        )

    @router.post("/groups/{group_id}/runs/{run_id}/match/jobs", response_model=JobResponse)
    def queue_match(group_id: int, run_id: str, payload: MatchPayload) -> JobResponse:
        _group(group_id, active=True)
        run = _run(group_id, run_id)
        if db.has_active_run_job(run_id):
            raise HTTPException(status_code=409, detail="Для запуска уже выполняется обработка")
        lk_mapping, client_mapping = _to_mapping(payload.lk_mapping), _to_mapping(payload.client_mapping)
        if not lk_mapping.lkid_column or not lk_mapping.source_column:
            raise HTTPException(status_code=400, detail="Для ЛК нужны LKID и полный источник")
        if not client_mapping.status_column:
            raise HTTPException(status_code=400, detail="Для клиента нужен статус")
        group_key = db.group_key(group_id)
        payload_json = {
            "project": run["group_name"], "lk_mapping": payload.lk_mapping.model_dump(),
            "client_mapping": payload.client_mapping.model_dump(),
        }
        try:
            job = db.create_processing_job(run_id, "match", payload_json, group_id=group_id, deferred=True)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Для запуска уже выполняется обработка") from exc
        try:
            db.ensure_project(group_key)
            db.save_match_mapping(group_key, "lk", lk_mapping)
            db.save_match_mapping(group_key, "client", client_mapping)
            db.update_processing_job(int(job["id"]), status="queued", phase="В очереди")
        except Exception as exc:
            db.update_processing_job(int(job["id"]), status="failed", phase="Ошибка",
                                     error_text="Не удалось сохранить настройки сопоставления")
            raise HTTPException(status_code=500, detail="Не удалось сохранить настройки сопоставления") from exc
        start_worker()
        return _job_response(db.get_processing_job(int(job["id"])))

    @router.post("/groups/{group_id}/runs/{run_id}/analyze/setup", response_model=AnalyzeSetupResponse)
    def analyze_setup(group_id: int, run_id: str, payload: AnalyzeSetupPayload) -> AnalyzeSetupResponse:
        _run(group_id, run_id)
        path = _stored_output(group_id, run_id, _latest_match_name(group_id, run_id))
        saved = db.get_column_mapping(db.group_key(group_id))
        mapping = (prepare_analyze_mapping(path, _to_mapping(payload.mapping)) if payload.mapping
                   else _restore_saved_analyze_mapping(path, saved))
        if not mapping.status_column:
            raise HTTPException(status_code=400, detail="Для аналитики нужен столбец статуса")
        df = read_excel_sheet(path, mapping.sheet_name)
        values = df[mapping.status_column].tolist()
        unknown = unknown_statuses(values, db.group_key(group_id))
        return AnalyzeSetupResponse(filename=path.name, mapping=_from_mapping(mapping), sheets=_inspect_workbook(path),
                                    unknown_statuses=unknown, unknown_status_counts=_unknown_status_counts(values, unknown),
                                    status_groups=ALL_GROUPS)

    def _latest_match_name(group_id: int, run_id: str) -> str | None:
        output = _output_dir(group_id, run_id)
        matches = sorted(output.glob("*_сопоставление.xlsx"), key=lambda item: item.stat().st_mtime)
        return matches[-1].name if matches else None

    @router.post("/groups/{group_id}/runs/{run_id}/analyze/jobs", response_model=JobResponse)
    def queue_analyze(group_id: int, run_id: str, payload: AnalyzePayload) -> JobResponse:
        _group(group_id, active=True)
        run = _run(group_id, run_id)
        if db.has_active_run_job(run_id):
            raise HTTPException(status_code=409, detail="Для запуска уже выполняется обработка")
        _validate_periods(payload.periods)
        periods_json = [{"period_start": item.period_start.isoformat(), "period_end": item.period_end.isoformat()} for item in payload.periods]
        if periods_json != run["periods"]:
            raise HTTPException(status_code=400, detail="Периоды должны совпадать с подготовленными данными запуска")
        mapping = _to_mapping(payload.mapping)
        if not mapping.status_column or not mapping.date_column:
            raise HTTPException(status_code=400, detail="Для аналитики выберите колонки даты и статуса")
        match_path = _stored_output(group_id, run_id, _latest_match_name(group_id, run_id))
        mapping = prepare_analyze_mapping(match_path, mapping)
        if not mapping.status_column or not mapping.date_column:
            raise HTTPException(status_code=400, detail="Для аналитики нужны колонки даты и статуса")
        df = read_excel_sheet(match_path, mapping.sheet_name)
        unknown = unknown_statuses(df[mapping.status_column].tolist(), db.group_key(group_id))
        _validate_unknown_status_rules(unknown, payload.status_rules)
        group_key = db.group_key(group_id)
        rules_by_pattern = {
            str(row["pattern"]).strip().casefold(): {
                "pattern": row["pattern"], "match_type": row["match_type"], "group_name": row["group_name"],
                "subgroup_name": row["subgroup_name"], "comment": row["comment"],
                "priority": row["priority"], "project_code": row["project_code"],
            }
            for row in db.list_project_status_rules(group_key)
        }
        for status, group_name in payload.status_rules.items():
            if status.strip() and group_name.strip():
                rules_by_pattern[status.strip().casefold()] = {
                    "pattern": status.strip(), "match_type": "exact", "group_name": group_name.strip(),
                    "subgroup_name": None, "comment": "Добавлено через web", "priority": 10,
                    "project_code": group_key,
                }
        rule_snapshot = list(rules_by_pattern.values())
        settings_snapshot = {"mapping": _from_mapping(mapping).model_dump(), "status_rules": rule_snapshot}
        try:
            job = db.create_processing_job(run_id, "analyze", {
                "project": run["group_name"], "storage_key": group_key, "mapping": _from_mapping(mapping).model_dump(),
                "rule_snapshot": rule_snapshot, "periods": periods_json,
                "analysis_date": payload.analysis_date.isoformat() if payload.analysis_date else None,
                "source_file_name": payload.source_file_name or str(run["source_file_name"]),
                "match_output": match_path.name, "settings_snapshot": settings_snapshot,
            }, group_id=group_id, deferred=True)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Для запуска уже выполняется обработка") from exc
        try:
            db.ensure_project(group_key)
            db.save_column_mapping(group_key, mapping)
            for status, group_name in payload.status_rules.items():
                if status.strip() and group_name.strip():
                    db.add_status_rule(StatusRule(
                        project_code=group_key, pattern=status.strip(), match_type="exact", group_name=group_name.strip(),
                        priority=10, comment="Добавлено через web",
                    ))
            db.update_run_status(run_id, "queued")
            db.update_processing_job(int(job["id"]), status="queued", phase="В очереди")
        except Exception as exc:
            db.update_processing_job(int(job["id"]), status="failed", phase="Ошибка",
                                     error_text="Не удалось сохранить настройки аналитики")
            raise HTTPException(status_code=500, detail="Не удалось сохранить настройки аналитики") from exc
        start_worker()
        return _job_response(db.get_processing_job(int(job["id"])))

    @router.get("/groups/{group_id}/jobs/{job_id}", response_model=JobResponse)
    def processing_job(group_id: int, job_id: int) -> JobResponse:
        _group(group_id)
        try:
            job = db.get_processing_job(job_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if int(job.get("group_id") or -1) != group_id:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        return _job_response(job)

    @router.get("/groups/{group_id}/jobs/{job_id}/preview", response_model=WorkbookPreview)
    def processing_job_preview(group_id: int, job_id: int) -> WorkbookPreview:
        _group(group_id)
        try:
            job = db.get_processing_job(job_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if int(job.get("group_id") or -1) != group_id:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        if job["status"] != "completed":
            raise HTTPException(status_code=409, detail="Задание ещё не завершено")
        path = _stored_output(group_id, str(job["run_id"]), str(job["output_file_name"]))
        return _preview(path)

    return router
