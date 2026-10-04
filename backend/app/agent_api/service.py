"""Read-only operational views for the Agent Interface."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from .. import crud, models
from .contracts import AgentError


def _error(code: str, message: str, status: int = 400) -> AgentError:
    return AgentError(code, message, status)


def _date_value(value: Any, name: str, default: date) -> date:
    if value is None:
        return default
    if isinstance(value, datetime):
        raise _error("INVALID_DATE_RANGE", f"{name} должен иметь формат YYYY-MM-DD", 422)
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.isoformat() == value:
            return parsed
    raise _error("INVALID_DATE_RANGE", f"{name} должен иметь формат YYYY-MM-DD", 422)


def _date_range(params: dict, settings: dict) -> tuple[date, date, datetime, datetime, datetime]:
    try:
        tz = ZoneInfo(str(settings.get("SHEETS_TZ") or "Europe/Moscow"))
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone(timedelta(hours=3))
    today = datetime.now(tz).date()
    start_date = _date_value(params.get("from_date"), "from_date", today)
    end_date = _date_value(params.get("to_date"), "to_date", today)
    days = (end_date - start_date).days + 1
    if days < 1:
        raise _error("INVALID_DATE_RANGE", "Дата окончания не может быть раньше даты начала", 422)
    if days > 366:
        raise _error("INVALID_DATE_RANGE", "Период не может превышать 366 дней", 422)
    if end_date == date.max:
        raise _error("INVALID_DATE_RANGE", "Некорректная граница периода", 422)
    start = datetime.combine(start_date, time.min)
    end_exclusive = datetime.combine(end_date + timedelta(days=1), time.min)
    end_inclusive = end_exclusive - timedelta(seconds=1)
    return start_date, end_date, start, end_exclusive, end_inclusive


def _positive_id(params: dict, name: str) -> int:
    value = params.get(name)
    if isinstance(value, int) and not isinstance(value, bool):
        result = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        result = int(value)
    else:
        raise _error("INVALID_PARAMETERS", f"Требуется целый параметр {name}", 422)
    if result < 1:
        raise _error("INVALID_PARAMETERS", f"Некорректный параметр {name}", 422)
    return result


def _pagination(params: dict) -> tuple[int, int]:
    def integer(name: str, default: int) -> int:
        value = params.get(name, default)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isascii() and value.isdigit():
            return int(value)
        raise _error("INVALID_PARAMETERS", "Некорректные параметры пагинации", 422)

    limit, offset = integer("limit", 50), integer("offset", 0)
    if not 1 <= limit <= 200 or offset < 0:
        raise _error("INVALID_PARAMETERS", "Пагинация: limit 1..200, offset от 0", 422)
    return limit, offset


def _require_client(db: Session, client_id: int) -> models.User:
    user = db.get(models.User, client_id)
    if user is None or int(user.id) == 1 or user.role != crud.ROLE_CLIENT:
        raise _error("CLIENT_NOT_FOUND", "Клиент не найден", 404)
    return user


def _client_items(db: Session, start: datetime, end: datetime):
    return crud.admin_clients_summary(db, start_local=start, end_local=end).items


def _client_summary(item) -> dict:
    return {
        "client_id": int(item.user.id),
        "name": str(item.user.name or ""),
        "projects": int(item.projectCount),
        "used_total": int(item.usedTotal),
        "used_period": int(item.usedPeriod),
        "used_period_by_source": {str(key): int(value) for key, value in item.usedPeriodBySource.items()},
        "remaining": int(item.remaining),
        "data_collection_status": str(item.dataCollectionStatus),
    }


def _project_summary(item) -> dict:
    return {
        "project_id": int(item.id),
        "client_id": int(item.user.id) if item.user and item.user.id else None,
        "name": str(item.name or ""),
        "status": str(item.status),
        "delivery_status": str(item.deliveryStatus),
        "collection_source": str(item.collectionSource),
        "data_source_code": str(item.dataSourceCode),
        "data_limit": int(item.dataLimit),
        "is_top": bool(item.isTop),
        "numbers_today": int(item.numbersToday),
        "numbers_total": int(item.numbersTotal),
        "numbers_period": int(item.numbersPeriod),
        "days_received": str(item.daysReceived or ""),
        "sources_count": int(item.sourcesCount),
        "created_at": str(item.createdAt),
    }


def _dashboard(
    db: Session,
    settings: dict,
    start_date: date,
    end_date: date,
    client_id: int | None = None,
    selected_chart_range: bool = False,
):
    tz_name = str(settings.get("SHEETS_TZ") or "Europe/Moscow")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone(timedelta(hours=3))
    today = datetime.now(tz).date()

    def day(value: date) -> datetime:
        return datetime.combine(value, time.min)

    today_start = day(today)
    today_end = day(today + timedelta(days=1))
    return crud.admin_dashboard(
        db,
        start_local=day(start_date),
        end_local=day(end_date + timedelta(days=1)),
        today_start=today_start,
        today_end=today_end,
        yesterday_start=day(today - timedelta(days=1)),
        yesterday_end=today_start,
        last7_start=day(today - timedelta(days=6)),
        last30_start=day(today - timedelta(days=29)),
        chart_start=day(start_date if selected_chart_range else today - timedelta(days=29)),
        chart_end=day(end_date + timedelta(days=1)) if selected_chart_range else today_end,
        client_id=client_id,
    )


def _safe_dashboard(data) -> dict:
    s = data.summary
    attention = data.attention

    def client(item) -> dict:
        return {
            "client_id": int(item.clientId), "name": str(item.clientName),
            "remaining": int(item.remaining), "level": str(item.level),
            "active_projects": int(item.activeProjects), "daily_spend": int(item.dailySpend),
        }

    def project(item) -> dict:
        return {
            "project_id": int(item.projectId), "name": str(item.projectName),
            "client_id": item.clientId, "client_name": item.clientName,
            "source": str(item.source), "detected_at": item.detectedAt,
        }

    def point(item) -> dict:
        return {"date": str(item.date), "value": int(item.value)}

    def breakdown(item) -> dict:
        return {"key": str(item.key), "label": str(item.label), "value": int(item.value)}

    return {
        "summary": {
            "clients": int(s.clients), "projects": int(s.projects),
            "active_projects": int(s.activeProjects), "paused_projects": int(s.pausedProjects),
            "operator_blocked_projects": int(s.operatorBlockedProjects),
            "total_remaining": int(s.totalRemaining), "leads_period": int(s.leadsPeriod),
            "leads_today": int(s.leadsToday), "leads_yesterday": int(s.leadsYesterday),
            "leads_7_days": int(s.leads7Days), "leads_30_days": int(s.leads30Days),
            "average_workday_7": float(s.averageWorkday7),
            "average_workday_3": float(s.averageWorkday3),
            "unlinked_leads": int(s.unlinkedLeads), "operation_errors": int(s.operationErrors),
        },
        "attention": {
            "critical_clients": [client(item) for item in attention.criticalClients],
            "risk_clients": [client(item) for item in attention.riskClients],
            "warning_clients": [client(item) for item in attention.warningClients],
            "operator_blocked_projects": [project(item) for item in attention.operatorBlockedProjects],
            "unlinked_leads": {
                "total": int(attention.unlinkedLeads.total),
                "ambiguous": int(attention.unlinkedLeads.ambiguous),
                "not_found": int(attention.unlinkedLeads.notFound),
                "unknown": int(attention.unlinkedLeads.unknown),
            },
            # The dashboard's item details contain free-form provider error text.
            "operation_errors": {"total": int(attention.operationErrors.total)},
        },
        "charts": {
            "leads_daily": [point(item) for item in data.charts.leadsDaily],
            "source_breakdown": [breakdown(item) for item in data.charts.sourceBreakdown],
            "project_statuses": [breakdown(item) for item in data.charts.projectStatuses],
        },
        "rankings": {
            "top_clients_by_leads": [
                {"client_id": int(item.clientId), "name": str(item.clientName),
                 "value": int(item.value), "active_projects": int(item.activeProjects)}
                for item in data.rankings.topClientsByLeads
            ],
            "top_clients_by_active_projects": [
                {"client_id": int(item.clientId), "name": str(item.clientName),
                 "value": int(item.value), "active_projects": int(item.activeProjects)}
                for item in data.rankings.topClientsByActiveProjects
            ],
        },
    }


def _project_stats(db: Session, settings: dict, project_id: int, params: dict) -> dict:
    project = db.get(models.Project, project_id)
    if project is None:
        raise _error("PROJECT_NOT_FOUND", "Проект не найден", 404)
    start_date, end_date, start, end_exclusive, _ = _date_range(params, settings)
    chart = crud.project_leads_chart(db, project=project, start_local=start, end_local=end_exclusive)
    return {
        "project_id": int(chart.projectId), "name": str(chart.projectName),
        "from_date": start_date.isoformat(), "to_date": end_date.isoformat(),
        "total": int(chart.total), "average_daily": float(chart.averageDaily),
        "daily": [{"date": str(item.date), "value": int(item.value)} for item in chart.leadsDaily],
        "by_source": [{"key": str(item.key), "value": int(item.value)} for item in chart.sourceBreakdown],
    }


def execute_read(action: str, db_session: Session, params: dict, settings: dict) -> dict:
    """Execute one registered non-mutating operational read."""
    db = db_session
    if action in {"overview", "clients.list", "clients.find", "client.show", "projects.list", "leads.stats", "project.stats"}:
        start_date, end_date, start, end_exclusive, end_inclusive = _date_range(params, settings)

    if action == "overview":
        client_id = params.get("client_id")
        if client_id is not None:
            _require_client(db, _positive_id(params, "client_id"))
        return {
            "from_date": start_date.isoformat(), "to_date": end_date.isoformat(),
            **_safe_dashboard(_dashboard(db, settings, start_date, end_date, client_id)),
        }

    if action in {"clients.list", "clients.find", "client.show"}:
        items = _client_items(db, start, end_inclusive)
        if action == "client.show":
            client_id = _positive_id(params, "client_id")
            _require_client(db, client_id)
            item = next((item for item in items if int(item.user.id) == client_id), None)
            if item is None:
                raise _error("CLIENT_NOT_FOUND", "Клиент не найден", 404)
            dashboard = _dashboard(db, settings, start_date, end_date, client_id)
            result = _client_summary(item)
            result["leads_30d"] = int(dashboard.summary.leads30Days)
            result["from_date"] = start_date.isoformat()
            result["to_date"] = end_date.isoformat()
            return result

        if action == "clients.find":
            query = str(params.get("q") or "").strip()
            if not query:
                raise _error("INVALID_PARAMETERS", "Для поиска укажите q", 422)
            folded = query.casefold()
            items = [item for item in items if folded in str(item.user.name or "").casefold()]
        limit, offset = _pagination(params)
        total = len(items)
        return {
            "from_date": start_date.isoformat(), "to_date": end_date.isoformat(),
            "items": [_client_summary(item) for item in items[offset:offset + limit]],
            "total": total, "limit": limit, "offset": offset,
        }

    if action == "projects.list":
        client_id = _positive_id(params, "client_id")
        _require_client(db, client_id)
        limit, offset = _pagination(params)
        query = params.get("q")
        result = crud.admin_list_all_projects(
            db, offset=offset, limit=limit, q=query, user_id_filter=client_id,
            start_local=start, end_local=end_inclusive,
            include_deleted=True, include_archived=True,
        )
        return {
            "client_id": client_id, "from_date": start_date.isoformat(), "to_date": end_date.isoformat(),
            "items": [_project_summary(item) for item in result.items],
            "total": int(result.total), "limit": limit, "offset": offset,
        }

    if action == "project.show":
        project_id = _positive_id(params, "project_id")
        item = crud.admin_get_project(db, project_id)
        if item is None:
            raise _error("PROJECT_NOT_FOUND", "Проект не найден", 404)
        return _project_summary(item)

    if action == "project.stats":
        return _project_stats(db, settings, _positive_id(params, "project_id"), params)

    if action == "leads.stats":
        project_id = params.get("project_id")
        client_id = params.get("client_id")
        if project_id is not None:
            project_id = _positive_id(params, "project_id")
            project = db.get(models.Project, project_id)
            if project is None or (client_id is not None and int(project.user_id or 0) != _positive_id(params, "client_id")):
                raise _error("PROJECT_NOT_FOUND", "Проект не найден", 404)
            return _project_stats(db, settings, project_id, params)
        if client_id is None:
            raise _error("CLIENT_OR_PROJECT_REQUIRED", "Укажите client_id или project_id", 422)
        client_id = _positive_id(params, "client_id")
        _require_client(db, client_id)
        dashboard = _dashboard(db, settings, start_date, end_date, client_id, selected_chart_range=True)
        return {
            "client_id": client_id, "from_date": start_date.isoformat(), "to_date": end_date.isoformat(),
            "total": int(dashboard.summary.leadsPeriod),
            "daily": [{"date": str(item.date), "value": int(item.value)} for item in dashboard.charts.leadsDaily],
            "by_source": [{"key": str(item.key), "value": int(item.value)} for item in dashboard.charts.sourceBreakdown],
        }

    raise _error("CAPABILITY_NOT_FOUND", "Read capability не найдена", 404)
