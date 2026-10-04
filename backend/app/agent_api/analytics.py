"""Read aggregates and enqueue prepared runs in the existing backend worker."""
from copy import deepcopy
from datetime import date
import json
import re

from fastapi import HTTPException
from sqlalchemy import select

from .. import models
from ..lead_analytics import db, router as pipeline
from ..lead_analytics.export_history import list_exports, analysis_report_path
from ..lead_analytics.excel_reader import read_excel_sheet
from ..lead_analytics.structure_detector import prepare_analyze_mapping
from ..lead_analytics.status_classifier import ALL_GROUPS, unknown_statuses
from .contracts import AgentError


def _required(params, key):
    if not params.get(key):
        raise AgentError("INVALID_PARAMETERS", f"Требуется {key}", 422)
    return params[key]


def _group(group_id, active=False):
    group = db.get_group(group_id)
    if not group:
        raise AgentError("GROUP_NOT_FOUND", "Группа аналитики не найдена", 404)
    if active and group["archived"]:
        raise AgentError("GROUP_ARCHIVED", "Группа архивирована", 409)
    return group


def _page(items, params):
    offset, limit = params.get("offset", 0), params.get("limit", 50)
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}


def _report(item):
    fields = ("id", "export_number", "period_start", "period_end", "analysis_date",
              "total_count", "missed_count", "missed_rate", "quality_count", "quality_rate",
              "demand_count", "demand_rate", "periods", "run_id")
    result = {key: item.get(key) for key in fields}
    path = analysis_report_path(item.get("report_file_name"))
    result["report_available"] = bool(path and path.is_file())
    return result


def _job(run_id):
    # Kept inside the backend storage boundary; never available to the CLI.
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM processing_jobs WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
    if not row:
        return None
    item = db.get_processing_job(int(row["id"]))
    result = {key: item.get(key) for key in ("id", "run_id", "kind", "status", "processed_rows", "total_rows", "export_id")}
    result["error_code"] = "ANALYTICS_FAILED" if item.get("status") == "failed" else None
    return result


def _active_jobs(group_id):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, run_id, kind, status FROM processing_jobs "
            "WHERE group_id=? AND status IN ('queued','running') ORDER BY id DESC",
            (int(group_id),),
        ).fetchall()
    return [{key: row[key] for key in ("id", "run_id", "kind", "status")} for row in rows]


def _latest_runs(group_id):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, status, periods_json FROM analytics_runs WHERE group_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 5",
            (int(group_id),),
        ).fetchall()
    result = []
    for row in rows:
        run_id = str(row["id"])
        try:
            periods = json.loads(row["periods_json"])
        except (TypeError, ValueError):
            periods = []
        job = _job(run_id)
        result.append({
            "run_id": run_id, "status": str(row["status"]), "periods": periods,
            "job": ({key: job.get(key) for key in ("id", "kind", "status", "error_code")} if job else None),
        })
    return result


def _validate_plan_period(params):
    start, end = params.get("period_start"), params.get("period_end")
    if not start and not end:
        return None
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last or (last - first).days >= 366:
            raise ValueError()
    except (ValueError, TypeError):
        raise AgentError("INVALID_PERIOD", "Укажите обе даты YYYY-MM-DD; максимум 366 дней", 422) from None
    return {"period_start": first.isoformat(), "period_end": last.isoformat()}


