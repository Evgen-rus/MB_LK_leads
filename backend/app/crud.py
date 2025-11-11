"""
Файл: backend/app/crud.py
Назначение: бизнес-логика/CRUD, аудит изменений, планирование "тихого окна".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import Session

from . import models, schemas


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


def list_projects_paginated(db: Session, offset: int, limit: int, q: str | None) -> schemas.ProjectListOut:
    stmt = select(models.Project)
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

def get_project(db: Session, project_id: int) -> Optional[schemas.ProjectOut]:
    p = db.get(models.Project, project_id)
    return _project_to_out(p) if p else None


def create_projects(db: Session, items: List[schemas.CreateProjectItem]) -> List[schemas.ProjectOut]:
    created: List[schemas.ProjectOut] = []
    now = datetime.utcnow()
    for it in items:
        sites = it.sites or None
        phones = it.phones or None
        sms = (it.smsSenderName or None)
        days_received = _join_days(it.days)
        sources_count = _calc_sources_count(sites, phones, sms)

        p = models.Project(
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


def update_project(db: Session, project_id: int, update: schemas.ProjectUpdate) -> Optional[schemas.ProjectOut]:
    p = db.get(models.Project, project_id)
    if not p:
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
        project_id=p.id,
        action='update',
        before=before,
        after=after,
        changed_fields=changed,
    ))
    db.commit()
    db.refresh(p)
    return _project_to_out(p)


def delete_project(db: Session, project_id: int) -> bool:
    p = db.get(models.Project, project_id)
    if not p:
        return False
    before = _snapshot_project(p)
    db.delete(p)
    db.flush()
    db.add(models.AuditEvent(
        project_id=project_id,
        action='delete',
        before=before,
        after=None,
        changed_fields=list(before.keys()),
    ))
    db.commit()
    return True


def list_blacklist_paginated(db: Session, offset: int, limit: int, q: str | None) -> schemas.BlacklistListOut:
    stmt = select(models.BlacklistPhone)
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


# -------- Черный список --------
def list_blacklist(db: Session) -> List[schemas.BlacklistPhoneOut]:
    rows = db.execute(select(models.BlacklistPhone).order_by(models.BlacklistPhone.id.desc())).scalars().all()
    out: List[schemas.BlacklistPhoneOut] = []
    for r in rows:
        out.append(schemas.BlacklistPhoneOut(id=r.id, phone=r.phone, createdAt=r.created_at.strftime('%Y-%m-%d')))
    return out


def add_to_blacklist(db: Session, phones: List[str]) -> List[schemas.BlacklistPhoneOut]:
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
        # вставка, игнорировать дубликаты по unique(phone)
        exists = db.execute(select(models.BlacklistPhone).where(models.BlacklistPhone.phone == digits)).scalar_one_or_none()
        if exists:
            continue
        row = models.BlacklistPhone(phone=digits, created_at=now)
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
            project_id=None,
            action='blacklist_add',
            before=None,
            after=payload_after,
            changed_fields=list(payload_after.keys()),
        ))
    db.commit()
    return created


def delete_from_blacklist(db: Session, row_id: int) -> bool:
    row = db.get(models.BlacklistPhone, row_id)
    if not row:
        return False
    before_phone = row.phone
    db.delete(row)
    db.flush()
    # Аудит: фиксируем удалённый номер
    db.add(models.AuditEvent(
        project_id=None,
        action='blacklist_delete',
        before={"phone": before_phone},
        after=None,
        changed_fields=["phone"],
    ))
    db.commit()
    return True