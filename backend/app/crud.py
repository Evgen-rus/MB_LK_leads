from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import select
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