def _plan(group_id, session, params):
    group = _group(group_id)
    key = db.group_key(group_id)
    lk = db.get_match_mapping(key, "lk")
    client = db.get_match_mapping(key, "client")
    mapping = db.get_column_mapping(key)
    rules = db.list_project_status_rules(key)
    missing = []
    if group.get("archived"):
        missing.append("group_archived")
    if not group.get("spreadsheet_url"):
        missing.append("spreadsheet_url")
    if lk is None or not lk.sheet_name or not lk.lkid_column or not lk.source_column:
        missing.append("lk_match_mapping")
    if client is None or not client.sheet_name or not client.status_column:
        missing.append("client_match_mapping")
    if mapping is None or not mapping.sheet_name or not mapping.date_column or not mapping.status_column:
        missing.append("analysis_mapping")
    if any(str(rule["group_name"]) not in ALL_GROUPS for rule in rules):
        missing.append("status_rules_invalid")

    client_id = int(group["client_id"])
    project_ids = [int(value) for value in group.get("project_ids", [])]
    projects = session.execute(
        select(models.Project).where(models.Project.user_id == client_id)
    ).scalars().all()
    project_by_id = {int(project.id): project for project in projects}
    group_projects = [project_by_id[project_id] for project_id in project_ids if project_id in project_by_id]
    if len(group_projects) != len(set(project_ids)) or len(project_ids) != len(set(project_ids)):
        missing.append("group_projects_invalid")
    tag_pattern = re.compile(r"\[(LR\d+)\]")
    tags = {tag for project in group_projects for tag in tag_pattern.findall(str(project.name or ""))}
    current_ids = set(project_ids)
    candidates = [
        {"project_id": int(project.id), "name": str(project.name), "status": str(project.status),
         "tags": sorted(tags.intersection(tag_pattern.findall(str(project.name or ""))))}
        for project in projects
        if int(project.id) not in current_ids and tags.intersection(tag_pattern.findall(str(project.name or "")))
    ]
    candidates.sort(key=lambda item: item["project_id"])

    return {
        "group_id": int(group["id"]), "group_name": str(group["name"]), "client_id": client_id,
        "archived": bool(group.get("archived")), "project_ids": project_ids,
        "spreadsheet_url": str(group.get("spreadsheet_url") or "") or None,
        "saved_sheets": {
            "lk": lk.sheet_name if lk else None,
            "client": client.sheet_name if client else None,
            "analysis": mapping.sheet_name if mapping else None,
        },
        "settings_ready": not missing, "state": "ready" if not missing else "needs_input",
        "missing_settings": missing, "status_rule_count": len(rules),
        "period": _validate_plan_period(params), "new_projects": candidates,
        "excluded_candidate_project_ids": [item["project_id"] for item in candidates],
        "active_jobs": _active_jobs(group_id), "latest_runs": _latest_runs(group_id),
    }


def execute_read(action, session, params):
    if action == "analytics.groups":
        client_id = _required(params, "client_id")
        user = session.get(models.User, client_id)
        if not user or user.role != "client" or user.id == 1:
            raise AgentError("CLIENT_NOT_FOUND", "Клиент не найден", 404)
        fields = ("id", "client_id", "name", "project_ids", "archived", "created_at", "updated_at")
        return _page([{key: group.get(key) for key in fields} for group in db.list_groups(client_id)], params)
    if action == "analytics.plan":
        group_id = _required(params, "group_id")
        return _plan(group_id, session, params)
    group_id = _required(params, "group_id")
    _group(group_id)
    exports = list_exports(db.group_key(group_id))
    if action == "analytics.history":
        return _page([_report(item) for item in exports], params)
    if action == "analytics.result":
        if bool(params.get("export_id")) == bool(params.get("run_id")):
            raise AgentError("INVALID_PARAMETERS", "Укажите export_id или run_id", 422)
        if params.get("export_id"):
            item = next((item for item in exports if item["id"] == params["export_id"]), None)
            if not item:
                raise AgentError("RESULT_NOT_FOUND", "Результат аналитики не найден", 404)
            return _report(item)
        run = db.get_run(params["run_id"])
        if not run or int(run["group_id"]) != group_id:
            raise AgentError("RUN_NOT_FOUND", "Запуск не найден", 404)
        item = next((item for item in exports if item.get("run_id") == run["id"]), None)
        job = _job(run["id"])
        return {"run_id": run["id"], "group_id": group_id, "status": run["status"],
                "periods": run["periods"], "client_id": run["client_id"],
                "project_ids": run["project_ids"],
                "job": job, "error_code": (job or {}).get("error_code"),
                "needs_input": bool(job and job.get("status") == "failed"),
                "result": _report(item) if item else None}
    raise AgentError("CAPABILITY_NOT_FOUND", "Capability не найдена", 404)


def _needs(code, message, data=None):
    raise AgentError(code, message, 409, "needs_input", data)


