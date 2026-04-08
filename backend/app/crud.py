"""
Файл: backend/app/crud.py
Назначение: бизнес-логика/CRUD, аудит изменений, планирование "тихого окна".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from .time_utils import now_msk
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Any, Literal
import os
import re
import secrets
import string

from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import Session

from . import models, schemas, auth


PROJECT_PROVIDER_LEADS_GRACE_HOURS = 48
POSTGRES_INT_MAX = 2_147_483_647
ROLE_ADMIN = "admin"
ROLE_CLIENT = "client"
ROLE_AGENT = "agent"


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
    base = f"Тариф клиента #{int(tariff_id)}: {action_map.get(action, 'Изменение тарифа')}"
    return _append_balance_comment(base, comment)


def _get_tariff_current_amount(db: Session, tariff: models.ClientTariff) -> int:
    adjustment = _get_tariff_adjustment_map(db, [int(tariff.id)]).get(int(tariff.id), {"credit": 0, "debit": 0})
    return int(tariff.base_amount or 0) + int(adjustment.get("credit", 0)) - int(adjustment.get("debit", 0))


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
    if value in ("Активен", "На паузе", "Удалён"):
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


def _project_to_out(p: models.Project, numbers_period: int = 0, numbers_total: Optional[int] = None) -> schemas.ProjectOut:
    return schemas.ProjectOut(
        id=p.id,
        status=p.status,  # type: ignore
        deliveryStatus=p.delivery_status,  # type: ignore
        name=p.name,
        tag=p.tag,
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
            # поиск по id, name, tag
            cond = or_(
                models.Project.name.ilike(f"%{q}%"),
                models.Project.tag.ilike(f"%{q}%"),
            )
            # Длинные числовые строки могут быть телефонами в имени проекта.
            # По id ищем только если значение безопасно для PostgreSQL INTEGER.
            if q.isdigit():
                qid = int(q)
                if 0 < qid <= POSTGRES_INT_MAX:
                    cond = or_(cond, models.Project.id == qid)
            stmt = stmt.where(cond)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.Project.id.desc()).offset(offset).limit(limit)).scalars().all()
    # Подсчёт лидов за период, если диапазон задан
    counts_period_map: Dict[int, int] = {}
    counts_total_map: Dict[int, int] = {}
    if start_local and end_local and rows:
        proj_ids = [p.id for p in rows]
        ts_col = func.coalesce(models.ProviderLead.prov_created_at, models.ProviderLead.imported_at)
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
        )
        for p in rows
    ]
    return schemas.ProjectListOut(items=items, total=total)


def _audit_event_compact_description(ev: models.AuditEvent) -> str:
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
                name = ev.after.get("name")
        except Exception:
            name = None
        if name:
            return f'Создан проект "{name}"'
        return "Создан проект"

    if action == "update":
        before = ev.before or {}
        after = ev.after or {}
        if isinstance(before, dict) and isinstance(after, dict):
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
) -> schemas.ProjectHistoryItem:
    """
    Преобразует AuditEvent в компактный элемент истории для фронтенда.
    Здесь мы формируем человекочитаемое краткое описание изменения.
    """
    created_at_str = ev.created_at.strftime("%Y-%m-%d %H:%M:%S")
    action_raw = ev.action or "update"
    # История проекта на фронте ожидает только create/update/delete.
    action = action_raw if action_raw in ("create", "update", "delete") else "update"
    desc = _audit_event_compact_description(ev)

    return schemas.ProjectHistoryItem(
        id=ev.id,
        action=action,  # type: ignore[arg-type]
        createdAt=created_at_str,
        description=desc,
        actor=actor,
        actorMode=actor_mode,  # type: ignore[arg-type]
    )


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

    Источники:
    - audit_events: create/update/delete проектов, blacklist_add/blacklist_delete;
    - client_balance_operations: credit/debit;
    - report_exports: создание отчётов.
    """
    limit = max(1, min(500, limit))
    offset = max(0, offset)

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

    rows: List[Dict[str, Any]] = []

    if "project" in selected_entities or "blacklist" in selected_entities:
        audit_actions: List[str] = []
        if "project" in selected_entities:
            audit_actions.extend(["create", "update", "delete"])
        if "blacklist" in selected_entities:
            audit_actions.extend(["blacklist_add", "blacklist_delete"])

        audit_stmt = (
            select(models.AuditEvent, models.Project.name, models.Project.user_id)
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
                    models.Project.user_id == client_id,
                ),
                and_(
                    models.AuditEvent.project_id.is_(None),
                    models.AuditEvent.user_id == client_id,
                    models.AuditEvent.action.in_(["blacklist_add", "blacklist_delete"]),
                ),
            )
        )

        audit_rows = db.execute(audit_stmt).all()
        for ev, project_name, _project_owner_id in audit_rows:
            entity = "blacklist" if ev.action in ("blacklist_add", "blacklist_delete") else "project"
            actor_id = _audit_event_actor_user_id(ev)
            actor = get_user_info(actor_id)
            description = _audit_event_compact_description(ev)
            event_id = f"AE-{ev.id}"
            actor_login = actor.login if actor else ""
            search_blob = " ".join(
                [
                    event_id,
                    entity,
                    ev.action or "",
                    description,
                    project_name or "",
                    actor_login,
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
                        actor=actor,
                        description=description,
                        projectId=ev.project_id,
                        projectName=project_name,
                    ),
                }
            )

    if "balance" in selected_entities:
        balance_stmt = select(models.ClientBalanceOperation).where(
            models.ClientBalanceOperation.client_id == client_id
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
            search_blob = " ".join(
                [
                    event_id,
                    "balance",
                    op.op_type,
                    description,
                    actor_login,
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
                        actor=actor,
                        description=description,
                        projectId=None,
                        projectName=None,
                    ),
                }
            )

    if "report" in selected_entities:
        report_stmt = select(models.ReportExport).where(
            models.ReportExport.target_client_id == client_id
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
            search_blob = " ".join(
                [
                    event_id,
                    "report",
                    "create",
                    description,
                    actor_login,
                    rep.from_date or "",
                    rep.to_date or "",
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
                        actor=actor,
                        description=description,
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


def list_project_history(db: Session, project_id: int, user_id: int, limit: int = 100) -> List[schemas.ProjectHistoryItem]:
    """
    Возвращает историю изменений конкретного проекта для текущего пользователя.

    Важно: клиент видит только свои изменения, поэтому фильтруем по user_id.
    """
    limit = max(1, min(500, limit))
    stmt = (
        select(models.AuditEvent)
        .where(models.AuditEvent.project_id == project_id)
        .where(models.AuditEvent.user_id == user_id)
        .where(models.AuditEvent.action.in_(["create", "update", "delete"]))
        .order_by(models.AuditEvent.created_at.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    actor_ids = {aid for aid in (_audit_event_actor_user_id(ev) for ev in rows) if aid}
    users_map: Dict[int, schemas.UserInfo] = {}
    if actor_ids:
        users = db.execute(select(models.User).where(models.User.id.in_(actor_ids))).scalars().all()
        for u in users:
            users_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

    items: List[schemas.ProjectHistoryItem] = []
    for ev in rows:
        actor_id = _audit_event_actor_user_id(ev)
        actor = users_map.get(actor_id) if actor_id else None
        actor_mode = _audit_event_actor_mode(ev, actor_id)
        items.append(_audit_event_to_history_item(ev, actor=actor, actor_mode=actor_mode))
    return items


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

    # Собираем actor info
    user_ids = {aid for aid in (_audit_event_actor_user_id(ev) for ev in rows) if aid}
    users_map: Dict[int, schemas.UserInfo] = {}
    if user_ids:
        users = db.execute(select(models.User).where(models.User.id.in_(user_ids))).scalars().all()
        for u in users:
            users_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

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
                action=hist_item.action,
                createdAt=hist_item.createdAt,
                description=hist_item.description,
                user=hist_item.actor,
                actorMode=hist_item.actorMode,  # type: ignore[arg-type]
                status=status_val,  # type: ignore[arg-type]
                projectSnapshot=ev.after or ev.before,
            )
        )

    return schemas.AdminProjectHistoryListOut(items=items, total=total)

def get_project(db: Session, project_id: int, user_id: int) -> Optional[schemas.ProjectOut]:
    p = db.get(models.Project, project_id)
    if not p or p.user_id != user_id:
        return None
    return _project_to_out(p) if p else None


def build_project_model_from_create_item(
    item: schemas.CreateProjectItem,
    *,
    user_id: int,
    provider_id: Optional[str],
    created_at: Optional[datetime] = None,
    unique_name_applied: bool = False,
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
        )
        db.add(p)
        db.flush()

        after = _project_to_out(p).dict()
        db.add(models.AuditEvent(
            user_id=user_id,
            actor_user_id=actor_user_id or user_id,
            project_id=p.id,
            batch_id=batch_id,
            action='create',
            before=None,
            after=after,
            changed_fields=list(after.keys()),
            via_impersonation=via_impersonation,
        ))
        created.append(_project_to_out(p))

    db.commit()
    return created


def _snapshot_project(p: models.Project) -> dict:
    snapshot = _project_to_out(p).dict()
    deleted_at = getattr(p, "deleted_at", None)
    grace_until = getattr(p, "provider_leads_grace_until", None)
    snapshot["deletedAt"] = deleted_at.strftime("%Y-%m-%d %H:%M:%S") if deleted_at else None
    snapshot["providerLeadsGraceUntil"] = grace_until.strftime("%Y-%m-%d %H:%M:%S") if grace_until else None
    return snapshot


def _apply_project_deleted_state(
    p: models.Project,
    *,
    deleted: bool,
    now: Optional[datetime] = None,
) -> None:
    ts = now or now_msk()
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
    db.add(models.AuditEvent(
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        project_id=p.id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed,
        via_impersonation=via_impersonation,
    ))
    db.commit()
    db.refresh(p)
    return _project_to_out(p)


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
    db.add(models.AuditEvent(
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        project_id=project_id,
        action='delete',
        before=before,
        after=after,
        changed_fields=['status', 'deletedAt', 'providerLeadsGraceUntil'],
        via_impersonation=via_impersonation,
    ))
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

    now = now_msk()
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


def _provider_lead_to_export_row(
    lead: models.ProviderLead,
    user_info: Optional[schemas.UserInfo] = None,
) -> dict:
    phone_value = lead.phone
    if not phone_value and lead.phones_raw:
        try:
            phone_value = ", ".join([str(x) for x in lead.phones_raw if x is not None])
        except Exception:
            phone_value = None
    display_dt = lead.prov_created_at or lead.imported_at
    return {
        "ext_id": lead.vid,
        "project_id": lead.project_id,
        "project_name": lead.project_name or "",
        "source": lead.prov_chanel,
        "imported_at": display_dt.strftime("%Y-%m-%d %H:%M:%S") if display_dt else "",
        "phone": phone_value or "",
        "utm_campaign": lead.prov_source,
        "user_login": user_info.login if user_info else "",
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
) -> Iterator[dict]:
    remaining = max(0, int(max_rows))
    if remaining == 0:
        return

    chunk_size = min(2000, remaining)
    ts_col = func.coalesce(models.ProviderLead.prov_created_at, models.ProviderLead.imported_at)
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

        for lead in batch:
            yield _provider_lead_to_export_row(lead, user_info=user_info)

        remaining -= len(batch)
        tail = batch[-1]
        last_ts = tail.prov_created_at or tail.imported_at
        last_id = int(tail.id)


def fetch_provider_leads_for_export(
    db: Session,
    start_local: datetime,
    end_local: datetime,
    max_rows: int,
    project_ids: Optional[List[int]] = None,
    sources: Optional[List[str]] = None,
    user_info: Optional[schemas.UserInfo] = None,
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

    ts_col = func.coalesce(models.ProviderLead.prov_created_at, models.ProviderLead.imported_at)
    base = select(models.ProviderLead).where(
        and_(ts_col >= start_local, ts_col < end_local)
    )
    if proj_ids is not None:
        base = base.where(models.ProviderLead.project_id.in_(proj_ids))
    if sources:
        base = base.where(models.ProviderLead.prov_chanel.in_(sources))

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(
        base.order_by(ts_col.desc()).offset(offset).limit(limit)
    ).scalars().all()

    items: List[schemas.LeadOut] = []
    for r in rows:
        phone_value = r.phone
        if not phone_value and r.phones_raw:
            try:
                phone_value = ", ".join([str(x) for x in r.phones_raw if x is not None])
            except Exception:
                phone_value = None
        created_at = r.prov_created_at or r.imported_at
        items.append(schemas.LeadOut(
            ext_id=str(r.vid),
            project_id=r.project_id,
            created_at=created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else "",
            imported_at=r.imported_at.strftime('%Y-%m-%d %H:%M:%S') if r.imported_at else "",
            phone=phone_value or "",
            utm_campaign=r.prov_source,
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
    if agent_ids:
        rows = db.execute(
            select(models.User.owner_agent_id, func.count(models.User.id))
            .where(models.User.role == ROLE_CLIENT, models.User.owner_agent_id.in_(agent_ids))
            .group_by(models.User.owner_agent_id)
        ).all()
        client_count_map = {int(owner_agent_id): int(total or 0) for owner_agent_id, total in rows if owner_agent_id is not None}

    balance_rows = {}
    if agent_ids:
        rows = db.execute(
            select(
                models.ClientBalanceOperation.client_id,
                models.ClientBalanceOperation.op_type,
                func.coalesce(func.sum(models.ClientBalanceOperation.amount), 0),
            )
            .where(models.ClientBalanceOperation.client_id.in_(agent_ids))
            .group_by(models.ClientBalanceOperation.client_id, models.ClientBalanceOperation.op_type)
        ).all()
        for client_id, op_type, total_amt in rows:
            aid = int(client_id)
            balance_rows.setdefault(aid, {"credit": 0, "debit": 0})
            balance_rows[aid][str(op_type)] = int(total_amt or 0)

    items = []
    for agent in agents:
        credited = balance_rows.get(int(agent.id), {}).get("credit", 0)
        debited = balance_rows.get(int(agent.id), {}).get("debit", 0)
        items.append(
            schemas.AdminAgentSummaryItem(
                user=_user_info_from_user(agent),
                clientCount=client_count_map.get(int(agent.id), 0),
                credited=credited,
                debited=debited,
                balance=credited - debited,
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
        select(models.User).where(models.User.role == ROLE_CLIENT)
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
            func.count(models.Project.id),
            func.coalesce(func.sum(models.Project.data_limit), 0),
        ).group_by(models.Project.user_id)
    ).all()
    for uid, cnt, limit_sum in proj_rows:
        if uid is None:
            continue
        by_user.setdefault(int(uid), {"projects": 0, "limit": 0, "used_total": 0, "used_period": 0})
        by_user[int(uid)]["projects"] = int(cnt or 0)
        by_user[int(uid)]["limit"] = int(limit_sum or 0)

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

    # Использование за период (по prov_created_at/imported_at, локальное время без tz)
    ts_col = func.coalesce(models.ProviderLead.prov_created_at, models.ProviderLead.imported_at)
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
    ts_col = func.coalesce(models.ProviderLead.prov_created_at, models.ProviderLead.imported_at)
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

    prev_agent = db.get(models.User, int(prev_agent_id)) if prev_agent_id is not None else None
    if prev_agent is not None:
        _add_balance_operation_row(
            db,
            user_id=int(prev_agent.id),
            actor_user_id=int(admin_user_id),
            amount=transferred_balance,
            op_type="credit",
            comment=f"Перенос клиента id={int(client.id)} от агента: возврат текущего остатка клиента",
        )

    if next_agent is not None:
        _add_balance_operation_row(
            db,
            user_id=int(next_agent.id),
            actor_user_id=int(admin_user_id),
            amount=transferred_balance,
            op_type="debit",
            comment=f"Перенос клиента id={int(client.id)} агенту: принят текущий остаток клиента",
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
) -> schemas.ClientTariffOut:
    target_user = _ensure_tariff_target_user(db, int(client_id))
    tariff = models.ClientTariff(
        client_id=client_id,
        base_amount=amount,
        comment=(comment or "").strip() or None,
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
    if op_type == "debit":
        current_amount = _get_tariff_current_amount(db, tariff)
        if current_amount < int(amount):
            raise ValueError("Недостаточно объёма в тарифе для списания.")
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
) -> schemas.AdminProjectListOut:
    """
    Список всех проектов всех пользователей (для админа).
    Опционально фильтрация по user_id, id, name, tag.
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
            cond = or_(
                models.Project.name.ilike(f"%{q}%"),
                models.Project.tag.ilike(f"%{q}%"),
            )
            # Длинные числовые строки могут быть телефонами в имени проекта.
            # По id и user_id ищем только если значение безопасно для PostgreSQL INTEGER.
            if q.isdigit():
                qid = int(q)
                if 0 < qid <= POSTGRES_INT_MAX:
                    cond = or_(cond, models.Project.id == qid)
                    cond = or_(cond, models.Project.user_id == qid)
            stmt = stmt.where(cond)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.Project.id.desc()).offset(offset).limit(limit)).scalars().all()

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
        ts_col = func.coalesce(models.ProviderLead.prov_created_at, models.ProviderLead.imported_at)
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
    db.add(models.AuditEvent(
        user_id=admin_user_id,
        actor_user_id=admin_user_id,
        project_id=p.id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed,
        via_impersonation=False,
    ))
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

    db.add(models.AuditEvent(
        user_id=event_user_id,
        actor_user_id=actor_user_id,
        project_id=p.id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed or ['status'],
        via_impersonation=False,
    ))
    db.commit()
    return True


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
    db.add(models.AuditEvent(
        user_id=admin_user_id,
        actor_user_id=admin_user_id,
        project_id=project_id,
        action='delete',
        before=before,
        after=after,
        changed_fields=['status', 'deletedAt', 'providerLeadsGraceUntil'],
        via_impersonation=False,
    ))
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

    ts_col = func.coalesce(models.ProviderLead.prov_created_at, models.ProviderLead.imported_at)
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
        created_at = r.prov_created_at or r.imported_at
        imported_at = r.imported_at
        items.append(schemas.AdminLeadOut(
            ext_id=str(r.vid),
            project_id=r.project_id,
            created_at=created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else "",
            imported_at=imported_at.strftime('%Y-%m-%d %H:%M:%S') if imported_at else "",
            phone=phone_value or "",
            utm_campaign=r.prov_source,
            source=r.prov_chanel,
            project_name=r.project_name,
            user=user_info,
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
