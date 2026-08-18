"""
Файл: backend/app/models.py
Назначение: ORM-модели БД (Project, AuditEvent, NotifyState, Lead).
"""
from datetime import datetime
from .time_utils import now_msk
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, Boolean, BigInteger, UniqueConstraint, ForeignKey, Index
from sqlalchemy.orm import declarative_base
from sqlalchemy.types import JSON


Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    inn = Column(String, nullable=True, index=True)
    phone = Column(String, nullable=True)
    # Роль пользователя: admin | client | agent.
    # Инвариант проекта сохраняется: админ определяется как user.id == 1.
    role = Column(String, nullable=False, default="client", index=True)
    # Для клиентов: какой агент владеет клиентом. NULL = прямой клиент админа.
    owner_agent_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # Используем для отключения агента; для остальных ролей обычно False.
    is_disabled = Column(Boolean, nullable=False, default=False)
    # Админская блокировка изменений проектов в клиентском кабинете.
    projects_mutation_locked = Column(Boolean, nullable=False, default=False)
    projects_mutation_locked_at = Column(DateTime, nullable=True)
    projects_mutation_locked_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    projects_mutation_lock_reason = Column(String, nullable=True)
    # Пер-клиентный флаг: включен ли автоматический контроль лимитов.
    auto_limit_control_enabled = Column(Boolean, nullable=False, default=False)
    # Необязательный chat id конкретной Telegram-группы/чата клиента.
    # Используется для маршрутизации системных уведомлений по клиенту.
    telegram_notifications_chat_id = Column(String, nullable=True)
    # Legacy-имя поля: если True, тарифные сигналы и операции уходят в клиентский чат,
    # иначе для них используется общий TELEGRAM_CHAT_ID.
    # Автопауза по лимитам и B4-блокировки всегда отправляются в общий TELEGRAM_CHAT_ID.
    telegram_auto_pause_enabled = Column(Boolean, nullable=False, default=False)
    # Если True, новым проектам клиента добавляется внутренний идентификатор из карточки клиента.
    unique_project_names_enabled = Column(Boolean, nullable=False, default=False)
    # Последний отправленный порог уведомления по остатку: 3 / 2 / 1 / 0.
    # Нужен, чтобы не слать одно и то же сообщение повторно на каждом пересчёте.
    telegram_balance_alert_level = Column(Integer, nullable=True)
    # Последний достигнутый тарифный сигнал по остатку клиента: 0 / 1 / 2 / 3 / 4.
    # Уровень 4 — системный сигнал "тариф закончился" при remaining <= 0.
    # Нужен, чтобы повторно слать уведомление только после восстановления остатка выше порога.
    telegram_tariff_signal_level = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now_msk, nullable=False)


class ClientProfile(Base):
    __tablename__ = "client_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_client_profiles_user"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    inn = Column(String, nullable=True, index=True)
    phone = Column(String, nullable=True)
    contact = Column(String, nullable=True)
    internal_client_id = Column(String, nullable=True)
    table_url = Column(String, nullable=True)
    pixel_table_url = Column(String, nullable=True)
    work_status = Column(String, nullable=False, default="В работе")
    created_at = Column(DateTime, default=now_msk, nullable=False)
    updated_at = Column(DateTime, default=now_msk, nullable=False)

    @property
    def internalClientId(self) -> Optional[str]:
        return self.internal_client_id

    @property
    def tableUrl(self) -> Optional[str]:
        return self.table_url

    @property
    def pixelTableUrl(self) -> Optional[str]:
        return self.pixel_table_url

    @property
    def workStatus(self) -> str:
        return self.work_status or "В работе"


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # владелец проекта
    provider_project_id = Column(String, nullable=True, index=True)  # id проекта у поставщика
    name = Column(String, nullable=False)
    tag = Column(String, nullable=False)
    client_internal_prefix = Column(String, nullable=True)
    # Legacy: True, если проект был создан по старой схеме с неизменяемым маркером [MB{id}] в имени.
    unique_name_applied = Column(Boolean, nullable=False, default=False)
    # Момент мягкого удаления проекта у нас.
    deleted_at = Column(DateTime, nullable=True, index=True)
    # До этого момента webhook ещё может привязывать хвостовые provider leads
    # к удалённому проекту, если среди неудалённых совпадений уже нет.
    provider_leads_grace_until = Column(DateTime, nullable=True, index=True)
    collection_source = Column(String, nullable=False)  # 'Сайты' | 'Звонки' | ...
    data_source_code = Column(String, nullable=False)  # 'B1' | 'B2' | 'B3' | 'B4'
    region_mode = Column(String, nullable=True)  # 'include' | 'exclude'
    regions = Column(JSON, nullable=True)
    sites = Column(JSON, nullable=True)
    phones = Column(JSON, nullable=True)
    sms_sender_name = Column(String, nullable=True)

    status = Column(String, nullable=False)  # 'Активен' | 'На паузе' | 'Удалён' | 'Архив' | 'Блокировка оператора'
    delivery_status = Column(String, nullable=False, default='На модерации')  # 'Активна' | 'На модерации' | 'Отключена'

    data_limit = Column(Integer, nullable=False, default=0)
    daily_limit_reached_notified_at = Column(DateTime, nullable=True)
    daily_limit_reached_notified_limit = Column(Integer, nullable=True)
    is_top = Column(Boolean, nullable=False, default=False)
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