def execute_run(params):
    run_id = params.get("run_id")
    if not run_id:
        group_id = _required(params, "group_id")
        _group(group_id, active=True)
        if not _validate_requested_period(params):
            raise AgentError("INVALID_PARAMETERS", "Укажите period_start и period_end", 422)
        _needs("PREPARATION_REQUIRED", "Подготовьте снимок и сопоставление в разделе Аналитика, затем укажите run_id",
               {"group_id": group_id, "required": ["client_snapshot", "matched_workbook", "saved_mapping", "status_rules"]})
    run = db.get_run(run_id)
    if not run or (params.get("group_id") and int(run["group_id"]) != params["group_id"]):
        raise AgentError("RUN_NOT_FOUND", "Запуск не найден", 404)
    group_id = int(run["group_id"])
    _group(group_id, active=True)
    requested = _validate_requested_period(params)
    if requested and run["periods"] != [requested]:
        raise AgentError("PERIOD_MISMATCH", "Период должен совпадать со снимком запуска", 409)
    if run["status"] == "completed":
        _needs("NEW_RUN_REQUIRED", "Анализ завершён; для нового анализа подготовьте новый запуск")
    job = _job(run_id)
    if job and job["status"] in {"queued", "running"}:
        return {"run_id": run_id, "group_id": group_id, "job": job, "already_active": True}
    if run["status"] != "matched":
        _needs("MATCH_REQUIRED", "Завершите сопоставление и проверьте данные в разделе Аналитика")
    key = db.group_key(group_id)
    mapping = db.get_column_mapping(key)
    if not mapping or not mapping.date_column or not mapping.status_column:
        _needs("MAPPING_REQUIRED", "Сохраните проверенное сопоставление колонок даты и статуса")
    try:
        path = pipeline._stored_output(group_id, run_id, pipeline._latest_match_name(group_id, run_id))
        frame = read_excel_sheet(path, mapping.sheet_name)
        fields = ("date_column", "phone_column", "channel_column", "source_column", "status_column", "comment_column")
        if any(getattr(mapping, key) and getattr(mapping, key) not in frame.columns for key in fields):
            _needs("MAPPING_REQUIRED", "Сохранённые колонки не соответствуют снимку")
        prepared = prepare_analyze_mapping(path, deepcopy(mapping))
        if pipeline._from_mapping(prepared) != pipeline._from_mapping(mapping):
            _needs("MAPPING_REQUIRED", "Сопоставление требует проверки в разделе Аналитика")
        unknown = unknown_statuses(frame[mapping.status_column].tolist(), key)
        if unknown:
            _needs("UNKNOWN_STATUSES", "Распределите неизвестные статусы в разделе Аналитика",
                   {"statuses": unknown, "allowed_categories": list(ALL_GROUPS)})
        payload = pipeline.AnalyzePayload(mapping=pipeline._from_mapping(mapping), status_rules={},
                                          periods=[pipeline.AnalysisPeriodPayload(**period) for period in run["periods"]])
        queued = pipeline.enqueue_analysis(group_id, run_id, payload, save_settings=False)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise AgentError("RUN_BUSY", "Для запуска уже выполняется обработка", 409) from None
        if exc.status_code >= 500:
            raise AgentError("ANALYTICS_FAILED", "Не удалось поставить анализ в очередь", 500) from None
        _needs("PREPARATION_REQUIRED", "Подготовленные данные требуют проверки в разделе Аналитика")
    except (ValueError, KeyError, FileNotFoundError):
        _needs("MAPPING_REQUIRED", "Проверьте файл и сопоставление колонок в разделе Аналитика")
    return {"run_id": run_id, "group_id": group_id, "job": {
        key: getattr(queued, key) for key in ("id", "run_id", "kind", "status", "processed_rows", "total_rows", "export_id")}}


def _validate_requested_period(params):
    start, end = params.get("period_start"), params.get("period_end")
    if not start and not end:
        return None
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last or (last - first).days >= 366:
            raise ValueError()
    except (ValueError, TypeError):
        raise AgentError("INVALID_PERIOD", "Укажите обе даты YYYY-MM-DD; максимум 366 дней", 422) from None
    return {"period_start": first.isoformat(), "period_end": last.isoformat()}
