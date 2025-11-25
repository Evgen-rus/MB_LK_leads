"""
Файл: backend/app/crud.py
Назначение: бизнес-логика/CRUD, аудит изменений, планирование "тихого окна".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple, Any
import os

from sqlalchemy import select, func, or_, and_, text, inspect
from sqlalchemy.orm import Session

from . import models, schemas, auth


def _join_days(days: Iterable[str]) -> str:
    return " ".join([f"{d}." for d in days])


def _calc_sources_count(sites: Optional[List[str]], phones: Optional[List[str]], sms_sender_name: Optional[str]) -> int:
    return (len(sites or [])) + (len(phones or [])) + (1 if sms_sender_name else 0)


def _project_to_out(p: models.Project) -> schemas.ProjectOut:
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
        numbersTotal=p.numbers_total,
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
    state.next_send_at = datetime.utcnow() + timedelta(minutes=minutes)
    db.commit()


def list_projects(db: Session) -> List[schemas.ProjectOut]:
    rows = db.execute(select(models.Project).order_by(models.Project.id.desc())).scalars().all()
    return [_project_to_out(p) for p in rows]


def list_projects_paginated(db: Session, offset: int, limit: int, q: str | None, user_id: int) -> schemas.ProjectListOut:
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
    items = [_project_to_out(p) for p in rows]
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
    now = datetime.utcnow()
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
    p.updated_at = datetime.utcnow()

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
    now = datetime.utcnow()
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


def list_leads(db: Session, project_ids: Optional[List[int]], start_local: datetime, end_local: datetime, limit: int = 1000) -> List[schemas.LeadOut]:
    stmt = (
        select(models.Lead)
        .where(models.Lead.created_at >= start_local)
        .where(models.Lead.created_at < end_local)
        .order_by(models.Lead.created_at.desc())
        .limit(limit)
    )
    if project_ids:
        stmt = stmt.where(models.Lead.project_id.in_(project_ids))
    rows = db.execute(stmt).scalars().all()
    out: List[schemas.LeadOut] = []
    for r in rows:
        out.append(schemas.LeadOut(
            ext_id=r.ext_id,
            project_id=r.project_id,
            created_at=r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            phone=r.phone,
            utm_campaign=r.utm_campaign,
        ))
    return out


def fetch_leads_for_export(db: Session, project_ids: Optional[List[int]], start_local: datetime, end_local: datetime, max_rows: int) -> List[models.Lead]:
    stmt = (
        select(models.Lead)
        .where(models.Lead.created_at >= start_local)
        .where(models.Lead.created_at < end_local)
        .order_by(models.Lead.created_at.asc())
        .limit(max_rows)
    )
    if project_ids:
        stmt = stmt.where(models.Lead.project_id.in_(project_ids))
    return db.execute(stmt).scalars().all()


def list_leads_paginated(db: Session, project_ids: Optional[List[int]], start_local: datetime, end_local: datetime, offset: int, limit: int) -> schemas.LeadsListOut:
    base = select(models.Lead).where(
        and_(models.Lead.created_at >= start_local, models.Lead.created_at < end_local)
    )
    if project_ids:
        base = base.where(models.Lead.project_id.in_(project_ids))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(models.Lead.created_at.desc()).offset(offset).limit(limit)).scalars().all()
    items: List[schemas.LeadOut] = []
    for r in rows:
        items.append(schemas.LeadOut(
            ext_id=r.ext_id,
            project_id=r.project_id,
            created_at=r.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            phone=r.phone,
            utm_campaign=r.utm_campaign,
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
    now = datetime.utcnow()
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


def ensure_blacklist_user_id_column(db: Session) -> None:
    engine = db.get_bind()
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("blacklist_phones")]
    if "user_id" not in cols:
        db.execute(text("ALTER TABLE blacklist_phones ADD COLUMN user_id INTEGER"))
        # существующие записи считаем админскими
        db.execute(text("UPDATE blacklist_phones SET user_id = 1 WHERE user_id IS NULL"))
        db.commit()