class ProjectOperationEvent(Base):
    __tablename__ = "project_operation_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    operation = Column(String, nullable=False, index=True)  # 'create' | 'update' | 'delete'
    status = Column(String, nullable=False, default="failed", index=True)
    project_name = Column(String, nullable=True)
    request_payload = Column(JSON, nullable=True)
    error_message = Column(String, nullable=False)
    error_code = Column(String, nullable=True)
    via_impersonation = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=now_msk, nullable=False)


class NotifyState(Base):
    __tablename__ = "notify_state"

    id = Column(Integer, primary_key=True)
    next_send_at = Column(DateTime, nullable=True)
    window_minutes = Column(Integer, nullable=False, default=30)


class TelegramNotification(Base):
    __tablename__ = "telegram_notifications"
    __table_args__ = (
        Index("ix_telegram_notifications_claim", "status", "next_attempt_at", "created_at"),
        Index("ix_telegram_notifications_locked_until", "locked_until"),
    )

    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False, default="system", index=True)
    chat_id = Column(String, nullable=False, index=True)
    text = Column(String, nullable=False)
    parse_mode = Column(String, nullable=True, default="HTML")
    status = Column(String, nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    last_error = Column(String, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True, index=True)
    locked_until = Column(DateTime, nullable=True)
    locked_by = Column(String, nullable=True)
    telegram_message_id = Column(String, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now_msk, nullable=False, index=True)
    updated_at = Column(DateTime, default=now_msk, nullable=False)
    payload_metadata = Column("metadata", JSON, nullable=True)


class PixelTelegramReportState(Base):
    __tablename__ = "pixel_telegram_report_states"
    __table_args__ = (
        UniqueConstraint(
            "kind",
            "client_id",
            "period_start",
            "period_end",
            name="uq_pixel_telegram_report_state_period",
        ),
        Index("ix_pixel_telegram_report_states_client", "client_id", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False, index=True)
    client_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False, index=True)
    queued_notification_id = Column(Integer, ForeignKey("telegram_notifications.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=now_msk, nullable=False)


class ProviderLead(Base):
    __tablename__ = "provider_leads"

    id = Column(Integer, primary_key=True)
    vid = Column(String, nullable=False, index=True)
    lead_source = Column(String, nullable=False, default="provider", index=True)
    phone = Column(String, nullable=True)
    phones_raw = Column(JSON, nullable=True)
    project_name = Column(String, nullable=True)
    prov_created_at = Column(DateTime, nullable=True, index=True)
    prov_chanel = Column(String, nullable=True, index=True)
    prov_source = Column(String, nullable=True, index=True)
    subdomain = Column(String, nullable=True, index=True)
    pixel_url = Column(String, nullable=True)
    imported_at = Column(DateTime, nullable=False, default=now_msk, index=True)
    client_sheet_exported_at = Column(DateTime, nullable=True, index=True)
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


class ClientTariff(Base):
    """
    Отдельный тариф клиента.
    Тарифные операции зеркалятся в ClientBalanceOperation, поэтому тариф влияет
    на remaining, лимит-контроль и автопаузу.
    """
    __tablename__ = "client_tariffs"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    base_amount = Column(Integer, nullable=False)
    comment = Column(String, nullable=True)
    signal1 = Column(Integer, nullable=True)
    signal2 = Column(Integer, nullable=True)
    signal3 = Column(Integer, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=now_msk)
    updated_at = Column(DateTime, nullable=False, default=now_msk)


class ClientTariffOperation(Base):
    """
    Корректировка конкретного тарифа: доначисление или списание.
    """
    __tablename__ = "client_tariff_operations"

    id = Column(Integer, primary_key=True, index=True)
    tariff_id = Column(Integer, ForeignKey("client_tariffs.id"), nullable=False, index=True)
    amount = Column(Integer, nullable=False)
    op_type = Column(String, nullable=False)  # 'credit' | 'debit'
    comment = Column(String, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=now_msk)
