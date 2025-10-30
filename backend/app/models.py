from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.orm import declarative_base
from sqlalchemy.types import JSON


Base = declarative_base()


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    tag = Column(String, nullable=False)
    collection_source = Column(String, nullable=False)  # 'Сайты' | 'Звонки' | ...
    data_source_code = Column(String, nullable=False)  # 'B1' | 'B2' | 'B3' | 'B4'
    region_mode = Column(String, nullable=True)  # 'include' | 'exclude'
    regions = Column(JSON, nullable=True)
    sites = Column(JSON, nullable=True)
    phones = Column(JSON, nullable=True)
    sms_sender_name = Column(String, nullable=True)

    status = Column(String, nullable=False)  # 'Активен' | 'На паузе'
    delivery_status = Column(String, nullable=False, default='На модерации')  # 'Активна' | 'На модерации' | 'Отключена'

    data_limit = Column(Integer, nullable=False, default=0)
    numbers_today = Column(Integer, nullable=False, default=0)
    numbers_total = Column(Integer, nullable=False, default=0)
    days_received = Column(String, nullable=False, default='')  # "Вт. Ср. ..."
    sources_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, nullable=True)
    action = Column(String, nullable=False)  # 'create' | 'update' | 'delete'
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)
    changed_fields = Column(JSON, nullable=True)  # list[str]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent = Column(Boolean, default=False, nullable=False)


class NotifyState(Base):
    __tablename__ = "notify_state"

    id = Column(Integer, primary_key=True)
    next_send_at = Column(DateTime, nullable=True)
    window_minutes = Column(Integer, nullable=False, default=30)


