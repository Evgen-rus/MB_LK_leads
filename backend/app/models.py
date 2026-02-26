"""
Файл: backend/app/models.py
Назначение: ORM-модели БД (Project, AuditEvent, NotifyState, Lead).
"""
from datetime import datetime
from .time_utils import now_msk
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, Boolean, BigInteger, UniqueConstraint, ForeignKey
from sqlalchemy.orm import declarative_base
from sqlalchemy.types import JSON


Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    # Админская блокировка изменений проектов в клиентском кабинете.
    projects_mutation_locked = Column(Boolean, nullable=False, default=False)
    projects_mutation_locked_at = Column(DateTime, nullable=True)
    projects_mutation_locked_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    projects_mutation_lock_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=now_msk, nullable=False)


class ClientProfile(Base):
    __tablename__ = "client_profiles"
    __table_args__ = (
        UniqueConstraint("inn", name="uq_client_profiles_inn"),
        UniqueConstraint("user_id", name="uq_client_profiles_user"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    inn = Column(String, nullable=False, index=True)
    phone = Column(String, nullable=False)
    contact = Column(String, nullable=True)
    created_at = Column(DateTime, default=now_msk, nullable=False)
    updated_at = Column(DateTime, default=now_msk, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # владелец проекта
    provider_project_id = Column(String, nullable=True, index=True)  # id проекта у поставщика
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

    created_at = Column(DateTime, default=now_msk, nullable=False)
    updated_at = Column(DateTime, default=now_msk, nullable=False)


class ClientProjectPauseSnapshot(Base):
    __tablename__ = "client_project_pause_snapshots"
    __table_args__ = (
        UniqueConstraint("client_id", name="uq_client_project_pause_snapshot_client"),
    )

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Список id проектов, которые были реально поставлены на паузу этой механикой.
    project_ids = Column(JSON, nullable=False)
    paused_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=now_msk, nullable=False)
    updated_at = Column(DateTime, default=now_msk, nullable=False)


class ProjectIdMap(Base):
    __tablename__ = "project_id_map"
    __table_args__ = (
        UniqueConstraint("external_id", "source", name="uq_project_id_map_external_source"),
    )

    id = Column(Integer, primary_key=True)
    external_id = Column(Integer, nullable=False, index=True)
    source = Column(String, nullable=True)
    project_id = Column(Integer, nullable=False, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # Фактический актор (кто сделал действие). Для старых записей может быть NULL.
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    project_id = Column(Integer, nullable=True)
    batch_id = Column(String, nullable=True, index=True)  # идентификатор батча (для группировки созданий)
    action = Column(String, nullable=False)  # 'create' | 'update' | 'delete'
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)
    changed_fields = Column(JSON, nullable=True)  # list[str]
    # Признак, что действие выполнено админом в режиме имперсонации клиента.
    via_impersonation = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=now_msk, nullable=False)
    sent = Column(Boolean, default=False, nullable=False)
    # Поля для админской отметки обработки изменений (review)
    admin_processed_at = Column(DateTime, nullable=True)
    admin_processed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)


class NotifyState(Base):
    __tablename__ = "notify_state"

    id = Column(Integer, primary_key=True)
    next_send_at = Column(DateTime, nullable=True)
    window_minutes = Column(Integer, nullable=False, default=30)



class ProviderLead(Base):
    __tablename__ = "provider_leads"

    id = Column(Integer, primary_key=True)
    vid = Column(String, nullable=False, unique=True, index=True)
    phone = Column(String, nullable=True)
    phones_raw = Column(JSON, nullable=True)
    project_name = Column(String, nullable=True)
    prov_created_at = Column(DateTime, nullable=True, index=True)
    prov_chanel = Column(String, nullable=True, index=True)
    prov_source = Column(String, nullable=True, index=True)
    subdomain = Column(String, nullable=True, index=True)
    imported_at = Column(DateTime, nullable=False, default=now_msk)
    project_id = Column(Integer, nullable=True, index=True)


class BlacklistPhone(Base):
    __tablename__ = "blacklist_phones"
    # Примечание: для существующих БД добавить уникальность (user_id, phone) сложно без мигратора.
    # В коде перед вставкой проверяем дубликат по user_id+phone.
    __table_args__ = (UniqueConstraint('user_id', 'phone', name='uq_blacklist_user_phone'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    phone = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=now_msk)


class ReportExport(Base):
    """
    Лог экспортов отчётов (лидов) пользователем.

    Мы НЕ храним файлы, только параметры запроса:
    - период (from_date, to_date)
    - список проектов (project_ids в виде строки "1,2,3")
    - формат (csv/xlsx)
    - target_client_id (для какого клиента формировали отчёт админ)
    """
    __tablename__ = "report_exports"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    target_client_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=now_msk)

    from_date = Column(String, nullable=False)  # YYYY-MM-DD
    to_date = Column(String, nullable=False)    # YYYY-MM-DD
    project_ids = Column(String, nullable=True)  # "1,2,3" или NULL (все проекты)
    format = Column(String, nullable=False, default="csv")


class ClientBalanceOperation(Base):
    """
    Операции по номерам (идентификациям) на уровне клиента.
    Хранит только ручные начисления/списания; фактическое использование считаем по лидам.
    """
    __tablename__ = "client_balance_operations"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Integer, nullable=False)  # целое количество номеров
    op_type = Column(String, nullable=False)  # 'credit' | 'debit'
    comment = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=now_msk)
