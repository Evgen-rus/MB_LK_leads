"""Safe re-analysis workflow using the existing Lead Analytics queue."""
import threading

from fastapi import HTTPException

from ..lead_analytics import db, router as pipeline
from ..lead_analytics.config import MATCHED_SHEET_NAME
from ..lead_analytics.excel_reader import read_excel_sheet
from ..lead_analytics.models import ColumnMapping, StatusRule
from ..lead_analytics.status_classifier import ALL_GROUPS, unknown_statuses
from .contracts import AgentError
from .periods import requested_periods

_PREPARE_LOCK = threading.Lock()


def _needs(code, message, data=None):
    raise AgentError(code, message, 409, "needs_input", data)


def _active_jobs(group_id):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, run_id, kind, status FROM processing_jobs "
            "WHERE group_id=? AND status IN ('queued','running') ORDER BY id DESC",
            (int(group_id),),
        ).fetchall()
    return [{key: row[key] for key in ("id", "run_id", "kind", "status")} for row in rows]


def execute_prepare(session, params, *, analytics_display=None):
    if not _PREPARE_LOCK.acquire(blocking=False):
        raise AgentError("RUN_BUSY", "Уже выполняется подготовка аналитики", 409)
    try:
        return _execute_prepare(session, params, analytics_display=analytics_display)
    finally:
        _PREPARE_LOCK.release()


