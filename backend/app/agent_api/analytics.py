"""Read aggregates and enqueue prepared runs in the existing backend worker."""
from copy import deepcopy
from datetime import date

from fastapi import HTTPException

from .. import models
from ..lead_analytics import db, router as pipeline
from ..lead_analytics.export_history import list_exports, analysis_report_path
from ..lead_analytics.excel_reader import read_excel_sheet
from ..lead_analytics.structure_detector import prepare_analyze_mapping
from ..lead_analytics.status_classifier import unknown_statuses
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
    return {key: item.get(key) for key in ("id", "run_id", "kind", "status", "processed_rows", "total_rows", "export_id")}


def execute_read(action, session, params):
    if action == "analytics.groups":
        client_id = _required(params, "client_id")
        user = session.get(models.User, client_id)
        if not user or user.role != "client" or user.id == 1:
            raise AgentError("CLIENT_NOT_FOUND", "Клиент не найден", 404)
        fields = ("id", "client_id", "name", "project_ids", "archived", "created_at", "updated_at")
        return _page([{key: group.get(key) for key in fields} for group in db.list_groups(client_id)], params)
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
        return {"run_id": run["id"], "group_id": group_id, "status": run["status"],
                "job": _job(run["id"]), "result": _report(item) if item else None}
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
            _needs("UNKNOWN_STATUSES", "Распределите неизвестные статусы в разделе Аналитика", {"statuses": unknown})
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
