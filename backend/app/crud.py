"""
Файл: backend/app/crud.py
Назначение: бизнес-логика/CRUD, аудит изменений, планирование "тихого окна".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from .time_utils import now_msk
from typing import Dict, Iterable, List, Optional, Tuple, Any
import os

from sqlalchemy import select, func, or_, and_, text, inspect
from sqlalchemy.orm import Session

from . import models, schemas, auth


def _join_days(days: Iterable[str]) -> str:
    return " ".join([f"{d}." for d in days])


def _calc_sources_count(sites: Optional[List[str]], phones: Optional[List[str]], sms_sender_name: Optional[str]) -> int:
    return (len(sites or [])) + (len(phones or [])) + (1 if sms_sender_name else 0)


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


def ensure_notify_state(db: Session, window_minutes: int) -> None:
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
) -> schemas.ProjectListOut:
    stmt = select(models.Project).where(models.Project.user_id == user_id)
    if q:
        q = q.strip()
        if q:
            # поиск по id, name, tag
            cond = or_(
                models.Project.name.ilike(f"%{q}%"),
                models.Project.tag.ilike(f"%{q}%"),
            )
            # если q число — искать и по id
            try:
                qid = int(q)
                cond = or_(cond, models.Project.id == qid)
            except Exception:
                pass
            stmt = stmt.where(cond)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.Project.id.desc()).offset(offset).limit(limit)).scalars().all()
    # Подсчёт лидов за период, если диапазон задан
    counts_period_map: Dict[int, int] = {}
    counts_total_map: Dict[int, int] = {}
    if start_local and end_local and rows:
        proj_ids = [p.id for p in rows]
        cnt_rows_period = (
            db.execute(
                select(models.Lead.project_id, func.count())
                .where(
                    models.Lead.project_id.in_(proj_ids),
                    models.Lead.created_at >= start_local,
                    models.Lead.created_at <= end_local,
                )
                .group_by(models.Lead.project_id)
            ).all()
        )
        counts_period_map = {int(pid): int(cnt) for pid, cnt in cnt_rows_period}

        cnt_rows_total = (
            db.execute(
                select(models.Lead.project_id, func.count())
                .where(models.Lead.project_id.in_(proj_ids))
                .group_by(models.Lead.project_id)
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


def _audit_event_to_history_item(ev: models.AuditEvent) -> schemas.ProjectHistoryItem:
    """
    Преобразует AuditEvent в компактный элемент истории для фронтенда.
    Здесь мы формируем человекочитаемое краткое описание изменения.
    """
    created_at_str = ev.created_at.strftime('%Y-%m-%d %H:%M:%S')
    action = ev.action or 'update'

    # Краткое текстовое описание
    desc: str
    if action == 'create':
        name = None
        try:
            if isinstance(ev.after, dict):
                name = ev.after.get("name")
        except Exception:
            name = None
        if name:
            desc = f'Создан проект "{name}"'
        else:
            desc = "Создан проект"
    elif action == 'update':
        # Пытаемся построить детальное описание изменений.
        before = ev.before or {}
        after = ev.after or {}
        if isinstance(before, dict) and isinstance(after, dict):
            diff = _diff_dict(before, after)
            desc = _format_changes_compact(diff)
        else:
            fields = ev.changed_fields or []
            if isinstance(fields, list) and fields:
                fields_str = ", ".join(str(f) for f in fields)
                desc = f"Обновлены поля: {fields_str}"
            else:
                desc = "Обновлены параметры проекта"
    elif action == 'delete':
        desc = "Проект удалён"
    else:
        # На будущее: другие типы событий (например, blacklist_*).
        desc = action

    return schemas.ProjectHistoryItem(
        id=ev.id,
        action=action,  # type: ignore[arg-type]
        createdAt=created_at_str,
        description=desc,
    )


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
    return [_audit_event_to_history_item(ev) for ev in rows]

def get_project(db: Session, project_id: int, user_id: int) -> Optional[schemas.ProjectOut]:
    p = db.get(models.Project, project_id)
    if not p or p.user_id != user_id:
        return None
    return _project_to_out(p) if p else None


def create_projects(db: Session, items: List[schemas.CreateProjectItem], user_id: int) -> List[schemas.ProjectOut]:
    created: List[schemas.ProjectOut] = []
    now = now_msk()
    for it in items:
        sites = it.sites or None
        phones = it.phones or None
        sms = (it.smsSenderName or None)
        days_received = _join_days(it.days)
        sources_count = _calc_sources_count(sites, phones, sms)

        p = models.Project(
            user_id=user_id,
            name=it.name,
            tag=it.tag or it.name,
            collection_source=it.collectionSource,
            data_source_code=it.dataSourceCode,
            region_mode=it.regionMode,
            regions=it.regions or None,
            sites=sites,
            phones=phones,
            sms_sender_name=sms,
            status=it.status,
            delivery_status='На модерации',
            data_limit=it.dataLimit,
            numbers_today=0,
            numbers_total=0,
            days_received=days_received,
            sources_count=sources_count,
            created_at=now,
            updated_at=now,
        )
        db.add(p)
        db.flush()

        after = _project_to_out(p).dict()
        db.add(models.AuditEvent(
            user_id=user_id,
            project_id=p.id,
            action='create',
            before=None,
            after=after,
            changed_fields=list(after.keys()),
        ))
        created.append(_project_to_out(p))

    db.commit()
    return created


def _snapshot_project(p: models.Project) -> dict:
    return _project_to_out(p).dict()


def update_project(db: Session, project_id: int, update: schemas.ProjectUpdate, user_id: int) -> Optional[schemas.ProjectOut]:
    p = db.get(models.Project, project_id)
    if not p:
        return None
    if p.user_id != user_id:
        return None
    before = _snapshot_project(p)

    p.name = update.name
    p.tag = update.tag or update.name
    p.status = update.status
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
        project_id=p.id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed,
    ))
    db.commit()
    db.refresh(p)
    return _project_to_out(p)


def delete_project(db: Session, project_id: int, user_id: int) -> bool:
    p = db.get(models.Project, project_id)
    if not p:
        return False
    if p.user_id != user_id:
        return False
    before = _snapshot_project(p)
    db.delete(p)
    db.flush()
    db.add(models.AuditEvent(
        user_id=user_id,
        project_id=project_id,
        action='delete',
        before=before,
        after=None,
        changed_fields=list(before.keys()),
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


def list_leads(db: Session, project_ids: Optional[List[int]], start_local: datetime, end_local: datetime, limit: int = 1000, sources: Optional[List[str]] = None) -> List[schemas.LeadOut]:
    stmt = (
        select(models.Lead)
        .where(models.Lead.imported_at >= start_local)
        .where(models.Lead.imported_at < end_local)
        .order_by(models.Lead.imported_at.desc())
        .limit(limit)
    )
    if project_ids:
        stmt = stmt.where(models.Lead.project_id.in_(project_ids))
    if sources:
        stmt = stmt.where(models.Lead.source.in_(sources))
    rows = db.execute(stmt).scalars().all()
    out: List[schemas.LeadOut] = []
    for r in rows:
        out.append(schemas.LeadOut(
            ext_id=r.ext_id,
            project_id=r.project_id,
            created_at=r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            imported_at=r.imported_at.strftime('%Y-%m-%d %H:%M:%S') if r.imported_at else "",
            phone=r.phone,
            utm_campaign=r.utm_campaign,
            source=r.source,
        ))
    return out


def fetch_leads_for_export(
    db: Session,
    project_ids: Optional[List[int]],
    start_local: datetime,
    end_local: datetime,
    max_rows: int,
    sources: Optional[List[str]] = None,
    current_user_id: Optional[int] = None,
) -> List[dict]:
    stmt = (
        select(
            models.Lead,
            models.Project.name.label("project_name"),
            models.Project.user_id.label("project_user_id"),
            models.User.login.label("user_login"),
        )
        .join(models.Project, models.Project.id == models.Lead.project_id)
        .join(models.User, models.User.id == models.Project.user_id, isouter=True)
        .where(models.Lead.imported_at >= start_local)
        .where(models.Lead.imported_at < end_local)
        .order_by(models.Lead.imported_at.asc())
        .limit(max_rows)
    )
    if project_ids:
        stmt = stmt.where(models.Lead.project_id.in_(project_ids))
    if sources:
        stmt = stmt.where(models.Lead.source.in_(sources))
    rows = db.execute(stmt).all()
    out: List[dict] = []
    for lead, proj_name, proj_user_id, user_login in rows:
        out.append(
            {
                "ext_id": lead.ext_id,
                "project_id": lead.project_id,
                "project_name": proj_name or "",
                "source": lead.source,
                "imported_at": lead.imported_at.strftime("%Y-%m-%d %H:%M:%S") if lead.imported_at else "",
                "phone": lead.phone,
                "utm_campaign": lead.utm_campaign,
                "user_login": user_login or "",
                "user_id": proj_user_id or "",
            }
        )
    return out


def list_leads_paginated(db: Session, project_ids: Optional[List[int]], start_local: datetime, end_local: datetime, offset: int, limit: int, sources: Optional[List[str]] = None) -> schemas.LeadsListOut:
    base = select(models.Lead).where(
        and_(models.Lead.imported_at >= start_local, models.Lead.imported_at < end_local)
    )
    if project_ids:
        base = base.where(models.Lead.project_id.in_(project_ids))
    if sources:
        base = base.where(models.Lead.source.in_(sources))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(models.Lead.imported_at.desc()).offset(offset).limit(limit)).scalars().all()
    items: List[schemas.LeadOut] = []
    for r in rows:
        items.append(schemas.LeadOut(
            ext_id=r.ext_id,
            project_id=r.project_id,
            created_at=r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            imported_at=r.imported_at.strftime('%Y-%m-%d %H:%M:%S') if r.imported_at else "",
            phone=r.phone,
            utm_campaign=r.utm_campaign,
            source=r.source,
        ))
    return schemas.LeadsListOut(items=items, total=total)


# -------- Отчёты (экспорт) --------
def log_report_export(
    db: Session,
    user_id: int,
    from_date: str,
    to_date: str,
    project_ids: Optional[List[int]],
    fmt: str,
) -> None:
    """
    Фиксирует факт экспорта отчёта.

    Храним только параметры запроса, сам файл не сохраняем.
    """
    proj_str = ",".join(str(pid) for pid in project_ids) if project_ids else None
    row = models.ReportExport(
        user_id=user_id,
        from_date=from_date,
        to_date=to_date,
        project_ids=proj_str,
        format=fmt or "csv",
    )
    db.add(row)
    db.commit()


def list_reports_paginated(
    db: Session,
    user_id: int,
    offset: int,
    limit: int,
) -> schemas.ReportListOut:
    """
    Возвращает историю экспортов отчётов конкретного пользователя.
    """
    limit = max(1, min(500, limit))
    offset = max(0, offset)

    base = select(models.ReportExport).where(models.ReportExport.user_id == user_id)
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


def add_to_blacklist(db: Session, user_id: int, phones: List[str]) -> List[schemas.BlacklistPhoneOut]:
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
            project_id=None,
            action='blacklist_add',
            before=None,
            after=payload_after,
            changed_fields=list(payload_after.keys()),
        ))
    db.commit()
    return created


def delete_from_blacklist(db: Session, user_id: int, row_id: int) -> bool:
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
        project_id=None,
        action='blacklist_delete',
        before={"phone": before_phone},
        after=None,
        changed_fields=["phone"],
    ))
    db.commit()
    return True


# -------- Пользователи и инициализация --------
def get_user_by_login(db: Session, login: str) -> Optional[models.User]:
    return db.execute(select(models.User).where(models.User.login == login)).scalar_one_or_none()


def create_user(db: Session, login: str, password_plain: str) -> models.User:
    user = models.User(login=login, password_hash=auth.hash_password(password_plain))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def ensure_users_from_env(db: Session) -> None:
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
            create_user(db, login, password)
            created_any = True
        idx += 1
    if created_any:
        # убедиться, что есть индексы/структура
        db.commit()


def ensure_projects_user_id_column(db: Session) -> None:
    """
    Добавляем столбец user_id в projects при его отсутствии и проставляем 1 (admin) для старых строк.
    Работает на SQLite простым ALTER TABLE.
    """
    engine = db.get_bind()
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("projects")]
    if "user_id" not in cols:
        db.execute(text("ALTER TABLE projects ADD COLUMN user_id INTEGER"))
        # по умолчанию привяжем к пользователю 1
        db.execute(text("UPDATE projects SET user_id = 1 WHERE user_id IS NULL"))
        db.commit()


def get_user_project_ids(db: Session, user_id: int) -> List[int]:
    rows = db.execute(select(models.Project.id).where(models.Project.user_id == user_id)).all()
    return [int(r[0]) for r in rows]


def ensure_audit_user_id_column(db: Session) -> None:
    engine = db.get_bind()
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("audit_events")]
    if "user_id" not in cols:
        db.execute(text("ALTER TABLE audit_events ADD COLUMN user_id INTEGER"))
        db.commit()


def ensure_audit_admin_columns(db: Session) -> None:
    """
    Добавляем служебные поля для отметки обработки изменений админом:
    - admin_processed_at: когда админ обработал событие
    - admin_processed_by: id админа, который обработал

    Реализовано через простой ALTER TABLE для SQLite.
    """
    engine = db.get_bind()
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("audit_events")]
    changed = False
    if "admin_processed_at" not in cols:
        db.execute(text("ALTER TABLE audit_events ADD COLUMN admin_processed_at DATETIME"))
        changed = True
    if "admin_processed_by" not in cols:
        db.execute(text("ALTER TABLE audit_events ADD COLUMN admin_processed_by INTEGER"))
        changed = True
    if changed:
        db.commit()


def admin_list_client_changes_summary(db: Session) -> List[schemas.AdminClientChangesSummaryItem]:
    """
    Краткая сводка по необработанным изменениям по клиентам.

    Считаем только события, созданные НЕ админом (user_id != 1),
    с action в ['create','update','delete'] и admin_processed_at IS NULL.
    """
    # Собираем пары (user_id, login, count)
    rows = db.execute(
        select(
            models.User.id,
            models.User.login,
            func.count(models.AuditEvent.id),
        )
        .join(models.AuditEvent, models.AuditEvent.user_id == models.User.id)
        .where(models.User.id != 1)
        .where(models.AuditEvent.action.in_(["create", "update", "delete"]))
        .where(models.AuditEvent.admin_processed_at.is_(None))
        .group_by(models.User.id, models.User.login)
    ).all()

    items: List[schemas.AdminClientChangesSummaryItem] = []
    for uid, login, cnt in rows:
        user_info = schemas.UserInfo(id=int(uid), login=login)
        items.append(
            schemas.AdminClientChangesSummaryItem(
                user=user_info,
                pendingChanges=int(cnt or 0),
            )
        )
    return items


def admin_list_client_changes(db: Session, client_id: int) -> schemas.AdminClientChangesOut:
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

    # Берём только "проектные" события клиента, которые ещё не обработаны админом
    rows = db.execute(
        select(models.AuditEvent, models.Project.name)
        .outerjoin(models.Project, models.Project.id == models.AuditEvent.project_id)
        .where(models.AuditEvent.user_id == client_id)
        .where(models.AuditEvent.action.in_(["create", "update", "delete"]))
        .where(models.AuditEvent.admin_processed_at.is_(None))
        .order_by(models.AuditEvent.created_at.desc())
    ).all()

    items: List[schemas.AdminChangeOut] = []
    for ev, proj_name in rows:
        # Используем уже существующую утилиту для человекочитаемого описания
        hist_item = _audit_event_to_history_item(ev)
        items.append(
            schemas.AdminChangeOut(
                id=ev.id,
                projectId=ev.project_id,
                projectName=proj_name,
                createdAt=hist_item.createdAt,
                description=hist_item.description,
            )
        )

    return schemas.AdminClientChangesOut(
        user=schemas.UserInfo(id=user.id, login=user.login),
        items=items,
    )


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


def ensure_blacklist_user_id_column(db: Session) -> None:
    engine = db.get_bind()
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("blacklist_phones")]
    if "user_id" not in cols:
        db.execute(text("ALTER TABLE blacklist_phones ADD COLUMN user_id INTEGER"))
        # существующие записи считаем админскими
        db.execute(text("UPDATE blacklist_phones SET user_id = 1 WHERE user_id IS NULL"))
        db.commit()


# =====================================================
# =================== ADMIN CRUD ======================
# =====================================================

def _get_user_info(db: Session, user_id: int) -> Optional[schemas.UserInfo]:
    """Получить UserInfo по id."""
    user = db.get(models.User, user_id)
    if not user:
        return None
    return schemas.UserInfo(id=user.id, login=user.login)


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
) -> schemas.AdminProjectListOut:
    """
    Список всех проектов всех пользователей (для админа).
    Опционально фильтрация по user_id, id, name, tag.
    """
    stmt = select(models.Project)

    # Фильтр по user_id
    if user_id_filter is not None:
        stmt = stmt.where(models.Project.user_id == user_id_filter)

    # Текстовый поиск
    if q:
        q = q.strip()
        if q:
            cond = or_(
                models.Project.name.ilike(f"%{q}%"),
                models.Project.tag.ilike(f"%{q}%"),
            )
            # Поиск по id проекта
            try:
                qid = int(q)
                cond = or_(cond, models.Project.id == qid)
            except Exception:
                pass
            # Поиск по user_id
            try:
                uid = int(q)
                cond = or_(cond, models.Project.user_id == uid)
            except Exception:
                pass
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
        cnt_rows_period = (
            db.execute(
                select(models.Lead.project_id, func.count())
                .where(
                    models.Lead.project_id.in_(proj_ids),
                    models.Lead.created_at >= start_local,
                    models.Lead.created_at <= end_local,
                )
                .group_by(models.Lead.project_id)
            ).all()
        )
        counts_period_map = {int(pid): int(cnt) for pid, cnt in cnt_rows_period}

        cnt_rows_total = (
            db.execute(
                select(models.Lead.project_id, func.count())
                .where(models.Lead.project_id.in_(proj_ids))
                .group_by(models.Lead.project_id)
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
        project_id=p.id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed,
    ))
    db.commit()
    db.refresh(p)

    user_info = _get_user_info(db, p.user_id) or schemas.UserInfo(id=p.user_id or 0, login="(unknown)")
    return _admin_project_to_out(p, user_info)


def admin_delete_project(db: Session, project_id: int, admin_user_id: int) -> bool:
    """Удалить проект админом."""
    p = db.get(models.Project, project_id)
    if not p:
        return False
    before = _snapshot_project(p)
    db.delete(p)
    db.flush()
    db.add(models.AuditEvent(
        user_id=admin_user_id,
        project_id=project_id,
        action='delete',
        before=before,
        after=None,
        changed_fields=list(before.keys()),
    ))
    db.commit()
    return True


def admin_list_all_leads(
    db: Session,
    start_local: datetime,
    end_local: datetime,
    offset: int,
    limit: int,
    user_id_filter: int | None = None,
    project_ids_filter: Optional[List[int]] = None,
    sources_filter: Optional[List[str]] = None,
) -> schemas.AdminLeadsListOut:
    """
    Список всех лидов (для админа).
    Опционально фильтрация по user_id владельца проекта.
    """
    # Собираем project_ids если нужна фильтрация по user
    proj_ids: Optional[List[int]] = None
    if user_id_filter is not None:
        proj_ids = get_user_project_ids(db, user_id_filter)
        if not proj_ids:
            return schemas.AdminLeadsListOut(items=[], total=0)
    if project_ids_filter is not None:
        if proj_ids is None:
            proj_ids = project_ids_filter
        else:
            proj_ids = [pid for pid in proj_ids if pid in project_ids_filter]
        if not proj_ids:
            return schemas.AdminLeadsListOut(items=[], total=0)

    base = select(models.Lead).where(
        and_(models.Lead.imported_at >= start_local, models.Lead.imported_at < end_local)
    )
    if proj_ids is not None:
        base = base.where(models.Lead.project_id.in_(proj_ids))
    if sources_filter:
        base = base.where(models.Lead.source.in_(sources_filter))

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(models.Lead.imported_at.desc()).offset(offset).limit(limit)).scalars().all()

    # Собираем user info для всех лидов через их проекты
    project_ids_in_rows = set(r.project_id for r in rows)
    projects_map: Dict[int, Tuple[int, str]] = {}  # project_id -> (user_id, name)
    if project_ids_in_rows:
        proj_rows = db.execute(
            select(models.Project.id, models.Project.user_id, models.Project.name)
            .where(models.Project.id.in_(project_ids_in_rows))
        ).all()
        for pid, uid, name in proj_rows:
            projects_map[pid] = (uid, name)

    user_ids = set(uid for uid, _ in projects_map.values())
    users_map: Dict[int, schemas.UserInfo] = {}
    if user_ids:
        users = db.execute(select(models.User).where(models.User.id.in_(user_ids))).scalars().all()
        for u in users:
            users_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

    items: List[schemas.AdminLeadOut] = []
    for r in rows:
        uid, pname = projects_map.get(r.project_id, (0, "(unknown)"))
        user_info = users_map.get(uid, schemas.UserInfo(id=uid, login="(unknown)"))
        items.append(schemas.AdminLeadOut(
            ext_id=r.ext_id,
            project_id=r.project_id,
            created_at=r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            imported_at=r.imported_at.strftime('%Y-%m-%d %H:%M:%S') if r.imported_at else "",
            phone=r.phone,
            utm_campaign=r.utm_campaign,
            source=r.source,
            project_name=pname,
            user=user_info,
        ))

    return schemas.AdminLeadsListOut(items=items, total=total)


def admin_list_all_blacklist(
    db: Session,
    offset: int,
    limit: int,
    q: str | None = None,
    user_id_filter: int | None = None,
) -> schemas.AdminBlacklistListOut:
    """
    Список всех записей черного списка (для админа).
    """
    stmt = select(models.BlacklistPhone)

    if user_id_filter is not None:
        stmt = stmt.where(models.BlacklistPhone.user_id == user_id_filter)

    if q:
        q = q.strip()
        cond = models.BlacklistPhone.phone.contains(q)
        # Поиск по user_id
        try:
            uid = int(q)
            cond = or_(cond, models.BlacklistPhone.user_id == uid)
        except Exception:
            pass
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
) -> schemas.AdminReportListOut:
    """
    Список всех отчётов (для админа).
    """
    stmt = select(models.ReportExport)

    if user_id_filter is not None:
        stmt = stmt.where(models.ReportExport.user_id == user_id_filter)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.ReportExport.created_at.desc()).offset(offset).limit(limit)).scalars().all()

    user_ids = set(r.user_id for r in rows if r.user_id)
    users_map: Dict[int, schemas.UserInfo] = {}
    if user_ids:
        users = db.execute(select(models.User).where(models.User.id.in_(user_ids))).scalars().all()
        for u in users:
            users_map[u.id] = schemas.UserInfo(id=u.id, login=u.login)

    items: List[schemas.AdminReportOut] = []
    for r in rows:
        user_info = users_map.get(r.user_id, schemas.UserInfo(id=r.user_id or 0, login="(unknown)"))
        items.append(schemas.AdminReportOut(
            id=r.id,
            createdAt=r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            fromDate=r.from_date,
            toDate=r.to_date,
            projectIds=r.project_ids,
            format=r.format,
            user=user_info,
        ))

    return schemas.AdminReportListOut(items=items, total=total)


def get_all_users(db: Session) -> List[schemas.UserInfo]:
    """Получить список всех пользователей."""
    users = db.execute(select(models.User).order_by(models.User.id)).scalars().all()
    return [schemas.UserInfo(id=u.id, login=u.login) for u in users]