def _execute_prepare(session, params, *, analytics_display=None):
    group_id = int(params["group_id"])
    group = db.get_group(group_id)
    if not group:
        raise AgentError("GROUP_NOT_FOUND", "Группа аналитики не найдена", 404)
    if group["archived"]:
        raise AgentError("GROUP_ARCHIVED", "Группа архивирована", 409)
    periods = requested_periods(params)
    active = _active_jobs(group_id)
    if active:
        raise AgentError("RUN_BUSY", "Для группы уже выполняется обработка", 409, data={"active_jobs": active})

    # Check durable settings before the only external read in this workflow.
    from .analytics import execute_read
    plan = execute_read("analytics.plan", session, {"group_id": group_id})
    if not plan["settings_ready"]:
        _needs("PREPARATION_REQUIRED", "Сохранённые настройки группы требуют проверки", {
            "group_id": group_id, "missing_settings": plan["missing_settings"],
        })
    url = group.get("spreadsheet_url")
    try:
        url = pipeline._sheet_url(url)
    except HTTPException:
        _needs("PREPARATION_REQUIRED", "Сохранённая Google Таблица требует проверки", {
            "group_id": group_id, "missing_settings": ["spreadsheet_url"],
        })
    if not url:
        _needs("PREPARATION_REQUIRED", "Для группы не настроена Google Таблица", {
            "group_id": group_id, "missing_settings": ["spreadsheet_url"],
        })
    lk_mapping = db.get_match_mapping(db.group_key(group_id), "lk")
    client_mapping = db.get_match_mapping(db.group_key(group_id), "client")
    analysis_mapping = db.get_column_mapping(db.group_key(group_id))
    if not lk_mapping or not client_mapping or not analysis_mapping:
        _needs("PREPARATION_REQUIRED", "Сохранённые сопоставления требуют проверки", {
            "group_id": group_id, "missing_settings": ["match_mappings"],
        })
    matched_columns = {
        "Дата", "Телефон", "Канал", "Источники", "Проект", "lk id", "ext_id", "Клиент",
        "LKID", "Нормализованный телефон", "Дата из ЛК", "Полный источник из ЛК", "Статус клиента",
        "Комментарий клиента", "Дата клиента", "Ключ сопоставления", "Тип сопоставления",
        "Признак дубля клиента", "Комментарий сопоставления",
    }
    analysis_fields = ("date_column", "phone_column", "channel_column", "source_column", "status_column", "comment_column")
    if (analysis_mapping.sheet_name != MATCHED_SHEET_NAME or not analysis_mapping.date_column
            or not analysis_mapping.status_column
            or any(getattr(analysis_mapping, field) and getattr(analysis_mapping, field) not in matched_columns
                   for field in analysis_fields)):
        _needs("MAPPING_REQUIRED", "Сохранённые колонки даты и статуса не соответствуют результату сопоставления", {
            "group_id": group_id, "missing_settings": ["analysis_mapping_columns"],
        })

    current_ids = [int(value) for value in group["project_ids"]]
    if params.get("confirmed_project_ids") is None and plan["new_projects"]:
        _needs("PROJECT_CONFIRMATION_REQUIRED", "Подтвердите состав группы для новых проектов", {
            "group_id": group_id, "new_projects": plan["new_projects"],
            "excluded_candidate_project_ids": plan["excluded_candidate_project_ids"],
        })
    requested_additions = params.get("confirmed_project_ids") or []
    if len(requested_additions) != len(set(requested_additions)):
        raise AgentError("INVALID_PROJECTS", "Список подтверждённых проектов содержит повторы", 422)
    candidates = {item["project_id"] for item in plan["new_projects"]}
    additions = [int(value) for value in requested_additions]
    if any(value in current_ids or value not in candidates for value in additions):
        raise AgentError("INVALID_PROJECTS", "Можно добавить только проекты из списка новых проектов группы", 422,
                         data={"candidate_project_ids": sorted(candidates)})
    if additions:
        client_id = int(group["client_id"])
        try:
            pipeline.validate_project_ids(session, client_id, additions)
        except HTTPException:
            raise AgentError("INVALID_PROJECTS", "Подтверждённые проекты не принадлежат клиенту группы", 422) from None
        updated_ids = sorted(set(current_ids).union(additions))
        if not db.update_group(group_id, str(group["name"]), updated_ids, group.get("spreadsheet_url")):
            raise AgentError("GROUP_NOT_FOUND", "Группа аналитики недоступна", 404)

    if analytics_display is None or len(analytics_display) != 3 or not all(callable(fn) for fn in analytics_display):
        raise AgentError("ANALYTICS_UNAVAILABLE", "Не настроено представление идентификаций для аналитики", 503)
    period_payloads = [pipeline.AnalysisPeriodPayload(**item) for item in periods]
    try:
        prepared = pipeline.prepare_run_from_source(
            session, group_id, period_payloads, *analytics_display, client_url=url,
            required_mappings=(lk_mapping, client_mapping),
        )
        queued = pipeline.enqueue_match_job(
            group_id, prepared.run_id, lk_mapping, client_mapping, save_mappings=False,
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            _needs("MAPPING_REQUIRED", "Сохранённые листы и колонки требуют проверки", {
                "group_id": group_id, "missing_settings": ["match_mapping_columns"],
            })
        if exc.status_code == 502:
            raise AgentError("SHEETS_UNAVAILABLE", "Не удалось прочитать настроенную Google Таблицу", 502) from None
        if exc.status_code == 404:
            raise AgentError("GROUP_NOT_FOUND", "Группа аналитики не найдена", 404) from None
        raise AgentError("PREPARATION_FAILED", "Не удалось подготовить входные снимки аналитики", 422) from None
    except Exception:
        # Never expose Google responses, local paths, workbook contents, or exception text.
        raise AgentError("PREPARATION_FAILED", "Не удалось подготовить входные снимки аналитики", 500) from None

    return {
        "group_id": group_id, "run_id": prepared.run_id,
        "periods": periods,
        "added_project_ids": sorted(additions),
        "excluded_candidate_project_ids": sorted(candidates.difference(additions)),
        "job": {key: getattr(queued, key) for key in ("id", "run_id", "kind", "status", "processed_rows", "total_rows")},
    }


def execute_confirm_statuses(session, params):
    group_id, run_id = int(params["group_id"]), str(params["run_id"])
    group = db.get_group(group_id)
    if not group:
        raise AgentError("GROUP_NOT_FOUND", "Группа аналитики не найдена", 404)
    if group["archived"]:
        raise AgentError("GROUP_ARCHIVED", "Группа архивирована", 409)
    run = db.get_run(run_id)
    if not run or int(run["group_id"]) != group_id:
        raise AgentError("RUN_NOT_FOUND", "Запуск не найден", 404)
    if _active_jobs(group_id):
        raise AgentError("RUN_BUSY", "Для группы уже выполняется обработка", 409)
    if run["status"] != "matched":
        _needs("MATCH_REQUIRED", "Сначала завершите сопоставление строк", {"run_id": run_id})

    key = db.group_key(group_id)
    mapping = db.get_column_mapping(key)
    try:
        path = pipeline._stored_output(group_id, run_id, pipeline._latest_match_name(group_id, run_id))
        if not mapping or not mapping.sheet_name or not mapping.status_column:
            _needs("MAPPING_REQUIRED", "Сохраните проверенные колонки даты и статуса")
        frame = read_excel_sheet(path, mapping.sheet_name)
        if mapping.status_column not in frame.columns:
            _needs("MAPPING_REQUIRED", "Сохранённая колонка статуса отсутствует в результате сопоставления")
        unknown = unknown_statuses(frame[mapping.status_column].tolist(), key)
    except AgentError:
        raise
    except HTTPException:
        _needs("MATCH_REQUIRED", "Результат сопоставления недоступен для этой группы")
    except Exception:
        _needs("MATCH_REQUIRED", "Проверьте результат сопоставления в разделе Аналитика")

    assignments = params["status_rules"]
    if not isinstance(assignments, dict) or set(assignments) != set(unknown):
        _needs("UNKNOWN_STATUSES", "Подтвердите категорию для каждого текущего неизвестного статуса", {
            "statuses": unknown, "allowed_categories": list(ALL_GROUPS),
        })
    if any(not isinstance(category, str) or category not in ALL_GROUPS for category in assignments.values()):
        raise AgentError("INVALID_STATUS_CATEGORY", "Выберите категорию из разрешённого списка", 422,
                         data={"allowed_categories": list(ALL_GROUPS)})
    db.ensure_project(key)
    for status, category in assignments.items():
        db.add_status_rule(StatusRule(
            project_code=key, pattern=status, match_type="exact", group_name=category,
            priority=10, comment="Confirmed via Agent Interface",
        ))
    return {"group_id": group_id, "run_id": run_id,
            "confirmed_statuses": [{"status": status, "category": assignments[status]} for status in unknown],
            "analysis_queued": False}
