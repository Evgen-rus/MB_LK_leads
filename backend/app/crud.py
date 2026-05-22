"""
Файл: backend/app/crud.py
Назначение: бизнес-логика/CRUD, аудит изменений, планирование "тихого окна".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from .time_utils import as_local_naive, now_msk, now_msk_naive
from .provider_lead_ids import format_provider_lead_lk_id
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Any, Literal
import html
import os
import re
import secrets
import string

from sqlalchemy import String, cast, select, func, or_, and_, case
from sqlalchemy.orm import Session

from . import models, schemas, auth


PROJECT_PROVIDER_LEADS_GRACE_HOURS = 48
PROJECT_NAME_UNAVAILABLE_MESSAGE = "Название проекта не доступно. Выберите другое название."
PROJECT_STATUS_OPERATOR_BLOCK = "Блокировка оператора"
POSTGRES_INT_MAX = 2_147_483_647
ROLE_ADMIN = "admin"
ROLE_CLIENT = "client"
ROLE_AGENT = "agent"
CLIENT_WORK_STATUSES = {
    "В работе",
    "Ждём оплату",
    "Ждём данные",
    "На согласовании",
    "Пауза по клиенту",
    "Неактивен",
}
CLIENT_WORK_STATUS_DEFAULT = "В работе"
PROJECT_SORT_FIELDS = {
    "id",
    "name",
    "dataSourceCode",
    "status",
    "dataLimit",
    "collectionSource",
    "sourcesCount",
    "createdAt",
    "numbersPeriod",
    "numbersTotal",
}
PROJECT_SORT_DIRECTIONS = {"asc", "desc"}
TELEGRAM_NOTIFICATION_STATUS_PENDING = "pending"
TELEGRAM_NOTIFICATION_STATUS_PROCESSING = "processing"
TELEGRAM_NOTIFICATION_STATUS_SENT = "sent"
TELEGRAM_NOTIFICATION_STATUS_FAILED = "failed"
TELEGRAM_NOTIFICATION_DEFAULT_MAX_ATTEMPTS = 5
TELEGRAM_NOTIFICATION_LOCK_SECONDS = 120
TELEGRAM_NOTIFICATION_SENT_RETENTION_DAYS = 30
TELEGRAM_NOTIFICATION_FAILED_RETENTION_DAYS = 60


def _telegram_retry_delay_seconds(attempt_count: int) -> int:
    delays = [60, 300, 900, 3600]
    index = max(0, min(len(delays) - 1, int(attempt_count) - 1))
    return delays[index]


def create_telegram_notification(
    db: Session,
    *,
    kind: str,
    chat_id: str,
    text: str,
    parse_mode: Optional[str] = "HTML",
    metadata: Optional[Dict[str, Any]] = None,
    max_attempts: int = TELEGRAM_NOTIFICATION_DEFAULT_MAX_ATTEMPTS,
) -> models.TelegramNotification:
    now = now_msk_naive()
    row = models.TelegramNotification(
        kind=(str(kind or "").strip() or "system"),
        chat_id=str(chat_id or "").strip(),
        text=str(text or ""),
        parse_mode=(str(parse_mode).strip() if parse_mode is not None else None),
        status=TELEGRAM_NOTIFICATION_STATUS_PENDING,
        attempt_count=0,
        max_attempts=max(1, int(max_attempts or TELEGRAM_NOTIFICATION_DEFAULT_MAX_ATTEMPTS)),
        next_attempt_at=now,
        locked_until=None,
        locked_by=None,
        created_at=now,
        updated_at=now,
        payload_metadata=metadata or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _telegram_notification_to_claim_item(row: models.TelegramNotification) -> schemas.TelegramNotificationClaimItemOut:
    return schemas.TelegramNotificationClaimItemOut(
        id=int(row.id),
        kind=str(row.kind or "system"),
        chatId=str(row.chat_id or ""),
        text=str(row.text or ""),
        parseMode=row.parse_mode,
        metadata=row.payload_metadata or None,
    )


def claim_telegram_notifications(
    db: Session,
    *,
    worker_id: str,
    limit: int = 50,
    lock_seconds: int = TELEGRAM_NOTIFICATION_LOCK_SECONDS,
) -> List[schemas.TelegramNotificationClaimItemOut]:
    now = now_msk_naive()
    worker = str(worker_id or "").strip() or "telegram-worker"
    limit_value = max(1, min(100, int(limit or 50)))
    lock_until = now + timedelta(seconds=max(30, int(lock_seconds or TELEGRAM_NOTIFICATION_LOCK_SECONDS)))
    stmt = (
        select(models.TelegramNotification)
        .where(
            or_(
                and_(
                    models.TelegramNotification.status == TELEGRAM_NOTIFICATION_STATUS_PENDING,
                    or_(
                        models.TelegramNotification.next_attempt_at.is_(None),
                        models.TelegramNotification.next_attempt_at <= now,
                    ),
                ),
                and_(
                    models.TelegramNotification.status == TELEGRAM_NOTIFICATION_STATUS_FAILED,
                    models.TelegramNotification.attempt_count < models.TelegramNotification.max_attempts,
                    or_(
                        models.TelegramNotification.next_attempt_at.is_(None),
                        models.TelegramNotification.next_attempt_at <= now,
                    ),
                ),
                and_(
                    models.TelegramNotification.status == TELEGRAM_NOTIFICATION_STATUS_PROCESSING,
                    models.TelegramNotification.locked_until.is_not(None),
                    models.TelegramNotification.locked_until <= now,
                ),
            )
        )
        .order_by(models.TelegramNotification.created_at.asc(), models.TelegramNotification.id.asc())
        .limit(limit_value)
        .with_for_update(skip_locked=True)
    )
    rows = db.execute(stmt).scalars().all()
    for row in rows:
        row.status = TELEGRAM_NOTIFICATION_STATUS_PROCESSING
        row.locked_by = worker
        row.locked_until = lock_until
        row.updated_at = now
        db.add(row)
    db.commit()
    return [_telegram_notification_to_claim_item(row) for row in rows]


def mark_telegram_notification_sent(
    db: Session,
    *,
    notification_id: int,
    telegram_message_id: Optional[str] = None,
) -> Optional[models.TelegramNotification]:
    row = db.get(models.TelegramNotification, int(notification_id))
    if not row:
        return None
    now = now_msk_naive()
    row.status = TELEGRAM_NOTIFICATION_STATUS_SENT
    row.telegram_message_id = str(telegram_message_id).strip() if telegram_message_id is not None else None
    row.sent_at = now
    row.locked_until = None
    row.locked_by = None
    row.last_error = None
    row.updated_at = now
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def mark_telegram_notification_failed(
    db: Session,
    *,
    notification_id: int,
    error: Optional[str] = None,
) -> Optional[models.TelegramNotification]:
    row = db.get(models.TelegramNotification, int(notification_id))
    if not row:
        return None
    now = now_msk_naive()
    next_attempt = int(row.attempt_count or 0) + 1
    row.status = TELEGRAM_NOTIFICATION_STATUS_FAILED
    row.attempt_count = next_attempt
    row.last_error = (str(error or "").strip() or "send_failed")[:2000]
    row.locked_until = None
    row.locked_by = None
    row.updated_at = now
    if next_attempt < int(row.max_attempts or TELEGRAM_NOTIFICATION_DEFAULT_MAX_ATTEMPTS):
        row.next_attempt_at = now + timedelta(seconds=_telegram_retry_delay_seconds(next_attempt))
    else:
        row.next_attempt_at = None
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def cleanup_old_telegram_notifications(db: Session) -> int:
    now = now_msk_naive()
    sent_threshold = now - timedelta(days=TELEGRAM_NOTIFICATION_SENT_RETENTION_DAYS)
    failed_threshold = now - timedelta(days=TELEGRAM_NOTIFICATION_FAILED_RETENTION_DAYS)
    rows = db.execute(
        select(models.TelegramNotification).where(
            or_(
                and_(
                    models.TelegramNotification.status == TELEGRAM_NOTIFICATION_STATUS_SENT,
                    models.TelegramNotification.sent_at.is_not(None),
                    models.TelegramNotification.sent_at < sent_threshold,
                ),
                and_(
                    models.TelegramNotification.status == TELEGRAM_NOTIFICATION_STATUS_FAILED,
                    models.TelegramNotification.updated_at < failed_threshold,
                ),
            )
        )
    ).scalars().all()
    count = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return count


def get_user_role(user: Optional[models.User]) -> str:
    if not user:
        return ROLE_CLIENT
    if int(getattr(user, "id", 0) or 0) == 1:
        return ROLE_ADMIN
    role = str(getattr(user, "role", "") or ROLE_CLIENT).strip().lower()
    if role in (ROLE_ADMIN, ROLE_CLIENT, ROLE_AGENT):
        return role
    return ROLE_CLIENT


def is_admin_user(user: Optional[models.User]) -> bool:
    return get_user_role(user) == ROLE_ADMIN


def is_agent_user(user: Optional[models.User]) -> bool:
    return get_user_role(user) == ROLE_AGENT


def is_client_user(user: Optional[models.User]) -> bool:
    return get_user_role(user) == ROLE_CLIENT


def user_is_disabled(user: Optional[models.User]) -> bool:
    return bool(getattr(user, "is_disabled", False)) if user else False


def get_accessible_client_ids_for_manager(db: Session, manager_user: models.User) -> List[int]:
    if is_admin_user(manager_user):
        rows = db.execute(
            select(models.User.id).where(models.User.id != 1, models.User.role == ROLE_CLIENT)
        ).all()
        return [int(row[0]) for row in rows]
    if is_agent_user(manager_user):
        rows = db.execute(
            select(models.User.id).where(
                models.User.role == ROLE_CLIENT,
                models.User.owner_agent_id == int(manager_user.id),
            )
        ).all()
        return [int(row[0]) for row in rows]
    return []


def manager_can_access_client(db: Session, manager_user: models.User, client_id: int) -> bool:
    client = db.get(models.User, int(client_id))
    if not client or not is_client_user(client):
        return False
    if is_admin_user(manager_user):
        return True
    if is_agent_user(manager_user):
        return int(getattr(client, "owner_agent_id", 0) or 0) == int(manager_user.id)
    return False


def manager_can_access_project(db: Session, manager_user: models.User, project_id: int) -> bool:
    project = db.get(models.Project, int(project_id))
    if not project or project.user_id is None:
        return False
    return manager_can_access_client(db, manager_user, int(project.user_id))


def manager_can_access_tariff(db: Session, manager_user: models.User, tariff_id: int) -> bool:
    tariff = db.get(models.ClientTariff, int(tariff_id))
    if not tariff:
        return False
    return manager_can_access_client(db, manager_user, int(tariff.client_id))


def get_client_owner_type_and_id(client_user: models.User) -> tuple[str, Optional[int]]:
    owner_agent_id = getattr(client_user, "owner_agent_id", None)
    if owner_agent_id is None:
        return "admin", None
    return "agent", int(owner_agent_id)


def normalize_client_work_status(value: Optional[str]) -> str:
    status = str(value or "").strip() or CLIENT_WORK_STATUS_DEFAULT
    if status not in CLIENT_WORK_STATUSES:
        raise ValueError("Недопустимый рабочий статус клиента.")
    return status


def resolve_client_finance_status(
    remaining: int,
    tariff: Optional[models.ClientTariff],
) -> Optional[schemas.ClientFinanceStatus]:
    if tariff is None:
        return None
    if int(remaining) <= 0:
        return "Долг"
    signal1 = getattr(tariff, "signal1", None)
    signal2 = getattr(tariff, "signal2", None)
    signal3 = getattr(tariff, "signal3", None)
    if signal1 is None or signal2 is None or signal3 is None:
        return None
    if int(remaining) <= int(signal3):
        return "Дожим 3"
    if int(remaining) <= int(signal2):
        return "Дожим 2"
    if int(remaining) <= int(signal1):
        return "Дожим 1"
    return None


def get_user_manual_balance(db: Session, user_id: int) -> int:
    credits = db.execute(
        select(func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0)).where(
            models.ClientBalanceOperation.client_id == int(user_id),
            models.ClientBalanceOperation.op_type == "credit",
        )
    ).scalar_one()
    debits = db.execute(
        select(func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0)).where(
            models.ClientBalanceOperation.client_id == int(user_id),
            models.ClientBalanceOperation.op_type == "debit",
        )
    ).scalar_one()
    return int(credits or 0) - int(debits or 0)


def _ensure_tariff_target_user(db: Session, user_id: int) -> models.User:
    user = db.get(models.User, int(user_id))
    if not user or not is_client_user(user):
        raise ValueError("Тариф можно назначать только клиенту.")
    return user


def _append_balance_comment(base: str, comment: Optional[str]) -> str:
    extra = (comment or "").strip()
    if not extra:
        return base
    return f"{base}: {extra}"


def _build_target_tariff_balance_comment(target_user: models.User, action: str, tariff_id: int, comment: Optional[str]) -> str:
    action_map = {
        "create": "Создание тарифа",
        "credit": "Добавление к тарифу",
        "debit": "Списание из тарифа",
    }
    client_label = (
        str(getattr(target_user, "display_name", "") or "").strip()
        or str(getattr(target_user, "login", "") or "").strip()
        or f"id {int(getattr(target_user, 'id', 0) or 0)}"
    )
    base = f"Тариф клиента: {client_label}: {action_map.get(action, 'Изменение тарифа')}"
    return _append_balance_comment(base, comment)


def _get_tariff_current_amount(db: Session, tariff: models.ClientTariff) -> int:
    adjustment = _get_tariff_adjustment_map(db, [int(tariff.id)]).get(int(tariff.id), {"credit": 0, "debit": 0})
    return int(tariff.base_amount or 0) + int(adjustment.get("credit", 0)) - int(adjustment.get("debit", 0))


def _validate_tariff_signals(amount: int, signal1: int, signal2: int, signal3: int) -> None:
    if int(signal3) <= 0:
        raise ValueError("Сигнал 3 должен быть больше нуля.")
    if int(signal2) <= int(signal3):
        raise ValueError("Сигнал 2 должен быть больше сигнала 3.")
    if int(signal1) <= int(signal2):
        raise ValueError("Сигнал 1 должен быть больше сигнала 2.")
    if int(signal1) >= int(amount):
        raise ValueError("Сигнал 1 должен быть меньше тарифа.")


def _validate_existing_tariff_signals_for_amount(tariff: models.ClientTariff, amount: int) -> None:
    if (
        getattr(tariff, "signal1", None) is None
        or getattr(tariff, "signal2", None) is None
        or getattr(tariff, "signal3", None) is None
    ):
        return
    _validate_tariff_signals(
        amount=int(amount),
        signal1=int(tariff.signal1 or 0),
        signal2=int(tariff.signal2 or 0),
        signal3=int(tariff.signal3 or 0),
    )


def _apply_tariff_balance_effect(
    db: Session,
    *,
    target_user: models.User,
    actor_user_id: int,
    amount: int,
    op_type: str,
    tariff_id: int,
    action: str,
    comment: Optional[str],
) -> None:
    normalized_amount = int(amount or 0)
    if normalized_amount <= 0:
        raise ValueError("Сумма тарифа должна быть положительной.")

    _add_balance_operation_row(
        db,
        user_id=int(target_user.id),
        actor_user_id=int(actor_user_id),
        amount=normalized_amount,
        op_type=op_type,
        comment=_build_target_tariff_balance_comment(
            target_user=target_user,
            action=action,
            tariff_id=int(tariff_id),
            comment=comment,
        ),
    )


def _join_days(days: Iterable[str]) -> str:
    return " ".join([f"{d}." for d in days])


def _calc_sources_count(sites: Optional[List[str]], phones: Optional[List[str]], sms_sender_name: Optional[str]) -> int:
    return (len(sites or [])) + (len(phones or [])) + (1 if sms_sender_name else 0)


def _normalize_project_status(value: Any) -> schemas.ProjectStatus:
    if value in ("Активен", "На паузе", "Удалён", PROJECT_STATUS_OPERATOR_BLOCK):
        return value  # type: ignore[return-value]
    return "Удалён"


def _apply_project_status_filter(stmt, *, project_status: Optional[schemas.ProjectStatus], include_deleted: bool):
    """
    Применяет единое правило фильтрации статуса для списков проектов.

    Если статус задан явно, он имеет приоритет над include_deleted.
    Это позволяет корректно получать выборку "только удалённые" даже при
    стандартном include_deleted=False.
    """
    if project_status is not None:
        return stmt.where(models.Project.status == project_status)
    if not include_deleted:
        return stmt.where(models.Project.status != 'Удалён')
    return stmt


def _normalize_project_sort(
    sort_by: Optional[str],
    sort_dir: Optional[str],
) -> Tuple[str, str]:
    safe_sort_by = sort_by if sort_by in PROJECT_SORT_FIELDS else "id"
    safe_sort_dir = sort_dir if sort_dir in PROJECT_SORT_DIRECTIONS else "desc"
    return safe_sort_by, safe_sort_dir


def _apply_project_sort(
    stmt,
    *,
    sort_by: Optional[str],
    sort_dir: Optional[str],
    start_local: Optional[datetime] = None,
    end_local: Optional[datetime] = None,
):
    safe_sort_by, safe_sort_dir = _normalize_project_sort(sort_by, sort_dir)
    direction = "asc" if safe_sort_dir == "asc" else "desc"

    if safe_sort_by == "numbersPeriod":
        ts_col = _provider_lead_ts_col()
        counts_stmt = select(
            models.ProviderLead.project_id.label("project_id"),
            func.count().label("sort_count"),
        ).where(models.ProviderLead.project_id.is_not(None))
        if start_local and end_local:
            counts_stmt = counts_stmt.where(ts_col >= start_local, ts_col <= end_local)
        counts_subq = counts_stmt.group_by(models.ProviderLead.project_id).subquery()
        stmt = stmt.outerjoin(counts_subq, models.Project.id == counts_subq.c.project_id)
        sort_expr = func.coalesce(counts_subq.c.sort_count, 0)
    elif safe_sort_by == "numbersTotal":
        counts_subq = (
            select(
                models.ProviderLead.project_id.label("project_id"),
                func.count().label("sort_count"),
            )
            .where(models.ProviderLead.project_id.is_not(None))
            .group_by(models.ProviderLead.project_id)
            .subquery()
        )
        stmt = stmt.outerjoin(counts_subq, models.Project.id == counts_subq.c.project_id)
        sort_expr = func.coalesce(counts_subq.c.sort_count, 0)
    else:
        sort_expr = {
            "id": models.Project.id,
            "name": models.Project.name,
            "dataSourceCode": models.Project.data_source_code,
            "status": models.Project.status,
            "dataLimit": models.Project.data_limit,
            "collectionSource": models.Project.collection_source,
            "sourcesCount": models.Project.sources_count,
            "createdAt": models.Project.created_at,
        }[safe_sort_by]

    primary_order = sort_expr.asc() if direction == "asc" else sort_expr.desc()
    secondary_order = models.Project.id.asc() if direction == "asc" else models.Project.id.desc()
    return stmt.order_by(primary_order, secondary_order)


def _apply_daily_limit_reached_filter(
    stmt,
    *,
    start_local: Optional[datetime],
    end_local: Optional[datetime],
):
    ts_col = _provider_lead_ts_col()
    counts_stmt = select(
        models.ProviderLead.project_id.label("project_id"),
        func.count().label("period_count"),
    ).where(models.ProviderLead.project_id.is_not(None))
    if start_local and end_local:
        counts_stmt = counts_stmt.where(ts_col >= start_local, ts_col <= end_local)
    counts_subq = counts_stmt.group_by(models.ProviderLead.project_id).subquery()
    return (
        stmt.join(counts_subq, models.Project.id == counts_subq.c.project_id)
        .where(
            models.Project.data_limit > 0,
            counts_subq.c.period_count >= models.Project.data_limit,
        )
    )


def find_duplicates_in_projects(
    db: Session,
    items: List[str],
    target_type: str,
    user_id: int,
    provider_project_id: Optional[str],
    exclude_project_id: Optional[int] = None,
) -> Dict[str, List[str]]:
    """
    Ищет дубликаты доменов/номеров только среди наших проектов в БД.
    Возвращает карту: item -> ["<id>|<name>", ...]
    """
    if target_type not in ("hosts", "calls"):
        return {}

    normalized = {str(x).strip() for x in items if x is not None and str(x).strip()}
    if not normalized:
        return {}

    if not provider_project_id:
        return {}

    query = db.query(models.Project).filter(
        models.Project.status != "Удалён",
        models.Project.user_id == user_id,
        models.Project.provider_project_id == str(provider_project_id).strip(),
    )
    if exclude_project_id is not None:
        query = query.filter(models.Project.id != exclude_project_id)

    duplicates: Dict[str, List[str]] = {}
    for project in query:
        sources = project.sites if target_type == "hosts" else project.phones
        if not sources or not isinstance(sources, list):
            continue
        source_set = {str(x).strip() for x in sources if x is not None and str(x).strip()}
        intersect = normalized.intersection(source_set)
        if not intersect:
            continue
        label = f"{project.id}|{project.name}"
        for value in intersect:
            duplicates.setdefault(value, []).append(label)

    return duplicates


def normalize_client_internal_prefix(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    return raw if raw.endswith("_") else f"{raw}_"


def _clean_client_internal_prefix_from_name(
    name: Optional[str],
    *,
    data_source_code: Optional[str],
    client_internal_prefix: Optional[str],
) -> str:
    raw = str(name or "").strip()
    prefix = normalize_client_internal_prefix(client_internal_prefix)
    if not raw or not prefix:
        return raw
    provider_prefix = f"{str(data_source_code or '').strip()}_"
    full_prefix = f"{provider_prefix}{prefix}"
    if provider_prefix.strip("_") and raw.startswith(full_prefix):
        return f"{provider_prefix}{raw[len(full_prefix):].strip()}"
    if raw.startswith(prefix):
        return raw[len(prefix):].strip()
    return raw


def _project_name_for_view(p: models.Project, *, expose_internal_name: bool) -> str:
    if expose_internal_name:
        return p.name
    return _clean_client_internal_prefix_from_name(
        p.name,
        data_source_code=p.data_source_code,
        client_internal_prefix=getattr(p, "client_internal_prefix", None),
    )


def _project_tag_for_view(p: models.Project, *, expose_internal_name: bool) -> str:
    if expose_internal_name:
        return p.tag
    return _clean_client_internal_prefix_from_name(
        p.tag,
        data_source_code=p.data_source_code,
        client_internal_prefix=getattr(p, "client_internal_prefix", None),
    )


def _clean_project_snapshot_for_client(snapshot: Any, project: Optional[models.Project]) -> Any:
    if not isinstance(snapshot, dict) or project is None:
        return snapshot
    cleaned = dict(snapshot)
    for key in ("name", "tag"):
        if isinstance(cleaned.get(key), str):
            cleaned[key] = _clean_client_internal_prefix_from_name(
                cleaned.get(key),
                data_source_code=getattr(project, "data_source_code", None),
                client_internal_prefix=getattr(project, "client_internal_prefix", None),
            )
    return cleaned


def _project_to_out(
    p: models.Project,
    numbers_period: int = 0,
    numbers_total: Optional[int] = None,
    *,
    expose_internal_name: bool = True,
) -> schemas.ProjectOut:
    return schemas.ProjectOut(
        id=p.id,
        status=p.status,  # type: ignore
        deliveryStatus=p.delivery_status,  # type: ignore
        name=_project_name_for_view(p, expose_internal_name=expose_internal_name),
        tag=_project_tag_for_view(p, expose_internal_name=expose_internal_name),
        collectionSource=p.collection_source,  # type: ignore
        dataSourceCode=p.data_source_code,  # type: ignore
        regionMode=p.region_mode,  # type: ignore
        regions=p.regions,  # type: ignore
        sites=p.sites,  # type: ignore
        phones=p.phones,  # type: ignore
        smsSenderName=p.sms_sender_name,  # type: ignore
        dataLimit=p.data_limit,
        numbersToday=p.numbers_today,
        numbersTotal=p.numbers_total if numbers_total is None else numbers_total,
        numbersPeriod=numbers_period,
        daysReceived=p.days_received,
        sourcesCount=p.sources_count,
        createdAt=p.created_at.isoformat()[:10],
    )


def _diff_dict(before: dict, after: dict) -> Dict[str, Tuple[Any, Any]]:
    """
    Утилита для вычисления отличий между снепшотами проекта.
    Возвращает словарь {поле: (старое значение, новое значение)}.
    """
    diff: Dict[str, Tuple[Any, Any]] = {}
    keys = set(before.keys()) | set(after.keys())
    for k in keys:
        if before.get(k) != after.get(k):
            diff[k] = (before.get(k), after.get(k))
    return diff


def _format_changes_compact(diff: Dict[str, Tuple[Any, Any]]) -> str:
    """
    Компактное человекочитаемое описание изменений для фронтенда.

    Здесь мы не используем HTML/эмодзи, только короткие фразы:
    - Лимит: 100 → 200
    - Регионы: 10 → 12 (+2)
    и т.п.
    """
    # Человекочитаемые заголовки полей
    mapping = {
        "dataLimit": "Лимит",
        "daysReceived": "Дни",
        "tag": "Тег",
        "status": "Статус проекта",
        "regionMode": "Режим регионов",
        "regions": "Регионы",
        "sites": "Сайты",
        "phones": "Телефоны",
        "smsSenderName": "СМС отправитель",
        "name": "Название",
        "collectionSource": "Источник данных",
        "dataSourceCode": "Код источника",
        "sourcesCount": "Источники",
        "limitControlReason": "Причина автопаузы",
        "operatorBlockReason": "Причина блокировки оператора",
    }

    def _human_region_mode(v: Any) -> str:
        if v == "include":
            return "включить"
        if v == "exclude":
            return "исключить"
        if v is None:
            return "все"
        return str(v)

    def _fmt_list_change(before_v: Any, after_v: Any, title: str) -> str:
        before_list = before_v or []
        after_list = after_v or []
        try:
            before_set = set(before_list)
            after_set = set(after_list)
        except Exception:
            # fallback: только длины
            return f"{title}: {len(before_list)} → {len(after_list)}"
        added = len(after_set - before_set)
        removed = len(before_set - after_set)
        extra: List[str] = []
        if added:
            extra.append(f"+{added}")
        if removed:
            extra.append(f"-{removed}")
        extra_str = f" ({', '.join(extra)})" if extra else ""
        return f"{title}: {len(before_list)} → {len(after_list)}{extra_str}"

    parts: List[str] = []
    for key, (before_v, after_v) in diff.items():
        title = mapping.get(key, key)
        if key in ("regions", "sites", "phones"):
            parts.append(_fmt_list_change(before_v, after_v, title))
        elif key == "regionMode":
            parts.append(
                f"{title}: {_human_region_mode(before_v)} → {_human_region_mode(after_v)}"
            )
        else:
            parts.append(f"{title}: {before_v} → {after_v}")

    # Чтобы строка не разрасталась бесконечно, ограничим 3–4 полями.
    if not parts:
        return "Обновлены параметры проекта"
    max_items = 4
    main = "; ".join(parts[:max_items])
    if len(parts) > max_items:
        main += "; …"
    return main


def seed_notify_state(db: Session, window_minutes: int) -> None:
    state = db.get(models.NotifyState, 1)
    if not state:
        state = models.NotifyState(id=1, window_minutes=window_minutes, next_send_at=None)
        db.add(state)
        db.commit()


def schedule_debounce(db: Session, minutes: int) -> None:
    state = db.get(models.NotifyState, 1)
    if not state:
        state = models.NotifyState(id=1, window_minutes=minutes)
        db.add(state)
        db.flush()
    state.window_minutes = minutes
    state.next_send_at = now_msk() + timedelta(minutes=minutes)
    db.commit()


def list_projects(db: Session) -> List[schemas.ProjectOut]:
    rows = db.execute(select(models.Project).order_by(models.Project.id.desc())).scalars().all()
    return [_project_to_out(p) for p in rows]


def list_projects_paginated(
    db: Session,
    offset: int,
    limit: int,
    q: str | None,
    user_id: int,
    start_local: Optional[datetime] = None,
    end_local: Optional[datetime] = None,
    include_deleted: bool = False,
    project_status: Optional[schemas.ProjectStatus] = None,
    daily_limit_reached: bool = False,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
) -> schemas.ProjectListOut:
    stmt = select(models.Project).where(models.Project.user_id == user_id)
    stmt = _apply_project_status_filter(
        stmt,
        project_status=project_status,
        include_deleted=include_deleted,
    )
    if q:
        q = q.strip()
        if q:
            # Поиск проектов выполняется строго по названию.
            search_conditions = [models.Project.name.ilike(f"%{q}%")]
            m = re.match(r"^(B1|B2|B3|B4)_(.+)$", q, flags=re.IGNORECASE)
            if m:
                code = m.group(1).upper()
                suffix = m.group(2).strip()
                if suffix:
                    search_conditions.append(models.Project.name.ilike(f"%{code}\\_%{suffix}%", escape="\\"))
            stmt = stmt.where(or_(*search_conditions))
    if daily_limit_reached:
        stmt = _apply_daily_limit_reached_filter(
            stmt,
            start_local=start_local,
            end_local=end_local,
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = _apply_project_sort(
        stmt,
        sort_by=sort_by,
        sort_dir=sort_dir,
        start_local=start_local,
        end_local=end_local,
    )
    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    # Подсчёт лидов за период, если диапазон задан
    counts_period_map: Dict[int, int] = {}
    counts_total_map: Dict[int, int] = {}
    if start_local and end_local and rows:
        proj_ids = [p.id for p in rows]
        ts_col = _provider_lead_ts_col()
        cnt_rows_period = (
            db.execute(
                select(models.ProviderLead.project_id, func.count())
                .where(
                    models.ProviderLead.project_id.in_(proj_ids),
                    ts_col >= start_local,
                    ts_col <= end_local,
                )
                .group_by(models.ProviderLead.project_id)
            ).all()
        )
        counts_period_map = {int(pid): int(cnt) for pid, cnt in cnt_rows_period}

        cnt_rows_total = (
            db.execute(
                select(models.ProviderLead.project_id, func.count())
                .where(models.ProviderLead.project_id.in_(proj_ids))
                .group_by(models.ProviderLead.project_id)
            ).all()
        )
        counts_total_map = {int(pid): int(cnt) for pid, cnt in cnt_rows_total}

    items = [
        _project_to_out(
            p,
            numbers_period=counts_period_map.get(p.id, 0),
            numbers_total=counts_total_map.get(p.id),
            expose_internal_name=False,
        )
        for p in rows
    ]
    return schemas.ProjectListOut(items=items, total=total)


def _audit_event_compact_description(
    ev: models.AuditEvent,
    *,
    project: Optional[models.Project] = None,
    expose_internal_name: bool = True,
) -> str:
    """
    Человекочитаемое описание события аудита для фронтенда.

    Важно: это описание используется и для админского списка изменений клиента,
    поэтому поддерживаем не только project create/update/delete, но и blacklist_*.
    """
    action = ev.action or "update"

    if action == "create":
        name = None
        try:
            if isinstance(ev.after, dict):
                after = ev.after if expose_internal_name else _clean_project_snapshot_for_client(ev.after, project)
                name = after.get("name") if isinstance(after, dict) else None
        except Exception:
            name = None
        if name:
            return f'Создан проект "{name}"'
        return "Создан проект"

    if action == "update":
        before = ev.before or {}
        after = ev.after or {}
        if not expose_internal_name:
            before = _clean_project_snapshot_for_client(before, project)
            after = _clean_project_snapshot_for_client(after, project)
        if isinstance(before, dict) and isinstance(after, dict):
            if after.get("status") == PROJECT_STATUS_OPERATOR_BLOCK:
                before_status = before.get("status") or "Активен"
                return (
                    "Поставщик отключил проект. "
                    f"Статус изменён системой: {before_status} → {PROJECT_STATUS_OPERATOR_BLOCK}."
                )
            diff = _diff_dict(before, after)
            return _format_changes_compact(diff)
        fields = ev.changed_fields or []
        if isinstance(fields, list) and fields:
            fields_str = ", ".join(str(f) for f in fields)
            return f"Обновлены поля: {fields_str}"
        return "Обновлены параметры проекта"

    if action == "delete":
        return "Проект удалён"

    if action == "blacklist_add":
        # after: {"phones": [...], "count": N}
        count = None
        phones_preview = None
        try:
            if isinstance(ev.after, dict):
                raw_count = ev.after.get("count")
                if isinstance(raw_count, int):
                    count = raw_count
                raw_phones = ev.after.get("phones")
                if isinstance(raw_phones, list):
                    phones = [str(x) for x in raw_phones if x is not None]
                    if phones:
                        phones_preview = ", ".join(phones[:3]) + ("…" if len(phones) > 3 else "")
        except Exception:
            count = None
            phones_preview = None
        desc = f"ЧС: добавлено телефонов: {count}" if count is not None else "ЧС: добавлены телефоны"
        if phones_preview:
            desc += f" ({phones_preview})"
        return desc

    if action == "blacklist_delete":
        # before: {"phone": "..."}
        phone = None
        try:
            if isinstance(ev.before, dict):
                raw_phone = ev.before.get("phone")
                if isinstance(raw_phone, str) and raw_phone.strip():
                    phone = raw_phone.strip()
        except Exception:
            phone = None
        return f"ЧС: удалён телефон {phone}" if phone else "ЧС: удалён телефон"

    # На будущее: другие типы событий.
    return action


def _audit_event_actor_user_id(ev: models.AuditEvent) -> Optional[int]:
    raw_actor = getattr(ev, "actor_user_id", None)
    try:
        if raw_actor is not None:
            actor_id = int(raw_actor)
            if actor_id > 0:
                return actor_id
    except Exception:
        pass
    if ev.user_id:
        return int(ev.user_id)
    return None


def _audit_event_actor_mode(ev: models.AuditEvent, actor_user_id: Optional[int]) -> Optional[str]:
    """
    Режим актора:
    - admin_impersonation: действие админа через ЛК клиента;
    - admin: действие админа не через имперсонацию;
    - client: действие клиента.
    """
    try:
        if bool(getattr(ev, "via_impersonation", False)):
            return "admin_impersonation"
    except Exception:
        pass
    if actor_user_id == 1:
        return "admin"
    if actor_user_id:
        return "client"
    return None


def _audit_event_to_history_item(
    ev: models.AuditEvent,
    actor: Optional[schemas.UserInfo] = None,
    actor_mode: Optional[str] = None,
    project: Optional[models.Project] = None,
    expose_internal_name: bool = True,
) -> schemas.ProjectHistoryItem:
    """
    Преобразует AuditEvent в компактный элемент истории для фронтенда.
    Здесь мы формируем человекочитаемое краткое описание изменения.
    """
    created_at_str = ev.created_at.strftime("%Y-%m-%d %H:%M:%S")
    action_raw = ev.action or "update"
    # История проекта на фронте ожидает только create/update/delete.
    action = action_raw if action_raw in ("create", "update", "delete") else "update"
    desc = _audit_event_compact_description(
        ev,
        project=project,
        expose_internal_name=expose_internal_name,
    )

    return schemas.ProjectHistoryItem(
        id=ev.id,
        eventId=f"AE-{ev.id}",
        action=action,  # type: ignore[arg-type]
        createdAt=created_at_str,
        description=desc,
        actor=actor,
        actorMode=actor_mode,  # type: ignore[arg-type]
    )


def _normalize_project_operation(operation: Optional[str]) -> str:
    if operation in ("create", "update", "delete"):
        return operation
    return "update"


def _clean_project_operation_error_message(value: Any) -> str:
    """
    Приводит технические ошибки внешних сервисов к тексту для истории.
    Например, nginx иногда возвращает HTML-страницу 502, которую не стоит
    показывать пользователю как сырой HTML.
    """
    raw = str(value or "").strip()
    if not raw:
        return "Операция не выполнена"

    text = html.unescape(raw)
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "Операция не выполнена"

    # Частый HTML-ответ nginx даёт дубль: title + h1.
    text = re.sub(r"\b(502 Bad Gateway)\s+\1\b", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(504 Gateway Time-out)\s+\1\b", r"\1", text, flags=re.IGNORECASE)

    max_len = 500
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def _project_operation_description(
    ev: models.ProjectOperationEvent,
    *,
    project: Optional[models.Project] = None,
    expose_internal_name: bool = True,
) -> str:
    operation = _normalize_project_operation(getattr(ev, "operation", None))
    if operation == "create":
        base = "Не удалось создать проект"
    elif operation == "delete":
        base = "Не удалось удалить проект"
    else:
        base = "Не удалось изменить проект"

    project_name = str(getattr(ev, "project_name", "") or "").strip()
    if project_name and not expose_internal_name and project is not None:
        project_name = _clean_client_internal_prefix_from_name(
            project_name,
            data_source_code=getattr(project, "data_source_code", None),
            client_internal_prefix=getattr(project, "client_internal_prefix", None),
        )
    if project_name:
        base += f' "{project_name}"'

    message = _clean_project_operation_error_message(getattr(ev, "error_message", ""))
    return f"{base}: {message}" if message else base


def _project_operation_to_history_item(
    ev: models.ProjectOperationEvent,
    actor: Optional[schemas.UserInfo] = None,
    actor_mode: Optional[str] = None,
    project: Optional[models.Project] = None,
    expose_internal_name: bool = True,
) -> schemas.ProjectHistoryItem:
    created_at_str = ev.created_at.strftime("%Y-%m-%d %H:%M:%S")
    operation = _normalize_project_operation(getattr(ev, "operation", None))
    error_message = _clean_project_operation_error_message(getattr(ev, "error_message", "")) or None
    return schemas.ProjectHistoryItem(
        id=ev.id,
        eventId=f"POE-{ev.id}",
        action=operation,  # type: ignore[arg-type]
        createdAt=created_at_str,
        description=_project_operation_description(
            ev,
            project=project,
            expose_internal_name=expose_internal_name,
        ),
        outcome="failed",
        errorMessage=error_message,
        actor=actor,
        actorMode=actor_mode,  # type: ignore[arg-type]
    )


def _operation_event_actor_user_id(ev: models.ProjectOperationEvent) -> Optional[int]:
    raw_actor = getattr(ev, "actor_user_id", None)
    try:
        if raw_actor is not None:
            actor_id = int(raw_actor)
            if actor_id > 0:
                return actor_id
    except Exception:
        pass
    try:
        if ev.user_id:
            return int(ev.user_id)
    except Exception:
        pass
    return None


def _operation_event_actor_mode(ev: models.ProjectOperationEvent, actor_user_id: Optional[int]) -> Optional[str]:
    try:
        if bool(getattr(ev, "via_impersonation", False)):
            return "admin_impersonation"
    except Exception:
        pass
    if actor_user_id == 1:
        return "admin"
    if actor_user_id:
        return "client"
    return None


def _load_user_info_map(db: Session, user_ids: Iterable[int]) -> Dict[int, schemas.UserInfo]:
    ids = {int(uid) for uid in user_ids if uid}
    users_map: Dict[int, schemas.UserInfo] = {}
    if not ids:
        return users_map
    users = db.execute(select(models.User).where(models.User.id.in_(ids))).scalars().all()
    for u in users:
        users_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)
    return users_map


def record_project_operation_failed(
    db: Session,
    *,
    user_id: int,
    operation: str,
    error_message: str,
    actor_user_id: Optional[int] = None,
    project_id: Optional[int] = None,
    project_name: Optional[str] = None,
    request_payload: Optional[Dict[str, Any]] = None,
    error_code: Optional[str] = None,
    via_impersonation: bool = False,
) -> models.ProjectOperationEvent:
    """
    Фиксирует неудачную попытку операции с проектом.

    Это отдельный журнал, а не audit_events: такие записи не становятся
    задачами для админской обработки и не считаются успешными изменениями.
    """
    operation = _normalize_project_operation(operation)
    message = _clean_project_operation_error_message(error_message)
    row = models.ProjectOperationEvent(
        user_id=int(user_id),
        actor_user_id=actor_user_id or user_id,
        project_id=project_id,
        operation=operation,
        status="failed",
        project_name=(str(project_name).strip() if project_name else None),
        request_payload=request_payload,
        error_message=message,
        error_code=(str(error_code).strip() if error_code else None),
        via_impersonation=via_impersonation,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _history_viewer_can_access_owner(db: Session, viewer: models.User, owner_id: Optional[int]) -> bool:
    if owner_id is None:
        return False
    if is_admin_user(viewer):
        return True
    if is_agent_user(viewer):
        return manager_can_access_client(db, viewer, int(owner_id))
    return int(viewer.id) == int(owner_id)


def _project_name_from_snapshot(snapshot: Any) -> Optional[str]:
    if isinstance(snapshot, dict):
        name = snapshot.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _history_detail_actor(db: Session, actor_id: Optional[int]) -> Optional[schemas.UserInfo]:
    if not actor_id:
        return None
    user = db.get(models.User, int(actor_id))
    if not user:
        return schemas.UserInfo(id=int(actor_id), login="(unknown)")
    return schemas.UserInfo(id=user.id, login=user.login)


def get_history_event_detail(
    db: Session,
    *,
    event_id: str,
    viewer: models.User,
) -> Optional[schemas.HistoryEventDetailOut]:
    """
    Возвращает полную карточку события истории по публичному eventId.
    Поддерживает только проектные события: AE-* и POE-*.
    """
    raw = str(event_id or "").strip().upper()
    if raw.startswith("AE-"):
        try:
            source_id = int(raw[3:])
        except ValueError:
            return None
        ev = db.get(models.AuditEvent, source_id)
        if not ev or ev.action not in ("create", "update", "delete"):
            return None

        project = db.get(models.Project, int(ev.project_id)) if ev.project_id else None
        owner_id = int(project.user_id) if project and project.user_id is not None else ev.user_id
        if not _history_viewer_can_access_owner(db, viewer, owner_id):
            return None

        expose_internal_name = not (is_client_user(viewer) and owner_id is not None and int(viewer.id) == int(owner_id))
        actor_id = _audit_event_actor_user_id(ev)
        actor_mode = _audit_event_actor_mode(ev, actor_id)
        after = ev.after if isinstance(ev.after, dict) else None
        before = ev.before if isinstance(ev.before, dict) else None
        if not expose_internal_name:
            after = _clean_project_snapshot_for_client(after, project)
            before = _clean_project_snapshot_for_client(before, project)
        project_name = (
            (str(project.name).strip() if project and project.name else None)
            or _project_name_from_snapshot(after)
            or _project_name_from_snapshot(before)
        )
        if not expose_internal_name and project_name and project is not None:
            project_name = _clean_client_internal_prefix_from_name(
                project_name,
                data_source_code=getattr(project, "data_source_code", None),
                client_internal_prefix=getattr(project, "client_internal_prefix", None),
            )
        changed_fields = ev.changed_fields if isinstance(ev.changed_fields, list) else None
        return schemas.HistoryEventDetailOut(
            eventId=f"AE-{ev.id}",
            source="audit",
            sourceId=ev.id,
            action=ev.action,  # type: ignore[arg-type]
            createdAt=ev.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            description=_audit_event_compact_description(
                ev,
                project=project,
                expose_internal_name=expose_internal_name,
            ),
            outcome="success",
            errorMessage=None,
            projectId=ev.project_id,
            projectName=project_name,
            actor=_history_detail_actor(db, actor_id),
            actorMode=actor_mode,  # type: ignore[arg-type]
            projectSnapshot=after or before,
            beforeSnapshot=before,
            changedFields=[str(f) for f in changed_fields] if changed_fields else None,
        )

    if raw.startswith("POE-"):
        try:
            source_id = int(raw[4:])
        except ValueError:
            return None
        ev = db.get(models.ProjectOperationEvent, source_id)
        if not ev or ev.operation not in ("create", "update", "delete"):
            return None
        if not _history_viewer_can_access_owner(db, viewer, int(ev.user_id)):
            return None

        project = db.get(models.Project, int(ev.project_id)) if ev.project_id else None
        expose_internal_name = not (is_client_user(viewer) and int(viewer.id) == int(ev.user_id))
        actor_id = _operation_event_actor_user_id(ev)
        actor_mode = _operation_event_actor_mode(ev, actor_id)
        snapshot = dict(ev.request_payload) if isinstance(ev.request_payload, dict) else None
        if snapshot and "days" in snapshot and "daysReceived" not in snapshot:
            raw_days = snapshot.pop("days")
            if isinstance(raw_days, list):
                snapshot["daysReceived"] = _join_days(str(day) for day in raw_days)
        project_name = (
            str(ev.project_name).strip()
            if ev.project_name
            else _project_name_from_snapshot(snapshot)
        )
        if not expose_internal_name:
            snapshot = _clean_project_snapshot_for_client(snapshot, project)
            if project_name and project is not None:
                project_name = _clean_client_internal_prefix_from_name(
                    project_name,
                    data_source_code=getattr(project, "data_source_code", None),
                    client_internal_prefix=getattr(project, "client_internal_prefix", None),
                )
        changed_fields = list(snapshot.keys()) if snapshot else None
        error_message = _clean_project_operation_error_message(ev.error_message)
        return schemas.HistoryEventDetailOut(
            eventId=f"POE-{ev.id}",
            source="project_operation",
            sourceId=ev.id,
            action=_normalize_project_operation(ev.operation),  # type: ignore[arg-type]
            createdAt=ev.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            description=_project_operation_description(
                ev,
                project=project,
                expose_internal_name=expose_internal_name,
            ),
            outcome="failed",
            errorMessage=error_message,
            projectId=ev.project_id,
            projectName=project_name or None,
            actor=_history_detail_actor(db, actor_id),
            actorMode=actor_mode,  # type: ignore[arg-type]
            projectSnapshot=snapshot,
            beforeSnapshot=None,
            changedFields=[str(f) for f in changed_fields] if changed_fields else None,
        )

    return None


def _build_balance_activity_description(op_type: str, amount: int, comment: Optional[str]) -> str:
    action_label = "Начисление" if op_type == "credit" else "Списание"
    desc = f"{action_label} остатка: {amount}"
    if comment and comment.strip():
        desc += f" ({comment.strip()})"
    return desc


def _build_report_activity_description(row: models.ReportExport) -> str:
    fmt = (row.format or "csv").upper()
    projects_str = (row.project_ids or "").strip()
    if not projects_str:
        projects_part = "по всем проектам"
    else:
        ids = [x.strip() for x in projects_str.split(",") if x.strip()]
        projects_part = f"по {len(ids)} проектам"
    return f"Сформирован отчёт ({fmt}), {projects_part}"


def _load_client_info_map(db: Session, client_ids: Iterable[int]) -> Dict[int, schemas.UserInfo]:
    ids = {int(client_id) for client_id in client_ids if client_id}
    if not ids:
        return {}
    users = db.execute(select(models.User).where(models.User.id.in_(ids))).scalars().all()
    profiles = db.execute(
        select(models.ClientProfile).where(models.ClientProfile.user_id.in_(ids))
    ).scalars().all()
    profile_map = {int(profile.user_id): profile for profile in profiles}
    result: Dict[int, schemas.UserInfo] = {}
    for user in users:
        profile = profile_map.get(int(user.id))
        result[int(user.id)] = schemas.UserInfo(
            id=int(user.id),
            login=user.login,
            name=(profile.name if profile else getattr(user, "display_name", None)),
            inn=(profile.inn if profile else None),
            phone=(profile.phone if profile else None),
            role=get_user_role(user),  # type: ignore[arg-type]
            ownerAgentId=getattr(user, "owner_agent_id", None),
        )
    return result


def _activity_status_matches(status_filter: Optional[str], *, outcome: str, status: str) -> bool:
    if not status_filter or status_filter == "all":
        return True
    if status_filter == "success":
        return outcome == "success"
    if status_filter == "failed":
        return outcome == "failed"
    if status_filter == "pending":
        return status == "pending"
    if status_filter == "done":
        return status == "done"
    return True


def list_activity_events_for_clients(
    db: Session,
    client_ids: List[int],
    offset: int,
    limit: int,
    start_local: Optional[datetime] = None,
    end_local: Optional[datetime] = None,
    entities: Optional[List[str]] = None,
    q: Optional[str] = None,
    status: Optional[str] = None,
    include_client: bool = False,
    expose_internal_names: bool = True,
) -> schemas.ActivityEventListOut:
    """
    Единая лента активности по одному или нескольким клиентским аккаунтам.

    Источники:
    - audit_events: create/update/delete проектов, blacklist_add/blacklist_delete;
    - project_operation_events: неуспешные create/update/delete проектов;
    - client_balance_operations: credit/debit;
    - report_exports: создание отчётов.
    """
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    normalized_client_ids = sorted({int(client_id) for client_id in client_ids if client_id})
    if not normalized_client_ids:
        return schemas.ActivityEventListOut(items=[], total=0)

    allowed_entities = {"project", "blacklist", "balance", "report"}
    selected_entities = {e for e in (entities or list(allowed_entities)) if e in allowed_entities}
    if not selected_entities:
        selected_entities = allowed_entities

    users_map: Dict[int, schemas.UserInfo] = {}

    def get_user_info(user_id: Optional[int]) -> Optional[schemas.UserInfo]:
        if not user_id:
            return None
        if user_id in users_map:
            return users_map[user_id]
        user = db.get(models.User, user_id)
        info = schemas.UserInfo(id=user_id, login=user.login if user else "(unknown)")
        users_map[user_id] = info
        return info

    clients_map = _load_client_info_map(db, normalized_client_ids) if include_client else {}

    def get_client_info(client_id: Optional[int]) -> Optional[schemas.UserInfo]:
        if not include_client or not client_id:
            return None
        return clients_map.get(int(client_id))

    rows: List[Dict[str, Any]] = []

    include_success = status not in ("failed",)
    include_failed = status in (None, "all", "failed", "done")

    if include_success and ("project" in selected_entities or "blacklist" in selected_entities):
        audit_actions: List[str] = []
        if "project" in selected_entities:
            audit_actions.extend(["create", "update", "delete"])
        if "blacklist" in selected_entities:
            audit_actions.extend(["blacklist_add", "blacklist_delete"])

        audit_stmt = (
            select(models.AuditEvent, models.Project)
            .outerjoin(models.Project, models.Project.id == models.AuditEvent.project_id)
            .where(models.AuditEvent.action.in_(audit_actions))
        )
        if start_local:
            audit_stmt = audit_stmt.where(models.AuditEvent.created_at >= start_local)
        if end_local:
            audit_stmt = audit_stmt.where(models.AuditEvent.created_at <= end_local)

        audit_stmt = audit_stmt.where(
            or_(
                and_(
                    models.AuditEvent.project_id.is_not(None),
                    models.Project.user_id.in_(normalized_client_ids),
                ),
                and_(
                    models.AuditEvent.project_id.is_(None),
                    models.AuditEvent.user_id.in_(normalized_client_ids),
                    models.AuditEvent.action.in_(["blacklist_add", "blacklist_delete"]),
                ),
            )
        )
        if status == "pending":
            audit_stmt = audit_stmt.where(models.AuditEvent.admin_processed_at.is_(None))
        elif status == "done":
            audit_stmt = audit_stmt.where(models.AuditEvent.admin_processed_at.is_not(None))

        audit_rows = db.execute(audit_stmt).all()
        for ev, project in audit_rows:
            entity = "blacklist" if ev.action in ("blacklist_add", "blacklist_delete") else "project"
            project_name = getattr(project, "name", None) if project is not None else None
            project_owner_id = getattr(project, "user_id", None) if project is not None else None
            if not expose_internal_names and project_name and project is not None:
                project_name = _clean_client_internal_prefix_from_name(
                    project_name,
                    data_source_code=getattr(project, "data_source_code", None),
                    client_internal_prefix=getattr(project, "client_internal_prefix", None),
                )
            client_id = int(project_owner_id or ev.user_id or 0)
            actor_id = _audit_event_actor_user_id(ev)
            actor = get_user_info(actor_id)
            description = _audit_event_compact_description(
                ev,
                project=project,
                expose_internal_name=expose_internal_names,
            )
            event_id = f"AE-{ev.id}"
            actor_login = actor.login if actor else ""
            client = get_client_info(client_id)
            client_label = f"{client.name or client.login} {client.id}" if client else ""
            item_status = "done" if ev.admin_processed_at is not None else "pending"
            if not _activity_status_matches(status, outcome="success", status=item_status):
                continue
            search_blob = " ".join(
                [
                    event_id,
                    entity,
                    ev.action or "",
                    description,
                    project_name or "",
                    actor_login,
                    client_label,
                ]
            ).lower()
            rows.append(
                {
                    "created_at": ev.created_at,
                    "search": search_blob,
                    "item": schemas.ActivityEventOut(
                        eventId=event_id,
                        sourceId=ev.id,
                        entity=entity,  # type: ignore[arg-type]
                        action=ev.action or "update",
                        createdAt=ev.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        client=client,
                        actor=actor,
                        description=description,
                        status=item_status,  # type: ignore[arg-type]
                        projectId=ev.project_id,
                        projectName=project_name,
                    ),
                }
            )

    if include_failed and "project" in selected_entities:
        failed_stmt = select(models.ProjectOperationEvent).where(
            models.ProjectOperationEvent.user_id.in_(normalized_client_ids),
            models.ProjectOperationEvent.status == "failed",
            models.ProjectOperationEvent.operation.in_(["create", "update", "delete"]),
        )
        if start_local:
            failed_stmt = failed_stmt.where(models.ProjectOperationEvent.created_at >= start_local)
        if end_local:
            failed_stmt = failed_stmt.where(models.ProjectOperationEvent.created_at <= end_local)

        failed_rows = db.execute(failed_stmt).scalars().all()
        failed_project_ids = sorted({int(ev.project_id) for ev in failed_rows if ev.project_id is not None})
        failed_projects_map = {
            int(project.id): project
            for project in (
                db.execute(select(models.Project).where(models.Project.id.in_(failed_project_ids))).scalars().all()
                if failed_project_ids else []
            )
        }
        for ev in failed_rows:
            project = failed_projects_map.get(int(ev.project_id)) if ev.project_id is not None else None
            actor_id = _operation_event_actor_user_id(ev)
            actor = get_user_info(actor_id)
            description = _project_operation_description(
                ev,
                project=project,
                expose_internal_name=expose_internal_names,
            )
            event_id = f"POE-{ev.id}"
            actor_login = actor.login if actor else ""
            project_name = str(getattr(ev, "project_name", "") or "").strip() or None
            if not expose_internal_names and project_name and project is not None:
                project_name = _clean_client_internal_prefix_from_name(
                    project_name,
                    data_source_code=getattr(project, "data_source_code", None),
                    client_internal_prefix=getattr(project, "client_internal_prefix", None),
                )
            client_id = int(ev.user_id)
            client = get_client_info(client_id)
            client_label = f"{client.name or client.login} {client.id}" if client else ""
            if not _activity_status_matches(status, outcome="failed", status="done"):
                continue
            search_blob = " ".join(
                [
                    event_id,
                    "project",
                    ev.operation or "",
                    "failed",
                    description,
                    project_name or "",
                    actor_login,
                    client_label,
                ]
            ).lower()
            rows.append(
                {
                    "created_at": ev.created_at,
                    "search": search_blob,
                    "item": schemas.ActivityEventOut(
                        eventId=event_id,
                        sourceId=ev.id,
                        entity="project",
                        action=_normalize_project_operation(ev.operation),
                        createdAt=ev.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        client=client,
                        actor=actor,
                        description=description,
                        outcome="failed",
                        errorMessage=_clean_project_operation_error_message(ev.error_message),
                        status="done",
                        projectId=ev.project_id,
                        projectName=project_name,
                    ),
                }
            )

    if include_success and "balance" in selected_entities and status != "pending":
        balance_stmt = select(models.ClientBalanceOperation).where(
            models.ClientBalanceOperation.client_id.in_(normalized_client_ids)
        )
        if start_local:
            balance_stmt = balance_stmt.where(models.ClientBalanceOperation.created_at >= start_local)
        if end_local:
            balance_stmt = balance_stmt.where(models.ClientBalanceOperation.created_at <= end_local)

        balance_rows = db.execute(balance_stmt).scalars().all()
        for op in balance_rows:
            actor = get_user_info(op.created_by)
            description = _build_balance_activity_description(op.op_type, op.amount, op.comment)
            event_id = f"BO-{op.id}"
            actor_login = actor.login if actor else ""
            client = get_client_info(int(op.client_id))
            client_label = f"{client.name or client.login} {client.id}" if client else ""
            search_blob = " ".join(
                [
                    event_id,
                    "balance",
                    op.op_type,
                    description,
                    actor_login,
                    client_label,
                ]
            ).lower()
            rows.append(
                {
                    "created_at": op.created_at,
                    "search": search_blob,
                    "item": schemas.ActivityEventOut(
                        eventId=event_id,
                        sourceId=op.id,
                        entity="balance",
                        action=op.op_type,
                        createdAt=op.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        client=client,
                        actor=actor,
                        description=description,
                        status="done",
                        projectId=None,
                        projectName=None,
                    ),
                }
            )

    if include_success and "report" in selected_entities and status != "pending":
        report_stmt = select(models.ReportExport).where(
            models.ReportExport.target_client_id.in_(normalized_client_ids)
        )
        if start_local:
            report_stmt = report_stmt.where(models.ReportExport.created_at >= start_local)
        if end_local:
            report_stmt = report_stmt.where(models.ReportExport.created_at <= end_local)

        report_rows = db.execute(report_stmt).scalars().all()
        for rep in report_rows:
            actor = get_user_info(rep.user_id)
            description = _build_report_activity_description(rep)
            event_id = f"RE-{rep.id}"
            actor_login = actor.login if actor else ""
            client = get_client_info(int(rep.target_client_id))
            client_label = f"{client.name or client.login} {client.id}" if client else ""
            search_blob = " ".join(
                [
                    event_id,
                    "report",
                    "create",
                    description,
                    actor_login,
                    rep.from_date or "",
                    rep.to_date or "",
                    client_label,
                ]
            ).lower()
            rows.append(
                {
                    "created_at": rep.created_at,
                    "search": search_blob,
                    "item": schemas.ActivityEventOut(
                        eventId=event_id,
                        sourceId=rep.id,
                        entity="report",
                        action="create",
                        createdAt=rep.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        client=client,
                        actor=actor,
                        description=description,
                        status="done",
                        projectId=None,
                        projectName=None,
                        periodFrom=rep.from_date,
                        periodTo=rep.to_date,
                    ),
                }
            )

    q_norm = (q or "").strip().lower()
    if q_norm:
        rows = [row for row in rows if q_norm in row["search"]]

    rows.sort(key=lambda row: row["created_at"], reverse=True)
    total = len(rows)
    page_rows = rows[offset : offset + limit]
    items = [row["item"] for row in page_rows]
    return schemas.ActivityEventListOut(items=items, total=total)


def list_client_activity_events(
    db: Session,
    client_id: int,
    offset: int,
    limit: int,
    start_local: Optional[datetime] = None,
    end_local: Optional[datetime] = None,
    entities: Optional[List[str]] = None,
    q: Optional[str] = None,
) -> schemas.ActivityEventListOut:
    """
    Единая лента активности по аккаунту клиента.
    """
    return list_activity_events_for_clients(
        db,
        client_ids=[client_id],
        offset=offset,
        limit=limit,
        start_local=start_local,
        end_local=end_local,
        entities=entities,
        q=q,
        include_client=False,
        expose_internal_names=False,
    )


def list_project_history(db: Session, project_id: int, user_id: int, limit: int = 100) -> List[schemas.ProjectHistoryItem]:
    """
    Возвращает историю изменений конкретного проекта для текущего пользователя.

    Важно: клиент видит только свои изменения, поэтому фильтруем по user_id.
    """
    limit = max(1, min(500, limit))
    project = db.get(models.Project, project_id)
    stmt = (
        select(models.AuditEvent)
        .where(models.AuditEvent.project_id == project_id)
        .where(models.AuditEvent.user_id == user_id)
        .where(models.AuditEvent.action.in_(["create", "update", "delete"]))
        .order_by(models.AuditEvent.created_at.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    failed_rows = (
        db.execute(
            select(models.ProjectOperationEvent)
            .where(models.ProjectOperationEvent.project_id == project_id)
            .where(models.ProjectOperationEvent.user_id == user_id)
            .where(models.ProjectOperationEvent.status == "failed")
            .where(models.ProjectOperationEvent.operation.in_(["create", "update", "delete"]))
            .order_by(models.ProjectOperationEvent.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    actor_ids = {
        aid
        for aid in (
            [_audit_event_actor_user_id(ev) for ev in rows]
            + [_operation_event_actor_user_id(ev) for ev in failed_rows]
        )
        if aid
    }
    users_map = _load_user_info_map(db, actor_ids)

    items: List[schemas.ProjectHistoryItem] = []
    for ev in rows:
        actor_id = _audit_event_actor_user_id(ev)
        actor = users_map.get(actor_id) if actor_id else None
        actor_mode = _audit_event_actor_mode(ev, actor_id)
        items.append(_audit_event_to_history_item(
            ev,
            actor=actor,
            actor_mode=actor_mode,
            project=project,
            expose_internal_name=False,
        ))
    for ev in failed_rows:
        actor_id = _operation_event_actor_user_id(ev)
        actor = users_map.get(actor_id) if actor_id else None
        actor_mode = _operation_event_actor_mode(ev, actor_id)
        items.append(_project_operation_to_history_item(
            ev,
            actor=actor,
            actor_mode=actor_mode,
            project=project,
            expose_internal_name=False,
        ))
    items.sort(key=lambda item: item.createdAt, reverse=True)
    return items[:limit]


def admin_list_project_history(
    db: Session,
    project_id: int,
    limit: int = 100,
    user_id_filter: Optional[int] = None,
    start_local: Optional[datetime] = None,
    end_local: Optional[datetime] = None,
    status: Optional[str] = None,
) -> schemas.AdminProjectHistoryListOut:
    """
    История изменений проекта для админа с возможностью фильтровать по менеджеру, дате и статусу (pending/done/all).
    """
    limit = max(1, min(500, limit))
    base = (
        select(models.AuditEvent)
        .where(models.AuditEvent.project_id == project_id)
        .where(models.AuditEvent.action.in_(["create", "update", "delete"]))
    )
    actor_expr = func.coalesce(models.AuditEvent.actor_user_id, models.AuditEvent.user_id)
    if user_id_filter:
        base = base.where(actor_expr == user_id_filter)
    if start_local:
        base = base.where(models.AuditEvent.created_at >= start_local)
    if end_local:
        base = base.where(models.AuditEvent.created_at <= end_local)
    if status == "pending":
        base = base.where(models.AuditEvent.admin_processed_at.is_(None))
    elif status == "done":
        base = base.where(models.AuditEvent.admin_processed_at.is_not(None))

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        db.execute(
            base.order_by(models.AuditEvent.created_at.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
    failed_rows: List[models.ProjectOperationEvent] = []
    failed_total = 0
    if status is None:
        failed_base = (
            select(models.ProjectOperationEvent)
            .where(models.ProjectOperationEvent.project_id == project_id)
            .where(models.ProjectOperationEvent.status == "failed")
            .where(models.ProjectOperationEvent.operation.in_(["create", "update", "delete"]))
        )
        failed_actor_expr = func.coalesce(models.ProjectOperationEvent.actor_user_id, models.ProjectOperationEvent.user_id)
        if user_id_filter:
            failed_base = failed_base.where(failed_actor_expr == user_id_filter)
        if start_local:
            failed_base = failed_base.where(models.ProjectOperationEvent.created_at >= start_local)
        if end_local:
            failed_base = failed_base.where(models.ProjectOperationEvent.created_at <= end_local)
        failed_total = db.execute(select(func.count()).select_from(failed_base.subquery())).scalar_one()
        failed_rows = (
            db.execute(
                failed_base.order_by(models.ProjectOperationEvent.created_at.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
        total += failed_total

    # Собираем actor info
    user_ids = {
        aid
        for aid in (
            [_audit_event_actor_user_id(ev) for ev in rows]
            + [_operation_event_actor_user_id(ev) for ev in failed_rows]
        )
        if aid
    }
    users_map = _load_user_info_map(db, user_ids)

    items: List[schemas.AdminProjectHistoryItem] = []
    for ev in rows:
        actor_id = _audit_event_actor_user_id(ev)
        actor_mode = _audit_event_actor_mode(ev, actor_id)
        hist_item = _audit_event_to_history_item(
            ev,
            actor=users_map.get(actor_id) if actor_id else None,
            actor_mode=actor_mode,
        )
        status_val = "done" if ev.admin_processed_at is not None else "pending"
        items.append(
            schemas.AdminProjectHistoryItem(
                id=hist_item.id,
                eventId=hist_item.eventId,
                action=hist_item.action,
                createdAt=hist_item.createdAt,
                description=hist_item.description,
                user=hist_item.actor,
                actorMode=hist_item.actorMode,  # type: ignore[arg-type]
                status=status_val,  # type: ignore[arg-type]
                projectSnapshot=ev.after or ev.before,
            )
        )
    for ev in failed_rows:
        actor_id = _operation_event_actor_user_id(ev)
        actor_mode = _operation_event_actor_mode(ev, actor_id)
        hist_item = _project_operation_to_history_item(
            ev,
            actor=users_map.get(actor_id) if actor_id else None,
            actor_mode=actor_mode,
        )
        items.append(
            schemas.AdminProjectHistoryItem(
                id=hist_item.id,
                eventId=hist_item.eventId,
                action=hist_item.action,
                createdAt=hist_item.createdAt,
                description=hist_item.description,
                outcome=hist_item.outcome,
                errorMessage=hist_item.errorMessage,
                user=hist_item.actor,
                actorMode=hist_item.actorMode,  # type: ignore[arg-type]
                status="done",
                projectSnapshot=ev.request_payload,
            )
        )
    items.sort(key=lambda item: item.createdAt, reverse=True)
    items = items[:limit]

    return schemas.AdminProjectHistoryListOut(items=items, total=total)

def get_project(db: Session, project_id: int, user_id: int) -> Optional[schemas.ProjectOut]:
    p = db.get(models.Project, project_id)
    if not p or p.user_id != user_id:
        return None
    return _project_to_out(p, expose_internal_name=False) if p else None


def active_project_name_exists(
    db: Session,
    project_name: str,
    *,
    exclude_project_id: Optional[int] = None,
) -> bool:
    name = str(project_name or "").strip()
    if not name:
        return False
    stmt = select(models.Project.id).where(
        models.Project.name == name,
        models.Project.status != "Удалён",
    )
    if exclude_project_id is not None:
        stmt = stmt.where(models.Project.id != int(exclude_project_id))
    return db.execute(stmt.limit(1)).scalar_one_or_none() is not None


def build_project_model_from_create_item(
    item: schemas.CreateProjectItem,
    *,
    user_id: int,
    provider_id: Optional[str],
    created_at: Optional[datetime] = None,
    unique_name_applied: bool = False,
    client_internal_prefix: Optional[str] = None,
) -> models.Project:
    now = created_at or now_msk()
    sites = item.sites or None
    phones = item.phones or None
    sms = item.smsSenderName or None
    return models.Project(
        user_id=user_id,
        provider_project_id=(str(provider_id).strip() if provider_id is not None and str(provider_id).strip() else None),
        name=item.name,
        tag=item.tag or item.name,
        client_internal_prefix=normalize_client_internal_prefix(client_internal_prefix),
        unique_name_applied=bool(unique_name_applied),
        collection_source=item.collectionSource,
        data_source_code=item.dataSourceCode,
        region_mode=item.regionMode,
        regions=item.regions or None,
        sites=sites,
        phones=phones,
        sms_sender_name=sms,
        status=item.status,
        delivery_status='На модерации',
        data_limit=item.dataLimit,
        numbers_today=0,
        numbers_total=0,
        days_received=_join_days(item.days),
        sources_count=_calc_sources_count(sites, phones, sms),
        created_at=now,
        updated_at=now,
    )


def create_projects(
    db: Session,
    items: List[schemas.CreateProjectItem],
    user_id: int,
    provider_ids: Optional[List[Optional[str]]] = None,
    actor_user_id: Optional[int] = None,
    via_impersonation: bool = False,
    client_internal_prefix: Optional[str] = None,
) -> List[schemas.ProjectOut]:
    created: List[schemas.ProjectOut] = []
    now = now_msk()
    batch_id = secrets.token_hex(8)
    for idx, it in enumerate(items):
        provider_id = None
        if provider_ids and idx < len(provider_ids):
            raw_provider_id = provider_ids[idx]
            provider_id = str(raw_provider_id).strip() if raw_provider_id is not None else None
        p = build_project_model_from_create_item(
            it,
            user_id=user_id,
            provider_id=provider_id,
            created_at=now,
            unique_name_applied=False,
            client_internal_prefix=client_internal_prefix,
        )
        db.add(p)
        db.flush()

        after = _project_to_out(p).dict()
        add_project_audit_event(
            db,
            project=p,
            user_id=user_id,
            actor_user_id=actor_user_id or user_id,
            batch_id=batch_id,
            action='create',
            before=None,
            after=after,
            changed_fields=list(after.keys()),
            via_impersonation=via_impersonation,
        )
        created.append(_project_to_out(p, expose_internal_name=False))

    db.commit()
    return created


def _snapshot_project(p: models.Project) -> dict:
    snapshot = _project_to_out(p).dict()
    deleted_at = getattr(p, "deleted_at", None)
    grace_until = getattr(p, "provider_leads_grace_until", None)
    snapshot["deletedAt"] = deleted_at.strftime("%Y-%m-%d %H:%M:%S") if deleted_at else None
    snapshot["providerLeadsGraceUntil"] = grace_until.strftime("%Y-%m-%d %H:%M:%S") if grace_until else None
    return snapshot


def _project_audit_should_start_done(project: models.Project) -> bool:
    return str(getattr(project, "collection_source", "") or "").strip() != "СМС"


def add_project_audit_event(
    db: Session,
    *,
    project: models.Project,
    user_id: int,
    actor_user_id: Optional[int],
    action: str,
    before: Optional[dict],
    after: Optional[dict],
    changed_fields: Optional[List[str]],
    via_impersonation: bool,
    batch_id: Optional[str] = None,
) -> None:
    event = models.AuditEvent(
        user_id=user_id,
        actor_user_id=actor_user_id,
        project_id=project.id,
        batch_id=batch_id,
        action=action,
        before=before,
        after=after,
        changed_fields=changed_fields,
        via_impersonation=via_impersonation,
    )
    if _project_audit_should_start_done(project):
        event.admin_processed_at = now_msk()
    db.add(event)


def _apply_project_deleted_state(
    p: models.Project,
    *,
    deleted: bool,
    now: Optional[datetime] = None,
) -> None:
    # Эти поля в БД хранятся как naive TIMESTAMP, поэтому сравниваем
    # и записываем локальное время без tzinfo.
    ts = as_local_naive(now or now_msk())
    if deleted:
        if getattr(p, "deleted_at", None) is None:
            p.deleted_at = ts
        if getattr(p, "provider_leads_grace_until", None) is None or p.provider_leads_grace_until < ts:
            p.provider_leads_grace_until = ts + timedelta(hours=PROJECT_PROVIDER_LEADS_GRACE_HOURS)
        return
    p.deleted_at = None
    p.provider_leads_grace_until = None


def _keep_first_item(value: Any) -> Optional[List[Any]]:
    if not value:
        return None
    if isinstance(value, (list, tuple, set)):
        items = [item for item in value if item]
        return [items[0]] if items else None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        return [items[0]] if items else None
    return None


def update_project(
    db: Session,
    project_id: int,
    update: schemas.ProjectUpdate,
    user_id: int,
    actor_user_id: Optional[int] = None,
    via_impersonation: bool = False,
) -> Optional[schemas.ProjectOut]:
    p = db.get(models.Project, project_id)
    if not p:
        return None
    if p.user_id != user_id:
        return None
    before = _snapshot_project(p)

    p.name = update.name
    p.tag = update.tag or update.name
    p.status = update.status
    _apply_project_deleted_state(p, deleted=(update.status == "Удалён"))
    p.data_limit = update.dataLimit
    p.region_mode = update.regionMode
    p.regions = update.regions or None
    p.sites = update.sites or None
    p.phones = update.phones or None
    p.sms_sender_name = update.smsSenderName or None
    p.days_received = _join_days(update.days)
    p.sources_count = _calc_sources_count(p.sites, p.phones, p.sms_sender_name)
    p.updated_at = now_msk()

    after = _snapshot_project(p)
    changed = [k for k in after.keys() if before.get(k) != after.get(k)]
    add_project_audit_event(
        db,
        project=p,
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed,
        via_impersonation=via_impersonation,
    )
    db.commit()
    db.refresh(p)
    return _project_to_out(p, expose_internal_name=False)


def delete_project(
    db: Session,
    project_id: int,
    user_id: int,
    actor_user_id: Optional[int] = None,
    via_impersonation: bool = False,
) -> bool:
    p = db.get(models.Project, project_id)
    if not p:
        return False
    if p.user_id != user_id:
        return False
    before = _snapshot_project(p)
    deleted_now = now_msk()
    p.status = 'Удалён'  # мягкое удаление: только статус
    _apply_project_deleted_state(p, deleted=True, now=deleted_now)
    p.updated_at = deleted_now
    db.flush()
    after = _snapshot_project(p)
    add_project_audit_event(
        db,
        project=p,
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        action='delete',
        before=before,
        after=after,
        changed_fields=['status', 'deletedAt', 'providerLeadsGraceUntil'],
        via_impersonation=via_impersonation,
    )
    db.commit()
    return True


def list_blacklist_paginated(db: Session, user_id: int, offset: int, limit: int, q: str | None) -> schemas.BlacklistListOut:
    stmt = select(models.BlacklistPhone).where(models.BlacklistPhone.user_id == user_id)
    if q:
        stmt = stmt.where(models.BlacklistPhone.phone.contains(q))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.BlacklistPhone.id.desc()).offset(offset).limit(limit)).scalars().all()
    items = [schemas.BlacklistPhoneOut(id=r.id, phone=r.phone, createdAt=r.created_at.strftime('%Y-%m-%d')) for r in rows]
    return schemas.BlacklistListOut(items=items, total=total)


def fetch_pending_events(db: Session) -> Tuple[Optional[models.NotifyState], List[models.AuditEvent]]:
    state = db.get(models.NotifyState, 1)
    if not state or not state.next_send_at:
        return state, []
    now = now_msk()
    if now < state.next_send_at:
        return state, []
    rows = db.execute(select(models.AuditEvent).where(models.AuditEvent.sent == False).order_by(models.AuditEvent.created_at.asc())).scalars().all()  # noqa: E712
    return state, rows


def mark_events_sent_and_clear(db: Session, events: List[models.AuditEvent]) -> None:
    for ev in events:
        ev.sent = True
    state = db.get(models.NotifyState, 1)
    if state:
        state.next_send_at = None
    db.commit()


def get_provider_lead_by_vid(db: Session, vid: str) -> Optional[models.ProviderLead]:
    if not vid:
        return None
    return db.execute(
        select(models.ProviderLead).where(models.ProviderLead.vid == vid)
    ).scalar_one_or_none()


def _provider_lead_ts_col():
    """Операционное время идентификации в ЛК — момент записи в БД."""
    return models.ProviderLead.imported_at


def _provider_lead_display_dt(lead: models.ProviderLead) -> Optional[datetime]:
    return lead.imported_at


def resolve_project_by_name_for_provider_lead(
    db: Session,
    project_name: str,
) -> Tuple[Optional[int], Literal["not_found", "matched", "ambiguous"], List[models.Project]]:
    if not project_name:
        return None, "not_found", []
    active_rows = db.execute(
        select(models.Project)
        .where(
            models.Project.name == project_name,
            models.Project.status != "Удалён",
        )
        .order_by(models.Project.id.asc())
    ).scalars().all()
    if len(active_rows) == 1:
        return int(active_rows[0].id), "matched", active_rows
    if len(active_rows) > 1:
        return None, "ambiguous", active_rows

    now = now_msk_naive()
    deleted_rows = db.execute(
        select(models.Project)
        .where(
            models.Project.name == project_name,
            models.Project.status == "Удалён",
            models.Project.provider_leads_grace_until.is_not(None),
            models.Project.provider_leads_grace_until >= now,
        )
        .order_by(models.Project.id.asc())
    ).scalars().all()
    if len(deleted_rows) == 1:
        return int(deleted_rows[0].id), "matched", deleted_rows
    if len(deleted_rows) > 1:
        return None, "ambiguous", deleted_rows
    return None, "not_found", []


def get_project_id_by_name(db: Session, project_name: str) -> Optional[int]:
    project_id, _status, _rows = resolve_project_by_name_for_provider_lead(db, project_name)
    return project_id


def create_provider_lead(
    db: Session,
    *,
    vid: str,
    phone: Optional[str],
    phones_raw: Optional[List[str]],
    project_name: Optional[str],
    prov_created_at: Optional[datetime],
    prov_chanel: Optional[str],
    prov_source: Optional[str],
    subdomain: Optional[str],
    project_id: Optional[int],
) -> models.ProviderLead:
    row = models.ProviderLead(
        vid=vid,
        phone=phone,
        phones_raw=phones_raw,
        project_name=project_name,
        prov_created_at=prov_created_at,
        prov_chanel=prov_chanel,
        prov_source=prov_source,
        subdomain=subdomain,
        project_id=project_id,
        imported_at=now_msk(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _first_subdomain_value(subdomain: Optional[str]) -> Optional[str]:
    raw = str(subdomain or "").strip()
    if not raw:
        return None
    first = raw.split(";", 1)[0].strip()
    return first or None


def _build_utm_campaign(prov_source: Optional[str], subdomain: Optional[str]) -> Optional[str]:
    source = str(prov_source or "").strip()
    first_subdomain = _first_subdomain_value(subdomain)
    if source and first_subdomain:
        return f"{source}_{first_subdomain}"
    if source:
        return source
    return first_subdomain


def _provider_lead_to_export_row(
    lead: models.ProviderLead,
    user_info: Optional[schemas.UserInfo] = None,
    project: Optional[models.Project] = None,
    expose_internal_name: bool = True,
) -> dict:
    phone_value = lead.phone
    if not phone_value and lead.phones_raw:
        try:
            phone_value = ", ".join([str(x) for x in lead.phones_raw if x is not None])
        except Exception:
            phone_value = None
    display_dt = _provider_lead_display_dt(lead)
    project_name = lead.project_name or ""
    if not expose_internal_name and project is not None:
        project_name = _clean_client_internal_prefix_from_name(
            project_name,
            data_source_code=getattr(project, "data_source_code", None),
            client_internal_prefix=getattr(project, "client_internal_prefix", None),
        )
    return {
        "ext_id": lead.vid,
        "lk_id": format_provider_lead_lk_id(lead.id),
        "project_id": lead.project_id,
        "project_name": project_name,
        "source": lead.prov_chanel,
        "imported_at": display_dt.strftime("%Y-%m-%d %H:%M:%S") if display_dt else "",
        "phone": phone_value or "",
        "utm_campaign": _build_utm_campaign(lead.prov_source, lead.subdomain),
        "user_login": user_info.login if user_info else "",
        "user_name": (user_info.name or user_info.login) if user_info else "",
        "user_id": user_info.id if user_info else 0,
    }


def iter_provider_leads_for_export(
    db: Session,
    start_local: datetime,
    end_local: datetime,
    max_rows: int,
    project_ids: Optional[List[int]] = None,
    sources: Optional[List[str]] = None,
    user_info: Optional[schemas.UserInfo] = None,
    expose_internal_names: bool = True,
) -> Iterator[dict]:
    remaining = max(0, int(max_rows))
    if remaining == 0:
        return

    chunk_size = min(2000, remaining)
    ts_col = _provider_lead_ts_col()
    last_ts: Optional[datetime] = None
    last_id: Optional[int] = None

    while remaining > 0:
        limit_value = min(chunk_size, remaining)
        stmt = select(models.ProviderLead).where(and_(ts_col >= start_local, ts_col < end_local))
        if project_ids:
            stmt = stmt.where(models.ProviderLead.project_id.in_(project_ids))
        if sources:
            stmt = stmt.where(models.ProviderLead.prov_chanel.in_(sources))
        if last_ts is not None and last_id is not None:
            stmt = stmt.where(
                or_(
                    ts_col > last_ts,
                    and_(ts_col == last_ts, models.ProviderLead.id > last_id),
                ),
            )

        batch = (
            db.execute(
                stmt.order_by(ts_col.asc(), models.ProviderLead.id.asc()).limit(limit_value),
            )
            .scalars()
            .all()
        )
        if not batch:
            break

        user_info_by_project_id: Dict[int, schemas.UserInfo] = {}
        project_by_id: Dict[int, models.Project] = {}
        batch_project_ids = sorted({int(lead.project_id) for lead in batch if lead.project_id is not None})
        if batch_project_ids:
            projects = (
                db.execute(select(models.Project).where(models.Project.id.in_(batch_project_ids)))
                .scalars()
                .all()
            )
            project_by_id = {int(p.id): p for p in projects}
            if user_info is None:
                user_ids = sorted({int(p.user_id) for p in projects if p.user_id is not None})
                user_info_by_id = {
                    uid: info
                    for uid in user_ids
                    if (info := _get_user_info(db, uid)) is not None
                }
                user_info_by_project_id = {
                    int(p.id): user_info_by_id[int(p.user_id)]
                    for p in projects
                    if p.user_id is not None and int(p.user_id) in user_info_by_id
                }

        for lead in batch:
            row_user_info = user_info
            if row_user_info is None and lead.project_id is not None:
                row_user_info = user_info_by_project_id.get(int(lead.project_id))
            project = project_by_id.get(int(lead.project_id)) if lead.project_id is not None else None
            yield _provider_lead_to_export_row(
                lead,
                user_info=row_user_info,
                project=project,
                expose_internal_name=expose_internal_names,
            )

        remaining -= len(batch)
        tail = batch[-1]
        last_ts = _provider_lead_display_dt(tail)
        last_id = int(tail.id)


def fetch_provider_leads_for_export(
    db: Session,
    start_local: datetime,
    end_local: datetime,
    max_rows: int,
    project_ids: Optional[List[int]] = None,
    sources: Optional[List[str]] = None,
    user_info: Optional[schemas.UserInfo] = None,
    expose_internal_names: bool = True,
) -> List[dict]:
    # Совместимость со старым интерфейсом (возврат списка).
    return list(
        iter_provider_leads_for_export(
            db=db,
            start_local=start_local,
            end_local=end_local,
            max_rows=max_rows,
            project_ids=project_ids,
            sources=sources,
            user_info=user_info,
            expose_internal_names=expose_internal_names,
        ),
    )


def list_provider_leads_paginated(
    db: Session,
    project_ids: Optional[List[int]],
    start_local: datetime,
    end_local: datetime,
    offset: int,
    limit: int,
    sources: Optional[List[str]] = None,
    search_query: Optional[str] = None,
    user_id: Optional[int] = None,
) -> schemas.LeadsListOut:
    proj_ids: Optional[List[int]] = None
    if user_id is not None:
        proj_ids = get_user_project_ids(db, user_id)
        if not proj_ids:
            return schemas.LeadsListOut(items=[], total=0)
    if project_ids is not None:
        if proj_ids is None:
            proj_ids = project_ids
        else:
            proj_ids = [pid for pid in proj_ids if pid in project_ids]
        if not proj_ids:
            return schemas.LeadsListOut(items=[], total=0)

    ts_col = _provider_lead_ts_col()
    base = select(models.ProviderLead).where(
        and_(ts_col >= start_local, ts_col < end_local)
    )
    if proj_ids is not None:
        base = base.where(models.ProviderLead.project_id.in_(proj_ids))
    if sources:
        base = base.where(models.ProviderLead.prov_chanel.in_(sources))
    if search_query:
        search = search_query.strip()
        if search:
            like_value = f"%{search}%"
            utm_expr = (
                func.coalesce(models.ProviderLead.prov_source, "")
                + "_"
                + func.coalesce(models.ProviderLead.subdomain, "")
            )
            base = base.where(
                or_(
                    models.ProviderLead.phone.ilike(like_value),
                    cast(models.ProviderLead.phones_raw, String).ilike(like_value),
                    models.ProviderLead.prov_source.ilike(like_value),
                    models.ProviderLead.subdomain.ilike(like_value),
                    utm_expr.ilike(like_value),
                )
            )

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(
        base.order_by(ts_col.desc()).offset(offset).limit(limit)
    ).scalars().all()

    project_ids_from_rows = sorted(
        {int(row.project_id) for row in rows if row.project_id is not None}
    )
    projects_map: Dict[int, models.Project] = {}
    if project_ids_from_rows:
        projects = db.execute(
            select(models.Project).where(models.Project.id.in_(project_ids_from_rows))
        ).scalars().all()
        projects_map = {int(project.id): project for project in projects}

    items: List[schemas.LeadOut] = []
    for r in rows:
        phone_value = r.phone
        if not phone_value and r.phones_raw:
            try:
                phone_value = ", ".join([str(x) for x in r.phones_raw if x is not None])
            except Exception:
                phone_value = None
        created_at = _provider_lead_display_dt(r)
        project = projects_map.get(int(r.project_id)) if r.project_id is not None else None
        project_name = (
            _project_name_for_view(project, expose_internal_name=False)
            if project is not None
            else r.project_name
        )
        items.append(schemas.LeadOut(
            ext_id=str(r.vid),
            lk_id=format_provider_lead_lk_id(r.id),
            project_id=r.project_id,
            project_name=project_name,
            created_at=created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else "",
            imported_at=r.imported_at.strftime('%Y-%m-%d %H:%M:%S') if r.imported_at else "",
            phone=phone_value or "",
            utm_campaign=_build_utm_campaign(r.prov_source, r.subdomain),
            source=r.prov_chanel,
        ))
    return schemas.LeadsListOut(items=items, total=total)


# -------- Отчёты (экспорт) --------
def log_report_export(
    db: Session,
    user_id: int,
    client_id: Optional[int],
    from_date: str,
    to_date: str,
    project_ids: Optional[List[int]],
    fmt: str,
) -> models.ReportExport:
    """
    Фиксирует факт экспорта отчёта.

    Храним только параметры запроса, сам файл не сохраняем.
    """
    proj_str = ",".join(str(pid) for pid in project_ids) if project_ids else None
    row = models.ReportExport(
        user_id=user_id,
        target_client_id=client_id,
        from_date=from_date,
        to_date=to_date,
        project_ids=proj_str,
        format=fmt or "csv",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_reports_paginated(
    db: Session,
    user_id: int,
    offset: int,
    limit: int,
    start_local: datetime,
    end_local: datetime,
) -> schemas.ReportListOut:
    """
    Возвращает историю экспортов отчётов конкретного пользователя.
    """
    limit = max(1, min(500, limit))
    offset = max(0, offset)

    base = select(models.ReportExport).where(
        and_(
            models.ReportExport.user_id == user_id,
            models.ReportExport.created_at >= start_local,
            models.ReportExport.created_at <= end_local,
        )
    )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        db.execute(
            base.order_by(models.ReportExport.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    items: List[schemas.ReportOut] = []
    for r in rows:
        items.append(
            schemas.ReportOut(
                id=r.id,
                createdAt=r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                fromDate=r.from_date,
                toDate=r.to_date,
                projectIds=r.project_ids,
                format=r.format,
            )
        )
    return schemas.ReportListOut(items=items, total=total)


# -------- Черный список --------
def list_blacklist(db: Session, user_id: int) -> List[schemas.BlacklistPhoneOut]:
    rows = db.execute(select(models.BlacklistPhone).order_by(models.BlacklistPhone.id.desc())).scalars().all()
    out: List[schemas.BlacklistPhoneOut] = []
    for r in rows:
        out.append(schemas.BlacklistPhoneOut(id=r.id, phone=r.phone, createdAt=r.created_at.strftime('%Y-%m-%d')))
    return out


def add_to_blacklist(
    db: Session,
    user_id: int,
    phones: List[str],
    actor_user_id: Optional[int] = None,
    via_impersonation: bool = False,
) -> List[schemas.BlacklistPhoneOut]:
    created: List[schemas.BlacklistPhoneOut] = []
    now = now_msk()
    normalized_seen = set()
    for p in phones:
        # нормализация: только цифры, 11 символов, привести 8 к 7
        digits = "".join([c for c in p if c.isdigit()])
        if len(digits) == 10:
            digits = "7" + digits
        elif len(digits) == 11 and digits[0] == "8":
            digits = "7" + digits[1:]
        if len(digits) != 11 or digits[0] != "7":
            continue
        if digits in normalized_seen:
            continue
        normalized_seen.add(digits)
        # вставка, игнорировать дубликаты по user_id+phone
        exists = db.execute(
            select(models.BlacklistPhone).where(
                and_(models.BlacklistPhone.user_id == user_id, models.BlacklistPhone.phone == digits)
            )
        ).scalar_one_or_none()
        if exists:
            continue
        row = models.BlacklistPhone(user_id=user_id, phone=digits, created_at=now)
        db.add(row)
        db.flush()
        created.append(schemas.BlacklistPhoneOut(id=row.id, phone=row.phone, createdAt=row.created_at.strftime('%Y-%m-%d')))

    # Аудит: единым событием фиксируем добавленные номера
    if created:
        payload_after = {
            "phones": [c.phone for c in created],
            "count": len(created),
        }
        db.add(models.AuditEvent(
            user_id=user_id,
            actor_user_id=actor_user_id or user_id,
            project_id=None,
            action='blacklist_add',
            before=None,
            after=payload_after,
            changed_fields=list(payload_after.keys()),
            via_impersonation=via_impersonation,
        ))
    db.commit()
    return created


def delete_from_blacklist(
    db: Session,
    user_id: int,
    row_id: int,
    actor_user_id: Optional[int] = None,
    via_impersonation: bool = False,
) -> bool:
    row = db.get(models.BlacklistPhone, row_id)
    if not row:
        return False
    if row.user_id is not None and row.user_id != user_id:
        return False
    before_phone = row.phone
    db.delete(row)
    db.flush()
    # Аудит: фиксируем удалённый номер
    db.add(models.AuditEvent(
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        project_id=None,
        action='blacklist_delete',
        before={"phone": before_phone},
        after=None,
        changed_fields=["phone"],
        via_impersonation=via_impersonation,
    ))
    db.commit()
    return True


# -------- Пользователи и инициализация --------
def get_user_by_login(db: Session, login: str) -> Optional[models.User]:
    return db.execute(select(models.User).where(models.User.login == login)).scalar_one_or_none()


def create_user(db: Session, login: str, password_plain: str, role: str = ROLE_CLIENT) -> models.User:
    normalized_role = ROLE_ADMIN if login == "admin" else str(role or ROLE_CLIENT)
    user = models.User(login=login, password_hash=auth.hash_password(password_plain), role=normalized_role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _translit_login_base(text: str) -> str:
    mapping = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
    cleaned = []
    for ch in text.lower():
        if ch.isalnum():
            cleaned.append(mapping.get(ch, ch))
        elif ch in (" ", "-", "_", "."):
            cleaned.append("-")
    base = "".join(cleaned)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    if not base:
        return "client"
    return base[:40]


def _random_suffix(length: int = 4) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _ensure_unique_login(db: Session, preferred: str) -> str:
    base = preferred or "client"
    base = re.sub(r"[^a-z0-9_-]", "", base.lower()).strip("-_") or "client"
    base = base[:40]
    for attempt in range(20):
        candidate = base if attempt == 0 else f"{base}-{_random_suffix(4)}"
        exists = db.execute(select(func.count()).where(models.User.login == candidate)).scalar_one()
        if not exists:
            return candidate
    raise ValueError("Не удалось сгенерировать уникальный логин, попробуйте вручную.")


def _ensure_agent_owner(db: Session, owner_agent_id: Optional[int]) -> Optional[models.User]:
    if owner_agent_id is None:
        return None
    agent = db.get(models.User, int(owner_agent_id))
    if not agent or not is_agent_user(agent):
        raise ValueError("Агент не найден.")
    if user_is_disabled(agent):
        raise ValueError("Нельзя закрепить клиента за отключённым агентом.")
    return agent


def admin_create_client(
    db: Session,
    name: str,
    inn: str,
    phone: str,
    contact: Optional[str],
    login: Optional[str],
    password: Optional[str],
    auto_limit_control_enabled: bool = False,
    telegram_notifications_chat_id: Optional[str] = None,
    telegram_auto_pause_enabled: bool = False,
    unique_project_names_enabled: bool = False,
    internal_client_id: Optional[str] = None,
    table_url: Optional[str] = None,
    owner_agent_id: Optional[int] = None,
) -> schemas.AdminClientCreateOut:
    now = now_msk()
    name_clean = (name or "").strip()
    if not name_clean:
        raise ValueError("Имя клиента не может быть пустым.")

    inn_digits = re.sub(r"\D+", "", inn or "")
    if len(inn_digits) not in (10, 12):
        raise ValueError("ИНН должен содержать 10 или 12 цифр.")
    existing_by_inn = db.execute(
        select(models.ClientProfile).where(models.ClientProfile.inn == inn_digits)
    ).scalar_one_or_none()
    if existing_by_inn:
        raise ValueError("Клиент с таким ИНН уже существует.")

    phone_clean = (phone or "").strip()
    phone_digits = re.sub(r"\D+", "", phone_clean)
    if len(phone_digits) < 10:
        raise ValueError("Телефон должен содержать минимум 10 цифр.")

    if login:
        preferred_login = (login or "").strip().lower()
    else:
        preferred_login = _translit_login_base(name_clean)
    final_login = _ensure_unique_login(db, preferred_login)

    raw_password = password.strip() if password else _generate_password()
    telegram_chat_id = (telegram_notifications_chat_id or "").strip() or None
    owner_agent = _ensure_agent_owner(db, owner_agent_id)
    user = models.User(
        login=final_login,
        password_hash=auth.hash_password(raw_password),
        display_name=name_clean,
        role=ROLE_CLIENT,
        owner_agent_id=(int(owner_agent.id) if owner_agent is not None else None),
        auto_limit_control_enabled=bool(auto_limit_control_enabled),
        telegram_notifications_chat_id=telegram_chat_id,
        telegram_auto_pause_enabled=bool(telegram_auto_pause_enabled),
        unique_project_names_enabled=bool(unique_project_names_enabled),
        created_at=now,
    )
    db.add(user)
    db.flush()

    profile = models.ClientProfile(
        user_id=user.id,
        name=name_clean,
        inn=inn_digits,
        phone=phone_clean,
        contact=(contact or "").strip() or None,
        internal_client_id=(internal_client_id or "").strip() or None,
        table_url=(table_url or "").strip() or None,
        work_status=CLIENT_WORK_STATUS_DEFAULT,
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    db.commit()
    db.refresh(user)
    db.refresh(profile)

    return schemas.AdminClientCreateOut(
        user=_user_info_from_user(user, name=name_clean),
        profile=schemas.ClientProfileOut.from_orm(profile),
        login=final_login,
        password=raw_password,
    )


def admin_update_client(
    db: Session,
    client_id: int,
    name: Optional[str],
    inn: Optional[str],
    phone: Optional[str],
    contact: Optional[str],
    login: Optional[str],
    password: Optional[str],
    auto_limit_control_enabled: Optional[bool] = None,
    telegram_notifications_chat_id: Optional[str] = None,
    telegram_auto_pause_enabled: Optional[bool] = None,
    unique_project_names_enabled: Optional[bool] = None,
    internal_client_id: Optional[str] = None,
    table_url: Optional[str] = None,
    owner_agent_id: Optional[int] = None,
    commit: bool = True,
) -> schemas.AdminClientUpdateOut:
    user = db.get(models.User, client_id)
    if not user:
        raise ValueError("Клиент не найден.")
    if not is_client_user(user):
        raise ValueError("Пользователь не является клиентом.")

    profile = db.execute(
        select(models.ClientProfile).where(models.ClientProfile.user_id == client_id)
    ).scalar_one_or_none()
    now = now_msk()

    # Логин / пароль
    final_login = user.login
    raw_password: Optional[str] = None
    login_changed = False
    password_changed = False
    if login is not None:
        preferred_login = (login or "").strip().lower()
        if preferred_login and preferred_login != user.login:
            final_login = _ensure_unique_login(db, preferred_login)
            user.login = final_login
            login_changed = True
    if password:
        raw_password = password.strip() if password else None
        if raw_password:
            user.password_hash = auth.hash_password(raw_password)
            password_changed = True
    if login_changed or password_changed:
        user.created_at = user.created_at or now  # safety
    if auto_limit_control_enabled is not None:
        user.auto_limit_control_enabled = bool(auto_limit_control_enabled)
    if telegram_notifications_chat_id is not None:
        user.telegram_notifications_chat_id = telegram_notifications_chat_id.strip() or None
    if telegram_auto_pause_enabled is not None:
        user.telegram_auto_pause_enabled = bool(telegram_auto_pause_enabled)
    if unique_project_names_enabled is not None:
        user.unique_project_names_enabled = bool(unique_project_names_enabled)

    # Профиль
    name_clean = name.strip() if name else None
    inn_digits = re.sub(r"\D+", "", inn or "") if inn is not None else None
    phone_clean = phone.strip() if phone else None

    if inn_digits is not None:
        if len(inn_digits) not in (10, 12):
            raise ValueError("ИНН должен содержать 10 или 12 цифр.")
        existing_by_inn = db.execute(
            select(models.ClientProfile).where(
                models.ClientProfile.inn == inn_digits,
                models.ClientProfile.user_id != client_id,
            )
        ).scalar_one_or_none()
        if existing_by_inn:
            raise ValueError("Клиент с таким ИНН уже существует.")

    if phone_clean is not None:
        phone_digits = re.sub(r"\D+", "", phone_clean)
        if len(phone_digits) < 10:
            raise ValueError("Телефон должен содержать минимум 10 цифр.")

    if not profile:
        profile = models.ClientProfile(
            user_id=client_id,
            name=name_clean or user.login,
            inn=inn_digits or "",
            phone=phone_clean or "",
            contact=(contact or "").strip() or None,
            internal_client_id=(internal_client_id or "").strip() or None,
            table_url=(table_url or "").strip() or None,
            work_status=CLIENT_WORK_STATUS_DEFAULT,
            created_at=now,
            updated_at=now,
        )
        db.add(profile)
    else:
        if name_clean is not None:
            profile.name = name_clean
        if inn_digits is not None:
            profile.inn = inn_digits
        if phone_clean is not None:
            profile.phone = phone_clean
        if contact is not None:
            profile.contact = (contact or "").strip() or None
        if internal_client_id is not None:
            profile.internal_client_id = (internal_client_id or "").strip() or None
        if table_url is not None:
            profile.table_url = (table_url or "").strip() or None
        profile.updated_at = now

    if name_clean is not None:
        user.display_name = name_clean

    if commit:
        db.commit()
        db.refresh(user)
        db.refresh(profile)
    else:
        # Нужен flush, чтобы caller мог:
        # 1) проверить/отправить тестовое внешнее сообщение,
        # 2) затем либо commit, либо rollback без рассинхронизации настроек.
        db.flush()

    return schemas.AdminClientUpdateOut(
        user=_user_info_from_user(user, name=(profile.name if profile else None)),
        profile=schemas.ClientProfileOut.from_orm(profile),
        login=user.login,
        password=raw_password,
    )


def admin_update_client_work_status(
    db: Session,
    *,
    client_id: int,
    actor_user_id: int,
    work_status: str,
) -> schemas.AdminClientWorkStatusUpdateOut:
    user = db.get(models.User, int(client_id))
    if not user or not is_client_user(user):
        raise ValueError("Клиент не найден.")
    next_status = normalize_client_work_status(work_status)
    now = now_msk()
    profile = db.execute(
        select(models.ClientProfile).where(models.ClientProfile.user_id == int(client_id))
    ).scalar_one_or_none()
    if profile is None:
        profile = models.ClientProfile(
            user_id=int(client_id),
            name=user.display_name or user.login,
            inn="",
            phone="",
            work_status=CLIENT_WORK_STATUS_DEFAULT,
            created_at=now,
            updated_at=now,
        )
        db.add(profile)
        db.flush()

    prev_status = normalize_client_work_status(getattr(profile, "work_status", None))
    if prev_status != next_status:
        profile.work_status = next_status
        profile.updated_at = now
        db.add(
            models.AuditEvent(
                user_id=int(client_id),
                actor_user_id=int(actor_user_id),
                project_id=None,
                action="client_work_status_update",
                before={"workStatus": prev_status},
                after={"workStatus": next_status},
                changed_fields=["workStatus"],
                via_impersonation=False,
                created_at=now,
                sent=True,
                admin_processed_at=now,
                admin_processed_by=int(actor_user_id),
            )
        )

    db.commit()
    db.refresh(profile)
    return schemas.AdminClientWorkStatusUpdateOut(
        clientId=int(client_id),
        workStatus=normalize_client_work_status(getattr(profile, "work_status", None)),
    )


def admin_create_agent(
    db: Session,
    name: str,
    inn: str,
    phone: str,
    login: Optional[str],
    password: Optional[str],
) -> schemas.AdminAgentCreateOut:
    now = now_msk()
    name_clean = (name or "").strip()
    if not name_clean:
        raise ValueError("Имя агента не может быть пустым.")
    inn_digits = re.sub(r"\D+", "", inn or "")
    if len(inn_digits) not in (10, 12):
        raise ValueError("ИНН агента должен содержать 10 или 12 цифр.")
    phone_clean = (phone or "").strip()
    phone_digits = re.sub(r"\D+", "", phone_clean)
    if len(phone_digits) < 10:
        raise ValueError("Телефон агента должен содержать минимум 10 цифр.")

    preferred_login = (login or "").strip().lower() if login else _translit_login_base(name_clean)
    final_login = _ensure_unique_login(db, preferred_login)
    raw_password = password.strip() if password else _generate_password()

    user = models.User(
        login=final_login,
        password_hash=auth.hash_password(raw_password),
        display_name=name_clean,
        inn=inn_digits,
        phone=phone_clean,
        role=ROLE_AGENT,
        is_disabled=False,
        created_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return schemas.AdminAgentCreateOut(
        user=_user_info_from_user(user, name=name_clean),
        login=final_login,
        password=raw_password,
    )


def admin_update_agent(
    db: Session,
    agent_id: int,
    name: Optional[str],
    inn: Optional[str],
    phone: Optional[str],
    login: Optional[str],
    password: Optional[str],
    is_disabled: Optional[bool] = None,
) -> schemas.AdminAgentUpdateOut:
    user = db.get(models.User, int(agent_id))
    if not user or not is_agent_user(user):
        raise ValueError("Агент не найден.")

    raw_password: Optional[str] = None
    if name is not None:
        name_clean = name.strip()
        if not name_clean:
            raise ValueError("Имя агента не может быть пустым.")
        user.display_name = name_clean
    if inn is not None:
        inn_digits = re.sub(r"\D+", "", inn or "")
        if len(inn_digits) not in (10, 12):
            raise ValueError("ИНН агента должен содержать 10 или 12 цифр.")
        user.inn = inn_digits
    if phone is not None:
        phone_clean = phone.strip()
        phone_digits = re.sub(r"\D+", "", phone_clean)
        if len(phone_digits) < 10:
            raise ValueError("Телефон агента должен содержать минимум 10 цифр.")
        user.phone = phone_clean
    if login is not None:
        preferred_login = (login or "").strip().lower()
        if preferred_login and preferred_login != user.login:
            user.login = _ensure_unique_login(db, preferred_login)
    if password:
        raw_password = password.strip() if password else None
        if raw_password:
            user.password_hash = auth.hash_password(raw_password)
    if is_disabled is not None:
        user.is_disabled = bool(is_disabled)

    db.commit()
    db.refresh(user)
    return schemas.AdminAgentUpdateOut(
        user=_user_info_from_user(user),
        login=user.login,
        password=raw_password,
    )


def list_agents_summary(db: Session) -> schemas.AdminAgentsListOut:
    agents = db.execute(
        select(models.User).where(models.User.role == ROLE_AGENT).order_by(models.User.created_at.desc(), models.User.id.desc())
    ).scalars().all()
    agent_ids = [int(agent.id) for agent in agents]
    client_count_map: Dict[int, int] = {}
    agent_client_ids_map: Dict[int, List[int]] = {}
    if agent_ids:
        rows = db.execute(
            select(models.User.owner_agent_id, models.User.id)
            .where(models.User.role == ROLE_CLIENT, models.User.owner_agent_id.in_(agent_ids))
        ).all()
        for owner_agent_id, client_id in rows:
            if owner_agent_id is None or client_id is None:
                continue
            aid = int(owner_agent_id)
            agent_client_ids_map.setdefault(aid, []).append(int(client_id))
        client_count_map = {aid: len(client_ids) for aid, client_ids in agent_client_ids_map.items()}

    all_client_ids = [client_id for client_ids in agent_client_ids_map.values() for client_id in client_ids]
    remaining_map = _get_client_remaining_map(db, all_client_ids)
    client_balance_rows: Dict[int, Dict[str, int]] = {}
    if all_client_ids:
        rows = db.execute(
            select(
                models.ClientBalanceOperation.client_id,
                models.ClientBalanceOperation.op_type,
                func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0),
            )
            .where(models.ClientBalanceOperation.client_id.in_(all_client_ids))
            .group_by(models.ClientBalanceOperation.client_id, models.ClientBalanceOperation.op_type)
        ).all()
        for client_id, op_type, total_amt in rows:
            cid = int(client_id)
            client_balance_rows.setdefault(cid, {"credit": 0, "debit": 0})
            client_balance_rows[cid][str(op_type)] = int(total_amt or 0)

    items = []
    for agent in agents:
        agent_client_ids = agent_client_ids_map.get(int(agent.id), [])
        credited = sum(client_balance_rows.get(client_id, {}).get("credit", 0) for client_id in agent_client_ids)
        debited = sum(client_balance_rows.get(client_id, {}).get("debit", 0) for client_id in agent_client_ids)
        balance = sum(remaining_map.get(client_id, 0) for client_id in agent_client_ids)
        items.append(
            schemas.AdminAgentSummaryItem(
                user=_user_info_from_user(agent),
                clientCount=client_count_map.get(int(agent.id), 0),
                credited=credited,
                debited=debited,
                balance=balance,
                createdAt=agent.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
    return schemas.AdminAgentsListOut(items=items, total=len(items))


def seed_users_from_env(db: Session) -> None:
    """
    Идём по переменным USER_{N}_LOGIN / USER_{N}_PASSWORD и создаём отсутствующих.
    Первым будет admin с id=1 (если база пустая).
    """
    idx = 1
    created_any = False
    while True:
        login = os.getenv(f"USER_{idx}_LOGIN")
        password = os.getenv(f"USER_{idx}_PASSWORD")
        if not login or not password:
            break
        exists = get_user_by_login(db, login)
        if not exists:
            create_user(db, login, password, role=ROLE_ADMIN if idx == 1 else ROLE_CLIENT)
            created_any = True
        elif idx == 1 and get_user_role(exists) != ROLE_ADMIN:
            exists.role = ROLE_ADMIN
            created_any = True
        idx += 1
    if created_any:
        # убедиться, что есть индексы/структура
        db.commit()


def get_user_project_ids(db: Session, user_id: int) -> List[int]:
    rows = db.execute(select(models.Project.id).where(models.Project.user_id == user_id)).all()
    return [int(r[0]) for r in rows]


def admin_list_client_changes_summary(db: Session, actions: Optional[List[str]] = None) -> List[schemas.AdminClientChangesSummaryItem]:
    """
    Краткая сводка по необработанным изменениям по клиентам.

    Считаем только события, созданные НЕ админом (user_id != 1),
    с action в ['create','update','delete'] и admin_processed_at IS NULL.
    """
    allowed_actions = {"create", "update", "delete", "blacklist_add", "blacklist_delete"}
    default_actions = ["create", "update", "delete", "blacklist_add", "blacklist_delete"]
    action_filter = [a for a in (actions or default_actions) if a in allowed_actions]
    if not action_filter:
        action_filter = default_actions
    include_creates = "create" in action_filter
    include_updates = any(a in action_filter for a in ("update", "delete"))
    include_bl_add = "blacklist_add" in action_filter
    include_bl_del = "blacklist_delete" in action_filter
    # Собираем пары (user_id, login, count)
    # pending update/delete
    rows_updates = []
    if include_updates:
        rows_updates = db.execute(
            select(
                models.User.id,
                models.User.login,
                func.count(models.AuditEvent.id),
            )
            .join(models.AuditEvent, models.AuditEvent.user_id == models.User.id)
            .where(models.User.role == ROLE_CLIENT)
            .where(models.AuditEvent.action.in_([a for a in action_filter if a in ("update", "delete")]))
            .where(models.AuditEvent.admin_processed_at.is_(None))
            .group_by(models.User.id, models.User.login)
        ).all()

    # pending create
    rows_creates = []
    if include_creates:
        rows_creates = db.execute(
            select(
                models.User.id,
                models.User.login,
                func.count(models.AuditEvent.id),
            )
            .join(models.AuditEvent, models.AuditEvent.user_id == models.User.id)
            .where(models.User.role == ROLE_CLIENT)
            .where(models.AuditEvent.action == "create")
            .where(models.AuditEvent.admin_processed_at.is_(None))
            .group_by(models.User.id, models.User.login)
        ).all()

    rows_bl_add = []
    if include_bl_add:
        rows_bl_add = db.execute(
            select(
                models.User.id,
                models.User.login,
                func.count(models.AuditEvent.id),
            )
            .join(models.AuditEvent, models.AuditEvent.user_id == models.User.id)
            .where(models.User.role == ROLE_CLIENT)
            .where(models.AuditEvent.action == "blacklist_add")
            .where(models.AuditEvent.admin_processed_at.is_(None))
            .group_by(models.User.id, models.User.login)
        ).all()

    rows_bl_del = []
    if include_bl_del:
        rows_bl_del = db.execute(
            select(
                models.User.id,
                models.User.login,
                func.count(models.AuditEvent.id),
            )
            .join(models.AuditEvent, models.AuditEvent.user_id == models.User.id)
            .where(models.User.role == ROLE_CLIENT)
            .where(models.AuditEvent.action == "blacklist_delete")
            .where(models.AuditEvent.admin_processed_at.is_(None))
            .group_by(models.User.id, models.User.login)
        ).all()

    creates_map = {int(uid): (login, int(cnt or 0)) for uid, login, cnt in rows_creates}
    updates_map = {int(uid): (login, int(cnt or 0)) for uid, login, cnt in rows_updates}
    bl_add_map = {int(uid): (login, int(cnt or 0)) for uid, login, cnt in rows_bl_add}
    bl_del_map = {int(uid): (login, int(cnt or 0)) for uid, login, cnt in rows_bl_del}

    # Собираем все user_ids, которые имеют либо обновления, либо создания
    all_uids = set(creates_map.keys()) | set(updates_map.keys()) | set(bl_add_map.keys()) | set(bl_del_map.keys())

    items: List[schemas.AdminClientChangesSummaryItem] = []
    for uid in all_uids:
        upd_login, upd_cnt = updates_map.get(uid, (None, 0))
        crt_login, crt_cnt = creates_map.get(uid, (None, 0))
        add_login, add_cnt = bl_add_map.get(uid, (None, 0))
        del_login, del_cnt = bl_del_map.get(uid, (None, 0))
        login = upd_login or crt_login or add_login or del_login or ""
        user_info = schemas.UserInfo(id=int(uid), login=login)
        total = upd_cnt + crt_cnt + add_cnt + del_cnt
        items.append(
            schemas.AdminClientChangesSummaryItem(
                user=user_info,
                pendingChanges=upd_cnt,
                pendingCreates=crt_cnt,
                pendingBlacklistAdds=add_cnt,
                pendingBlacklistDeletes=del_cnt,
                pendingTotal=total,
            )
        )
    return items


def admin_clients_summary(
    db: Session,
    start_local: datetime,
    end_local: datetime,
) -> schemas.AdminClientsSummaryOut:
    """
    Агрегированная сводка по клиентам: проекты, использование номеров и остаток
    (начисления/списания − использовано по лидам).
    """
    # Все пользователи-клиенты (исключаем админа id=1)
    users = db.execute(
        select(models.User)
        .where(models.User.role == ROLE_CLIENT)
        .where(models.User.id != 1)
        .order_by(models.User.id.asc())
    ).scalars().all()
    user_row_map = {int(user.id): user for user in users}
    users_map: Dict[int, schemas.UserInfo] = {
        u.id: _user_info_from_user(u)
        for u in users
    }

    # Профили клиентов
    profiles_map: Dict[int, schemas.ClientProfileOut] = {}
    user_ids = list(users_map.keys())
    if user_ids:
        profiles = db.execute(
            select(models.ClientProfile).where(models.ClientProfile.user_id.in_(user_ids))
        ).scalars().all()
        for p in profiles:
            profiles_map[p.user_id] = schemas.ClientProfileOut.from_orm(p)
            if p.user_id in users_map:
                users_map[p.user_id].name = p.name

    owner_agent_ids = sorted({int(user.owner_agent_id) for user in users if getattr(user, "owner_agent_id", None) is not None})
    owner_user_map: Dict[int, schemas.UserInfo] = {}
    if owner_agent_ids:
        owner_users = db.execute(select(models.User).where(models.User.id.in_(owner_agent_ids))).scalars().all()
        owner_user_map = {int(owner_user.id): _user_info_from_user(owner_user) for owner_user in owner_users}

    # Статистика по проектам
    by_user: Dict[int, Dict[str, int]] = {uid: {"projects": 0, "limit": 0, "used_total": 0, "used_period": 0} for uid in users_map.keys()}
    proj_rows = db.execute(
        select(
            models.Project.user_id,
            func.coalesce(func.sum(case((models.Project.status != "Удалён", 1), else_=0)), 0),
            func.coalesce(func.sum(models.Project.data_limit), 0),
        ).group_by(models.Project.user_id)
    ).all()
    for uid, cnt, limit_sum in proj_rows:
        if uid is None:
            continue
        by_user.setdefault(int(uid), {"projects": 0, "limit": 0, "used_total": 0, "used_period": 0})
        by_user[int(uid)]["projects"] = int(cnt or 0)
        by_user[int(uid)]["limit"] = int(limit_sum or 0)

    collection_rows = db.execute(
        select(
            models.Project.user_id,
            func.coalesce(func.count(models.Project.id), 0),
            func.coalesce(func.sum(case((models.Project.status == "Активен", 1), else_=0)), 0),
        )
        .where(models.Project.deleted_at.is_(None))
        .where(models.Project.status != "Удалён")
        .group_by(models.Project.user_id)
    ).all()
    collection_map: Dict[int, schemas.ClientDataCollectionStatus] = {}
    for uid, project_count, active_count in collection_rows:
        if uid is None:
            continue
        if int(project_count or 0) <= 0:
            collection_map[int(uid)] = "Нет проектов"
        elif int(active_count or 0) > 0:
            collection_map[int(uid)] = "Сбор активен"
        else:
            collection_map[int(uid)] = "На паузе"

    # Использование за все время
    used_total_rows = db.execute(
        select(models.Project.user_id, func.count())
        .join(models.ProviderLead, models.ProviderLead.project_id == models.Project.id)
        .group_by(models.Project.user_id)
    ).all()
    for uid, cnt in used_total_rows:
        if uid is None:
            continue
        by_user.setdefault(int(uid), {"projects": 0, "limit": 0, "used_total": 0, "used_period": 0})
        by_user[int(uid)]["used_total"] = int(cnt or 0)

    # Использование за период (по imported_at, локальное время без tz)
    ts_col = _provider_lead_ts_col()
    used_period_rows = db.execute(
        select(models.Project.user_id, func.count())
        .join(models.ProviderLead, models.ProviderLead.project_id == models.Project.id)
        .where(ts_col >= start_local)
        .where(ts_col <= end_local)
        .group_by(models.Project.user_id)
    ).all()
    for uid, cnt in used_period_rows:
        if uid is None:
            continue
        by_user.setdefault(int(uid), {"projects": 0, "limit": 0, "used_total": 0, "used_period": 0})
        by_user[int(uid)]["used_period"] = int(cnt or 0)

    # Карта pending изменений
    pending_items = admin_list_client_changes_summary(db)
    pending_map = {item.user.id: item.pendingChanges for item in pending_items}
    pending_creates_map = {item.user.id: item.pendingCreates for item in pending_items}

    # Начисления/списания по номерам
    balance_rows = db.execute(
        select(
            models.ClientBalanceOperation.client_id,
            models.ClientBalanceOperation.op_type,
            func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0),
        ).group_by(models.ClientBalanceOperation.client_id, models.ClientBalanceOperation.op_type)
    ).all()
    balance_map: Dict[int, Dict[str, int]] = {}
    for client_id, op_type, total_amt in balance_rows:
        cid = int(client_id)
        balance_map.setdefault(cid, {"credit": 0, "debit": 0})
        balance_map[cid][str(op_type)] = int(total_amt or 0)

    last_tariff_rows = db.execute(
        select(models.ClientTariff)
        .order_by(models.ClientTariff.client_id.asc(), models.ClientTariff.created_at.desc(), models.ClientTariff.id.desc())
    ).scalars().all()
    last_tariff_by_client: Dict[int, models.ClientTariff] = {}
    for tariff in last_tariff_rows:
        client_id = int(tariff.client_id)
        if client_id not in last_tariff_by_client:
            last_tariff_by_client[client_id] = tariff

    last_tariff_ids = [int(tariff.id) for tariff in last_tariff_by_client.values()]
    tariff_ops_rows = []
    if last_tariff_ids:
        tariff_ops_rows = db.execute(
            select(
                models.ClientTariffOperation.tariff_id,
                models.ClientTariffOperation.op_type,
                func.coalesce(func.sum(models.ClientTariffOperation.amount), 0),
            )
            .where(models.ClientTariffOperation.tariff_id.in_(last_tariff_ids))
            .group_by(models.ClientTariffOperation.tariff_id, models.ClientTariffOperation.op_type)
        ).all()
    tariff_ops_map: Dict[int, Dict[str, int]] = {}
    for tariff_id, op_type, total_amt in tariff_ops_rows:
        tid = int(tariff_id)
        tariff_ops_map.setdefault(tid, {"credit": 0, "debit": 0})
        tariff_ops_map[tid][str(op_type)] = int(total_amt or 0)

    items: List[schemas.AdminClientSummaryItem] = []
    totals_projects = 0
    totals_limit = 0
    totals_used = 0
    totals_used_period = 0
    totals_remaining = 0
    totals_pending_creates = 0
    totals_pending_changes = 0
    for uid in users_map.keys():
        stats = by_user.get(uid) or {"projects": 0, "limit": 0, "used_total": 0, "used_period": 0}
        info = users_map.get(uid)
        if not info:
            continue
        credit = balance_map.get(uid, {}).get("credit", 0)
        debit = balance_map.get(uid, {}).get("debit", 0)
        manual_balance = credit - debit
        tariff_amount: Optional[int] = None
        last_tariff = last_tariff_by_client.get(uid)
        if last_tariff is not None:
            tariff_credit = tariff_ops_map.get(int(last_tariff.id), {}).get("credit", 0)
            tariff_debit = tariff_ops_map.get(int(last_tariff.id), {}).get("debit", 0)
            tariff_amount = int(last_tariff.base_amount or 0) + tariff_credit - tariff_debit
        used_total = int(stats["used_total"])
        used_period = int(stats["used_period"])
        remaining = manual_balance - used_total
        user_row = user_row_map.get(int(uid))
        client_owner_type, client_owner_agent_id = get_client_owner_type_and_id(user_row) if user_row else ("admin", None)
        item = schemas.AdminClientSummaryItem(
            user=info,
            profile=profiles_map.get(uid),
            ownerType=client_owner_type,  # type: ignore[arg-type]
            ownerUser=owner_user_map.get(int(client_owner_agent_id)) if client_owner_agent_id is not None else _get_user_info(db, 1),
            projectCount=int(stats["projects"]),
            totalLimit=int(stats["limit"]),
            usedTotal=used_total,
            usedPeriod=used_period,
            remaining=remaining,
            pendingChanges=pending_map.get(uid, 0),
            pendingCreates=pending_creates_map.get(uid, 0),
            numbersCredited=credit,
            numbersDebited=debit,
            numbersBalance=manual_balance,
            numbersUsed=used_total,
            numbersUsedPeriod=used_period,
            tariffAmount=tariff_amount,
            autoLimitControlEnabled=bool(getattr(info, "autoLimitControlEnabled", False)),
            dataCollectionStatus=collection_map.get(uid, "Нет проектов"),
            financeStatus=resolve_client_finance_status(remaining, last_tariff),
            workStatus=normalize_client_work_status(getattr(profiles_map.get(uid), "workStatus", None)),
        )
        totals_projects += item.projectCount
        totals_limit += item.totalLimit
        totals_used += item.usedTotal
        totals_used_period += item.usedPeriod
        totals_remaining += item.remaining
        totals_pending_creates += item.pendingCreates
        totals_pending_changes += item.pendingChanges
        items.append(item)

    totals = schemas.AdminClientSummaryTotals(
        clients=len(items),
        projects=totals_projects,
        totalLimit=totals_limit,
        usedTotal=totals_used,
        usedPeriod=totals_used_period,
        remaining=totals_remaining,
        pendingCreates=totals_pending_creates,
        pendingChanges=totals_pending_changes,
    )

    return schemas.AdminClientsSummaryOut(items=items, totals=totals)


def client_dashboard(
    db: Session,
    *,
    client_id: int,
    start_local: datetime,
    end_local: datetime,
    today_start: datetime,
    today_end: datetime,
    last7_start: datetime,
    last30_start: datetime,
    chart_start: datetime,
    chart_end: datetime,
    sources: Optional[List[str]] = None,
) -> schemas.ClientDashboardOut:
    source_filter = [str(s).strip().upper() for s in (sources or []) if str(s).strip()]
    source_filter = [s for s in source_filter if s in {"B1", "B2", "B3", "B4"}]
    ts_col = _provider_lead_ts_col()

    def scoped_project_stmt():
        stmt = (
            select(models.Project)
            .where(models.Project.user_id == int(client_id))
            .where(models.Project.deleted_at.is_(None))
            .where(models.Project.status != "Удалён")
        )
        if source_filter:
            stmt = stmt.where(models.Project.data_source_code.in_(source_filter))
        return stmt

    project_rows = db.execute(scoped_project_stmt().order_by(models.Project.id.asc())).scalars().all()
    project_by_id = {int(project.id): project for project in project_rows}
    project_ids = list(project_by_id.keys())

    active_projects = sum(1 for project in project_rows if project.status == "Активен")
    paused_projects = sum(1 for project in project_rows if project.status == "На паузе")
    blocked_projects = sum(1 for project in project_rows if project.status == PROJECT_STATUS_OPERATOR_BLOCK)

    status_order = ["Активен", "На паузе", PROJECT_STATUS_OPERATOR_BLOCK]
    status_counts: Dict[str, int] = {status: 0 for status in status_order}
    for project in project_rows:
        status_counts[str(project.status or "")] = status_counts.get(str(project.status or ""), 0) + 1
    status_breakdown = [
        schemas.AdminDashboardBreakdownItemOut(key=status, label=status, value=int(status_counts.get(status, 0)))
        for status in status_order
    ]

    def leads_count(start: datetime, end: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(models.ProviderLead)
            .join(models.Project, models.Project.id == models.ProviderLead.project_id)
            .where(models.Project.user_id == int(client_id))
            .where(models.ProviderLead.project_id.is_not(None))
            .where(ts_col >= start)
            .where(ts_col < end)
        )
        if source_filter:
            stmt = stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
        return int(db.execute(stmt).scalar_one() or 0)

    leads_period = leads_count(start_local, end_local)
    leads_today = leads_count(today_start, today_end)
    leads_7 = leads_count(last7_start, today_end)
    leads_30 = leads_count(last30_start, today_end)

    balance_summary = get_client_balance_summary(db, client_id=int(client_id), start_local=None, end_local=None)
    remaining = int(balance_summary.remaining)
    if leads_7 > 0:
        average_daily_spend = float(leads_7 / 7)
        spend_basis: Optional[schemas.DashboardSpendBasis] = "7d"
    elif leads_30 > 0:
        average_daily_spend = float(leads_30 / 30)
        spend_basis = "30d"
    else:
        average_daily_spend = 0.0
        spend_basis = None

    if remaining <= 0:
        estimated_days_left: Optional[int] = 0
    elif average_daily_spend > 0:
        estimated_days_left = int(remaining / average_daily_spend)
    else:
        estimated_days_left = None

    daily_stmt = (
        select(func.date(ts_col), func.count())
        .select_from(models.ProviderLead)
        .join(models.Project, models.Project.id == models.ProviderLead.project_id)
        .where(models.Project.user_id == int(client_id))
        .where(models.ProviderLead.project_id.is_not(None))
        .where(ts_col >= chart_start)
        .where(ts_col < chart_end)
        .group_by(func.date(ts_col))
    )
    if source_filter:
        daily_stmt = daily_stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
    daily_rows = db.execute(daily_stmt).all()
    daily_map = {str(day): int(cnt or 0) for day, cnt in daily_rows}
    leads_daily: List[schemas.AdminDashboardSeriesPointOut] = []
    current_day = chart_start.date()
    while current_day < chart_end.date():
        day_key = current_day.isoformat()
        leads_daily.append(schemas.AdminDashboardSeriesPointOut(date=day_key, value=daily_map.get(day_key, 0)))
        current_day = current_day + timedelta(days=1)

    period_by_project: Dict[int, int] = {}
    last7_by_project: Dict[int, int] = {}
    if project_ids:
        period_stmt = (
            select(models.ProviderLead.project_id, func.count())
            .where(models.ProviderLead.project_id.in_(project_ids))
            .where(ts_col >= start_local)
            .where(ts_col < end_local)
            .group_by(models.ProviderLead.project_id)
        )
        if source_filter:
            period_stmt = period_stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
        period_rows = db.execute(period_stmt).all()
        period_by_project = {int(pid): int(cnt or 0) for pid, cnt in period_rows if pid is not None}

        last7_stmt = (
            select(models.ProviderLead.project_id, func.count())
            .where(models.ProviderLead.project_id.in_(project_ids))
            .where(ts_col >= last7_start)
            .where(ts_col < today_end)
            .group_by(models.ProviderLead.project_id)
        )
        if source_filter:
            last7_stmt = last7_stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
        last7_rows = db.execute(last7_stmt).all()
        last7_by_project = {int(pid): int(cnt or 0) for pid, cnt in last7_rows if pid is not None}

    attention_projects: List[schemas.ClientDashboardAttentionProjectOut] = []
    reason_order = {"operator_blocked": 0, "paused": 1, "no_data_7d": 2}
    for project in project_rows:
        reason: Optional[Literal["operator_blocked", "paused", "no_data_7d"]] = None
        reason_label = ""
        if project.status == PROJECT_STATUS_OPERATOR_BLOCK:
            reason = "operator_blocked"
            reason_label = "Блокировка оператора"
        elif project.status == "На паузе":
            reason = "paused"
            reason_label = "На паузе"
        elif project.status == "Активен" and int(last7_by_project.get(int(project.id), 0)) == 0:
            reason = "no_data_7d"
            reason_label = "Нет данных за 7 дней"
        if reason is None:
            continue
        attention_projects.append(
            schemas.ClientDashboardAttentionProjectOut(
                projectId=int(project.id),
                projectName=str(project.name or ""),
                status=project.status,
                source=str(project.data_source_code or ""),
                reason=reason,
                reasonLabel=reason_label,
            )
        )
    attention_projects.sort(key=lambda item: (reason_order.get(item.reason, 99), item.projectName))

    top_projects = [
        schemas.ClientDashboardProjectRankingItemOut(
            projectId=pid,
            projectName=str(getattr(project_by_id[pid], "name", "") or ""),
            status=project_by_id[pid].status,
            source=str(getattr(project_by_id[pid], "data_source_code", "") or ""),
            value=int(value),
        )
        for pid, value in sorted(period_by_project.items(), key=lambda pair: pair[1], reverse=True)[:5]
        if pid in project_by_id and int(value) > 0
    ]

    recent_events = list_client_activity_events(
        db,
        client_id=int(client_id),
        offset=0,
        limit=5,
        start_local=None,
        end_local=None,
    ).items

    return schemas.ClientDashboardOut(
        summary=schemas.ClientDashboardSummaryOut(
            leadsPeriod=leads_period,
            leadsToday=leads_today,
            leads7Days=leads_7,
            leads30Days=leads_30,
            remaining=remaining,
            activeProjects=active_projects,
            pausedProjects=paused_projects,
            operatorBlockedProjects=blocked_projects,
        ),
        balance=schemas.ClientDashboardBalanceOut(
            remaining=remaining,
            averageDailySpend=round(average_daily_spend, 2),
            averageDailySpendBasis=spend_basis,
            estimatedDaysLeft=estimated_days_left,
        ),
        charts=schemas.ClientDashboardChartsOut(
            leadsDaily=leads_daily,
            projectStatuses=status_breakdown,
        ),
        attention=schemas.ClientDashboardAttentionOut(projects=attention_projects[:10]),
        rankings=schemas.ClientDashboardRankingsOut(topProjectsByLeads=top_projects),
        recentEvents=recent_events,
    )


def admin_dashboard(
    db: Session,
    *,
    start_local: datetime,
    end_local: datetime,
    today_start: datetime,
    today_end: datetime,
    yesterday_start: datetime,
    yesterday_end: datetime,
    last7_start: datetime,
    last30_start: datetime,
    chart_start: datetime,
    chart_end: datetime,
    client_id: Optional[int] = None,
    sources: Optional[List[str]] = None,
    include_agent_clients: bool = True,
) -> schemas.AdminDashboardOut:
    source_filter = [str(s).strip().upper() for s in (sources or []) if str(s).strip()]
    source_filter = [s for s in source_filter if s in {"B1", "B2", "B3", "B4"}]

    users_stmt = (
        select(models.User)
        .where(models.User.role == ROLE_CLIENT)
        .where(models.User.id != 1)
    )
    if client_id is not None:
        users_stmt = users_stmt.where(models.User.id == int(client_id))
    elif not include_agent_clients:
        users_stmt = users_stmt.where(models.User.owner_agent_id.is_(None))
    users = db.execute(users_stmt.order_by(models.User.id.asc())).scalars().all()
    client_ids = [int(user.id) for user in users]

    if not client_ids:
        empty_unlinked = schemas.AdminDashboardUnlinkedLeadsOut(total=0, ambiguous=0, notFound=0, unknown=0)
        return schemas.AdminDashboardOut(
            summary=schemas.AdminDashboardSummaryOut(
                clients=0,
                projects=0,
                activeProjects=0,
                pausedProjects=0,
                operatorBlockedProjects=0,
                totalRemaining=0,
                leadsPeriod=0,
                leadsToday=0,
                leadsYesterday=0,
                leads7Days=0,
                leads30Days=0,
                unlinkedLeads=0,
                operationErrors=0,
            ),
            attention=schemas.AdminDashboardAttentionOut(
                criticalClients=[],
                riskClients=[],
                warningClients=[],
                operatorBlockedProjects=[],
                unlinkedLeads=empty_unlinked,
                operationErrors=schemas.AdminDashboardOperationErrorsOut(total=0, items=[]),
            ),
            charts=schemas.AdminDashboardChartsOut(leadsDaily=[], sourceBreakdown=[], projectStatuses=[]),
            rankings=schemas.AdminDashboardRankingsOut(topClientsByLeads=[], topClientsByActiveProjects=[]),
        )

    profiles = db.execute(
        select(models.ClientProfile).where(models.ClientProfile.user_id.in_(client_ids))
    ).scalars().all()
    profile_by_client = {int(profile.user_id): profile for profile in profiles}
    user_by_id = {int(user.id): user for user in users}

    owner_agent_ids = sorted({
        int(user.owner_agent_id)
        for user in users
        if getattr(user, "owner_agent_id", None) is not None
    })
    owner_name_by_id: Dict[int, str] = {}
    if owner_agent_ids:
        owner_users = db.execute(select(models.User).where(models.User.id.in_(owner_agent_ids))).scalars().all()
        for owner_user in owner_users:
            owner_name_by_id[int(owner_user.id)] = (
                str(getattr(owner_user, "display_name", "") or "").strip()
                or str(getattr(owner_user, "login", "") or "").strip()
            )

    def client_name(uid: int) -> str:
        profile = profile_by_client.get(int(uid))
        user = user_by_id.get(int(uid))
        return (
            str(getattr(profile, "name", "") or "").strip()
            or str(getattr(user, "display_name", "") or "").strip()
            or str(getattr(user, "login", "") or "").strip()
            or f"Клиент {int(uid)}"
        )

    def owner_name(uid: int) -> Optional[str]:
        user = user_by_id.get(int(uid))
        owner_id = getattr(user, "owner_agent_id", None) if user is not None else None
        if owner_id is None:
            return None
        return owner_name_by_id.get(int(owner_id))

    ts_col = _provider_lead_ts_col()

    def scoped_project_stmt(include_deleted: bool = False):
        stmt = select(models.Project).where(models.Project.user_id.in_(client_ids))
        if not include_deleted:
            stmt = stmt.where(models.Project.deleted_at.is_(None)).where(models.Project.status != "Удалён")
        if source_filter:
            stmt = stmt.where(models.Project.data_source_code.in_(source_filter))
        return stmt

    project_rows = db.execute(scoped_project_stmt(include_deleted=False)).scalars().all()
    projects_count = len(project_rows)
    active_projects = sum(1 for p in project_rows if p.status == "Активен")
    paused_projects = sum(1 for p in project_rows if p.status == "На паузе")
    blocked_projects = sum(1 for p in project_rows if p.status == PROJECT_STATUS_OPERATOR_BLOCK)

    status_order = ["Активен", "На паузе", PROJECT_STATUS_OPERATOR_BLOCK, "Удалён"]
    status_counts: Dict[str, int] = {status: 0 for status in status_order}
    status_stmt = (
        select(models.Project.status, func.count())
        .where(models.Project.user_id.in_(client_ids))
        .where(models.Project.deleted_at.is_(None))
        .where(models.Project.status != "Удалён")
        .group_by(models.Project.status)
    )
    if source_filter:
        status_stmt = status_stmt.where(models.Project.data_source_code.in_(source_filter))
    all_project_status_rows = db.execute(status_stmt).all()
    for status, count_value in all_project_status_rows:
        status_counts[str(status or "")] = int(count_value or 0)
    status_breakdown = [
        schemas.AdminDashboardBreakdownItemOut(key=key, label=key, value=int(status_counts.get(key, 0)))
        for key in status_order
        if key != "Удалён" or int(status_counts.get(key, 0)) > 0
    ]

    operator_projects: List[schemas.AdminDashboardAttentionProjectOut] = []
    for project in project_rows:
        if project.status != PROJECT_STATUS_OPERATOR_BLOCK:
            continue
        uid = int(project.user_id) if project.user_id is not None else None
        operator_projects.append(
            schemas.AdminDashboardAttentionProjectOut(
                projectId=int(project.id),
                projectName=str(project.name or ""),
                clientId=uid,
                clientName=(client_name(uid) if uid is not None else None),
                source=str(project.data_source_code or ""),
            )
        )
    operator_projects = operator_projects[:10]

    def leads_count(start: datetime, end: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(models.ProviderLead)
            .join(models.Project, models.Project.id == models.ProviderLead.project_id)
            .where(models.Project.user_id.in_(client_ids))
            .where(models.ProviderLead.project_id.is_not(None))
            .where(ts_col >= start)
            .where(ts_col < end)
        )
        if source_filter:
            stmt = stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
        return int(db.execute(stmt).scalar_one() or 0)

    leads_period = leads_count(start_local, end_local)
    leads_today = leads_count(today_start, today_end)
    leads_yesterday = leads_count(yesterday_start, yesterday_end)
    leads_7 = leads_count(last7_start, today_end)
    leads_30 = leads_count(last30_start, today_end)

    period_lead_stmt = (
        select(models.Project.user_id, func.count())
        .select_from(models.ProviderLead)
        .join(models.Project, models.Project.id == models.ProviderLead.project_id)
        .where(models.Project.user_id.in_(client_ids))
        .where(models.ProviderLead.project_id.is_not(None))
        .where(ts_col >= start_local)
        .where(ts_col < end_local)
        .group_by(models.Project.user_id)
    )
    if source_filter:
        period_lead_stmt = period_lead_stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
    period_lead_rows = db.execute(period_lead_stmt).all()
    period_leads_by_client = {int(uid): int(cnt or 0) for uid, cnt in period_lead_rows if uid is not None}

    last7_lead_rows = db.execute(
        (
            select(models.Project.user_id, func.count())
            .select_from(models.ProviderLead)
            .join(models.Project, models.Project.id == models.ProviderLead.project_id)
            .where(models.Project.user_id.in_(client_ids))
            .where(models.ProviderLead.project_id.is_not(None))
            .where(ts_col >= last7_start)
            .where(ts_col < today_end)
            .group_by(models.Project.user_id)
        )
    ).all()
    last7_leads_by_client = {int(uid): int(cnt or 0) for uid, cnt in last7_lead_rows if uid is not None}

    active_stmt = (
        select(models.Project.user_id, func.count())
        .where(models.Project.user_id.in_(client_ids))
        .where(models.Project.deleted_at.is_(None))
        .where(models.Project.status == "Активен")
        .group_by(models.Project.user_id)
    )
    if source_filter:
        active_stmt = active_stmt.where(models.Project.data_source_code.in_(source_filter))
    active_rows = db.execute(active_stmt).all()
    active_by_client = {int(uid): int(cnt or 0) for uid, cnt in active_rows if uid is not None}

    used_total_rows = db.execute(
        select(models.Project.user_id, func.count())
        .select_from(models.ProviderLead)
        .join(models.Project, models.Project.id == models.ProviderLead.project_id)
        .where(models.Project.user_id.in_(client_ids))
        .where(models.ProviderLead.project_id.is_not(None))
        .group_by(models.Project.user_id)
    ).all()
    used_total_by_client = {int(uid): int(cnt or 0) for uid, cnt in used_total_rows if uid is not None}

    balance_rows = db.execute(
        select(
            models.ClientBalanceOperation.client_id,
            models.ClientBalanceOperation.op_type,
            func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0),
        )
        .where(models.ClientBalanceOperation.client_id.in_(client_ids))
        .group_by(models.ClientBalanceOperation.client_id, models.ClientBalanceOperation.op_type)
    ).all()
    balance_map: Dict[int, Dict[str, int]] = {}
    for cid, op_type, amount in balance_rows:
        balance_map.setdefault(int(cid), {"credit": 0, "debit": 0})
        balance_map[int(cid)][str(op_type)] = int(amount or 0)

    last_tariffs = db.execute(
        select(models.ClientTariff)
        .where(models.ClientTariff.client_id.in_(client_ids))
        .order_by(models.ClientTariff.client_id.asc(), models.ClientTariff.created_at.desc(), models.ClientTariff.id.desc())
    ).scalars().all()
    last_tariff_by_client: Dict[int, models.ClientTariff] = {}
    for tariff in last_tariffs:
        cid = int(tariff.client_id)
        if cid not in last_tariff_by_client:
            last_tariff_by_client[cid] = tariff

    total_remaining = 0
    critical_clients: List[schemas.AdminDashboardAttentionClientOut] = []
    risk_clients: List[schemas.AdminDashboardAttentionClientOut] = []
    warning_clients: List[schemas.AdminDashboardAttentionClientOut] = []
    for uid in client_ids:
        manual_balance = balance_map.get(uid, {}).get("credit", 0) - balance_map.get(uid, {}).get("debit", 0)
        remaining = int(manual_balance) - int(used_total_by_client.get(uid, 0))
        total_remaining += remaining
        profile = profile_by_client.get(uid)
        if normalize_client_work_status(getattr(profile, "work_status", None)) == "Неактивен":
            continue
        tariff = last_tariff_by_client.get(uid)
        if tariff is None or tariff.signal1 is None or tariff.signal2 is None or tariff.signal3 is None:
            continue
        signal1 = int(tariff.signal1)
        signal2 = int(tariff.signal2)
        signal3 = int(tariff.signal3)
        level: Optional[schemas.DashboardRiskLevel] = None
        if remaining <= 0:
            level = "debt"
        elif remaining <= signal3:
            level = "critical"
        elif remaining <= signal2:
            level = "risk"
        elif remaining <= signal1:
            level = "warning"
        if level is None:
            continue
        item = schemas.AdminDashboardAttentionClientOut(
            clientId=uid,
            clientName=client_name(uid),
            clientLogin=str(getattr(user_by_id.get(uid), "login", "") or ""),
            ownerType=("agent" if getattr(user_by_id.get(uid), "owner_agent_id", None) is not None else "admin"),  # type: ignore[arg-type]
            ownerName=owner_name(uid),
            remaining=remaining,
            tariffAmount=int(tariff.base_amount or 0),
            signal1=signal1,
            signal2=signal2,
            signal3=signal3,
            level=level,
            activeProjects=int(active_by_client.get(uid, 0)),
            dailySpend=max(0, int(round(int(last7_leads_by_client.get(uid, 0)) / 7))),
            lastTariffAt=tariff.created_at.strftime("%Y-%m-%d %H:%M:%S") if tariff.created_at else None,
        )
        if level in ("critical", "debt"):
            critical_clients.append(item)
        elif level == "risk":
            risk_clients.append(item)
        else:
            warning_clients.append(item)

    critical_clients.sort(key=lambda item: (item.remaining, item.clientName))
    risk_clients.sort(key=lambda item: (item.remaining, item.clientName))
    warning_clients.sort(key=lambda item: (item.remaining, item.clientName))

    unlinked_stmt = (
        select(models.ProviderLead.project_name, func.count())
        .where(models.ProviderLead.project_id.is_(None))
        .where(ts_col >= start_local)
        .where(ts_col < end_local)
        .group_by(models.ProviderLead.project_name)
    )
    if source_filter:
        unlinked_stmt = unlinked_stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
    unlinked_rows = db.execute(unlinked_stmt).all()
    unlinked_total = 0
    unlinked_ambiguous = 0
    unlinked_not_found = 0
    unlinked_unknown = 0
    for project_name, count_value in unlinked_rows:
        count_int = int(count_value or 0)
        unlinked_total += count_int
        if not str(project_name or "").strip():
            unlinked_unknown += count_int
            continue
        _project_id, status, _matches = resolve_project_by_name_for_provider_lead(db, str(project_name or ""))
        if status == "ambiguous":
            unlinked_ambiguous += count_int
        elif status == "not_found":
            unlinked_not_found += count_int
        else:
            unlinked_unknown += count_int

    failed_base = (
        select(models.ProjectOperationEvent)
        .where(models.ProjectOperationEvent.user_id.in_(client_ids))
        .where(models.ProjectOperationEvent.status == "failed")
        .where(models.ProjectOperationEvent.created_at >= start_local)
        .where(models.ProjectOperationEvent.created_at < end_local)
    )
    failed_total = int(db.execute(select(func.count()).select_from(failed_base.subquery())).scalar_one() or 0)
    failed_rows = db.execute(
        failed_base.order_by(models.ProjectOperationEvent.created_at.desc()).limit(5)
    ).scalars().all()
    failed_items = [
        {
            "id": int(row.id),
            "clientId": int(row.user_id),
            "clientName": client_name(int(row.user_id)),
            "projectId": int(row.project_id) if row.project_id is not None else None,
            "projectName": row.project_name,
            "operation": row.operation,
            "errorMessage": row.error_message,
            "createdAt": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else None,
        }
        for row in failed_rows
    ]

    daily_stmt = (
        select(func.date(ts_col), func.count())
        .select_from(models.ProviderLead)
        .join(models.Project, models.Project.id == models.ProviderLead.project_id)
        .where(models.Project.user_id.in_(client_ids))
        .where(models.ProviderLead.project_id.is_not(None))
        .where(ts_col >= chart_start)
        .where(ts_col < chart_end)
        .group_by(func.date(ts_col))
    )
    if source_filter:
        daily_stmt = daily_stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
    daily_rows = db.execute(daily_stmt).all()
    daily_map = {str(day): int(cnt or 0) for day, cnt in daily_rows}
    leads_daily: List[schemas.AdminDashboardSeriesPointOut] = []
    current_day = chart_start.date()
    while current_day < chart_end.date():
        day_key = current_day.isoformat()
        leads_daily.append(schemas.AdminDashboardSeriesPointOut(date=day_key, value=daily_map.get(day_key, 0)))
        current_day = current_day + timedelta(days=1)

    source_stmt = (
        select(models.ProviderLead.prov_chanel, func.count())
        .select_from(models.ProviderLead)
        .join(models.Project, models.Project.id == models.ProviderLead.project_id)
        .where(models.Project.user_id.in_(client_ids))
        .where(models.ProviderLead.project_id.is_not(None))
        .where(ts_col >= start_local)
        .where(ts_col < end_local)
        .group_by(models.ProviderLead.prov_chanel)
    )
    if source_filter:
        source_stmt = source_stmt.where(models.ProviderLead.prov_chanel.in_(source_filter))
    source_rows = db.execute(source_stmt).all()
    source_counts = {str(source or "UNMAPPED"): int(cnt or 0) for source, cnt in source_rows}
    source_breakdown = [
        schemas.AdminDashboardBreakdownItemOut(key=code, label=code, value=int(source_counts.get(code, 0)))
        for code in ["B1", "B2", "B3", "B4"]
        if not source_filter or code in source_filter
    ]

    top_by_leads = [
        schemas.AdminDashboardClientRankingItemOut(
            clientId=uid,
            clientName=client_name(uid),
            ownerName=owner_name(uid),
            value=int(value),
            activeProjects=int(active_by_client.get(uid, 0)),
        )
        for uid, value in sorted(period_leads_by_client.items(), key=lambda pair: pair[1], reverse=True)[:5]
    ]
    top_by_active = [
        schemas.AdminDashboardClientRankingItemOut(
            clientId=uid,
            clientName=client_name(uid),
            ownerName=owner_name(uid),
            value=int(value),
            activeProjects=int(value),
        )
        for uid, value in sorted(active_by_client.items(), key=lambda pair: pair[1], reverse=True)[:5]
        if int(value) > 0
    ]

    return schemas.AdminDashboardOut(
        summary=schemas.AdminDashboardSummaryOut(
            clients=len(client_ids),
            projects=projects_count,
            activeProjects=active_projects,
            pausedProjects=paused_projects,
            operatorBlockedProjects=blocked_projects,
            totalRemaining=total_remaining,
            leadsPeriod=leads_period,
            leadsToday=leads_today,
            leadsYesterday=leads_yesterday,
            leads7Days=leads_7,
            leads30Days=leads_30,
            unlinkedLeads=unlinked_total,
            operationErrors=failed_total,
        ),
        attention=schemas.AdminDashboardAttentionOut(
            criticalClients=critical_clients[:10],
            riskClients=risk_clients[:10],
            warningClients=warning_clients[:10],
            operatorBlockedProjects=operator_projects,
            unlinkedLeads=schemas.AdminDashboardUnlinkedLeadsOut(
                total=unlinked_total,
                ambiguous=unlinked_ambiguous,
                notFound=unlinked_not_found,
                unknown=unlinked_unknown,
            ),
            operationErrors=schemas.AdminDashboardOperationErrorsOut(total=failed_total, items=failed_items),
        ),
        charts=schemas.AdminDashboardChartsOut(
            leadsDaily=leads_daily,
            sourceBreakdown=source_breakdown,
            projectStatuses=status_breakdown,
        ),
        rankings=schemas.AdminDashboardRankingsOut(
            topClientsByLeads=top_by_leads,
            topClientsByActiveProjects=top_by_active,
        ),
    )


def admin_list_client_changes(db: Session, client_id: int, actions: Optional[List[str]] = None) -> schemas.AdminClientChangesOut:
    """
    Подробный список необработанных изменений конкретного клиента (по всем его проектам).
    """
    user = db.get(models.User, client_id)
    if not user:
        # Возвращаем пустой список, чтобы фронт мог просто показать "нет изменений"
        return schemas.AdminClientChangesOut(
            user=schemas.UserInfo(id=client_id, login="(unknown)"),
            items=[],
        )

    allowed_actions = {"create", "update", "delete", "blacklist_add", "blacklist_delete"}
    default_actions = ["create", "update", "delete", "blacklist_add", "blacklist_delete"]
    action_filter = [a for a in (actions or default_actions) if a in allowed_actions]
    if not action_filter:
        action_filter = default_actions

    # Берём только "проектные" события клиента, которые ещё не обработаны админом
    rows = db.execute(
        select(models.AuditEvent, models.Project.name)
        .outerjoin(models.Project, models.Project.id == models.AuditEvent.project_id)
        .where(models.AuditEvent.user_id == client_id)
        .where(models.AuditEvent.action.in_(action_filter))
        .where(models.AuditEvent.admin_processed_at.is_(None))
        .order_by(models.AuditEvent.created_at.desc())
    ).all()

    actor_ids = {aid for aid in (_audit_event_actor_user_id(ev) for ev, _ in rows) if aid}
    actors_map: Dict[int, schemas.UserInfo] = {}
    if actor_ids:
        actor_rows = db.execute(select(models.User).where(models.User.id.in_(actor_ids))).scalars().all()
        for actor_row in actor_rows:
            actors_map[actor_row.id] = schemas.UserInfo(id=actor_row.id, login=actor_row.login)

    items: List[schemas.AdminChangeOut] = []
    for ev, proj_name in rows:
        created_at_str = ev.created_at.strftime("%Y-%m-%d %H:%M:%S")
        description = _audit_event_compact_description(ev)
        snapshot = None
        try:
            snapshot = ev.after or ev.before
        except Exception:
            snapshot = None
        actor_id = _audit_event_actor_user_id(ev)
        actor = actors_map.get(actor_id) if actor_id else None
        actor_mode = _audit_event_actor_mode(ev, actor_id)
        status = "done" if ev.admin_processed_at is not None else "pending"
        items.append(
            schemas.AdminChangeOut(
                id=ev.id,
                projectId=ev.project_id,
                projectName=proj_name,
                batchId=getattr(ev, "batch_id", None),
                createdAt=created_at_str,
                action=ev.action or "update",  # type: ignore[arg-type]
                description=description,
                status=status,  # type: ignore[arg-type]
                projectSnapshot=snapshot,
                beforeSnapshot=ev.before,
                changedFields=ev.changed_fields if isinstance(ev.changed_fields, list) else None,
                actor=actor,
                actorMode=actor_mode,  # type: ignore[arg-type]
            )
        )

    return schemas.AdminClientChangesOut(
        user=schemas.UserInfo(id=user.id, login=user.login),
        items=items,
    )


def _normalize_pause_snapshot_ids(raw_ids: Any) -> List[int]:
    if not isinstance(raw_ids, list):
        return []
    out: List[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            pid = int(raw)
        except Exception:
            continue
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def _get_pause_snapshot_row(db: Session, client_id: int) -> Optional[models.ClientProjectPauseSnapshot]:
    return db.execute(
        select(models.ClientProjectPauseSnapshot).where(models.ClientProjectPauseSnapshot.client_id == client_id)
    ).scalar_one_or_none()


def admin_replace_pause_snapshot(
    db: Session,
    client_id: int,
    project_ids: List[int],
    admin_user_id: int,
) -> Optional[models.ClientProjectPauseSnapshot]:
    normalized_ids = _normalize_pause_snapshot_ids(project_ids)
    row = _get_pause_snapshot_row(db, client_id)

    if not normalized_ids:
        if row:
            db.delete(row)
            db.commit()
        return None

    now = now_msk()
    if not row:
        row = models.ClientProjectPauseSnapshot(
            client_id=client_id,
            project_ids=normalized_ids,
            paused_by=admin_user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.project_ids = normalized_ids
        row.paused_by = admin_user_id
        row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


def admin_set_client_projects_mutation_lock(
    db: Session,
    client_id: int,
    locked: bool,
    admin_user_id: int,
    reason: Optional[str] = None,
) -> Optional[models.User]:
    user = db.get(models.User, client_id)
    if not user:
        return None

    lock_enabled = bool(locked)
    user.projects_mutation_locked = lock_enabled
    if lock_enabled:
        user.projects_mutation_locked_at = now_msk()
        user.projects_mutation_locked_by = int(admin_user_id)
        user.projects_mutation_lock_reason = (reason or None)
    else:
        user.projects_mutation_locked_at = None
        user.projects_mutation_locked_by = None
        user.projects_mutation_lock_reason = None
    db.commit()
    db.refresh(user)
    return user


def admin_get_collection_state(db: Session, client_id: int) -> schemas.AdminClientCollectionStateOut:
    row = _get_pause_snapshot_row(db, client_id)
    snapshot_ids = _normalize_pause_snapshot_ids(row.project_ids if row else [])
    pause_candidates = int(
        db.execute(
            select(func.count()).where(
                models.Project.user_id == client_id,
                models.Project.status == "Активен",
                models.Project.provider_project_id.is_not(None),
            )
        ).scalar_one()
        or 0
    )

    snapshot_projects: List[schemas.AdminCollectionProjectItem] = []
    resume_candidates = 0
    has_active_in_snapshot = False
    if snapshot_ids:
        proj_rows = db.execute(
            select(models.Project).where(
                models.Project.user_id == client_id,
                models.Project.id.in_(snapshot_ids),
            )
        ).scalars().all()
        by_id = {int(p.id): p for p in proj_rows}
        for pid in snapshot_ids:
            p = by_id.get(pid)
            if p:
                status = _normalize_project_status(p.status)
                if status == "Активен":
                    has_active_in_snapshot = True
                if status == "На паузе" and p.provider_project_id:
                    resume_candidates += 1
                snapshot_projects.append(
                    schemas.AdminCollectionProjectItem(
                        id=int(p.id),
                        name=p.name,
                        status=status,
                    )
                )
            else:
                snapshot_projects.append(
                    schemas.AdminCollectionProjectItem(
                        id=pid,
                        name=f"Проект {pid} (не найден)",
                        status="Удалён",
                    )
                )

    has_snapshot = len(snapshot_ids) > 0
    all_snapshot_paused = has_snapshot and not has_active_in_snapshot
    client_user = db.get(models.User, client_id)
    client_locked = bool(getattr(client_user, "projects_mutation_locked", False)) if client_user else False
    # UI-статус должен отражать фактическое состояние проектов клиента:
    # если есть хотя бы 1 активный синхронизированный проект -> "Активен",
    # иначе -> "На паузе".
    collection_status: Literal["Активен", "На паузе"] = "Активен" if pause_candidates > 0 else "На паузе"
    # Если клиентский раздел уже заблокирован, приоритетно предлагаем "resume":
    # это снимает блокировку и (если есть snapshot) восстанавливает проекты.
    action: Literal["pause", "resume"] = "resume" if (all_snapshot_paused or client_locked) else "pause"
    if action == "resume":
        action_label = "Возобновить сбор и разблокировать проекты"
    else:
        action_label = "Поставить проекты на паузу снова" if has_snapshot else "Пауза + блокировка раздела проекты"

    return schemas.AdminClientCollectionStateOut(
        clientId=client_id,
        dataCollectionStatus=collection_status,
        action=action,
        actionLabel=action_label,
        pauseCandidates=pause_candidates,
        resumeCandidates=resume_candidates,
        actionEnabled=True,
        actionDisabledReason=None,
        projectsMutationLocked=bool(getattr(client_user, "projects_mutation_locked", False)) if client_user else False,
        projectsMutationLockedAt=client_user.projects_mutation_locked_at.isoformat() if client_user and client_user.projects_mutation_locked_at else None,
        projectsMutationLockedBy=int(client_user.projects_mutation_locked_by) if client_user and client_user.projects_mutation_locked_by is not None else None,
        projectsMutationLockReason=(client_user.projects_mutation_lock_reason or None) if client_user else None,
        snapshotProjects=snapshot_projects,
    )


# -------- Баланс номеров по клиенту --------
def _client_leads_usage(
    db: Session,
    client_id: int,
    start_local: Optional[datetime] = None,
    end_local: Optional[datetime] = None,
) -> int:
    """
    Количество выданных номеров (лидов) по всем проектам клиента.
    """
    ts_col = _provider_lead_ts_col()
    stmt = (
        select(func.count())
        .select_from(models.ProviderLead)
        .join(models.Project, models.Project.id == models.ProviderLead.project_id)
        .where(models.Project.user_id == client_id)
    )
    if start_local:
        stmt = stmt.where(ts_col >= start_local)
    if end_local:
        stmt = stmt.where(ts_col <= end_local)
    return int(db.execute(stmt).scalar_one() or 0)


def get_client_remaining_numbers(db: Session, client_id: int) -> int:
    credits = db.execute(
        select(func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0)).where(
            models.ClientBalanceOperation.client_id == client_id,
            models.ClientBalanceOperation.op_type == "credit",
        )
    ).scalar_one()
    debits = db.execute(
        select(func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0)).where(
            models.ClientBalanceOperation.client_id == client_id,
            models.ClientBalanceOperation.op_type == "debit",
        )
    ).scalar_one()
    manual_balance = int(credits or 0) - int(debits or 0)
    used_total = _client_leads_usage(db, client_id)
    return manual_balance - used_total


def _get_client_remaining_map(db: Session, client_ids: List[int]) -> Dict[int, int]:
    normalized_ids = [int(client_id) for client_id in client_ids if client_id is not None]
    if not normalized_ids:
        return {}

    balance_rows = db.execute(
        select(
            models.ClientBalanceOperation.client_id,
            models.ClientBalanceOperation.op_type,
            func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0),
        )
        .where(models.ClientBalanceOperation.client_id.in_(normalized_ids))
        .group_by(models.ClientBalanceOperation.client_id, models.ClientBalanceOperation.op_type)
    ).all()
    balance_map: Dict[int, Dict[str, int]] = {}
    for client_id, op_type, total_amt in balance_rows:
        cid = int(client_id)
        balance_map.setdefault(cid, {"credit": 0, "debit": 0})
        balance_map[cid][str(op_type)] = int(total_amt or 0)

    used_total_rows = db.execute(
        select(models.Project.user_id, func.count())
        .join(models.ProviderLead, models.ProviderLead.project_id == models.Project.id)
        .where(models.Project.user_id.in_(normalized_ids))
        .group_by(models.Project.user_id)
    ).all()
    used_total_map = {int(user_id): int(total or 0) for user_id, total in used_total_rows if user_id is not None}

    remaining_map: Dict[int, int] = {}
    for client_id in normalized_ids:
        credited = balance_map.get(client_id, {}).get("credit", 0)
        debited = balance_map.get(client_id, {}).get("debit", 0)
        used_total = used_total_map.get(client_id, 0)
        remaining_map[client_id] = credited - debited - used_total
    return remaining_map


def get_client_active_projects_limit_sum(
    db: Session,
    client_id: int,
    exclude_project_id: Optional[int] = None,
) -> int:
    stmt = select(func.coalesce(func.sum(models.Project.data_limit), 0)).where(
        models.Project.user_id == client_id,
        models.Project.status == "Активен",
        models.Project.provider_project_id.is_not(None),
    )
    if exclude_project_id is not None:
        stmt = stmt.where(models.Project.id != int(exclude_project_id))
    return int(db.execute(stmt).scalar_one() or 0)


def can_activate_project_under_limit_control(
    db: Session,
    project_id: int,
    projected_data_limit: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    p = db.get(models.Project, project_id)
    if not p:
        return False, "Проект не найден."
    if p.status == "Удалён":
        return False, "Проект удалён. Включение запрещено."

    owner = db.get(models.User, int(p.user_id)) if p.user_id else None
    if not owner:
        return False, "Клиент проекта не найден."
    if not bool(getattr(owner, "auto_limit_control_enabled", False)):
        return True, None

    if p.status == "Активен":
        return True, None

    remaining = get_client_remaining_numbers(db, int(owner.id))
    current_active_sum = get_client_active_projects_limit_sum(db, int(owner.id), exclude_project_id=int(p.id))
    next_limit = int(projected_data_limit if projected_data_limit is not None else (p.data_limit or 0))
    next_sum = current_active_sum + next_limit
    if next_sum <= remaining:
        return True, None

    reason = (
        "Нельзя включить проект: сумма лимитов активных проектов станет "
        f"{next_sum}, а остаток клиента {remaining}. Уменьшите лимиты или пополните баланс."
    )
    return False, reason


def list_clients_with_auto_limit_control(db: Session) -> List[int]:
    rows = db.execute(
        select(models.User.id).where(
            models.User.role == ROLE_CLIENT,
            models.User.auto_limit_control_enabled == True,  # noqa: E712
        )
    ).all()
    return [int(uid) for (uid,) in rows if uid is not None]


def list_client_ids_for_tariff_signal_checks(db: Session) -> List[int]:
    rows = db.execute(
        select(models.User.id).where(
            models.User.role == ROLE_CLIENT,
        )
    ).all()
    return [int(uid) for (uid,) in rows if uid is not None]


def get_client_balance_summary(
    db: Session,
    client_id: int,
    start_local: Optional[datetime],
    end_local: Optional[datetime],
) -> schemas.ClientBalanceSummaryOut:
    credits = db.execute(
        select(func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0)).where(
            models.ClientBalanceOperation.client_id == client_id,
            models.ClientBalanceOperation.op_type == "credit",
        )
    ).scalar_one()
    debits = db.execute(
        select(func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0)).where(
            models.ClientBalanceOperation.client_id == client_id,
            models.ClientBalanceOperation.op_type == "debit",
        )
    ).scalar_one()
    manual_balance = int(credits or 0) - int(debits or 0)
    used_total = _client_leads_usage(db, client_id)
    used_period = _client_leads_usage(db, client_id, start_local=start_local, end_local=end_local)
    remaining = manual_balance - used_total
    return schemas.ClientBalanceSummaryOut(
        clientId=client_id,
        credited=int(credits or 0),
        debited=int(debits or 0),
        manualBalance=manual_balance,
        usedTotal=used_total,
        usedPeriod=used_period,
        remaining=remaining,
        debt=remaining < 0,
        periodFrom=start_local.strftime("%Y-%m-%d") if start_local else None,
        periodTo=end_local.strftime("%Y-%m-%d") if end_local else None,
    )


def get_agent_balance_summary(
    db: Session,
    agent_id: int,
    start_local: Optional[datetime],
    end_local: Optional[datetime],
) -> schemas.ClientBalanceSummaryOut:
    client_ids = [
        int(client_id)
        for client_id, in db.execute(
            select(models.User.id).where(
                models.User.role == ROLE_CLIENT,
                models.User.owner_agent_id == int(agent_id),
            )
        ).all()
        if client_id is not None
    ]
    if not client_ids:
        return schemas.ClientBalanceSummaryOut(
            clientId=int(agent_id),
            credited=0,
            debited=0,
            manualBalance=0,
            usedTotal=0,
            usedPeriod=0,
            remaining=0,
            debt=False,
            periodFrom=start_local.strftime("%Y-%m-%d") if start_local else None,
            periodTo=end_local.strftime("%Y-%m-%d") if end_local else None,
        )

    credits = db.execute(
        select(func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0)).where(
            models.ClientBalanceOperation.client_id.in_(client_ids),
            models.ClientBalanceOperation.op_type == "credit",
        )
    ).scalar_one()
    debits = db.execute(
        select(func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0)).where(
            models.ClientBalanceOperation.client_id.in_(client_ids),
            models.ClientBalanceOperation.op_type == "debit",
        )
    ).scalar_one()
    manual_balance = int(credits or 0) - int(debits or 0)

    total_stmt = (
        select(func.count())
        .select_from(models.ProviderLead)
        .join(models.Project, models.Project.id == models.ProviderLead.project_id)
        .where(models.Project.user_id.in_(client_ids))
    )
    used_total = int(db.execute(total_stmt).scalar_one() or 0)

    ts_col = _provider_lead_ts_col()
    period_stmt = (
        select(func.count())
        .select_from(models.ProviderLead)
        .join(models.Project, models.Project.id == models.ProviderLead.project_id)
        .where(models.Project.user_id.in_(client_ids))
    )
    if start_local:
        period_stmt = period_stmt.where(ts_col >= start_local)
    if end_local:
        period_stmt = period_stmt.where(ts_col <= end_local)
    used_period = int(db.execute(period_stmt).scalar_one() or 0)

    remaining = manual_balance - used_total
    return schemas.ClientBalanceSummaryOut(
        clientId=int(agent_id),
        credited=int(credits or 0),
        debited=int(debits or 0),
        manualBalance=manual_balance,
        usedTotal=used_total,
        usedPeriod=used_period,
        remaining=remaining,
        debt=remaining < 0,
        periodFrom=start_local.strftime("%Y-%m-%d") if start_local else None,
        periodTo=end_local.strftime("%Y-%m-%d") if end_local else None,
    )


def _add_balance_operation_row(
    db: Session,
    *,
    user_id: int,
    actor_user_id: int,
    amount: int,
    op_type: str,
    comment: Optional[str],
) -> models.ClientBalanceOperation:
    row = models.ClientBalanceOperation(
        client_id=int(user_id),
        amount=int(amount),
        op_type=str(op_type),
        comment=comment,
        created_by=int(actor_user_id),
        created_at=now_msk(),
    )
    db.add(row)
    db.flush()
    return row


def create_client_balance_operation(
    db: Session,
    client_id: int,
    admin_id: int,
    amount: int,
    op_type: str,
    comment: Optional[str],
) -> schemas.BalanceOperationOut:
    op = _add_balance_operation_row(
        db,
        user_id=client_id,
        actor_user_id=admin_id,
        amount=amount,
        op_type=op_type,
        comment=comment,
    )
    db.commit()
    db.refresh(op)

    creator = db.get(models.User, admin_id)
    creator_info = _user_info_from_user(creator) if creator else schemas.UserInfo(id=admin_id, login="unknown")
    return schemas.BalanceOperationOut(
        id=op.id,
        clientId=client_id,
        amount=op.amount,
        type=op.op_type,  # type: ignore
        comment=op.comment,
        createdAt=op.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        createdBy=creator_info,
    )


def list_client_balance_operations(
    db: Session,
    client_id: int,
    offset: int,
    limit: int,
    start_local: Optional[datetime],
    end_local: Optional[datetime],
) -> schemas.ClientBalanceOpsListOut:
    base = select(models.ClientBalanceOperation).where(models.ClientBalanceOperation.client_id == client_id)
    if start_local:
        base = base.where(models.ClientBalanceOperation.created_at >= start_local)
    if end_local:
        base = base.where(models.ClientBalanceOperation.created_at <= end_local)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        db.execute(
            base.order_by(models.ClientBalanceOperation.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )

    creator_ids = {r.created_by for r in rows if r.created_by}
    creators_map: Dict[int, schemas.UserInfo] = {}
    if creator_ids:
        users = db.execute(select(models.User).where(models.User.id.in_(creator_ids))).scalars().all()
        for u in users:
            creators_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

    items: List[schemas.BalanceOperationOut] = []
    for r in rows:
        creator_info = creators_map.get(r.created_by) or schemas.UserInfo(id=r.created_by, login="unknown")
        items.append(
            schemas.BalanceOperationOut(
                id=r.id,
                clientId=r.client_id,
                amount=r.amount,
                type=r.op_type,  # type: ignore
                comment=r.comment,
                createdAt=r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                createdBy=creator_info,
            )
        )

    return schemas.ClientBalanceOpsListOut(items=items, total=total)


def list_agent_client_balance_operations(
    db: Session,
    agent_id: int,
    offset: int,
    limit: int,
    start_local: Optional[datetime],
    end_local: Optional[datetime],
) -> schemas.ClientBalanceOpsListOut:
    client_ids = [
        int(client_id)
        for client_id, in db.execute(
            select(models.User.id).where(
                models.User.role == ROLE_CLIENT,
                models.User.owner_agent_id == int(agent_id),
            )
        ).all()
        if client_id is not None
    ]
    if not client_ids:
        return schemas.ClientBalanceOpsListOut(items=[], total=0)

    base = select(models.ClientBalanceOperation).where(models.ClientBalanceOperation.client_id.in_(client_ids))
    if start_local:
        base = base.where(models.ClientBalanceOperation.created_at >= start_local)
    if end_local:
        base = base.where(models.ClientBalanceOperation.created_at <= end_local)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        db.execute(
            base.order_by(models.ClientBalanceOperation.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )

    creator_ids = {r.created_by for r in rows if r.created_by}
    creators_map: Dict[int, schemas.UserInfo] = {}
    if creator_ids:
        users = db.execute(select(models.User).where(models.User.id.in_(creator_ids))).scalars().all()
        for u in users:
            creators_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

    items: List[schemas.BalanceOperationOut] = []
    for r in rows:
        creator_info = creators_map.get(r.created_by) or schemas.UserInfo(id=r.created_by, login="unknown")
        items.append(
            schemas.BalanceOperationOut(
                id=r.id,
                clientId=r.client_id,
                amount=r.amount,
                type=r.op_type,  # type: ignore
                comment=r.comment,
                createdAt=r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                createdBy=creator_info,
            )
        )

    return schemas.ClientBalanceOpsListOut(items=items, total=total)


def transfer_client_to_owner(
    db: Session,
    *,
    client_id: int,
    owner_type: str,
    agent_id: Optional[int],
    admin_user_id: int,
) -> schemas.ClientOwnerTransferOut:
    client = db.get(models.User, int(client_id))
    if not client or not is_client_user(client):
        raise ValueError("Клиент не найден.")

    next_owner_type = "agent" if owner_type == "agent" else "admin"
    next_agent = _ensure_agent_owner(db, int(agent_id) if next_owner_type == "agent" and agent_id is not None else None)
    prev_owner_type, prev_agent_id = get_client_owner_type_and_id(client)
    transferred_balance = int(get_client_remaining_numbers(db, int(client.id)))

    if prev_owner_type == next_owner_type and int(prev_agent_id or 0) == int(getattr(next_agent, "id", 0) or 0):
        owner_user = next_agent if next_agent is not None else db.get(models.User, 1)
        return schemas.ClientOwnerTransferOut(
            client=_get_user_info(db, int(client.id)) or schemas.UserInfo(id=int(client.id), login=client.login),
            ownerType=next_owner_type,  # type: ignore[arg-type]
            ownerUser=_user_info_from_user(owner_user) if owner_user else None,
            transferredBalance=transferred_balance,
        )

    client.owner_agent_id = int(next_agent.id) if next_agent is not None else None
    db.add(client)
    db.commit()
    db.refresh(client)

    owner_user = next_agent if next_agent is not None else db.get(models.User, 1)
    return schemas.ClientOwnerTransferOut(
        client=_get_user_info(db, int(client.id)) or schemas.UserInfo(id=int(client.id), login=client.login),
        ownerType=next_owner_type,  # type: ignore[arg-type]
        ownerUser=_user_info_from_user(owner_user) if owner_user else None,
        transferredBalance=transferred_balance,
    )


def create_agent_credit_to_client_operation(
    db: Session,
    *,
    agent_user_id: int,
    client_id: int,
    amount: int,
    comment: Optional[str],
) -> schemas.BalanceOperationOut:
    agent = db.get(models.User, int(agent_user_id))
    client = db.get(models.User, int(client_id))
    if not agent or not is_agent_user(agent):
        raise ValueError("Агент не найден.")
    if user_is_disabled(agent):
        raise ValueError("Агент отключён.")
    if not client or not is_client_user(client):
        raise ValueError("Клиент не найден.")
    if int(getattr(client, "owner_agent_id", 0) or 0) != int(agent.id):
        raise ValueError("Нет доступа к этому клиенту.")
    available = get_user_manual_balance(db, int(agent.id))
    if available < int(amount):
        raise ValueError("Недостаточно доступного баланса агента для начисления.")

    agent_comment = f"Начисление клиенту id={int(client.id)}"
    if comment and comment.strip():
        agent_comment += f": {comment.strip()}"
    _add_balance_operation_row(
        db,
        user_id=int(agent.id),
        actor_user_id=int(agent.id),
        amount=int(amount),
        op_type="debit",
        comment=agent_comment,
    )
    client_op = _add_balance_operation_row(
        db,
        user_id=int(client.id),
        actor_user_id=int(agent.id),
        amount=int(amount),
        op_type="credit",
        comment=comment,
    )
    db.commit()
    db.refresh(client_op)

    return schemas.BalanceOperationOut(
        id=int(client_op.id),
        clientId=int(client.id),
        amount=int(client_op.amount),
        type=client_op.op_type,  # type: ignore[arg-type]
        comment=client_op.comment,
        createdAt=client_op.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        createdBy=_user_info_from_user(agent),
    )


def _load_user_infos(db: Session, user_ids: List[int]) -> Dict[int, schemas.UserInfo]:
    unique_ids = sorted({int(user_id) for user_id in user_ids if user_id})
    if not unique_ids:
        return {}
    users = db.execute(select(models.User).where(models.User.id.in_(unique_ids))).scalars().all()
    profiles = db.execute(select(models.ClientProfile).where(models.ClientProfile.user_id.in_(unique_ids))).scalars().all()
    profile_name_map = {int(profile.user_id): profile.name for profile in profiles}
    return {
        int(user.id): _user_info_from_user(user, name=profile_name_map.get(int(user.id)))
        for user in users
    }


def _get_tariff_adjustment_map(db: Session, tariff_ids: List[int]) -> Dict[int, Dict[str, int]]:
    if not tariff_ids:
        return {}
    rows = db.execute(
        select(
            models.ClientTariffOperation.tariff_id,
            models.ClientTariffOperation.op_type,
            func.coalesce(func.sum(models.ClientTariffOperation.amount), 0),
        )
        .where(models.ClientTariffOperation.tariff_id.in_(tariff_ids))
        .group_by(models.ClientTariffOperation.tariff_id, models.ClientTariffOperation.op_type)
    ).all()
    result: Dict[int, Dict[str, int]] = {}
    for tariff_id, op_type, total_amt in rows:
        tid = int(tariff_id)
        result.setdefault(tid, {"credit": 0, "debit": 0})
        result[tid][str(op_type)] = int(total_amt or 0)
    return result


def _tariff_to_out(
    tariff: models.ClientTariff,
    creator: schemas.UserInfo,
    adjustments: Optional[Dict[str, int]] = None,
) -> schemas.ClientTariffOut:
    adj = adjustments or {"credit": 0, "debit": 0}
    current_amount = int(tariff.base_amount or 0) + int(adj.get("credit", 0)) - int(adj.get("debit", 0))
    return schemas.ClientTariffOut(
        id=int(tariff.id),
        clientId=int(tariff.client_id),
        baseAmount=int(tariff.base_amount or 0),
        currentAmount=current_amount,
        comment=tariff.comment,
        signal1=(int(tariff.signal1) if getattr(tariff, "signal1", None) is not None else None),
        signal2=(int(tariff.signal2) if getattr(tariff, "signal2", None) is not None else None),
        signal3=(int(tariff.signal3) if getattr(tariff, "signal3", None) is not None else None),
        createdAt=tariff.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        updatedAt=tariff.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        createdBy=creator,
    )


def list_client_tariffs(
    db: Session,
    client_id: int,
    offset: int,
    limit: int,
) -> schemas.ClientTariffListOut:
    base = select(models.ClientTariff).where(models.ClientTariff.client_id == client_id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        db.execute(
            base.order_by(models.ClientTariff.created_at.desc(), models.ClientTariff.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )
    tariff_ids = [int(row.id) for row in rows]
    adjustments_map = _get_tariff_adjustment_map(db, tariff_ids)
    creators_map = _load_user_infos(db, [int(row.created_by) for row in rows if row.created_by])
    items = [
        _tariff_to_out(
            tariff=row,
            creator=creators_map.get(int(row.created_by)) or schemas.UserInfo(id=int(row.created_by), login="unknown"),
            adjustments=adjustments_map.get(int(row.id)),
        )
        for row in rows
    ]
    return schemas.ClientTariffListOut(items=items, total=int(total or 0))


def create_client_tariff(
    db: Session,
    client_id: int,
    admin_id: int,
    amount: int,
    comment: Optional[str],
    signal1: int,
    signal2: int,
    signal3: int,
) -> schemas.ClientTariffOut:
    target_user = _ensure_tariff_target_user(db, int(client_id))
    _validate_tariff_signals(amount=int(amount), signal1=int(signal1), signal2=int(signal2), signal3=int(signal3))
    tariff = models.ClientTariff(
        client_id=client_id,
        base_amount=amount,
        comment=(comment or "").strip() or None,
        signal1=int(signal1),
        signal2=int(signal2),
        signal3=int(signal3),
        created_by=admin_id,
        created_at=now_msk(),
        updated_at=now_msk(),
    )
    db.add(tariff)
    db.flush()
    _apply_tariff_balance_effect(
        db,
        target_user=target_user,
        actor_user_id=int(admin_id),
        amount=int(amount),
        op_type="credit",
        tariff_id=int(tariff.id),
        action="create",
        comment=comment,
    )
    db.commit()
    db.refresh(tariff)
    creator = _load_user_infos(db, [admin_id]).get(admin_id) or schemas.UserInfo(id=admin_id, login="unknown")
    return _tariff_to_out(tariff=tariff, creator=creator)


def update_client_tariff(
    db: Session,
    tariff_id: int,
    admin_id: int,
    amount: int,
    comment: Optional[str],
    signal1: int,
    signal2: int,
    signal3: int,
) -> schemas.ClientTariffOut:
    tariff = db.get(models.ClientTariff, tariff_id)
    if not tariff:
        raise ValueError("Tariff not found")
    target_user = _ensure_tariff_target_user(db, int(tariff.client_id))
    next_amount = int(amount)
    if next_amount < 0:
        raise ValueError("Тариф не может быть отрицательным.")
    _validate_tariff_signals(amount=next_amount, signal1=int(signal1), signal2=int(signal2), signal3=int(signal3))

    current_amount = _get_tariff_current_amount(db, tariff)
    normalized_comment = (comment or "").strip()
    signals_changed = (
        int(getattr(tariff, "signal1", 0) or 0) != int(signal1)
        or int(getattr(tariff, "signal2", 0) or 0) != int(signal2)
        or int(getattr(tariff, "signal3", 0) or 0) != int(signal3)
    )
    amount_changed = next_amount != current_amount

    if not amount_changed and not signals_changed:
        raise ValueError("Изменений нет.")
    if amount_changed and not normalized_comment:
        raise ValueError("Комментарий обязателен при изменении тарифа.")

    delta = next_amount - current_amount
    if delta != 0:
        op_type = "credit" if delta > 0 else "debit"
        if op_type == "debit" and current_amount < abs(delta):
            raise ValueError("Недостаточно объёма в тарифе для списания.")
        op = models.ClientTariffOperation(
            tariff_id=tariff_id,
            amount=abs(delta),
            op_type=op_type,
            comment=normalized_comment,
            created_by=admin_id,
            created_at=now_msk(),
        )
        db.add(op)
        db.flush()
        _apply_tariff_balance_effect(
            db,
            target_user=target_user,
            actor_user_id=int(admin_id),
            amount=abs(delta),
            op_type=op_type,
            tariff_id=int(tariff.id),
            action=op_type,
            comment=normalized_comment,
        )

    tariff.signal1 = int(signal1)
    tariff.signal2 = int(signal2)
    tariff.signal3 = int(signal3)
    tariff.updated_at = now_msk()
    db.add(tariff)
    db.commit()
    db.refresh(tariff)
    creator = _load_user_infos(db, [int(tariff.created_by)]).get(int(tariff.created_by)) or schemas.UserInfo(
        id=int(tariff.created_by),
        login="unknown",
    )
    adjustments_map = _get_tariff_adjustment_map(db, [int(tariff.id)])
    return _tariff_to_out(tariff=tariff, creator=creator, adjustments=adjustments_map.get(int(tariff.id)))


def get_client_tariff(db: Session, tariff_id: int) -> Optional[schemas.ClientTariffOut]:
    tariff = db.get(models.ClientTariff, tariff_id)
    if not tariff:
        return None
    adjustments_map = _get_tariff_adjustment_map(db, [int(tariff.id)])
    creator = _load_user_infos(db, [int(tariff.created_by)]).get(int(tariff.created_by)) or schemas.UserInfo(
        id=int(tariff.created_by),
        login="unknown",
    )
    return _tariff_to_out(tariff=tariff, creator=creator, adjustments=adjustments_map.get(int(tariff.id)))


def get_latest_client_tariff(db: Session, client_id: int) -> Optional[schemas.ClientTariffOut]:
    tariff = db.execute(
        select(models.ClientTariff)
        .where(models.ClientTariff.client_id == int(client_id))
        .order_by(models.ClientTariff.created_at.desc(), models.ClientTariff.id.desc())
        .limit(1)
    ).scalars().first()
    if not tariff:
        return None
    adjustments_map = _get_tariff_adjustment_map(db, [int(tariff.id)])
    creator = _load_user_infos(db, [int(tariff.created_by)]).get(int(tariff.created_by)) or schemas.UserInfo(
        id=int(tariff.created_by),
        login="unknown",
    )
    return _tariff_to_out(tariff=tariff, creator=creator, adjustments=adjustments_map.get(int(tariff.id)))


def create_client_tariff_operation(
    db: Session,
    tariff_id: int,
    admin_id: int,
    amount: int,
    op_type: str,
    comment: str,
) -> schemas.ClientTariffOperationOut:
    tariff = db.get(models.ClientTariff, tariff_id)
    if not tariff:
        raise ValueError("Tariff not found")
    target_user = _ensure_tariff_target_user(db, int(tariff.client_id))
    current_amount = _get_tariff_current_amount(db, tariff)
    next_amount = current_amount + int(amount) if op_type == "credit" else current_amount - int(amount)
    if op_type == "debit":
        if current_amount < int(amount):
            raise ValueError("Недостаточно объёма в тарифе для списания.")
    _validate_existing_tariff_signals_for_amount(tariff, next_amount)
    op = models.ClientTariffOperation(
        tariff_id=tariff_id,
        amount=amount,
        op_type=op_type,
        comment=comment.strip(),
        created_by=admin_id,
        created_at=now_msk(),
    )
    db.add(op)
    db.flush()
    _apply_tariff_balance_effect(
        db,
        target_user=target_user,
        actor_user_id=int(admin_id),
        amount=int(amount),
        op_type=op_type,
        tariff_id=int(tariff.id),
        action=op_type,
        comment=comment,
    )
    tariff.updated_at = now_msk()
    db.add(tariff)
    db.commit()
    db.refresh(op)
    creator = _load_user_infos(db, [admin_id]).get(admin_id) or schemas.UserInfo(id=admin_id, login="unknown")
    return schemas.ClientTariffOperationOut(
        id=int(op.id),
        tariffId=int(op.tariff_id),
        amount=int(op.amount),
        type=op.op_type,  # type: ignore[arg-type]
        comment=op.comment,
        createdAt=op.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        createdBy=creator,
    )


def list_client_tariff_operations(
    db: Session,
    tariff_id: int,
    offset: int,
    limit: int,
) -> schemas.ClientTariffOperationsListOut:
    base = select(models.ClientTariffOperation).where(models.ClientTariffOperation.tariff_id == tariff_id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = (
        db.execute(
            base.order_by(models.ClientTariffOperation.created_at.desc(), models.ClientTariffOperation.id.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )
    creators_map = _load_user_infos(db, [int(row.created_by) for row in rows if row.created_by])
    items = [
        schemas.ClientTariffOperationOut(
            id=int(row.id),
            tariffId=int(row.tariff_id),
            amount=int(row.amount),
            type=row.op_type,  # type: ignore[arg-type]
            comment=row.comment,
            createdAt=row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            createdBy=creators_map.get(int(row.created_by)) or schemas.UserInfo(id=int(row.created_by), login="unknown"),
        )
        for row in rows
    ]
    return schemas.ClientTariffOperationsListOut(items=items, total=int(total or 0))


def admin_mark_change_processed(db: Session, event_id: int, admin_user_id: int) -> bool:
    """
    Помечает событие аудита как обработанное админом.
    """
    ev = db.get(models.AuditEvent, event_id)
    if not ev:
        return False
    if ev.admin_processed_at is not None:
        # Уже обработано — считаем успехом
        return True
    ev.admin_processed_at = now_msk()
    ev.admin_processed_by = admin_user_id
    db.commit()
    return True


def admin_mark_batch_processed(db: Session, batch_id: str, admin_user_id: int) -> int:
    """
    Помечает все события батча (по batch_id) как обработанные.
    Возвращает число затронутых событий.
    """
    if not batch_id:
        return 0
    rows = db.execute(
        select(models.AuditEvent).where(models.AuditEvent.batch_id == batch_id)
    ).scalars().all()
    count = 0
    now = now_msk()
    for ev in rows:
        if ev.admin_processed_at is None:
            ev.admin_processed_at = now
            ev.admin_processed_by = admin_user_id
            count += 1
    if count:
        db.commit()
    return count


# =====================================================
# =================== ADMIN CRUD ======================
# =====================================================

def _user_info_from_user(user: models.User, *, name: Optional[str] = None) -> schemas.UserInfo:
    resolved_name = name if name is not None else (getattr(user, "display_name", None) or None)
    return schemas.UserInfo(
        id=int(user.id),
        login=user.login,
        name=resolved_name,
        inn=(getattr(user, "inn", None) or None),
        phone=(getattr(user, "phone", None) or None),
        role=get_user_role(user),  # type: ignore[arg-type]
        ownerAgentId=(int(user.owner_agent_id) if getattr(user, "owner_agent_id", None) is not None else None),
        isDisabled=bool(getattr(user, "is_disabled", False)),
        autoLimitControlEnabled=bool(getattr(user, "auto_limit_control_enabled", False)),
        telegramNotificationsChatId=(getattr(user, "telegram_notifications_chat_id", None) or None),
        telegramAutoPauseEnabled=bool(getattr(user, "telegram_auto_pause_enabled", False)),
        uniqueProjectNamesEnabled=bool(getattr(user, "unique_project_names_enabled", False)),
    )


def _get_user_info(db: Session, user_id: int) -> Optional[schemas.UserInfo]:
    """Получить UserInfo по id."""
    user = db.get(models.User, user_id)
    if not user:
        return None
    profile = db.execute(select(models.ClientProfile).where(models.ClientProfile.user_id == int(user.id))).scalar_one_or_none()
    return _user_info_from_user(user, name=(profile.name if profile else None))


def _admin_project_to_out(
    p: models.Project,
    user_info: schemas.UserInfo,
    numbers_period: int = 0,
    numbers_total: Optional[int] = None,
) -> schemas.AdminProjectOut:
    """Преобразует Project в AdminProjectOut (включая user info)."""
    base = _project_to_out(p, numbers_period=numbers_period, numbers_total=numbers_total)
    return schemas.AdminProjectOut(
        **base.dict(),
        user=user_info,
    )


def admin_list_all_projects(
    db: Session,
    offset: int,
    limit: int,
    q: str | None,
    user_id_filter: int | None = None,
    start_local: Optional[datetime] = None,
    end_local: Optional[datetime] = None,
    include_deleted: bool = True,
    project_status: Optional[schemas.ProjectStatus] = None,
    daily_limit_reached: bool = False,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
) -> schemas.AdminProjectListOut:
    """
    Список всех проектов всех пользователей (для админа).
    Опционально фильтрация по user_id и названию проекта.
    """
    stmt = select(models.Project)

    # Фильтр по user_id
    if user_id_filter is not None:
        stmt = stmt.where(models.Project.user_id == user_id_filter)

    # По умолчанию админ видит всё, но можно скрыть удалённые
    stmt = _apply_project_status_filter(
        stmt,
        project_status=project_status,
        include_deleted=include_deleted,
    )

    # Текстовый поиск
    if q:
        q = q.strip()
        if q:
            # Поиск проектов выполняется строго по названию.
            stmt = stmt.where(models.Project.name.ilike(f"%{q}%"))

    if daily_limit_reached:
        stmt = _apply_daily_limit_reached_filter(
            stmt,
            start_local=start_local,
            end_local=end_local,
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    stmt = _apply_project_sort(
        stmt,
        sort_by=sort_by,
        sort_dir=sort_dir,
        start_local=start_local,
        end_local=end_local,
    )
    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()

    # Собираем user info для всех проектов
    user_ids = set(p.user_id for p in rows if p.user_id)
    users_map: Dict[int, schemas.UserInfo] = {}
    if user_ids:
        users = db.execute(select(models.User).where(models.User.id.in_(user_ids))).scalars().all()
        for u in users:
            users_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

    counts_period_map: Dict[int, int] = {}
    counts_total_map: Dict[int, int] = {}
    if start_local and end_local and rows:
        proj_ids = [p.id for p in rows]
        ts_col = _provider_lead_ts_col()
        cnt_rows_period = (
            db.execute(
                select(models.ProviderLead.project_id, func.count())
                .where(
                    models.ProviderLead.project_id.in_(proj_ids),
                    # В админке и клиентском ЛК считаем «за период» по времени события,
                    # чтобы показатель совпадал с фильтрацией списков лидов.
                    ts_col >= start_local,
                    ts_col <= end_local,
                )
                .group_by(models.ProviderLead.project_id)
            ).all()
        )
        counts_period_map = {int(pid): int(cnt) for pid, cnt in cnt_rows_period}

        cnt_rows_total = (
            db.execute(
                select(models.ProviderLead.project_id, func.count())
                .where(models.ProviderLead.project_id.in_(proj_ids))
                .group_by(models.ProviderLead.project_id)
            ).all()
        )
        counts_total_map = {int(pid): int(cnt) for pid, cnt in cnt_rows_total}

    items: List[schemas.AdminProjectOut] = []
    for p in rows:
        user_info = users_map.get(p.user_id, schemas.UserInfo(id=p.user_id or 0, login="(unknown)"))
        items.append(
            _admin_project_to_out(
                p,
                user_info,
                numbers_period=counts_period_map.get(p.id, 0),
                numbers_total=counts_total_map.get(p.id),
            )
        )

    return schemas.AdminProjectListOut(items=items, total=total)


def admin_get_project(db: Session, project_id: int) -> Optional[schemas.AdminProjectOut]:
    """Получить проект по id (без проверки user_id)."""
    p = db.get(models.Project, project_id)
    if not p:
        return None
    user_info = _get_user_info(db, p.user_id) or schemas.UserInfo(id=p.user_id or 0, login="(unknown)")
    return _admin_project_to_out(p, user_info)


def admin_update_project(
    db: Session,
    project_id: int,
    update: schemas.AdminProjectUpdate,
    admin_user_id: int,
) -> Optional[schemas.AdminProjectOut]:
    """
    Обновить проект админом (включая delivery_status).
    admin_user_id — id админа для аудита.
    """
    p = db.get(models.Project, project_id)
    if not p:
        return None

    before = _snapshot_project(p)

    p.name = update.name
    p.tag = update.tag or update.name
    p.status = update.status
    _apply_project_deleted_state(p, deleted=(update.status == "Удалён"))
    p.delivery_status = update.deliveryStatus  # Админ может менять!
    p.data_limit = update.dataLimit
    p.region_mode = update.regionMode
    p.regions = update.regions or None
    p.sites = update.sites or None
    p.phones = update.phones or None
    p.sms_sender_name = update.smsSenderName or None
    p.days_received = _join_days(update.days)
    p.sources_count = _calc_sources_count(p.sites, p.phones, p.sms_sender_name)
    p.updated_at = now_msk()

    after = _snapshot_project(p)
    changed = [k for k in after.keys() if before.get(k) != after.get(k)]
    add_project_audit_event(
        db,
        project=p,
        user_id=admin_user_id,
        actor_user_id=admin_user_id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed,
        via_impersonation=False,
    )
    db.commit()
    db.refresh(p)

    user_info = _get_user_info(db, p.user_id) or schemas.UserInfo(id=p.user_id or 0, login="(unknown)")
    return _admin_project_to_out(p, user_info)


def update_project_status_with_audit(
    db: Session,
    project_id: int,
    status: schemas.ProjectStatus,
    event_user_id: int,
    actor_user_id: Optional[int],
    audit_reason: Optional[str] = None,
) -> bool:
    p = db.get(models.Project, project_id)
    if not p:
        return False
    if _normalize_project_status(p.status) == status:
        return True

    before = _snapshot_project(p)
    p.status = status
    status_changed_at = now_msk()
    _apply_project_deleted_state(p, deleted=(status == "Удалён"), now=status_changed_at)
    p.updated_at = status_changed_at
    after = _snapshot_project(p)
    if audit_reason:
        after["limitControlReason"] = audit_reason
    changed = [k for k in after.keys() if before.get(k) != after.get(k)]

    add_project_audit_event(
        db,
        project=p,
        user_id=event_user_id,
        actor_user_id=actor_user_id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed or ['status'],
        via_impersonation=False,
    )
    db.commit()
    return True


def list_operator_block_check_project_snapshots(db: Session) -> List[dict]:
    rows = db.execute(
        select(models.Project, models.User, models.ClientProfile)
        .join(models.User, models.User.id == models.Project.user_id)
        .outerjoin(models.ClientProfile, models.ClientProfile.user_id == models.User.id)
        .where(
            models.Project.status == "Активен",
            models.Project.data_source_code == "B4",
            models.Project.provider_project_id.is_not(None),
            models.Project.deleted_at.is_(None),
        )
        .order_by(models.Project.id.asc())
    ).all()

    snapshots: List[dict] = []
    for project, user, profile in rows:
        client_name = str(getattr(profile, "name", "") or "").strip()
        if not client_name:
            client_name = str(getattr(user, "display_name", "") or "").strip()
        if not client_name:
            client_name = str(getattr(user, "login", "") or "").strip()
        snapshots.append(
            {
                "id": int(project.id),
                "user_id": int(project.user_id) if project.user_id is not None else None,
                "name": str(project.name or ""),
                "provider_project_id": str(project.provider_project_id or "").strip(),
                "client_login": str(user.login or ""),
                "client_name": client_name,
                "telegram_notifications_chat_id": str(getattr(user, "telegram_notifications_chat_id", "") or "").strip(),
                "telegram_auto_pause_enabled": bool(getattr(user, "telegram_auto_pause_enabled", False)),
            }
        )
    return snapshots


def mark_project_operator_blocked_if_active(db: Session, project_id: int) -> Optional[dict]:
    p = db.get(models.Project, int(project_id))
    if not p or p.status != "Активен" or p.user_id is None:
        return None

    client = db.get(models.User, int(p.user_id)) if p.user_id is not None else None
    profile = None
    if client is not None:
        profile = db.execute(
            select(models.ClientProfile).where(models.ClientProfile.user_id == int(client.id))
        ).scalar_one_or_none()

    before = _snapshot_project(p)
    changed_at = now_msk()
    p.status = PROJECT_STATUS_OPERATOR_BLOCK
    _apply_project_deleted_state(p, deleted=False, now=changed_at)
    p.updated_at = changed_at
    after = _snapshot_project(p)
    after["operatorBlockReason"] = (
        "Система изменила статус: поставщик отключил проект при проверке B4."
    )

    add_project_audit_event(
        db,
        project=p,
        user_id=int(p.user_id),
        actor_user_id=None,
        action="update",
        before=before,
        after=after,
        changed_fields=["status", "operatorBlockReason"],
        via_impersonation=False,
    )
    db.commit()
    db.refresh(p)

    client_name = str(getattr(profile, "name", "") or "").strip()
    if not client_name and client is not None:
        client_name = str(getattr(client, "display_name", "") or "").strip()
    if not client_name and client is not None:
        client_name = str(getattr(client, "login", "") or "").strip()
    return {
        "id": int(p.id),
        "user_id": int(p.user_id) if p.user_id is not None else None,
        "name": str(p.name or ""),
        "provider_project_id": str(p.provider_project_id or "").strip(),
        "client_login": str(getattr(client, "login", "") or ""),
        "client_name": client_name,
        "telegram_notifications_chat_id": str(getattr(client, "telegram_notifications_chat_id", "") or "").strip(),
        "telegram_auto_pause_enabled": bool(getattr(client, "telegram_auto_pause_enabled", False)),
    }


def admin_update_project_status_only(
    db: Session,
    project_id: int,
    status: schemas.ProjectStatus,
    admin_user_id: int,
) -> bool:
    return update_project_status_with_audit(
        db,
        project_id=project_id,
        status=status,
        event_user_id=admin_user_id,
        actor_user_id=admin_user_id,
    )


def admin_delete_project(db: Session, project_id: int, admin_user_id: int) -> bool:
    """Удалить проект админом."""
    p = db.get(models.Project, project_id)
    if not p:
        return False
    before = _snapshot_project(p)
    deleted_now = now_msk()
    p.status = 'Удалён'
    _apply_project_deleted_state(p, deleted=True, now=deleted_now)
    p.updated_at = deleted_now
    db.flush()
    after = _snapshot_project(p)
    add_project_audit_event(
        db,
        project=p,
        user_id=admin_user_id,
        actor_user_id=admin_user_id,
        action='delete',
        before=before,
        after=after,
        changed_fields=['status', 'deletedAt', 'providerLeadsGraceUntil'],
        via_impersonation=False,
    )
    db.commit()
    return True


def admin_list_provider_leads(
    db: Session,
    start_local: datetime,
    end_local: datetime,
    offset: int,
    limit: int,
    project_ids_filter: Optional[List[int]] = None,
    sources_filter: Optional[List[str]] = None,
    user_id_filter: int | None = None,
) -> schemas.AdminLeadsListOut:
    """
    Список лидов из таблицы provider_leads (для админа).
    Для клиента id=1 возвращает все записи, для остальных — только по проектам клиента.
    """
    proj_ids: Optional[List[int]] = None
    if user_id_filter is not None and user_id_filter != 1:
        proj_ids = get_user_project_ids(db, user_id_filter)
        if not proj_ids:
            return schemas.AdminLeadsListOut(items=[], total=0)
    if project_ids_filter is not None and user_id_filter != 1:
        if proj_ids is None:
            proj_ids = project_ids_filter
        else:
            proj_ids = [pid for pid in proj_ids if pid in project_ids_filter]
        if not proj_ids:
            return schemas.AdminLeadsListOut(items=[], total=0)

    ts_col = _provider_lead_ts_col()
    base = select(models.ProviderLead).where(
        and_(ts_col >= start_local, ts_col < end_local)
    )
    if proj_ids is not None:
        base = base.where(models.ProviderLead.project_id.in_(proj_ids))
    if sources_filter:
        base = base.where(models.ProviderLead.prov_chanel.in_(sources_filter))

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(
        base.order_by(ts_col.desc()).offset(offset).limit(limit)
    ).scalars().all()

    user_id = user_id_filter or 0
    user_info = _get_user_info(db, user_id) or schemas.UserInfo(id=user_id, login="(unknown)")

    items: List[schemas.AdminLeadOut] = []
    for r in rows:
        phone_value = r.phone
        if not phone_value and r.phones_raw:
            try:
                phone_value = ", ".join([str(x) for x in r.phones_raw if x is not None])
            except Exception:
                phone_value = None
        created_at = _provider_lead_display_dt(r)
        items.append(schemas.AdminLeadOut(
            ext_id=str(r.vid),
            lk_id=format_provider_lead_lk_id(r.id),
            project_id=r.project_id,
            created_at=created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else "",
            imported_at=r.imported_at.strftime('%Y-%m-%d %H:%M:%S') if r.imported_at else "",
            phone=phone_value or "",
            utm_campaign=_build_utm_campaign(r.prov_source, r.subdomain),
            source=r.prov_chanel,
            project_name=r.project_name,
            user=user_info,
        ))

    return schemas.AdminLeadsListOut(items=items, total=total)


def admin_list_all_leads(
    db: Session,
    start_local: datetime,
    end_local: datetime,
    offset: int,
    limit: int,
    user_id_filter: int | None = None,
    project_ids_filter: Optional[List[int]] = None,
    sources_filter: Optional[List[str]] = None,
    unlinked_only: bool = False,
) -> schemas.AdminLeadsListOut:
    ts_col = _provider_lead_ts_col()
    base = select(models.ProviderLead).where(and_(ts_col >= start_local, ts_col < end_local))
    if unlinked_only:
        base = base.where(models.ProviderLead.project_id.is_(None))
    elif project_ids_filter is not None:
        base = base.where(models.ProviderLead.project_id.in_(project_ids_filter))
    if sources_filter:
        base = base.where(models.ProviderLead.prov_chanel.in_(sources_filter))

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(ts_col.desc()).offset(offset).limit(limit)).scalars().all()

    project_ids = sorted({int(row.project_id) for row in rows if row.project_id is not None})
    projects_map: Dict[int, models.Project] = {}
    if project_ids:
        projects = db.execute(select(models.Project).where(models.Project.id.in_(project_ids))).scalars().all()
        projects_map = {int(project.id): project for project in projects}
    user_ids = sorted({int(project.user_id) for project in projects_map.values() if project.user_id is not None})
    user_infos = _load_user_infos(db, user_ids) if user_ids else {}
    unlinked_user = schemas.UserInfo(id=0, login="(unlinked)", name="Без привязки")

    items: List[schemas.AdminLeadOut] = []
    for row in rows:
        phone_value = row.phone
        if not phone_value and row.phones_raw:
            try:
                phone_value = ", ".join([str(x) for x in row.phones_raw if x is not None])
            except Exception:
                phone_value = None
        created_at = _provider_lead_display_dt(row)
        project = projects_map.get(int(row.project_id)) if row.project_id is not None else None
        user_info = user_infos.get(int(project.user_id)) if project is not None and project.user_id is not None else unlinked_user
        items.append(schemas.AdminLeadOut(
            ext_id=str(row.vid),
            lk_id=format_provider_lead_lk_id(row.id),
            project_id=row.project_id,
            created_at=created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else "",
            imported_at=row.imported_at.strftime('%Y-%m-%d %H:%M:%S') if row.imported_at else "",
            phone=phone_value or "",
            utm_campaign=_build_utm_campaign(row.prov_source, row.subdomain),
            source=row.prov_chanel,
            project_name=row.project_name,
            user=user_info or unlinked_user,
        ))
    return schemas.AdminLeadsListOut(items=items, total=total)


def admin_list_all_blacklist(
    db: Session,
    offset: int,
    limit: int,
    q: str | None = None,
    user_id_filter: int | None = None,
    user_ids_filter: Optional[List[int]] = None,
) -> schemas.AdminBlacklistListOut:
    """
    Список всех записей черного списка (для админа).
    """
    stmt = select(models.BlacklistPhone)

    if user_id_filter is not None:
        stmt = stmt.where(models.BlacklistPhone.user_id == user_id_filter)
    elif user_ids_filter:
        stmt = stmt.where(models.BlacklistPhone.user_id.in_([int(user_id) for user_id in user_ids_filter]))

    if q:
        q = q.strip()
        cond = models.BlacklistPhone.phone.contains(q)
        # Длинные числовые строки здесь обычно являются телефоном.
        # По user_id ищем только безопасные значения для PostgreSQL INTEGER.
        if q.isdigit():
            uid = int(q)
            if 0 < uid <= POSTGRES_INT_MAX:
                cond = or_(cond, models.BlacklistPhone.user_id == uid)
        stmt = stmt.where(cond)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.BlacklistPhone.id.desc()).offset(offset).limit(limit)).scalars().all()

    user_ids = set(r.user_id for r in rows if r.user_id)
    users_map: Dict[int, schemas.UserInfo] = {}
    if user_ids:
        users = db.execute(select(models.User).where(models.User.id.in_(user_ids))).scalars().all()
        for u in users:
            users_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

    items: List[schemas.AdminBlacklistPhoneOut] = []
    for r in rows:
        user_info = users_map.get(r.user_id, schemas.UserInfo(id=r.user_id or 0, login="(unknown)"))
        items.append(schemas.AdminBlacklistPhoneOut(
            id=r.id,
            phone=r.phone,
            createdAt=r.created_at.strftime('%Y-%m-%d'),
            user=user_info,
        ))

    return schemas.AdminBlacklistListOut(items=items, total=total)


def admin_list_all_reports(
    db: Session,
    offset: int,
    limit: int,
    user_id_filter: int | None = None,
    start_local: datetime | None = None,
    end_local: datetime | None = None,
    target_client_id: int | None = None,
) -> schemas.AdminReportListOut:
    """
    Список всех отчётов (для админа).
    """
    stmt = select(models.ReportExport)

    if user_id_filter is not None:
        stmt = stmt.where(models.ReportExport.user_id == user_id_filter)
    if start_local is not None:
        stmt = stmt.where(models.ReportExport.created_at >= start_local)
    if end_local is not None:
        stmt = stmt.where(models.ReportExport.created_at <= end_local)
    if target_client_id is not None:
        stmt = stmt.where(models.ReportExport.target_client_id == target_client_id)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.ReportExport.created_at.desc()).offset(offset).limit(limit)).scalars().all()

    creator_ids = set(r.user_id for r in rows if r.user_id)
    client_ids = set(r.target_client_id for r in rows if r.target_client_id)
    users_map: Dict[int, schemas.UserInfo] = {}
    ids_to_fetch = list(creator_ids.union(client_ids))
    if ids_to_fetch:
        users = db.execute(select(models.User).where(models.User.id.in_(ids_to_fetch))).scalars().all()
        for u in users:
            users_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

    items: List[schemas.AdminReportOut] = []
    for r in rows:
        user_info = users_map.get(r.user_id, schemas.UserInfo(id=r.user_id or 0, login="(unknown)"))
        client_info = None
        if r.target_client_id:
            client_info = users_map.get(r.target_client_id, schemas.UserInfo(id=r.target_client_id, login="(unknown client)"))
        items.append(schemas.AdminReportOut(
            id=r.id,
            createdAt=r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            fromDate=r.from_date,
            toDate=r.to_date,
            projectIds=r.project_ids,
            format=r.format,
            user=user_info,
            client=client_info,
        ))

    return schemas.AdminReportListOut(items=items, total=total)


def get_all_users(db: Session) -> List[schemas.UserInfo]:
    """Получить список всех пользователей."""
    stmt = (
        select(models.User, models.ClientProfile.name)
        .outerjoin(models.ClientProfile, models.ClientProfile.user_id == models.User.id)
        .order_by(models.User.id)
    )
    rows = db.execute(stmt).all()
    out: List[schemas.UserInfo] = []
    for user, name in rows:
        out.append(_user_info_from_user(user, name=name))
    return out
