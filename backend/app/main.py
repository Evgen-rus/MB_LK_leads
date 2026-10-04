"""
Файл: backend/app/main.py
Назначение: точка входа FastAPI.
- Загружает .env, настраивает логирование и CORS
- Инициализирует БД и создаёт таблицы
- Выдаёт request_id (X-Request-Id), пишет access-лог
- Регистрирует эндпоинты /health, /projects (CRUD), /client-errors
- Стартует фоновый воркер уведомлений в Telegram ("тихое окно")
"""
import os
import csv
import io
import json
import tempfile
import html
import re
from dotenv import load_dotenv
import logging
import secrets
import uuid
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response, FileResponse
from pydantic import ValidationError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import inspect, or_, select, text
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect

from . import (
    auth,
    crud,
    daily_export_limit_groups,
    db,
    logging_setup,
    models,
    notifications,
    notify_worker,
    project_operations,
    project_operations_worker,
    schemas,
)
from . import provider_leads_xlsx_import as provider_leads_import
from .time_utils import now_msk
from .providers import prostats


SOURCE_CODE_DISPLAY_MAP = {
    "B1": "A",
    "B2": "B",
    "B3": "C",
    "B4": "D",
}


def _source_code_for_display(value: Optional[str]) -> str:
    normalized = str(value or "").strip().upper()
    return SOURCE_CODE_DISPLAY_MAP.get(normalized, normalized)


def _source_text_for_display(value: Optional[str]) -> str:
    text_value = str(value or "")
    return re.sub(
        r"(^|[^A-Za-z0-9А-Яа-яЁё])(B1|B2|B3|B4)(?=$|[^A-Za-z0-9А-Яа-яЁё])",
        lambda m: f"{m.group(1)}{_source_code_for_display(m.group(2))}",
        text_value,
        flags=re.IGNORECASE,
    )


def _project_name_for_display(value: Optional[str]) -> str:
    text_value = str(value or "")
    return re.sub(r"^(B1|B2|B3|B4)(?=[\s_-])", lambda m: _source_code_for_display(m.group(1)), text_value, flags=re.IGNORECASE)


def _csv_export_line(values: List[Any]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["" if value is None else value for value in values])
    return buf.getvalue()


def _parse_date_range_in_settings_tz(
    from_date: Optional[str],
    to_date: Optional[str],
) -> tuple[datetime, datetime]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today = datetime.now(tz).date()
    from_value = from_date or today.isoformat()
    to_value = to_date or from_value
    try:
        y, m, d = [int(x) for x in from_value.split("-")]
        y2, m2, d2 = [int(x) for x in to_value.split("-")]
        start_day = datetime(y, m, d, 0, 0, 0, tzinfo=tz).date()
        end_day = datetime(y2, m2, d2, 0, 0, 0, tzinfo=tz).date()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD") from exc
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    start_local = datetime(start_day.year, start_day.month, start_day.day, 0, 0, 0, tzinfo=tz).replace(tzinfo=None)
    end_exclusive = end_day + timedelta(days=1)
    end_local = datetime(end_exclusive.year, end_exclusive.month, end_exclusive.day, 0, 0, 0, tzinfo=tz).replace(tzinfo=None)
    return start_local, end_local


def get_settings():
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        "DEBOUNCE_WINDOW_MINUTES": int(os.getenv("DEBOUNCE_WINDOW_MINUTES", "30")),
        "AUTO_LIMIT_CHECK_SECONDS": int(os.getenv("AUTO_LIMIT_CHECK_SECONDS", "3600")),
        "DB_POOL_SIZE": int(os.getenv("DB_POOL_SIZE", "10")),
        "DB_MAX_OVERFLOW": int(os.getenv("DB_MAX_OVERFLOW", "20")),
        "DB_POOL_TIMEOUT": int(os.getenv("DB_POOL_TIMEOUT", "30")),
        "DB_POOL_RECYCLE": int(os.getenv("DB_POOL_RECYCLE", "1800")),
        "PROJECT_OPERATIONS_CONCURRENCY": int(os.getenv("PROJECT_OPERATIONS_CONCURRENCY", "3")),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
        "TELEGRAM_WORKER_API_TOKEN": os.getenv("TELEGRAM_WORKER_API_TOKEN", ""),
        "SHEETS_TZ": os.getenv("SHEETS_TZ", "Europe/Moscow"),
    }


load_dotenv()
from .lead_analytics.router import build_router as build_analytics_router
logging_setup.setup_logging()
settings = get_settings()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is not set")
PIXEL_WEBHOOK_SECRET = os.getenv("PIXEL_WEBHOOK_SECRET", "").strip()

provider_webhook_logger = logging_setup.setup_provider_webhook_logger()
pixel_webhook_logger = logging_setup.setup_pixel_webhook_logger()

engine, SessionLocal = db.init_engine_and_session(
    settings["DATABASE_URL"],
    pool_size=settings["DB_POOL_SIZE"],
    max_overflow=settings["DB_MAX_OVERFLOW"],
    pool_timeout=settings["DB_POOL_TIMEOUT"],
    pool_recycle=settings["DB_POOL_RECYCLE"],
)
models.Base.metadata.create_all(bind=engine)


def _ensure_audit_event_columns() -> None:
    """
    Лёгкая схема-эволюция для существующих БД без отдельного мигратора.
    Новые поля nullable, чтобы старые записи оставались валидными.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "audit_events" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("audit_events")}
    with engine.begin() as conn:
        if "actor_user_id" not in columns:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN actor_user_id INTEGER"))
        if "via_impersonation" not in columns:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN via_impersonation BOOLEAN"))


_ensure_audit_event_columns()


def _ensure_client_profile_columns() -> None:
    """
    Лёгкая схема-эволюция для карточек клиентов без отдельного мигратора.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "client_profiles" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("client_profiles")}
    with engine.begin() as conn:
        if "work_status" not in columns:
            conn.execute(text("ALTER TABLE client_profiles ADD COLUMN work_status VARCHAR"))
        conn.execute(
            text(
                "UPDATE client_profiles "
                "SET work_status = :default_status "
                "WHERE work_status IS NULL OR trim(work_status) = ''"
            ),
            {"default_status": "В работе"},
        )


_ensure_client_profile_columns()


def _ensure_optional_client_profile_contacts() -> None:
    """
    ИНН и телефон клиента необязательны. ИНН больше не является уникальным:
    несколько карточек могут относиться к одной организации.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "client_profiles" not in tables:
        return

    columns = {col.get("name"): col for col in inspector.get_columns("client_profiles")}
    unique_constraints = inspector.get_unique_constraints("client_profiles")
    has_unique_inn = any(
        set(constraint.get("column_names") or []) == {"inn"}
        for constraint in unique_constraints
    )
    needs_nullable_columns = any(
        not bool(columns.get(column_name, {}).get("nullable", True))
        for column_name in ("inn", "phone")
    )
    dialect_name = engine.dialect.name

    if dialect_name == "postgresql":
        with engine.begin() as conn:
            preparer = conn.dialect.identifier_preparer
            for constraint in unique_constraints:
                if set(constraint.get("column_names") or []) != {"inn"}:
                    continue
                constraint_name = constraint.get("name")
                if constraint_name:
                    conn.execute(
                        text(
                            "ALTER TABLE client_profiles "
                            f"DROP CONSTRAINT IF EXISTS {preparer.quote(constraint_name)}"
                        )
                    )
            conn.execute(text("ALTER TABLE client_profiles ALTER COLUMN inn DROP NOT NULL"))
            conn.execute(text("ALTER TABLE client_profiles ALTER COLUMN phone DROP NOT NULL"))
            conn.execute(text("UPDATE client_profiles SET inn = NULL WHERE trim(inn) = ''"))
            conn.execute(text("UPDATE client_profiles SET phone = NULL WHERE trim(phone) = ''"))
        return

    if dialect_name == "sqlite":
        if has_unique_inn or needs_nullable_columns:
            legacy_table = "client_profiles_before_optional_contacts"
            next_table = "client_profiles_optional_contacts"
            with engine.begin() as conn:
                for table_name in (legacy_table, next_table):
                    exists = conn.execute(
                        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
                        {"name": table_name},
                    ).scalar_one_or_none()
                    if exists:
                        raise RuntimeError(f"SQLite migration table already exists: {table_name}")

                conn.execute(text("ALTER TABLE client_profiles RENAME TO client_profiles_before_optional_contacts"))
                conn.execute(
                    text(
                        "CREATE TABLE client_profiles_optional_contacts ("
                        "id INTEGER NOT NULL PRIMARY KEY, "
                        "user_id INTEGER NOT NULL, "
                        "name VARCHAR NOT NULL, "
                        "inn VARCHAR, "
                        "phone VARCHAR, "
                        "contact VARCHAR, "
                        "internal_client_id VARCHAR, "
                        "table_url VARCHAR, "
                        "work_status VARCHAR NOT NULL, "
                        "created_at DATETIME NOT NULL, "
                        "updated_at DATETIME NOT NULL, "
                        "CONSTRAINT uq_client_profiles_user UNIQUE (user_id), "
                        "FOREIGN KEY(user_id) REFERENCES users (id)"
                        ")"
                    )
                )
                legacy_columns = {
                    row[1]
                    for row in conn.execute(
                        text("PRAGMA table_info('client_profiles_before_optional_contacts')")
                    ).fetchall()
                }
                copy_columns = [
                    column_name
                    for column_name in (
                        "id",
                        "user_id",
                        "name",
                        "inn",
                        "phone",
                        "contact",
                        "internal_client_id",
                        "table_url",
                        "work_status",
                        "created_at",
                        "updated_at",
                    )
                    if column_name in legacy_columns
                ]
                joined_columns = ", ".join(copy_columns)
                conn.execute(
                    text(
                        f"INSERT INTO client_profiles_optional_contacts ({joined_columns}) "
                        f"SELECT {joined_columns} FROM client_profiles_before_optional_contacts"
                    )
                )
                conn.execute(text("DROP TABLE client_profiles_before_optional_contacts"))
                conn.execute(text("ALTER TABLE client_profiles_optional_contacts RENAME TO client_profiles"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_client_profiles_user_id ON client_profiles (user_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_client_profiles_inn ON client_profiles (inn)"))
        with engine.begin() as conn:
            conn.execute(text("UPDATE client_profiles SET inn = NULL WHERE trim(inn) = ''"))
            conn.execute(text("UPDATE client_profiles SET phone = NULL WHERE trim(phone) = ''"))
        return

    if has_unique_inn or needs_nullable_columns:
        raise RuntimeError(f"Unsupported database dialect for client_profiles migration: {dialect_name}")


_ensure_optional_client_profile_contacts()


def _ensure_project_operation_event_columns() -> None:
    """
    Лёгкая schema-evolution для журнала неудачных операций с проектами.
    create_all создаёт таблицу на новых БД, а эта проверка помогает старым БД.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "project_operation_events" not in tables:
        models.ProjectOperationEvent.__table__.create(bind=engine, checkfirst=True)
        return

    columns = {col.get("name") for col in inspector.get_columns("project_operation_events")}
    with engine.begin() as conn:
        if "actor_user_id" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN actor_user_id INTEGER"))
        if "project_id" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN project_id INTEGER"))
        if "operation" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN operation VARCHAR"))
        if "status" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN status VARCHAR DEFAULT 'failed'"))
        if "project_name" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN project_name VARCHAR"))
        if "request_payload" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN request_payload JSON"))
        if "error_message" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN error_message VARCHAR"))
        if "error_code" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN error_code VARCHAR"))
        if "via_impersonation" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN via_impersonation BOOLEAN"))
        if "created_at" not in columns:
            conn.execute(text("ALTER TABLE project_operation_events ADD COLUMN created_at TIMESTAMP"))
        conn.execute(text("UPDATE project_operation_events SET status = 'failed' WHERE status IS NULL OR status = ''"))


_ensure_project_operation_event_columns()


def _ensure_project_operation_tables() -> None:
    """Новые долговечные job-таблицы создаются целиком через create_all/checkfirst."""
    models.ProjectOperationJob.__table__.create(bind=engine, checkfirst=True)
    models.ProjectOperationItem.__table__.create(bind=engine, checkfirst=True)


_ensure_project_operation_tables()


def _ensure_telegram_notification_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "telegram_notifications" not in tables:
        models.TelegramNotification.__table__.create(bind=engine, checkfirst=True)
        return

    columns = {col.get("name") for col in inspector.get_columns("telegram_notifications")}
    with engine.begin() as conn:
        if "kind" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN kind VARCHAR DEFAULT 'system'"))
        if "chat_id" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN chat_id VARCHAR"))
        if "text" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN text VARCHAR"))
        if "parse_mode" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN parse_mode VARCHAR DEFAULT 'HTML'"))
        if "status" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN status VARCHAR DEFAULT 'pending'"))
        if "attempt_count" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN attempt_count INTEGER DEFAULT 0"))
        if "max_attempts" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN max_attempts INTEGER DEFAULT 5"))
        if "last_error" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN last_error VARCHAR"))
        if "next_attempt_at" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN next_attempt_at TIMESTAMP"))
        if "locked_until" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN locked_until TIMESTAMP"))
        if "locked_by" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN locked_by VARCHAR"))
        if "telegram_message_id" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN telegram_message_id VARCHAR"))
        if "sent_at" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN sent_at TIMESTAMP"))
        if "created_at" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN created_at TIMESTAMP"))
        if "updated_at" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN updated_at TIMESTAMP"))
        if "metadata" not in columns:
            conn.execute(text("ALTER TABLE telegram_notifications ADD COLUMN metadata JSON"))
        conn.execute(text("UPDATE telegram_notifications SET kind = 'system' WHERE kind IS NULL OR kind = ''"))
        conn.execute(text("UPDATE telegram_notifications SET status = 'pending' WHERE status IS NULL OR status = ''"))
        conn.execute(text("UPDATE telegram_notifications SET attempt_count = 0 WHERE attempt_count IS NULL"))
        conn.execute(text("UPDATE telegram_notifications SET max_attempts = 5 WHERE max_attempts IS NULL"))
        conn.execute(text("UPDATE telegram_notifications SET next_attempt_at = created_at WHERE next_attempt_at IS NULL AND status IN ('pending', 'failed')"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telegram_notifications_claim ON telegram_notifications (status, next_attempt_at, created_at)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_telegram_notifications_locked_until ON telegram_notifications (locked_until)"))


_ensure_telegram_notification_columns()


def _ensure_pixel_telegram_report_state_table() -> None:
    models.PixelTelegramReportState.__table__.create(bind=engine, checkfirst=True)


_ensure_pixel_telegram_report_state_table()


def _ensure_user_projects_lock_columns() -> None:
    """
    Лёгкая schema-evolution: добавляем поля блокировки изменений проектов,
    автоконтроля лимитов и Telegram-настроек клиента.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("users")}
    with engine.begin() as conn:
        if "display_name" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR"))
        if "inn" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN inn VARCHAR"))
        if "phone" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR"))
        if "role" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'client'"))
        if "owner_agent_id" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN owner_agent_id INTEGER"))
        if "is_disabled" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_disabled BOOLEAN DEFAULT FALSE"))
        if "projects_mutation_locked" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN projects_mutation_locked BOOLEAN DEFAULT FALSE"))
        if "projects_mutation_locked_at" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN projects_mutation_locked_at TIMESTAMP"))
        if "projects_mutation_locked_by" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN projects_mutation_locked_by INTEGER"))
        if "projects_mutation_lock_reason" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN projects_mutation_lock_reason VARCHAR"))
        if "auto_limit_control_enabled" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN auto_limit_control_enabled BOOLEAN DEFAULT FALSE"))
        if "telegram_notifications_chat_id" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN telegram_notifications_chat_id VARCHAR"))
        if "telegram_auto_pause_enabled" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN telegram_auto_pause_enabled BOOLEAN DEFAULT FALSE"))
        if "unique_project_names_enabled" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN unique_project_names_enabled BOOLEAN DEFAULT FALSE"))
        if "telegram_balance_alert_level" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN telegram_balance_alert_level INTEGER"))
        if "telegram_tariff_signal_level" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN telegram_tariff_signal_level INTEGER"))
    tariff_columns = {c["name"] for c in inspect(engine).get_columns("client_tariffs")}
    with engine.begin() as conn:
        if "signal1" not in tariff_columns:
            conn.execute(text("ALTER TABLE client_tariffs ADD COLUMN signal1 INTEGER"))
        if "signal2" not in tariff_columns:
            conn.execute(text("ALTER TABLE client_tariffs ADD COLUMN signal2 INTEGER"))
        if "signal3" not in tariff_columns:
            conn.execute(text("ALTER TABLE client_tariffs ADD COLUMN signal3 INTEGER"))

    # На старых БД гарантируем не-null значение для bool-флагов.
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET role = 'admin' WHERE id = 1 AND (role IS NULL OR role = '')"))
        conn.execute(text("UPDATE users SET role = 'client' WHERE id != 1 AND (role IS NULL OR role = '')"))
        conn.execute(text("UPDATE users SET projects_mutation_locked = FALSE WHERE projects_mutation_locked IS NULL"))
        conn.execute(text("UPDATE users SET is_disabled = FALSE WHERE is_disabled IS NULL"))
        conn.execute(text("UPDATE users SET auto_limit_control_enabled = FALSE WHERE auto_limit_control_enabled IS NULL"))
        conn.execute(text("UPDATE users SET telegram_auto_pause_enabled = FALSE WHERE telegram_auto_pause_enabled IS NULL"))
        conn.execute(text("UPDATE users SET unique_project_names_enabled = FALSE WHERE unique_project_names_enabled IS NULL"))


_ensure_user_projects_lock_columns()


def _ensure_client_profile_extra_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "client_profiles" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("client_profiles")}
    with engine.begin() as conn:
        if "internal_client_id" not in columns:
            conn.execute(text("ALTER TABLE client_profiles ADD COLUMN internal_client_id VARCHAR"))
        if "table_url" not in columns:
            conn.execute(text("ALTER TABLE client_profiles ADD COLUMN table_url VARCHAR"))
        if "pixel_table_url" not in columns:
            conn.execute(text("ALTER TABLE client_profiles ADD COLUMN pixel_table_url VARCHAR"))


_ensure_client_profile_extra_columns()


def _ensure_project_unique_name_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "projects" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("projects")}
    with engine.begin() as conn:
        if "client_internal_prefix" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN client_internal_prefix VARCHAR"))
        if "unique_name_applied" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN unique_name_applied BOOLEAN DEFAULT FALSE"))
        conn.execute(text("UPDATE projects SET unique_name_applied = FALSE WHERE unique_name_applied IS NULL"))


_ensure_project_unique_name_columns()


def _ensure_project_provider_leads_grace_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "projects" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("projects")}
    with engine.begin() as conn:
        if "deleted_at" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN deleted_at TIMESTAMP"))
        if "provider_leads_grace_until" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN provider_leads_grace_until TIMESTAMP"))
        if "daily_limit_reached_notified_at" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN daily_limit_reached_notified_at TIMESTAMP"))
        if "daily_limit_reached_notified_limit" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN daily_limit_reached_notified_limit INTEGER"))


_ensure_project_provider_leads_grace_columns()


def _ensure_project_top_column() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "projects" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("projects")}
    with engine.begin() as conn:
        if "is_top" not in columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN is_top BOOLEAN DEFAULT FALSE"))
        conn.execute(text("UPDATE projects SET is_top = FALSE WHERE is_top IS NULL"))


_ensure_project_top_column()


def _ensure_provider_leads_imported_at_index() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "provider_leads" not in tables:
        return

    with engine.begin() as conn:
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_provider_leads_imported_at ON provider_leads (imported_at)")
        )


_ensure_provider_leads_imported_at_index()


def _ensure_provider_leads_pixel_columns_and_indexes() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "provider_leads" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("provider_leads")}
    with engine.begin() as conn:
        if "lead_source" not in columns:
            conn.execute(text("ALTER TABLE provider_leads ADD COLUMN lead_source VARCHAR DEFAULT 'provider'"))
        if "pixel_url" not in columns:
            conn.execute(text("ALTER TABLE provider_leads ADD COLUMN pixel_url VARCHAR"))
        if "client_sheet_exported_at" not in columns:
            conn.execute(text("ALTER TABLE provider_leads ADD COLUMN client_sheet_exported_at TIMESTAMP"))
        conn.execute(text("UPDATE provider_leads SET lead_source = 'provider' WHERE lead_source IS NULL OR lead_source = ''"))

        if engine.dialect.name == "postgresql":
            # Старый индекс/constraint vid был глобально уникальным. Для Пикселя нужен
            # отдельный контур дедупликации, поэтому заменяем его частичными индексами.
            conn.execute(text("ALTER TABLE provider_leads DROP CONSTRAINT IF EXISTS provider_leads_vid_key"))
            conn.execute(text("DROP INDEX IF EXISTS ix_provider_leads_vid"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_provider_leads_vid ON provider_leads (vid)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_provider_leads_lead_source ON provider_leads (lead_source)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_provider_leads_client_sheet_exported_at ON provider_leads (client_sheet_exported_at)"))
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_leads_provider_vid
                    ON provider_leads (vid)
                    WHERE lead_source = 'provider'
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_leads_pixel_vid_phone
                    ON provider_leads (vid, phone)
                    WHERE lead_source = 'pixel'
                    """
                )
            )


_ensure_provider_leads_pixel_columns_and_indexes()


def _ensure_provider_leads_provider_sheet_export_state() -> None:
    """
    Состояние выгрузки в промежуточную provider-таблицу.

    ``provider_sheet_exported_at`` НЕ связан с ``client_sheet_exported_at``
    (это контур Пикселя) и не переиспользует его семантику.

    Стратегия backfill для уже существующей БД:
    колонка добавляется как NULL, поэтому исторические строки формально
    «не выгружены».  Чтобы после деплоя не отправить в Google-таблицу заново
    всю историю, выполняется ОДНОРАЗОВЫЙ безопасный backfill: уже
    выгруженные строки (те, что попадали в lookback-окно старого экспорта)
    получают метку = imported_at.  Идемпотентность обеспечивается тем, что
    backfill трогает только строки с NULL-меткой и выполняется один раз:
    повторный запуск увидит, что незаполненных строк внутри lookback не
    осталось, и будет no-op.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "provider_leads" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("provider_leads")}
    with engine.begin() as conn:
        if "provider_sheet_exported_at" not in columns:
            conn.execute(text("ALTER TABLE provider_leads ADD COLUMN provider_sheet_exported_at TIMESTAMP"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_provider_leads_provider_sheet_exported_at "
                "ON provider_leads (provider_sheet_exported_at)"
            )
        )
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_provider_leads_provider_export_queue "
                    "ON provider_leads (lead_source, provider_sheet_exported_at, imported_at)"
                )
            )
        else:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_provider_leads_provider_export_queue "
                    "ON provider_leads (lead_source, provider_sheet_exported_at)"
                )
            )


_ensure_provider_leads_provider_sheet_export_state()


def _ensure_daily_export_limit_group_tables() -> None:
    """
    Таблицы «Лимита группы проектов на день» + UNIQUE(project_id) в membership.
    """
    models.ProjectDailyExportLimitGroup.__table__.create(bind=engine, checkfirst=True)
    models.ProjectDailyExportLimitGroupProject.__table__.create(bind=engine, checkfirst=True)


_ensure_daily_export_limit_group_tables()


def _ensure_active_project_name_unique_index() -> None:
    """
    PostgreSQL guard against duplicate names among non-deleted projects.
    The webhook resolves provider leads by exact project name, so active
    project names must be globally unique.
    """
    if engine.dialect.name != "postgresql":
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "projects" not in tables:
        return

    duplicate_check_sql = text(
        """
        SELECT name, COUNT(*) AS count
        FROM projects
        WHERE status <> 'Удалён'
        GROUP BY name
        HAVING COUNT(*) > 1
        LIMIT 10
        """
    )
    with engine.begin() as conn:
        duplicates = conn.execute(duplicate_check_sql).fetchall()
        if duplicates:
            logging.getLogger("app").error(
                "Cannot create uq_projects_active_name: duplicate active project names exist: %s",
                [{"name": row[0], "count": row[1]} for row in duplicates],
            )
            return
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_active_name
                ON projects (name)
                WHERE status <> 'Удалён'
                """
            )
        )


_ensure_active_project_name_unique_index()


def get_db():
    db_sess = SessionLocal()
    try:
        yield db_sess
    finally:
        db_sess.close()


def _cleanup_temp_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except Exception:
        logging.getLogger("app").warning("Failed to remove temp export file: %s", path, exc_info=True)


app = FastAPI(title="LK Projects API")
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
@app.middleware("http")
async def access_log(request, call_next):
    start = datetime.now(timezone.utc)
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    finally:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logging.getLogger("app.access").info("%s %s -> %s (%d ms) rid=%s", request.method, request.url.path, getattr(locals().get('response', None), 'status_code', '?'), duration_ms, request_id)
    # добавить request id в ответ
    if 'response' in locals() and response is not None:
        response.headers['X-Request-Id'] = request_id
        return response
    raise


async def _read_webhook_body(request: Request) -> tuple[dict, str]:
    """
    Возвращает (payload, format). Поддерживает JSON и form-data/URL-encoded.
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        if isinstance(payload, dict):
            return payload, "json"
        return {"raw": payload}, "json"

    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return dict(form), "form"

    raw = await request.body()
    return {"raw": raw.decode("utf-8", errors="replace")}, "raw"


def _get_msk_tz():
    tz = now_msk().tzinfo
    return tz or timezone(timedelta(hours=3))


def _parse_provider_time(value: object) -> Optional[datetime]:
    if value is None:
        return None
    try:
        ts = int(str(value).strip())
    except Exception:
        return None
    # Провайдер присылает unix-ts в UTC, прибавляем +3 часа (MSK)
    return datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(hours=3)


def _parse_page_parts(page: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not page:
        return None, None
    parts = page.split("_")
    prov_chanel = parts[0].strip() if parts else None
    prov_source = None
    if len(parts) >= 3:
        prov_source = "_".join(parts[2:]).strip() or None
    return prov_chanel, prov_source


def _extract_phones(raw: object) -> tuple[Optional[str], Optional[List[str]]]:
    if raw is None:
        return None, None
    if isinstance(raw, list):
        cleaned = [str(x).strip() for x in raw if str(x).strip()]
    else:
        cleaned = [str(raw).strip()] if str(raw).strip() else []
    if not cleaned:
        return None, None
    return ", ".join(cleaned), cleaned


_MB_MARKER_RE = re.compile(r"^\[MB\d+\]\s*")


def _provider_prefix_for_code(data_source_code: str) -> str:
    return f"{str(data_source_code or '').strip()}_"


def _is_pixel_collection_source(value: Optional[str]) -> bool:
    return crud.is_pixel_collection_source(value)


def _is_pixel_project(project: Any) -> bool:
    return _is_pixel_collection_source(str(_project_field(project, "collection_source", "") or ""))


def _prepare_pixel_create_item(item: schemas.CreateProjectItem) -> None:
    raw_name = str(item.name or "").strip()
    domain_source = (item.sites or [None])[0] if item.sites else raw_name
    domain = crud.normalize_pixel_domain(domain_source)
    if not raw_name:
        raise HTTPException(status_code=422, detail={"message": "Название проекта не может быть пустым."})
    if not domain:
        raise HTTPException(status_code=422, detail={"message": "Для Пиксель-проекта в названии или sites должен быть домен."})
    item.name = raw_name
    item.tag = str(item.tag or raw_name).strip() or raw_name
    item.dataSourceCode = "UNMAPPED"  # type: ignore[assignment]
    item.sites = [domain]
    item.phones = None
    item.smsSenderName = None


def _client_has_pixel_table_url(db_sess: Session, user_id: int) -> bool:
    profile = db_sess.execute(
        select(models.ClientProfile).where(models.ClientProfile.user_id == int(user_id))
    ).scalar_one_or_none()
    return bool(str(getattr(profile, "pixel_table_url", "") or "").strip())


def _strip_provider_prefix_from_name(name: Optional[str]) -> str:
    raw = str(name or "").strip()
    for code in ("B1", "B2", "B3", "B4"):
        prefix = f"{code}_"
        if raw.startswith(prefix):
            return raw[len(prefix):].strip()
    return raw


def _strip_mb_marker(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    return _MB_MARKER_RE.sub("", raw, count=1).strip()


def _extract_project_display_name(name: Optional[str]) -> str:
    return _strip_mb_marker(_strip_provider_prefix_from_name(name))


def _project_client_internal_prefix(project: models.Project) -> Optional[str]:
    return crud.normalize_client_internal_prefix(getattr(project, "client_internal_prefix", None))


def _build_project_name_with_client_internal_prefix(
    data_source_code: str,
    raw_name: str,
    client_internal_prefix: Optional[str],
) -> str:
    prefix = crud.normalize_client_internal_prefix(client_internal_prefix)
    base_name = _extract_project_display_name(raw_name)
    if prefix and base_name.startswith(prefix):
        base_name = base_name[len(prefix):].strip()
    if not base_name:
        raise HTTPException(status_code=422, detail={"message": "Название проекта не может быть пустым."})
    if not prefix:
        return f"{_provider_prefix_for_code(data_source_code)}{base_name}"
    return f"{_provider_prefix_for_code(data_source_code)}{prefix}{base_name}"


def _project_name_for_client_message(
    name: str,
    data_source_code: str,
    client_internal_prefix: Optional[str],
) -> str:
    prefix = crud.normalize_client_internal_prefix(client_internal_prefix)
    raw_name = str(name or "").strip()
    if not prefix:
        return raw_name
    provider_prefix = _provider_prefix_for_code(data_source_code)
    full_prefix = f"{provider_prefix}{prefix}"
    if raw_name.startswith(full_prefix):
        return f"{provider_prefix}{raw_name[len(full_prefix):].strip()}"
    return raw_name


def _client_internal_prefix_for_user(db_sess: Session, user_id: int) -> Optional[str]:
    profile = db_sess.execute(
        select(models.ClientProfile).where(models.ClientProfile.user_id == int(user_id))
    ).scalar_one_or_none()
    if not profile:
        return None
    return crud.normalize_client_internal_prefix(getattr(profile, "internal_client_id", None))


def _project_name_unavailable_detail() -> dict:
    return {
        "code": "PROJECT_NAME_UNAVAILABLE",
        "message": crud.PROJECT_NAME_UNAVAILABLE_MESSAGE,
    }


def _raise_project_name_unavailable() -> None:
    raise HTTPException(status_code=409, detail=_project_name_unavailable_detail())


def _is_project_name_unique_violation(exc: IntegrityError) -> bool:
    text_value = f"{getattr(exc, 'orig', '')} {exc}"
    return "uq_projects_active_name" in text_value


def _ensure_project_name_available(
    db_sess: Session,
    project_name: str,
    *,
    exclude_project_id: Optional[int] = None,
) -> None:
    if crud.active_project_name_exists(
        db_sess,
        project_name,
        exclude_project_id=exclude_project_id,
    ):
        _raise_project_name_unavailable()


def _required_project_name_prefix(project: models.Project) -> str:
    prefix = _provider_prefix_for_code(str(getattr(project, "data_source_code", "") or ""))
    internal_prefix = _project_client_internal_prefix(project)
    if internal_prefix:
        return f"{prefix}{internal_prefix}"
    if bool(getattr(project, "unique_name_applied", False)):
        return f"{prefix}[MB{int(project.id)}] "
    return prefix


def _validate_create_project_item_name(item: schemas.CreateProjectItem) -> None:
    if _is_pixel_collection_source(item.collectionSource):
        _prepare_pixel_create_item(item)
        return
    expected_prefix = _provider_prefix_for_code(item.dataSourceCode)
    raw_name = str(item.name or "").strip()
    if not raw_name.startswith(expected_prefix):
        raise HTTPException(
            status_code=422,
            detail={"message": f'Название проекта должно начинаться с префикса "{expected_prefix}"'},
        )
    if not _extract_project_display_name(raw_name):
        raise HTTPException(status_code=422, detail={"message": "Название проекта не может быть пустым."})
    item.name = raw_name


def _validate_project_name_update(
    project: models.Project,
    proposed_name: str,
    *,
    allow_client_internal_prefix_restore: bool = False,
) -> str:
    raw_name = str(proposed_name or "").strip()
    if _is_pixel_project(project):
        if not raw_name:
            raise HTTPException(status_code=422, detail={"message": "Название проекта не может быть пустым."})
        current_name = str(_project_field(project, "name", "") or "").strip()
        if raw_name != current_name:
            raise HTTPException(
                status_code=422,
                detail={"message": "Название Пиксель-проекта нельзя изменить. Для нового домена создайте новый проект."},
            )
        return raw_name
    required_prefix = _required_project_name_prefix(project)
    provider_prefix = _provider_prefix_for_code(str(getattr(project, "data_source_code", "") or ""))
    internal_prefix = _project_client_internal_prefix(project)
    if (
        allow_client_internal_prefix_restore
        and internal_prefix
        and raw_name.startswith(provider_prefix)
        and not raw_name.startswith(required_prefix)
    ):
        suffix = raw_name[len(provider_prefix):].strip()
        if suffix and not suffix.startswith(internal_prefix):
            raw_name = f"{required_prefix}{suffix}"
    if not raw_name.startswith(required_prefix):
        if internal_prefix or bool(getattr(project, "unique_name_applied", False)):
            raise HTTPException(
                status_code=422,
                detail={"message": f'Нельзя удалять технический префикс "{required_prefix}" из названия проекта.'},
            )
        raise HTTPException(
            status_code=422,
            detail={"message": f'Название проекта должно начинаться с префикса "{required_prefix}"'},
        )
    if not raw_name[len(required_prefix):].strip():
        raise HTTPException(status_code=422, detail={"message": "Название проекта после префикса не может быть пустым."})
    return raw_name


def _ensure_create_project_names_available(
    db_sess: Session,
    items: List[schemas.CreateProjectItem],
    *,
    user_id: int,
    actor_user_id: int,
    via_impersonation: bool,
) -> None:
    seen: set[str] = set()
    for item in items:
        name = str(item.name or "").strip()
        if name in seen or crud.active_project_name_exists(db_sess, name):
            _record_failed_project_operation(
                user_id=user_id,
                actor_user_id=actor_user_id,
                operation="create",
                project_name=name,
                request_payload=_project_payload_for_history(item),
                error_message=crud.PROJECT_NAME_UNAVAILABLE_MESSAGE,
                error_code="PROJECT_NAME_UNAVAILABLE",
                via_impersonation=via_impersonation,
            )
            _raise_project_name_unavailable()
        seen.add(name)


def _build_unique_project_name(data_source_code: str, project_id: int, raw_name: str) -> str:
    base_name = _extract_project_display_name(raw_name)
    if not base_name:
        raise HTTPException(status_code=422, detail={"message": "Название проекта не может быть пустым."})
    return f'{_provider_prefix_for_code(data_source_code)}[MB{int(project_id)}] {base_name}'


def _build_pending_unique_project_name(item: schemas.CreateProjectItem) -> str:
    base_name = _extract_project_display_name(item.name) or "project"
    return f"{_provider_prefix_for_code(item.dataSourceCode)}[MBPENDING{secrets.token_hex(8)}] {base_name}"


def _build_project_update_from_create_item(item: schemas.CreateProjectItem, *, name: str, tag: Optional[str] = None) -> schemas.ProjectUpdate:
    return schemas.ProjectUpdate(
        name=name,
        tag=tag or name,
        status=item.status,
        dataLimit=item.dataLimit,
        regionMode=item.regionMode,
        regions=item.regions or [],
        sites=item.sites,
        phones=item.phones,
        smsSenderName=item.smsSenderName,
        days=item.days,
    )


def _should_retry_prostats_error(exc: prostats.ProstatsError) -> bool:
    try:
        if int(getattr(exc, "status_code", 0) or 0) >= 500:
            return True
    except Exception:
        pass
    message = str(getattr(exc, "message", "") or "").lower()
    retry_markers = (
        "timeout",
        "timed out",
        "tempor",
        "temporary",
        "temporarily",
        "connection",
        "connect",
        "reset",
        "gateway",
        "bad gateway",
        "service unavailable",
        "too many requests",
        "временно",
        "таймаут",
        "соедин",
        "шлюз",
    )
    return any(marker in message for marker in retry_markers)


def _rename_project_in_prostats_with_retries(
    provider_project_id: str,
    project_row: models.Project,
    payload: schemas.ProjectUpdate,
    *,
    attempts: int = 3,
) -> dict:
    logger = logging.getLogger("app")
    delay_seconds = 1.0
    last_exc: Optional[prostats.ProstatsError] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return prostats.update_project(str(provider_project_id), project_row, payload)
        except prostats.ProstatsError as exc:
            last_exc = exc
            retryable = _should_retry_prostats_error(exc)
            logger.warning(
                "Unique-name rename failed in Prostats: project_id=%s provider_project_id=%s attempt=%s/%s retryable=%s message=%s",
                getattr(project_row, "id", None),
                provider_project_id,
                attempt,
                attempts,
                retryable,
                exc.message,
            )
            if attempt >= attempts or not retryable:
                raise
            time.sleep(delay_seconds)
            delay_seconds *= 2
    if last_exc:
        raise last_exc
    raise prostats.ProstatsError("Не удалось обновить имя проекта в Prostats", status_code=500)


@app.post("/client-errors")
def client_errors(payload: schemas.ClientErrorIn, request: Request):
    rid = getattr(request.state, 'request_id', '-')
    logging.getLogger("app.client").error(
        "frontend_error level=%s url=%s ua=%s msg=%s rid=%s\nstack=%s",
        payload.level,
        payload.url,
        payload.userAgent,
        (payload.message or '')[:500],
        rid,
        (payload.stack or '')[:4000],
    )
    return {"ok": True}


# Разрешенные источники для CORS (для cookie нужен конкретный список, не "*")
cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
for _local_origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
    if _local_origin not in cors_origins:
        cors_origins.append(_local_origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    from .lead_analytics import startup as analytics_startup

    analytics_startup()
    with SessionLocal() as s:
        # Минимальный seed для чистой БД
        crud.seed_notify_state(s, settings["DEBOUNCE_WINDOW_MINUTES"])
        crud.seed_users_from_env(s)

    # Start background notifier thread
    worker = threading.Thread(
        target=notify_worker.run_notifier_loop,
        kwargs={
            "SessionLocal": SessionLocal,
            "window_minutes": settings["DEBOUNCE_WINDOW_MINUTES"],
            "bot_token": "",
            "chat_id": settings["TELEGRAM_CHAT_ID"],
            "sleep_seconds": 60,
        },
        daemon=True,
        name="notify-worker-thread",
    )
    worker.start()

    project_operations_thread = threading.Thread(
        target=project_operations_worker.run_project_operations_worker,
        kwargs={
            "SessionLocal": SessionLocal,
            "processor": _process_project_operation_item,
            "poll_seconds": 5,
            "concurrency": settings["PROJECT_OPERATIONS_CONCURRENCY"],
        },
        daemon=True,
        name="project-operations-worker-thread",
    )
    project_operations_thread.start()

    # Периодический контроль: тарифные сигналы для всех клиентов,
    # автопауза лимитов только при users.auto_limit_control_enabled.
    limit_worker = threading.Thread(
        target=run_limit_control_loop,
        kwargs={
            "SessionLocal": SessionLocal,
            "sleep_seconds": max(30, int(settings["AUTO_LIMIT_CHECK_SECONDS"])),
        },
        daemon=True,
        name="limit-control-thread",
    )
    limit_worker.start()

    pixel_report_worker = threading.Thread(
        target=run_pixel_telegram_reports_loop,
        kwargs={
            "SessionLocal": SessionLocal,
            "sleep_seconds": 60,
        },
        daemon=True,
        name="pixel-telegram-reports-thread",
    )
    pixel_report_worker.start()

    telegram_cleanup_worker = threading.Thread(
        target=run_telegram_notifications_cleanup_loop,
        kwargs={
            "SessionLocal": SessionLocal,
            "sleep_seconds": 24 * 60 * 60,
        },
        daemon=True,
        name="telegram-notifications-cleanup-thread",
    )
    telegram_cleanup_worker.start()

    operator_block_worker = threading.Thread(
        target=run_operator_block_check_daily_loop,
        daemon=True,
        name="operator-block-check-thread",
    )
    operator_block_worker.start()


def run_limit_control_loop(SessionLocal, sleep_seconds: int = 300) -> None:
    while True:
        try:
            with SessionLocal() as s:  # type: Session
                tariff_signal_client_ids = crud.list_client_ids_for_tariff_signal_checks(s)
                for client_id in tariff_signal_client_ids:
                    _run_tariff_signal_check_for_client(s, client_id=int(client_id))

                _run_project_daily_limit_notifications(s)

                client_ids = crud.list_clients_with_auto_limit_control(s)
                for client_id in client_ids:
                    _run_limit_control_for_client(
                        s,
                        client_id=int(client_id),
                        trigger="schedule",
                        check_tariff_signal=False,
                    )
        except Exception:
            logging.getLogger("app").warning("limit-control loop failed", exc_info=True)
        time.sleep(max(30, int(sleep_seconds)))


def run_telegram_notifications_cleanup_loop(SessionLocal, sleep_seconds: int = 86400) -> None:
    while True:
        try:
            with SessionLocal() as s:  # type: Session
                deleted = crud.cleanup_old_telegram_notifications(s)
                if deleted:
                    logging.getLogger("app").info("Cleaned up old Telegram notifications: deleted=%s", deleted)
        except Exception:
            logging.getLogger("app").warning("telegram notifications cleanup loop failed", exc_info=True)
        time.sleep(max(3600, int(sleep_seconds)))


def _env_flag(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


PIXEL_REPORT_FIRST_HOUR_MSK = 6
PIXEL_REPORT_LAST_HOUR_MSK = 19
PIXEL_REPORT_MINUTE_MSK = 15
PIXEL_DAILY_REPORT_HOUR_MSK = 6


def _pixel_db_naive(dt: datetime) -> datetime:
    """Переводит aware-время отчёта в UTC без tzinfo для сравнения с БД."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=None)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _pixel_report_day_bounds(day, tz) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz)
    return _pixel_db_naive(start), _pixel_db_naive(start + timedelta(days=1))


def _due_pixel_hourly_checkpoints(now_local: datetime) -> List[datetime]:
    checkpoints = [
        now_local.replace(
            hour=hour,
            minute=PIXEL_REPORT_MINUTE_MSK,
            second=0,
            microsecond=0,
        )
        for hour in range(PIXEL_REPORT_FIRST_HOUR_MSK, PIXEL_REPORT_LAST_HOUR_MSK + 1)
    ]
    return [checkpoint for checkpoint in checkpoints if now_local >= checkpoint]


def _format_pixel_report_period(start_local: datetime, end_local: datetime) -> str:
    return f"{start_local:%H:%M}-{end_local:%H:%M}"


def _format_pixel_report_tariff(snapshot: dict) -> str:
    tariff_amount = snapshot.get("tariff_amount")
    if tariff_amount is None:
        return "-"
    remaining = int(snapshot.get("remaining") or 0)
    used = int(tariff_amount) - remaining
    return f"{_format_notification_number(used)}/{_format_notification_number(int(tariff_amount))}"


def _pixel_report_chat_id(snapshot: dict) -> str:
    client_chat_id = str(snapshot.get("telegram_chat_id") or "").strip()
    if client_chat_id:
        return client_chat_id
    return str(settings.get("TELEGRAM_CHAT_ID") or "").strip()


def _build_pixel_hourly_report_message(snapshot: dict, *, period_start: datetime, period_end: datetime) -> str:
    client_name = html.escape(str(snapshot.get("client_name") or ""))
    period_label = html.escape(_format_pixel_report_period(period_start, period_end))
    return (
        f"{client_name}\n\n"
        f"Новые идентификации за {period_label} МСК: "
        f"{_format_notification_number(int(snapshot.get('period_count') or 0))}\n"
        f"Всего сегодня: "
        f"{_format_notification_number(int(snapshot.get('total_count') or 0))}\n"
        f"Остаток по тарифу: {_format_notification_number(int(snapshot.get('remaining') or 0))}"
    )


def _build_pixel_daily_final_report_message(snapshot: dict, *, report_date) -> str:
    client_name = html.escape(str(snapshot.get("client_name") or ""))
    text = (
        f"Отчёт за вчера {report_date:%d.%m.%Y}\n\n"
        f"{client_name}\n"
        f"Получено за день: {_format_notification_number(int(snapshot.get('period_count') or 0))}"
    )
    tail_count = int(snapshot.get("tail_count") or 0)
    if tail_count > 0:
        tail_label = f"{PIXEL_REPORT_LAST_HOUR_MSK:02d}:{PIXEL_REPORT_MINUTE_MSK:02d}"
        text += f"\nПосле {tail_label} МСК: {_format_notification_number(tail_count)}"
    text += f"\nОстаток по тарифу: {_format_notification_number(int(snapshot.get('remaining') or 0))}"
    return text


def _queue_pixel_hourly_reports(db_sess: Session, now_local: datetime) -> tuple[int, int, int, int]:
    queued = 0
    checked = 0
    skipped_pending = 0
    pending_leads = 0
    for checkpoint in _due_pixel_hourly_checkpoints(now_local):
        checked += 1
        day_start, _day_end = _pixel_report_day_bounds(checkpoint.date(), checkpoint.tzinfo)
        period_end = _pixel_db_naive(checkpoint)
        if checkpoint.hour == PIXEL_REPORT_FIRST_HOUR_MSK:
            period_start = day_start
            period_start_label = checkpoint.replace(hour=0, minute=0)
        else:
            period_start_local = checkpoint - timedelta(hours=1)
            period_start = _pixel_db_naive(period_start_local)
            period_start_label = period_start_local

        snapshots = crud.list_pixel_telegram_report_snapshots(
            db_sess,
            period_start=period_start,
            period_end=period_end,
            total_start=day_start,
            total_end=period_end,
        )
        for snapshot in snapshots:
            pending_export_count = int(snapshot.get("pending_export_count") or 0)
            if pending_export_count > 0:
                skipped_pending += 1
                pending_leads += pending_export_count
                continue
            if int(snapshot.get("period_count") or 0) <= 0:
                continue
            chat_id = _pixel_report_chat_id(snapshot)
            if not chat_id:
                continue
            row = crud.queue_unique_pixel_telegram_report(
                db_sess,
                kind=crud.PIXEL_TELEGRAM_REPORT_KIND_HOURLY,
                client_id=int(snapshot["client_id"]),
                period_start=period_start,
                period_end=period_end,
                chat_id=chat_id,
                text=_build_pixel_hourly_report_message(
                    snapshot,
                    period_start=period_start_label,
                    period_end=checkpoint,
                ),
                parse_mode="HTML",
                metadata={
                    "kind": crud.PIXEL_TELEGRAM_REPORT_KIND_HOURLY,
                    "client_id": int(snapshot["client_id"]),
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "period_count": int(snapshot.get("period_count") or 0),
                    "total_count": int(snapshot.get("total_count") or 0),
                },
            )
            if row is not None:
                queued += 1
    return queued, checked, skipped_pending, pending_leads


def _queue_pixel_daily_final_reports(db_sess: Session, now_local: datetime) -> tuple[int, int, int]:
    daily_checkpoint = now_local.replace(
        hour=PIXEL_DAILY_REPORT_HOUR_MSK,
        minute=0,
        second=0,
        microsecond=0,
    )
    if now_local < daily_checkpoint:
        return 0, 0, 0

    report_day = (now_local - timedelta(days=1)).date()
    period_start, period_end = _pixel_report_day_bounds(report_day, now_local.tzinfo)
    tail_start = _pixel_db_naive(
        datetime(
            report_day.year,
            report_day.month,
            report_day.day,
            PIXEL_REPORT_LAST_HOUR_MSK,
            PIXEL_REPORT_MINUTE_MSK,
            0,
            tzinfo=now_local.tzinfo,
        )
    )
    snapshots = crud.list_pixel_telegram_report_snapshots(
        db_sess,
        period_start=period_start,
        period_end=period_end,
        tail_start=tail_start,
        tail_end=period_end,
    )
    queued = 0
    skipped_pending = 0
    pending_leads = 0
    for snapshot in snapshots:
        pending_export_count = int(snapshot.get("pending_export_count") or 0)
        if pending_export_count > 0:
            skipped_pending += 1
            pending_leads += pending_export_count
            continue
        if int(snapshot.get("period_count") or 0) <= 0:
            continue
        chat_id = _pixel_report_chat_id(snapshot)
        if not chat_id:
            continue
        row = crud.queue_unique_pixel_telegram_report(
            db_sess,
            kind=crud.PIXEL_TELEGRAM_REPORT_KIND_DAILY_FINAL,
            client_id=int(snapshot["client_id"]),
            period_start=period_start,
            period_end=period_end,
            chat_id=chat_id,
            text=_build_pixel_daily_final_report_message(snapshot, report_date=report_day),
            parse_mode="HTML",
            metadata={
                "kind": crud.PIXEL_TELEGRAM_REPORT_KIND_DAILY_FINAL,
                "client_id": int(snapshot["client_id"]),
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "period_count": int(snapshot.get("period_count") or 0),
                "tail_count": int(snapshot.get("tail_count") or 0),
            },
        )
        if row is not None:
            queued += 1
    return queued, skipped_pending, pending_leads


def run_pixel_telegram_reports_loop(SessionLocal, sleep_seconds: int = 60) -> None:
    while True:
        try:
            logger = logging.getLogger("app")
            if _env_flag("NOTIFICATIONS_TELEGRAM_ENABLED", default=True):
                now_local = datetime.now(_get_msk_tz())
                with SessionLocal() as s:  # type: Session
                    (
                        daily_queued,
                        daily_skipped_pending,
                        daily_pending_leads,
                    ) = _queue_pixel_daily_final_reports(s, now_local)
                    (
                        hourly_queued,
                        hourly_checked,
                        hourly_skipped_pending,
                        hourly_pending_leads,
                    ) = _queue_pixel_hourly_reports(s, now_local)
                logger.info(
                    "Pixel Telegram reports heartbeat: enabled=true now=%s "
                    "daily_queued=%s daily_skipped_pending=%s daily_pending_leads=%s "
                    "hourly_queued=%s hourly_checkpoints_checked=%s "
                    "hourly_skipped_pending=%s hourly_pending_leads=%s",
                    now_local.isoformat(),
                    daily_queued,
                    daily_skipped_pending,
                    daily_pending_leads,
                    hourly_queued,
                    hourly_checked,
                    hourly_skipped_pending,
                    hourly_pending_leads,
                )
            else:
                logger.info("Pixel Telegram reports heartbeat: enabled=false")
        except Exception:
            logging.getLogger("app").warning("pixel telegram reports loop failed", exc_info=True)
        time.sleep(max(30, int(sleep_seconds)))


def _seconds_until_next_operator_block_check() -> int:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo("Europe/Moscow")
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))
    now = datetime.now(tz)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def run_operator_block_check_daily_loop() -> None:
    logger = logging.getLogger("app")
    while True:
        time.sleep(_seconds_until_next_operator_block_check())
        try:
            result = _run_operator_block_check(trigger="schedule")
            logger.info(
                "operator block check completed: checked=%s blocked=%s skipped=%s errors=%s",
                result.checked,
                result.blocked,
                result.skipped,
                len(result.errors),
            )
        except Exception:
            logger.warning("operator block check loop failed", exc_info=True)


def _run_tariff_signal_check_for_client(db_sess: Session, client_id: int) -> Optional[int]:
    user = db_sess.get(models.User, int(client_id))
    if not user:
        return None

    user_snapshot = _snapshot_limit_control_user(user, db_sess=db_sess)
    remaining = crud.get_client_remaining_numbers(db_sess, client_id=int(client_id))

    # Закрываем текущую транзакцию перед постановкой Telegram-сообщения в outbox,
    # чтобы не держать соединение из пула БД во время возможных побочных операций.
    db_sess.rollback()

    return _sync_client_tariff_signal_alert(
        db_sess,
        user_snapshot=user_snapshot,
        remaining=remaining,
    )


def _run_limit_control_for_client(
    db_sess: Session,
    client_id: int,
    trigger: str,
    *,
    check_tariff_signal: bool = True,
) -> dict:
    user = db_sess.get(models.User, int(client_id))
    if not user:
        return {"paused": 0, "errors": [], "skipped": 0}

    # Массовая provider-операция владеет изменениями проектов клиента.
    # Тарифный сигнал остаётся независимым, а автопауза будет отложена.
    active_project_operation = crud.get_active_project_operation(db_sess, int(client_id))

    user_snapshot = _snapshot_limit_control_user(user, db_sess=db_sess)
    remaining = crud.get_client_remaining_numbers(db_sess, client_id=int(client_id))
    active_projects = db_sess.execute(
        select(models.Project).where(
            models.Project.user_id == int(client_id),
            models.Project.status == "Активен",
            or_(
                models.Project.provider_project_id.is_not(None),
                models.Project.collection_source == "СМС",
            ),
        )
    ).scalars().all()
    active_project_snapshots = [_snapshot_limit_control_project(project) for project in active_projects]
    active_sum = sum(int(p.data_limit or 0) for p in active_projects)

    # Закрываем текущую транзакцию перед сетевыми вызовами, чтобы не держать
    # соединение из пула БД во время ожидания Telegram/Prostats.
    db_sess.rollback()

    if check_tariff_signal:
        _sync_client_tariff_signal_alert(
            db_sess,
            user_snapshot=user_snapshot,
            remaining=remaining,
        )

    if active_project_operation is not None:
        return {"paused": 0, "errors": [], "skipped": 0, "deferred": True}

    if not bool(user_snapshot["auto_limit_control_enabled"]):
        return {"paused": 0, "errors": [], "skipped": 0}
    if active_sum <= remaining:
        return {"paused": 0, "errors": [], "skipped": 0}

    pause_candidates = sorted(
        active_project_snapshots,
        key=lambda p: (int(p["data_limit"] or 0), int(p["id"] or 0)),
        reverse=True,
    )
    paused_projects: List[dict] = []
    skipped = 0
    errors: List[str] = []

    for project in pause_candidates:
        if active_sum <= remaining:
            break
        skip_provider_sync = _should_skip_provider_sync_for_status_change(project, "На паузе")
        if not skip_provider_sync and not project["provider_project_id"]:
            skipped += 1
            continue
        try:
            if not skip_provider_sync:
                project_obj = SimpleNamespace(**project)
                prostats.update_project_status(str(project["provider_project_id"]), project_obj, "На паузе")
            with SessionLocal() as update_sess:  # type: Session
                ok = crud.update_project_status_with_audit(
                    update_sess,
                    project_id=int(project["id"]),
                    status="На паузе",
                    event_user_id=int(client_id),
                    actor_user_id=None,
                    audit_reason=(
                        f"Автопауза по лимитам ({trigger}): сумма активных лимитов была {active_sum}, остаток {remaining}."
                    ),
                )
            if ok:
                paused_projects.append(project)
                active_sum -= int(project["data_limit"] or 0)
        except prostats.ProstatsError as exc:
            errors.append(f'Проект {project["id"]} "{project["name"]}": {exc.message}')

    if paused_projects:
        with SessionLocal() as finalize_sess:  # type: Session
            crud.schedule_debounce(finalize_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
        _notify_auto_limit_pause(
            user_snapshot=user_snapshot,
            remaining=remaining,
            sum_before=sum(int(p["data_limit"] or 0) for p in active_project_snapshots),
            paused_projects=paused_projects,
            trigger=trigger,
            errors=errors,
        )
    return {"paused": len(paused_projects), "errors": errors, "skipped": skipped}


def _snapshot_limit_control_user(user: models.User, db_sess: Optional[Session] = None) -> dict:
    client_name = ""
    if db_sess is not None and getattr(user, "id", None):
        profile = db_sess.execute(
            select(models.ClientProfile).where(models.ClientProfile.user_id == int(user.id))
        ).scalar_one_or_none()
        if profile:
            client_name = str(getattr(profile, "name", "") or "").strip()
    if not client_name:
        client_name = str(getattr(user, "display_name", "") or "").strip()
    client_display_name = client_name
    if not client_name:
        client_name = str(getattr(user, "login", "") or "").strip()
    return {
        "id": int(getattr(user, "id", 0) or 0),
        "login": str(getattr(user, "login", "") or ""),
        "client_name": client_name,
        "client_display_name": client_display_name,
        "auto_limit_control_enabled": bool(getattr(user, "auto_limit_control_enabled", False)),
        "telegram_notifications_chat_id": str(getattr(user, "telegram_notifications_chat_id", "") or "").strip(),
        "telegram_auto_pause_enabled": bool(getattr(user, "telegram_auto_pause_enabled", False)),
        "telegram_tariff_signal_level": getattr(user, "telegram_tariff_signal_level", None),
    }


def _snapshot_limit_control_project(project: models.Project) -> dict:
    return {
        "id": int(getattr(project, "id", 0) or 0),
        "user_id": int(getattr(project, "user_id", 0) or 0) or None,
        "name": str(getattr(project, "name", "") or ""),
        "tag": str(getattr(project, "tag", "") or ""),
        "status": str(getattr(project, "status", "") or ""),
        "provider_project_id": str(getattr(project, "provider_project_id", "") or "").strip() or None,
        "collection_source": str(getattr(project, "collection_source", "") or ""),
        "data_source_code": str(getattr(project, "data_source_code", "") or ""),
        "data_limit": int(getattr(project, "data_limit", 0) or 0),
        "region_mode": getattr(project, "region_mode", None) or "include",
        "regions": list(getattr(project, "regions", None) or []),
        "sites": list(getattr(project, "sites", None) or []),
        "phones": list(getattr(project, "phones", None) or []),
        "sms_sender_name": getattr(project, "sms_sender_name", None),
        "days_received": getattr(project, "days_received", None),
        "unique_name_applied": bool(getattr(project, "unique_name_applied", False)),
    }


def _project_snapshot_for_prostats(project: models.Project) -> SimpleNamespace:
    return SimpleNamespace(**_snapshot_limit_control_project(project))


def _project_days_for_operation(project: models.Project) -> List[schemas.Day]:
    mapping = {"Пн": "Пн", "Вт": "Вт", "Ср": "Ср", "Чт": "Чт", "Пт": "Пт", "Сб": "Сб", "Вс": "Вс"}
    days: List[schemas.Day] = []
    for raw in str(getattr(project, "days_received", "") or "").split():
        normalized = raw.strip().rstrip(".")
        if normalized in mapping:
            days.append(mapping[normalized])  # type: ignore[arg-type]
    return days or ["Вт", "Ср", "Чт", "Пт", "Сб"]


def _project_update_data_for_operation(project: models.Project, patch: dict, *, admin: bool) -> dict:
    current_status = "Активен" if project.status == crud.PROJECT_STATUS_OPERATOR_BLOCK else project.status
    data = {
        "name": project.name,
        "tag": project.tag or project.name,
        "status": current_status,
        "dataLimit": int(project.data_limit or 0),
        "regionMode": project.region_mode or "include",
        "regions": list(project.regions or []),
        "sites": list(project.sites or []),
        "phones": list(project.phones or []),
        "smsSenderName": project.sms_sender_name,
        "days": _project_days_for_operation(project),
    }
    if admin:
        data["deliveryStatus"] = project.delivery_status
    data.update(dict(patch or {}))
    schema_type = schemas.AdminProjectUpdate if admin else schemas.ProjectUpdate
    validated = schema_type(**data)
    return validated.dict()


def _project_operation_error_result(exc: prostats.ProstatsError) -> project_operations.ProjectOperationItemResult:
    technical_error = (
        f"Prostats: {exc.message} "
        f"(HTTP {exc.status_code}, kind={exc.kind}, ambiguous={str(exc.ambiguous).lower()})"
    )
    if exc.retryable:
        return project_operations.ProjectOperationItemResult.waiting_retry(error=technical_error)
    return project_operations.ProjectOperationItemResult.needs_attention(error=technical_error)


PROJECT_STATUS_READBACK_ATTEMPTS = 3
PROJECT_STATUS_READBACK_DELAY_SECONDS = 0.25


def _confirm_project_status_after_write(
    provider_project_id: str,
    desired_status: schemas.ProjectMutableStatus,
) -> dict:
    """Коротко подтвердить статус после успешной записи у поставщика.

    Prostats может вернуть успешный ответ на запись раньше, чем новый статус
    станет виден чтению. Ограниченные повторные GET позволяют пережить такую
    короткую задержку, но не превращают worker в долгую блокирующую проверку.
    Ошибки чтения намеренно проходят к вызывающему коду как
    :class:`ProstatsError`, чтобы обычный retry-контур обработал timeout или
    неоднозначный транспортный результат.
    """
    last_reconciliation: Optional[dict] = None
    attempts = max(1, int(PROJECT_STATUS_READBACK_ATTEMPTS))
    for attempt in range(attempts):
        last_reconciliation = prostats.reconcile_project_status(
            provider_project_id,
            desired_status,
        )
        if last_reconciliation.get("already_applied"):
            return last_reconciliation
        if attempt + 1 < attempts:
            time.sleep(max(0.0, float(PROJECT_STATUS_READBACK_DELAY_SECONDS)))
    return last_reconciliation or {
        "provider_id": str(provider_project_id),
        "desired_status": desired_status,
        "already_applied": False,
    }


def _prostats_delete_already_applied(exc: prostats.ProstatsError) -> bool:
    message = str(exc.message or "").lower()
    return exc.status_code == 404 or "не найден" in message or "not found" in message


def _process_project_operation_item(
    item: project_operations.ProjectOperationItemContext,
) -> project_operations.ProjectOperationItemResult:
    """Выполнить один сохранённый item без удержания request-scoped DB-сессии."""
    payload_snapshot = dict(item.payload_snapshot or {})
    action = str(payload_snapshot.get("action") or "").strip()
    actor_user_id = int(payload_snapshot.get("actorUserId") or item.client_id)
    admin_update = bool(payload_snapshot.get("adminUpdate"))

    with SessionLocal() as read_sess:  # type: Session
        project = read_sess.get(models.Project, int(item.project_id))
        if not project or int(project.user_id or 0) != int(item.client_id):
            return project_operations.ProjectOperationItemResult.needs_attention(error="Проект не найден.")
        if _is_pixel_project(project):
            return project_operations.ProjectOperationItemResult.needs_attention(
                error="Pixel-проект не поддерживается этой операцией."
            )
        project_snapshot = _project_snapshot_for_prostats(project)
        local_status = str(project.status or "")
        provider_project_id = str(project.provider_project_id or "").strip()

    if action == "status":
        desired_status = str(payload_snapshot.get("status") or "")
        if desired_status not in {"Активен", "На паузе"}:
            return project_operations.ProjectOperationItemResult.needs_attention(error="Недопустимый статус проекта.")
        if desired_status == "Активен" and local_status != "Активен":
            with SessionLocal() as limit_sess:  # type: Session
                allowed, reason = crud.can_activate_project_under_limit_control(limit_sess, project_id=item.project_id)
            if not allowed:
                return project_operations.ProjectOperationItemResult.needs_attention(
                    error=reason or "Включение проекта ограничено настройками клиента."
                )
        skip_provider = _should_skip_provider_sync_for_status_change(project_snapshot, desired_status)
        if not skip_provider and not provider_project_id:
            return project_operations.ProjectOperationItemResult.needs_attention(
                error="Проект не связан с сервисом обработки данных."
            )
        try:
            if not skip_provider:
                reconciliation = prostats.reconcile_project_status(provider_project_id, desired_status)
                if not reconciliation.get("already_applied"):
                    prostats.update_project_status(provider_project_id, project_snapshot, desired_status)  # type: ignore[arg-type]
                    confirmation = _confirm_project_status_after_write(
                        provider_project_id,
                        desired_status,  # type: ignore[arg-type]
                    )
                    if not confirmation.get("already_applied"):
                        logging.getLogger("app").warning(
                            "Provider status readback mismatch: operation_id=%s project_id=%s provider_project_id=%s desired_status=%s current_status=%s",
                            item.operation_id,
                            item.project_id,
                            provider_project_id,
                            desired_status,
                            confirmation.get("current_status"),
                        )
                        return project_operations.ProjectOperationItemResult.waiting_retry(
                            error=(
                                "Prostats: статус проекта пока не подтверждён "
                                f"(ожидался {desired_status})."
                            ),
                        )
        except prostats.ProstatsError as exc:
            return _project_operation_error_result(exc)

        with SessionLocal() as write_sess:  # type: Session
            ok = crud.admin_update_project_status_only(
                write_sess,
                project_id=item.project_id,
                status=desired_status,  # type: ignore[arg-type]
                admin_user_id=actor_user_id,
            )
            if not ok:
                return project_operations.ProjectOperationItemResult.needs_attention(error="Проект не найден.")
        collection_action = str(payload_snapshot.get("collectionAction") or "")
        if collection_action == "pause":
            with SessionLocal() as snapshot_sess:  # type: Session
                crud.admin_add_pause_snapshot_project(
                    snapshot_sess,
                    client_id=item.client_id,
                    project_id=item.project_id,
                    admin_user_id=actor_user_id,
                )
        elif collection_action == "resume":
            with SessionLocal() as snapshot_sess:  # type: Session
                remaining = crud.admin_remove_pause_snapshot_project(
                    snapshot_sess,
                    client_id=item.client_id,
                    project_id=item.project_id,
                    admin_user_id=actor_user_id,
                )
                if remaining is None:
                    crud.admin_set_client_projects_mutation_lock(
                        snapshot_sess,
                        client_id=item.client_id,
                        locked=False,
                        admin_user_id=actor_user_id,
                    )
        _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
        return project_operations.ProjectOperationItemResult.completed(
            {"status": desired_status, "providerConfirmed": not skip_provider}
        )

    if action == "delete":
        if local_status == "Удалён":
            return project_operations.ProjectOperationItemResult.completed({"status": "Удалён"})
        if not provider_project_id:
            return project_operations.ProjectOperationItemResult.needs_attention(
                error="Проект не связан с сервисом обработки данных."
            )
        try:
            prostats.delete_project(provider_project_id, project_snapshot)
        except prostats.ProstatsError as exc:
            if not _prostats_delete_already_applied(exc):
                return _project_operation_error_result(exc)
        with SessionLocal() as write_sess:  # type: Session
            ok = (
                crud.admin_delete_project(write_sess, item.project_id, admin_user_id=actor_user_id)
                if admin_update
                else crud.delete_project(
                    write_sess,
                    item.project_id,
                    user_id=item.client_id,
                    actor_user_id=actor_user_id,
                    via_impersonation=actor_user_id != item.client_id,
                )
            )
            if not ok:
                return project_operations.ProjectOperationItemResult.needs_attention(error="Проект не найден.")
        _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
        return project_operations.ProjectOperationItemResult.completed({"status": "Удалён"})

    if action != "update":
        return project_operations.ProjectOperationItemResult.needs_attention(error="Неизвестный тип операции.")

    try:
        update_data = dict(payload_snapshot.get("update") or {})
        update_payload = (
            schemas.AdminProjectUpdate(**update_data)
            if admin_update
            else schemas.ProjectUpdate(**update_data)
        )
    except ValidationError as exc:
        return project_operations.ProjectOperationItemResult.needs_attention(error=str(exc)[:1000])

    if update_payload.status == "Активен" and local_status != "Активен":
        with SessionLocal() as limit_sess:  # type: Session
            allowed, reason = crud.can_activate_project_under_limit_control(
                limit_sess,
                project_id=item.project_id,
                projected_data_limit=int(update_payload.dataLimit),
            )
        if not allowed:
            return project_operations.ProjectOperationItemResult.needs_attention(
                error=reason or "Включение проекта ограничено настройками клиента."
            )

    skip_provider = _should_skip_provider_sync_for_status_change(project_snapshot, update_payload.status)
    if not skip_provider and not provider_project_id:
        return project_operations.ProjectOperationItemResult.needs_attention(
            error="Проект не связан с сервисом обработки данных."
        )
    try:
        if not skip_provider:
            result = prostats.update_project(provider_project_id, project_snapshot, update_payload)
            missing_items = list(result.get("missing_items") or [])
            target_type = result.get("target_type")
            if missing_items and target_type == "hosts":
                update_payload.sites = [value for value in (update_payload.sites or []) if value not in missing_items]
            elif missing_items and target_type == "calls":
                update_payload.phones = [value for value in (update_payload.phones or []) if value not in missing_items]
    except prostats.ProstatsError as exc:
        return _project_operation_error_result(exc)

    with SessionLocal() as write_sess:  # type: Session
        try:
            updated = (
                crud.admin_update_project(
                    write_sess,
                    item.project_id,
                    update_payload,  # type: ignore[arg-type]
                    admin_user_id=actor_user_id,
                )
                if admin_update
                else crud.update_project(
                    write_sess,
                    item.project_id,
                    update_payload,  # type: ignore[arg-type]
                    user_id=item.client_id,
                    actor_user_id=actor_user_id,
                    via_impersonation=actor_user_id != item.client_id,
                )
            )
        except IntegrityError as exc:
            write_sess.rollback()
            return project_operations.ProjectOperationItemResult.needs_attention(error=str(exc)[:1000])
        if not updated:
            return project_operations.ProjectOperationItemResult.needs_attention(error="Проект не найден.")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    try:
        _run_limit_control_for_client_in_new_session(item.client_id, trigger="project_operation_update")
    except Exception:
        logging.getLogger("app").warning(
            "Failed to run limit control after project operation: operation_id=%s project_id=%s",
            item.operation_id,
            item.project_id,
            exc_info=True,
        )
    if update_payload.status != "Удалён" and project_snapshot.collection_source == "СМС":
        _queue_sms_project_notification(
            project_id=int(item.project_id),
            action="update",
            actor_user_id=actor_user_id,
        )
    return project_operations.ProjectOperationItemResult.completed({"status": update_payload.status})


def _project_field(project: Any, key: str, default: Any = None) -> Any:
    if isinstance(project, dict):
        return project.get(key, default)
    return getattr(project, key, default)


def _should_skip_provider_sync_for_status_change(project: Any, next_status: str) -> bool:
    if _is_pixel_project(project):
        return True
    # Для SMS-проектов обновление и ручное переключение статуса живут только локально:
    # провайдер не поддерживает редактирование таких проектов.
    return next_status != "Удалён" and str(_project_field(project, "collection_source", "") or "").strip() == "СМС"


def _find_duplicates_with_new_session(
    items: List[str],
    target_type: str,
    *,
    user_id: int,
    provider_project_id: Optional[str],
    exclude_project_id: Optional[int] = None,
) -> dict[str, List[str]]:
    with SessionLocal() as s:  # type: Session
        return crud.find_duplicates_in_projects(
            s,
            items,
            target_type,
            user_id=user_id,
            provider_project_id=provider_project_id,
            exclude_project_id=exclude_project_id,
        )


def _build_admin_duplicate_diagnostics_with_new_session(
    items: List[str],
    target_type: str,
    *,
    reason: str,
    exclude_project_id: Optional[int] = None,
    mark_external_provider_only: bool = False,
    summary: Optional[str] = None,
) -> Optional[dict]:
    with SessionLocal() as s:  # type: Session
        return crud.build_project_duplicate_diagnostics(
            s,
            items,
            target_type,
            reason=reason,
            exclude_project_id=exclude_project_id,
            mark_external_provider_only=mark_external_provider_only,
            summary=summary,
        )


def _run_limit_control_for_client_in_new_session(client_id: int, trigger: str) -> dict:
    with SessionLocal() as s:  # type: Session
        return _run_limit_control_for_client(s, client_id=client_id, trigger=trigger)


def _schedule_debounce_in_new_session(minutes: int) -> None:
    with SessionLocal() as s:  # type: Session
        crud.schedule_debounce(s, minutes=minutes)


TARIFF_ZERO_SIGNAL_LEVEL = 4


def _normalize_tariff_signal_level(value: Any) -> int:
    try:
        return max(0, min(TARIFF_ZERO_SIGNAL_LEVEL, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _resolve_tariff_signal_level(remaining: int, tariff: Optional[schemas.ClientTariffOut]) -> Optional[int]:
    if not tariff:
        return None
    if int(remaining) <= 0:
        return TARIFF_ZERO_SIGNAL_LEVEL
    if tariff.signal1 is None or tariff.signal2 is None:
        return None
    signal1 = int(tariff.signal1)
    signal2 = int(tariff.signal2)
    signal3 = int(tariff.signal3) if tariff.signal3 is not None else None
    if signal2 <= 0 or signal1 <= signal2 or signal1 >= int(tariff.currentAmount):
        return None
    if signal3 is not None and (signal3 <= 0 or signal2 <= signal3):
        return None
    if signal3 is not None and remaining <= signal3:
        return 3
    if remaining <= signal2:
        return 2
    if remaining <= signal1:
        return 1
    return 0


def _set_client_tariff_signal_level(client_id: int, level: int) -> None:
    with SessionLocal() as s:  # type: Session
        user = s.get(models.User, int(client_id))
        if not user:
            return
        user.telegram_tariff_signal_level = _normalize_tariff_signal_level(level)
        s.add(user)
        s.commit()


def _build_client_tariff_signal_message(
    remaining: int,
    signal_level: int,
    usage_last_7_days: int,
    client_name: str = "",
) -> str:
    builders = {
        1: _build_client_tariff_signal_1_message,
        2: _build_client_tariff_signal_2_message,
        3: _build_client_tariff_signal_3_message,
        TARIFF_ZERO_SIGNAL_LEVEL: _build_client_tariff_zero_message,
    }
    builder = builders.get(signal_level)
    if not builder:
        return ""
    return builder(remaining=remaining, usage_last_7_days=usage_last_7_days, client_name=client_name)


def _format_notification_number(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def _format_average_days_remaining(remaining: int, usage_last_7_days: int) -> str:
    if int(usage_last_7_days) <= 0:
        return "невозможно рассчитать по текущей активности."
    days = max(0, int(int(remaining) / (int(usage_last_7_days) / 7)))
    last_two = days % 100
    last_one = days % 10
    if 11 <= last_two <= 14:
        unit = "дней"
    elif last_one == 1:
        unit = "день"
    elif 2 <= last_one <= 4:
        unit = "дня"
    else:
        unit = "дней"
    return f"{days} {unit}."


def _format_client_tariff_signal_client_line(client_name: str) -> str:
    normalized = str(client_name or "").strip()
    return f"{html.escape(normalized)}\n\n" if normalized else ""


def _build_client_tariff_signal_1_message(*, remaining: int, usage_last_7_days: int, client_name: str = "") -> str:
    return (
        "<b>Уведомление: в ближайшее время тариф закончится</b>\n\n"
        f"{_format_client_tariff_signal_client_line(client_name)}"
        f"По вашему тарифу осталось {_format_notification_number(remaining)} идентификаций.\n\n"
        f"В среднем хватит на: {_format_average_days_remaining(remaining, usage_last_7_days)}\n\n"
        "Рекомендуем заранее запланировать продление, чтобы работа проектов продолжалась без перерывов."
    )


def _build_client_tariff_signal_2_message(*, remaining: int, usage_last_7_days: int, client_name: str = "") -> str:
    return (
        "<b>Уведомление: требуется продление тарифа</b>\n\n"
        f"{_format_client_tariff_signal_client_line(client_name)}"
        f"По вашему тарифу осталось {_format_notification_number(remaining)} идентификаций.\n\n"
        f"В среднем хватит на: {_format_average_days_remaining(remaining, usage_last_7_days)}\n\n"
        "Остаток почти исчерпан. Рекомендуем оперативно согласовать новый тариф, "
        "чтобы проекты продолжили работу без перерыва."
    )


def _build_client_tariff_signal_3_message(*, remaining: int, usage_last_7_days: int, client_name: str = "") -> str:
    return (
        "<b>Уведомление: требуется продление тарифа</b>\n\n"
        f"{_format_client_tariff_signal_client_line(client_name)}"
        f"По вашему тарифу осталось {_format_notification_number(remaining)} идентификаций.\n\n"
        f"В среднем хватит на: {_format_average_days_remaining(remaining, usage_last_7_days)}\n\n"
        "Остаток почти исчерпан. Рекомендуем оперативно согласовать новый тариф, "
        "чтобы проекты продолжили работу без перерыва."
    )


def _build_client_tariff_zero_message(*, remaining: int, usage_last_7_days: int, client_name: str = "") -> str:
    _ = usage_last_7_days
    return (
        "<b>Уведомление: тариф закончился</b>\n\n"
        f"{_format_client_tariff_signal_client_line(client_name)}"
        f"По вашему тарифу осталось {_format_notification_number(remaining)} идентификаций.\n\n"
        "Работа проектов может быть остановлена. Рекомендуем оперативно продлить тариф."
    )


def _build_admin_tariff_signal_message(user_snapshot: dict, remaining: int, signal_level: int) -> str:
    level_labels = {
        1: "предупреждение",
        2: "критический",
        3: "критический повторный",
        TARIFF_ZERO_SIGNAL_LEVEL: "тариф закончился",
    }
    return (
        "<b>[ЛК | Остаток клиента]</b>\n\n"
        f"Клиент: {html.escape(str(user_snapshot.get('client_name', '') or ''))}\n"
        f"ID клиента: {html.escape(str(user_snapshot.get('id', '') or ''))}\n"
        f"Текущий остаток: {_format_notification_number(remaining)} идентификаций\n"
        f"Уровень уведомления: {level_labels.get(signal_level, 'неизвестный')}\n\n"
        "Персональная Telegram-группа клиента не настроена."
    )


def _sync_client_tariff_signal_alert(
    db_sess: Session,
    user_snapshot: dict,
    remaining: int,
) -> Optional[int]:
    latest_tariff = crud.get_latest_client_tariff(db_sess, client_id=int(user_snapshot["id"]))
    next_level = _resolve_tariff_signal_level(remaining, latest_tariff)
    saved_level = _normalize_tariff_signal_level(user_snapshot.get("telegram_tariff_signal_level"))

    if next_level is None:
        if saved_level != 0:
            _set_client_tariff_signal_level(int(user_snapshot["id"]), 0)
        return None
    if next_level == saved_level:
        return next_level
    if next_level < saved_level:
        _set_client_tariff_signal_level(int(user_snapshot["id"]), next_level)
        return next_level

    if next_level == TARIFF_ZERO_SIGNAL_LEVEL:
        personal_chat_id = _get_client_configured_telegram_chat_id(user_snapshot)
    else:
        personal_chat_id = _get_client_personal_telegram_chat_id(user_snapshot)
    chat_id = _resolve_notification_chat_id(bool(personal_chat_id), personal_chat_id)
    if not chat_id or not latest_tariff:
        return None

    if personal_chat_id:
        usage_last_7_days = crud.get_client_leads_usage_for_last_days(
            db_sess,
            client_id=int(user_snapshot["id"]),
            days=7,
        )
        text = _build_client_tariff_signal_message(
            remaining=remaining,
            signal_level=next_level,
            usage_last_7_days=usage_last_7_days,
            client_name=str(user_snapshot.get("client_display_name", "") or ""),
        )
    else:
        text = _build_admin_tariff_signal_message(
            user_snapshot=user_snapshot,
            remaining=remaining,
            signal_level=next_level,
        )
    result = notifications.send_system_notification(
        db_sess=db_sess,
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        metadata={"client_id": int(user_snapshot["id"]), "signal_level": next_level},
    )
    if not result.delivered:
        if result.reason != "telegram_disabled":
            logging.getLogger("app").warning(
                "Failed to send tariff signal alert for client_id=%s level=%s reason=%s",
                user_snapshot.get("id"),
                next_level,
                result.reason,
            )
        return None
    _set_client_tariff_signal_level(int(user_snapshot["id"]), next_level)
    return next_level


def _notify_auto_limit_pause(
    user_snapshot: dict,
    remaining: int,
    sum_before: int,
    paused_projects: List[dict],
    trigger: str,
    errors: List[str],
) -> None:
    # Автопауза остается техническим событием для общего админского чата.
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat_id:
        return
    paused_lines = [
        f'- {p["name"]} (id: {p["id"]}, лимит: {int(p["data_limit"] or 0)})'
        for p in paused_projects
    ]
    details = "\n".join(paused_lines)
    text = (
        "<b>[ЛК | Автопауза по лимитам]</b>\n"
        f'Клиент: <code>{html.escape(str(user_snapshot["login"]))}</code> (id={user_snapshot["id"]})\n'
        f"Триггер: <code>{trigger}</code>\n"
        f"Сумма активных лимитов: <b>{sum_before}</b>\n"
        f"Остаток клиента: <b>{remaining}</b>\n\n"
        "Отключены проекты:\n"
        f"{details}\n\n"
        "Чтобы включить обратно: уменьшите лимиты активных проектов и/или пополните баланс, "
        "после этого включите проекты вручную."
    )
    if errors:
        text += "\n\nОшибки:\n" + "\n".join(errors[:5])
    with SessionLocal() as notify_sess:  # type: Session
        result = notifications.send_system_notification(
            db_sess=notify_sess,
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            metadata={"client_id": int(user_snapshot["id"]), "trigger": trigger},
        )
    if not result.delivered and result.reason != "telegram_disabled":
        logging.getLogger("app").warning(
            "Failed to send auto limit pause notification for client_id=%s reason=%s",
            user_snapshot.get("id"),
            result.reason,
        )


def _get_client_telegram_chat_id_for_notifications(user: models.User) -> str:
    """
    Определяет, куда отправлять системные Telegram-уведомления по клиенту.

    Поведение специально безопасное:
    - если у клиента включён персональный Telegram-маршрут и задан chat id,
      отправляем туда;
    - иначе используем общий TELEGRAM_CHAT_ID из env, чтобы не потерять уведомление.
    """
    client_chat_id = _get_client_personal_telegram_chat_id(user)
    return _resolve_notification_chat_id(bool(client_chat_id), client_chat_id)


def _get_client_configured_telegram_chat_id(user: Any) -> str:
    if isinstance(user, dict):
        return str(user.get("telegram_notifications_chat_id", "") or "").strip()
    return str(getattr(user, "telegram_notifications_chat_id", "") or "").strip()


def _get_client_personal_telegram_chat_id(user: Any) -> str:
    if isinstance(user, dict):
        use_client_route = bool(user.get("telegram_auto_pause_enabled", False))
        client_chat_id = str(user.get("telegram_notifications_chat_id", "") or "").strip()
    else:
        use_client_route = bool(getattr(user, "telegram_auto_pause_enabled", False))
        client_chat_id = str(getattr(user, "telegram_notifications_chat_id", "") or "").strip()
    return client_chat_id if use_client_route and client_chat_id else ""


def _resolve_notification_chat_id(use_client_route: bool, client_chat_id: str) -> str:
    if use_client_route and client_chat_id:
        return client_chat_id
    return str(settings.get("TELEGRAM_CHAT_ID") or "").strip()


def _build_project_daily_limit_reached_message(project: dict) -> str:
    project_name = _project_name_for_display(str(project.get("name") or ""))
    source = _source_code_for_display(str(project.get("data_source_code") or ""))
    return (
        "<b>Проект достиг 100% дневного лимита</b>\n\n"
        f"Клиент: <code>{html.escape(str(project.get('client_name') or ''))}</code>\n"
        f"Проект: <code>{html.escape(project_name)}</code>\n"
        f"Канал: <b>{html.escape(source)}</b>\n"
        f"Лимит: <b>{html.escape(str(int(project.get('data_limit') or 0)))}</b> идентификаций\n"
        f"Получено: <b>{html.escape(str(int(project.get('received_count') or 0)))}</b> идентификаций\n\n"
        "Нужно проверить проект и при необходимости продлить лимит или остановить сбор."
    )


def _run_project_daily_limit_notifications(db_sess: Session) -> int:
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat_id:
        return 0

    start_local, end_local = _parse_date_range_in_settings_tz(None, None)
    projects = crud.list_daily_limit_reached_project_snapshots(
        db_sess,
        start_local=start_local,
        end_local=end_local,
    )
    queued = 0
    for project in projects:
        result = notifications.send_system_notification(
            db_sess=db_sess,
            chat_id=chat_id,
            text=_build_project_daily_limit_reached_message(project),
            parse_mode="HTML",
            metadata={
                "kind": "project_daily_limit_reached",
                "client_id": project.get("user_id"),
                "project_id": project.get("id"),
                "data_limit": project.get("data_limit"),
                "received_count": project.get("received_count"),
            },
        )
        if not result.delivered:
            if result.reason != "telegram_disabled":
                logging.getLogger("app").warning(
                    "Failed to send daily limit reached notification for project_id=%s reason=%s",
                    project.get("id"),
                    result.reason,
                )
            continue
        if crud.mark_project_daily_limit_reached_notified(
            db_sess,
            project_id=int(project["id"]),
            data_limit=int(project["data_limit"] or 0),
        ):
            queued += 1
    return queued


def _provider_project_is_disabled(detail: dict) -> bool:
    try:
        return int(str(detail.get("status", "")).strip()) == 0
    except (TypeError, ValueError):
        return False


def _build_operator_block_notification(project: dict, trigger: str) -> str:
    return (
        "<b>[ЛК | Блокировка оператора]</b>\n"
        f'Клиент: <code>{html.escape(str(project.get("client_name", "") or ""))}</code> '
        f'(id={html.escape(str(project.get("user_id", "") or ""))})\n'
        f'Проект: <code>{html.escape(str(project.get("name", "") or ""))}</code> '
        f'(id={html.escape(str(project.get("id", "") or ""))})\n'
        f"Триггер: <code>{html.escape(trigger)}</code>\n\n"
        "Поставщик отключил проект. В ЛК установлен статус "
        f"<b>{html.escape(crud.PROJECT_STATUS_OPERATOR_BLOCK)}</b>.\n"
        "Проект можно перезапустить вручную после расширения номеров или трафика."
    )


def _build_operator_block_empty_response_notification(project: dict, trigger: str) -> str:
    return (
        "<b>[ЛК | Блокировка оператора]</b>\n"
        f'Клиент: <code>{html.escape(str(project.get("client_name", "") or ""))}</code> '
        f'(id={html.escape(str(project.get("user_id", "") or ""))})\n'
        f'Проект: <code>{html.escape(_project_name_for_display(str(project.get("name", "") or "")))}</code> '
        f'(id={html.escape(str(project.get("id", "") or ""))})\n'
        f"Триггер: <code>{html.escape(trigger)}</code>\n\n"
        "Пустой ответ, нужна доп проверка админом.\n"
        "В ЛК установлен статус "
        f"<b>{html.escape(crud.PROJECT_STATUS_OPERATOR_BLOCK)}</b>."
    )


def _notify_operator_block(project: dict, trigger: str) -> None:
    # Блокировка поставщиком остается техническим событием для общего админского чата.
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat_id:
        return
    with SessionLocal() as notify_sess:  # type: Session
        result = notifications.send_system_notification(
            db_sess=notify_sess,
            chat_id=chat_id,
            text=_build_operator_block_notification(project, trigger),
            parse_mode="HTML",
            metadata={
                "client_id": project.get("user_id"),
                "project_id": project.get("id"),
                "provider_project_id": project.get("provider_project_id"),
                "trigger": trigger,
                "kind": "operator_block",
            },
        )
    if not result.delivered and result.reason != "telegram_disabled":
        logging.getLogger("app").warning(
            "Failed to send operator block notification for project_id=%s reason=%s",
            project.get("id"),
            result.reason,
        )


def _notify_operator_block_empty_response(project: dict, trigger: str) -> None:
    # Пустой ответ поставщика тоже является техническим событием для общего админского чата.
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat_id:
        return
    with SessionLocal() as notify_sess:  # type: Session
        result = notifications.send_system_notification(
            db_sess=notify_sess,
            chat_id=chat_id,
            text=_build_operator_block_empty_response_notification(project, trigger),
            parse_mode="HTML",
            metadata={
                "client_id": project.get("user_id"),
                "project_id": project.get("id"),
                "provider_project_id": project.get("provider_project_id"),
                "trigger": trigger,
                "kind": "operator_block_empty_response",
            },
        )
    if not result.delivered and result.reason != "telegram_disabled":
        logging.getLogger("app").warning(
            "Failed to send empty-response operator block notification for project_id=%s reason=%s",
            project.get("id"),
            result.reason,
        )


def _run_operator_block_check(trigger: str = "manual") -> schemas.OperatorBlockCheckOut:
    with SessionLocal() as s:  # type: Session
        projects = crud.list_operator_block_check_project_snapshots(s)
        s.rollback()

    checked = 0
    blocked = 0
    skipped = 0
    blocked_projects: List[schemas.OperatorBlockProjectOut] = []
    errors: List[str] = []

    for project in projects:
        checked += 1
        provider_project_id = str(project.get("provider_project_id") or "").strip()
        if not provider_project_id:
            skipped += 1
            continue
        try:
            detail = prostats.get_project(provider_project_id)
        except prostats.ProstatsError as exc:
            errors.append(f'Проект {project["id"]} "{project["name"]}": {exc.message}')
            continue
        if not detail:
            with SessionLocal() as write_sess:  # type: Session
                changed_project = crud.mark_project_operator_blocked_if_active(
                    write_sess,
                    project_id=int(project["id"]),
                    operator_block_reason=(
                        "Система изменила статус: поставщик вернул пустой ответ по проекту при проверке D."
                    ),
                )
            if changed_project:
                blocked += 1
                blocked_projects.append(
                    schemas.OperatorBlockProjectOut(
                        id=int(changed_project["id"]),
                        name=str(changed_project.get("name") or ""),
                        clientName=(str(changed_project.get("client_name") or "").strip() or None),
                        providerProjectId=(str(changed_project.get("provider_project_id") or "").strip() or None),
                    )
                )
                _notify_operator_block_empty_response(changed_project, trigger=trigger)
            else:
                skipped += 1
            continue
        if not _provider_project_is_disabled(detail):
            skipped += 1
            continue

        with SessionLocal() as write_sess:  # type: Session
            changed_project = crud.mark_project_operator_blocked_if_active(
                write_sess,
                project_id=int(project["id"]),
            )
        if changed_project:
            blocked += 1
            blocked_projects.append(
                schemas.OperatorBlockProjectOut(
                    id=int(changed_project["id"]),
                    name=str(changed_project.get("name") or ""),
                    clientName=(str(changed_project.get("client_name") or "").strip() or None),
                    providerProjectId=(str(changed_project.get("provider_project_id") or "").strip() or None),
                )
            )
            _notify_operator_block(changed_project, trigger=trigger)
        else:
            skipped += 1

    if blocked:
        _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    return schemas.OperatorBlockCheckOut(
        checked=checked,
        blocked=blocked,
        skipped=skipped,
        blockedProjects=blocked_projects,
        errors=errors,
    )


def _should_send_auto_pause_test_message(
    prev_enabled: bool,
    prev_chat_id: str,
    next_enabled: bool,
    next_chat_id: str,
) -> bool:
    """
    Тестовое сообщение шлём только в двух случаях:
    1) маршрут включили впервые (False -> True);
    2) chat id изменили при уже включённом маршруте.

    Если новый chat id пустой, тест отправлять некуда — пропускаем.
    """
    if not next_enabled or not next_chat_id:
        return False
    if not prev_enabled:
        return True
    return prev_chat_id != next_chat_id


def _build_auto_pause_test_message(user: models.User) -> str:
    return (
        "<b>Уведомление: тест Telegram-уведомлений</b>\n\n"
        "Telegram-чат для уведомлений настроен корректно.\n\n"
        "Дальше сюда будут приходить уведомления из личного кабинета."
    )


def _notify_provider_lead_project_ambiguity(
    *,
    vid: str,
    project_name: str,
    candidates: List[models.Project],
    prov_chanel: Optional[str],
    prov_source: Optional[str],
    subdomain: Optional[str],
) -> None:
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat_id:
        return

    candidate_lines = [
        (
            f'- id={int(project.id)}, client_id={project.user_id or "-"}, '
            f'status={html.escape(str(project.status or ""))}, '
            f'provider_project_id={html.escape(str(project.provider_project_id or ""))}'
        )
        for project in candidates[:10]
    ]
    if len(candidates) > 10:
        candidate_lines.append(f"... и еще {len(candidates) - 10} проект(ов)")

    text = (
        "<b>[ЛК | Неоднозначная привязка идентификации]</b>\n"
        "Лид сохранен без привязки к проекту, потому что найдено несколько неудаленных проектов с одинаковым именем.\n\n"
        f"vid: <code>{html.escape(vid)}</code>\n"
        f"page/project_name: <code>{html.escape(project_name)}</code>\n"
        f"channel: <code>{html.escape(str(prov_chanel or ''))}</code>\n"
        f"source: <code>{html.escape(str(prov_source or ''))}</code>\n"
        f"subdomain: <code>{html.escape(str(subdomain or ''))}</code>\n\n"
        "Кандидаты:\n"
        + "\n".join(candidate_lines)
    )

    with SessionLocal() as notify_sess:  # type: Session
        result = notifications.send_system_notification(
            db_sess=notify_sess,
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            metadata={"vid": vid, "project_name": project_name},
        )
    if not result.delivered:
        if result.reason != "telegram_disabled":
            logging.getLogger("app").warning(
                "Failed to send notification about ambiguous provider lead: vid=%s page=%s reason=%s",
                vid,
                project_name,
                result.reason,
            )


def _notify_unique_project_name_failure(
    *,
    client_id: int,
    project_name: str,
    provider_project_id: Optional[str],
    message: str,
) -> None:
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat_id:
        return
    text = (
        "<b>[ЛК | Ошибка финализации уникального имени проекта]</b>\n"
        f"client_id: <code>{html.escape(str(client_id))}</code>\n"
        f"project_name: <code>{html.escape(project_name)}</code>\n"
        f"provider_project_id: <code>{html.escape(str(provider_project_id or ''))}</code>\n"
        f"details: {html.escape(message)}"
    )
    with SessionLocal() as notify_sess:  # type: Session
        result = notifications.send_system_notification(
            db_sess=notify_sess,
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            metadata={"client_id": client_id, "provider_project_id": provider_project_id},
        )
    if not result.delivered:
        if result.reason != "telegram_disabled":
            logging.getLogger("app").warning(
                "Failed to send notification about unique project rename failure: client_id=%s provider_project_id=%s reason=%s",
                client_id,
                provider_project_id,
                result.reason,
            )


def _client_name_for_notification(db_sess: Session, client_id: Optional[int]) -> str:
    if not client_id:
        return "-"
    profile = db_sess.execute(
        select(models.ClientProfile).where(models.ClientProfile.user_id == int(client_id))
    ).scalar_one_or_none()
    if profile and str(getattr(profile, "name", "") or "").strip():
        return str(profile.name).strip()
    user = db_sess.get(models.User, int(client_id))
    if not user:
        return str(client_id)
    return (
        str(getattr(user, "display_name", "") or "").strip()
        or str(getattr(user, "login", "") or "").strip()
        or str(client_id)
    )


def _actor_name_for_notification(db_sess: Session, actor_user_id: Optional[int]) -> str:
    if not actor_user_id:
        return "Система"
    user = db_sess.get(models.User, int(actor_user_id))
    if not user:
        return f"id={int(actor_user_id)}"
    label = str(getattr(user, "display_name", "") or "").strip() or str(getattr(user, "login", "") or "").strip()
    return f"{label} (id={int(actor_user_id)})" if label else f"id={int(actor_user_id)}"


def _queue_client_tariff_operation_notification(
    *,
    client_id: int,
    tariff_id: int,
    action: str,
    amount: int,
    comment: Optional[str],
) -> None:
    action_label_map = {
        "create": "начислен новый тариф",
        "credit": "тариф увеличен",
        "debit": "корректировка тарифа",
    }
    action_label = action_label_map.get(action)
    if not action_label:
        return

    try:
        with SessionLocal() as notify_sess:  # type: Session
            client = notify_sess.get(models.User, int(client_id))
            if not client or not crud.is_client_user(client):
                return
            chat_id = _get_client_telegram_chat_id_for_notifications(client)
            if not chat_id:
                return
            tariff = crud.get_client_tariff(notify_sess, tariff_id=int(tariff_id))
            if not tariff:
                return
            remaining = crud.get_client_remaining_numbers(notify_sess, client_id=int(client_id))
            client_name = _client_name_for_notification(notify_sess, int(client_id))
            sign = "-" if action == "debit" else "+"
            operation_label = {
                "create": "Начислено",
                "credit": "Дополнительно начислено",
                "debit": "Скорректировано",
            }[action]
            text = (
                f"<b>Уведомление: {html.escape(action_label)}</b>\n\n"
                f"Клиент: {html.escape(client_name)}\n"
                f"{operation_label}: {sign}{_format_notification_number(amount)} идентификаций"
            )
            normalized_comment = str(comment or "").strip()
            if action != "create" and normalized_comment:
                text += f"\nКомментарий: {html.escape(normalized_comment)}"
            text += f"\n\nТекущий остаток: {_format_notification_number(remaining)} идентификаций"
            result = notifications.send_telegram_notification(
                db_sess=notify_sess,
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                kind="tariff_operation",
                metadata={
                    "client_id": int(client_id),
                    "tariff_id": int(tariff_id),
                    "action": action,
                    "amount": int(amount),
                },
            )
    except Exception:
        logging.getLogger("app").warning(
            "Failed to prepare tariff operation notification: client_id=%s tariff_id=%s action=%s",
            client_id,
            tariff_id,
            action,
            exc_info=True,
        )
        return
    if not result.delivered and result.reason != "telegram_disabled":
        logging.getLogger("app").warning(
            "Failed to queue tariff operation notification: client_id=%s tariff_id=%s action=%s reason=%s",
            client_id,
            tariff_id,
            action,
            result.reason,
        )


def _queue_sms_project_notification(
    *,
    project_id: int,
    action: str,
    actor_user_id: Optional[int],
) -> None:
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not chat_id:
        return

    with SessionLocal() as notify_sess:  # type: Session
        project = notify_sess.get(models.Project, int(project_id))
        if not project or str(getattr(project, "collection_source", "") or "").strip() != "СМС":
            return

        action_label = "создан" if action == "create" else "изменён"
        client_name = _client_name_for_notification(notify_sess, getattr(project, "user_id", None))
        actor_name = _actor_name_for_notification(notify_sess, actor_user_id)
        text = (
            f"<b>[ЛК | SMS-проект {action_label}]</b>\n"
            f"Клиент: <code>{html.escape(client_name)}</code>\n"
            f"Проект: <code>{html.escape(str(project.name or ''))}</code> (id={int(project.id)})\n"
            f"Статус: <b>{html.escape(str(project.status or ''))}</b>\n"
            f"Источник: <b>{html.escape(_source_code_for_display(str(project.data_source_code or '')))}</b>\n"
            f"Лимит: <b>{html.escape(str(int(project.data_limit or 0)))}</b>\n"
            f"SMS sender: <code>{html.escape(str(project.sms_sender_name or ''))}</code>\n"
            f"Дни: <code>{html.escape(str(project.days_received or ''))}</code>\n"
            f"Инициатор: <code>{html.escape(actor_name)}</code>"
        )
        result = notifications.send_telegram_notification(
            db_sess=notify_sess,
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            kind="sms_project",
            metadata={
                "project_id": int(project.id),
                "client_id": int(project.user_id) if getattr(project, "user_id", None) else None,
                "action": action,
                "actor_user_id": int(actor_user_id) if actor_user_id else None,
            },
        )
    if not result.delivered and result.reason != "telegram_disabled":
        logging.getLogger("app").warning(
            "Failed to queue SMS project notification: project_id=%s action=%s reason=%s",
            project_id,
            action,
            result.reason,
        )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


def require_telegram_worker(request: Request) -> None:
    expected = str(settings.get("TELEGRAM_WORKER_API_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="TELEGRAM_WORKER_API_TOKEN is not configured")
    auth_header = request.headers.get("Authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth_header.split(" ", 1)[1].strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/internal/telegram-notifications/claim", response_model=schemas.TelegramNotificationClaimOut)
def claim_telegram_notifications(
    payload: schemas.TelegramNotificationClaimIn,
    _worker_auth: None = Depends(require_telegram_worker),
    db_sess: Session = Depends(get_db),
):
    items = crud.claim_telegram_notifications(
        db_sess,
        worker_id=payload.workerId,
        limit=payload.limit,
    )
    return {"items": items}


@app.post("/internal/telegram-notifications/{notification_id}/result", response_model=schemas.TelegramNotificationResultOut)
def set_telegram_notification_result(
    notification_id: int,
    payload: schemas.TelegramNotificationResultIn,
    _worker_auth: None = Depends(require_telegram_worker),
    db_sess: Session = Depends(get_db),
):
    if payload.status == "sent":
        row = crud.mark_telegram_notification_sent(
            db_sess,
            notification_id=notification_id,
            telegram_message_id=payload.telegramMessageId,
        )
    else:
        row = crud.mark_telegram_notification_failed(
            db_sess,
            notification_id=notification_id,
            error=payload.error,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Telegram notification not found")
    return {"ok": True, "id": int(row.id), "status": row.status}


@app.post("/api/provider-test/{secret}")
async def provider_webhook(secret: str, request: Request, db_sess: Session = Depends(get_db)):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        payload, fmt = await _read_webhook_body(request)
    except ClientDisconnect:
        # Клиент оборвал соединение до того, как тело webhook было дочитано.
        # Логируем это как сетевой сбой без большого traceback.
        logging.getLogger("app.webhook").warning(
            "Client disconnected while sending webhook body: path=%s",
            request.url.path,
        )
        return Response(status_code=499)

    provider_webhook_logger.info(
        json.dumps(
            {
                "format": fmt,
                "headers": dict(request.headers),
                "payload": payload,
            },
            ensure_ascii=False,
        ),
    )

    if fmt != "json" or not isinstance(payload, dict):
        return {"ok": True, "stored": False, "reason": "invalid_format"}

    vid = str(payload.get("vid") or "").strip()
    if not vid:
        raise HTTPException(status_code=400, detail="vid is required")

    if crud.get_provider_lead_by_vid(db_sess, vid):
        return {"ok": True, "stored": False, "reason": "duplicate"}

    page = str(payload.get("page") or "").strip() or None
    prov_chanel, prov_source = _parse_page_parts(page)
    phone, phones_raw = _extract_phones(payload.get("phones"))
    subdomain = str(payload.get("subdomain")).strip() if payload.get("subdomain") else None
    prov_created_at = _parse_provider_time(payload.get("time"))
    project_id: Optional[int] = None
    project_match_status = "not_found"
    matched_projects: List[models.Project] = []
    if page:
        project_id, project_match_status, matched_projects = crud.resolve_project_by_name_for_provider_lead(
            db_sess,
            page,
        )
        if project_match_status == "ambiguous":
            provider_webhook_logger.warning(
                json.dumps(
                    {
                        "event": "ambiguous_project_match",
                        "vid": vid,
                        "project_name": page,
                        "project_ids": [int(project.id) for project in matched_projects],
                    },
                    ensure_ascii=False,
                ),
            )
            db_sess.rollback()
            _notify_provider_lead_project_ambiguity(
                vid=vid,
                project_name=page,
                candidates=matched_projects,
                prov_chanel=prov_chanel,
                prov_source=prov_source,
                subdomain=subdomain,
            )

    try:
        row = crud.create_provider_lead(
            db_sess,
            vid=vid,
            phone=phone,
            phones_raw=phones_raw,
            project_name=page,
            prov_created_at=prov_created_at,
            prov_chanel=prov_chanel,
            prov_source=prov_source,
            subdomain=subdomain,
            project_id=project_id,
        )
    except IntegrityError:
        db_sess.rollback()
        return {"ok": True, "stored": False, "reason": "duplicate"}

    return {"ok": True, "stored": True, "id": row.id}


def _normalize_pixel_phone(value: object) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D+", "", raw)
    if len(digits) == 11:
        if digits.startswith("8"):
            return f"7{digits[1:]}"
        return digits
    return raw


def _extract_pixel_phones(raw: object) -> List[str]:
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    phones: List[str] = []
    seen: set[str] = set()
    for item in values:
        phone = _normalize_pixel_phone(item)
        if phone and phone not in seen:
            phones.append(phone)
            seen.add(phone)
    return phones


@app.post("/api/pixel-webhook/{secret}")
async def pixel_webhook(secret: str, request: Request, db_sess: Session = Depends(get_db)):
    if not PIXEL_WEBHOOK_SECRET or secret != PIXEL_WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        payload, fmt = await _read_webhook_body(request)
    except ClientDisconnect:
        logging.getLogger("app.webhook").warning(
            "Client disconnected while sending pixel webhook body: path=%s",
            request.url.path,
        )
        return Response(status_code=499)
    except Exception as exc:
        pixel_webhook_logger.warning(
            json.dumps(
                {
                    "event": "pixel_webhook_read_error",
                    "path": request.url.path.replace(secret, "<secret>", 1),
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return {"ok": True, "stored": 0, "duplicates": 0, "reason": "invalid_format"}

    vid = str(payload.get("vid") or "").strip() if isinstance(payload, dict) else ""
    pixel_url = str(payload.get("page") or "").strip() or None if isinstance(payload, dict) else None
    site = str(payload.get("site") or "").strip() if isinstance(payload, dict) else ""
    if not site and pixel_url:
        site = crud.normalize_pixel_domain(pixel_url)
    domain = crud.normalize_pixel_domain(site)
    phones = _extract_pixel_phones(payload.get("phones") if isinstance(payload, dict) else None)

    log_base = {
        "event": "pixel_webhook",
        "format": fmt,
        "path": request.url.path.replace(secret, "<secret>", 1),
        "vid": vid,
        "site": site,
        "domain": domain,
        "phones_count": len(phones),
        "pixel_url": pixel_url,
    }

    if fmt != "json" or not isinstance(payload, dict):
        pixel_webhook_logger.info(json.dumps({**log_base, "result": "invalid_format"}, ensure_ascii=False))
        return {"ok": True, "stored": 0, "duplicates": 0, "reason": "invalid_format"}
    if not vid:
        pixel_webhook_logger.info(json.dumps({**log_base, "result": "invalid_format", "reason": "missing_vid"}, ensure_ascii=False))
        return {"ok": True, "stored": 0, "duplicates": 0, "reason": "missing_vid"}
    if not domain:
        pixel_webhook_logger.info(json.dumps({**log_base, "result": "invalid_format", "reason": "missing_site"}, ensure_ascii=False))
        return {"ok": True, "stored": 0, "duplicates": 0, "reason": "missing_site"}
    if not phones:
        pixel_webhook_logger.info(json.dumps({**log_base, "result": "no_phones"}, ensure_ascii=False))
        return {"ok": True, "stored": 0, "duplicates": 0, "reason": "no_phones"}

    project, match_status, matched_projects = crud.resolve_pixel_project_by_domain(db_sess, domain)
    if match_status != "matched" or project is None:
        result = "domain_not_found" if match_status == "not_found" else "domain_ambiguous"
        pixel_webhook_logger.info(
            json.dumps(
                {
                    **log_base,
                    "result": result,
                    "matched_project_ids": [int(p.id) for p in matched_projects],
                },
                ensure_ascii=False,
            )
        )
        return {"ok": True, "stored": 0, "duplicates": 0, "reason": result}

    prov_created_at = _parse_provider_time(payload.get("time"))
    stored_ids: List[int] = []
    duplicate_count = 0
    for phone in phones:
        if crud.get_pixel_lead_by_vid_phone(db_sess, vid, phone):
            duplicate_count += 1
            continue
        try:
            row = crud.create_pixel_provider_lead(
                db_sess,
                vid=vid,
                phone=phone,
                project=project,
                prov_created_at=prov_created_at,
                pixel_url=pixel_url,
            )
            stored_ids.append(int(row.id))
        except IntegrityError:
            db_sess.rollback()
            duplicate_count += 1

    result = "stored" if stored_ids else ("duplicate" if duplicate_count else "no_rows")
    pixel_webhook_logger.info(
        json.dumps(
            {
                **log_base,
                "result": result,
                "project_id": int(project.id),
                "project_name": str(project.name or ""),
                "stored": len(stored_ids),
                "duplicates": duplicate_count,
                "ids": stored_ids,
            },
            ensure_ascii=False,
        )
    )
    return {"ok": True, "stored": len(stored_ids), "duplicates": duplicate_count, "ids": stored_ids}


def require_auth(request: Request, db_sess: Session = Depends(get_db)):
    # Проверяем токен либо в заголовке Authorization: Bearer <token>, либо в query параметре token
    token = None

    # Сначала проверяем заголовок
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    else:
        # Если нет заголовка, проверяем query параметр token (для экспорта)
        token = request.query_params.get("token")

    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = auth.decode_token_payload(token or "")
    if not payload:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        user_id = int(payload.get("user_id"))
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db_sess.get(models.User, int(user_id))
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    impersonator_raw = payload.get("impersonator_user_id")
    impersonator_id: Optional[int] = None
    try:
        if impersonator_raw is not None:
            impersonator_id = int(impersonator_raw)
    except Exception:
        impersonator_id = None
    allow_disabled_target = bool(payload.get("allow_disabled_target"))
    if crud.is_agent_user(user) and crud.user_is_disabled(user):
        if not (allow_disabled_target and impersonator_id and impersonator_id != user.id):
            raise HTTPException(status_code=403, detail="Агент отключён.")

    if impersonator_id and impersonator_id != user.id:
        setattr(user, "_actor_user_id", impersonator_id)
        setattr(user, "_via_impersonation", True)
    else:
        setattr(user, "_actor_user_id", user.id)
        setattr(user, "_via_impersonation", False)
    return user


def require_manager(current_user: models.User = Depends(require_auth)):
    if not (crud.is_admin_user(current_user) or crud.is_agent_user(current_user)):
        raise HTTPException(status_code=403, detail="Manager access required")
    return current_user


def _ensure_manager_client_access(db_sess: Session, manager_user: models.User, client_id: int) -> models.User:
    client = db_sess.get(models.User, int(client_id))
    if not client or not crud.is_client_user(client):
        raise HTTPException(status_code=404, detail="Client not found")
    if not crud.manager_can_access_client(db_sess, manager_user, int(client_id)):
        raise HTTPException(status_code=403, detail="Нет доступа к этому клиенту")
    return client


def _ensure_manager_project_access(db_sess: Session, manager_user: models.User, project_id: int) -> models.Project:
    project = db_sess.get(models.Project, int(project_id))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not crud.manager_can_access_project(db_sess, manager_user, int(project_id)):
        raise HTTPException(status_code=403, detail="Нет доступа к этому проекту")
    return project


def _ensure_manager_tariff_access(db_sess: Session, manager_user: models.User, tariff_id: int) -> schemas.ClientTariffOut:
    tariff = crud.get_client_tariff(db_sess, tariff_id=tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    if not crud.manager_can_access_tariff(db_sess, manager_user, int(tariff_id)):
        raise HTTPException(status_code=403, detail="Нет доступа к этому тарифу")
    return tariff


def _audit_actor_context(current_user: models.User) -> tuple[int, bool]:
    raw_actor = getattr(current_user, "_actor_user_id", None)
    try:
        actor_user_id = int(raw_actor)
    except Exception:
        actor_user_id = int(current_user.id)
    via_impersonation = bool(getattr(current_user, "_via_impersonation", False))
    return actor_user_id, via_impersonation


def _http_error_message(detail: object) -> str:
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        if message:
            return str(message)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return "Операция не выполнена"


def _http_error_code(detail: object) -> Optional[str]:
    if isinstance(detail, dict):
        code = detail.get("code")
        if code:
            return str(code)
    return None


def _prostats_http_detail(exc: prostats.ProstatsError) -> dict:
    # details может содержать сырой HTML поставщика; наружу отдаём только безопасное сообщение.
    message = re.sub(r"(?i)prostats(?:_token)?", "сервис обработки данных", str(exc.message or ""))
    return {"message": message or "Не удалось выполнить операцию в сервисе обработки данных."}


def _project_payload_for_history(payload: object) -> dict:
    try:
        if hasattr(payload, "dict"):
            data = payload.dict()
        elif isinstance(payload, dict):
            data = dict(payload)
        else:
            data = {}
    except Exception:
        data = {}
    # Храним только параметры формы проекта, без служебных auth-данных.
    return data


def _project_payload_for_history_with_duplicate_diagnostics(
    payload: object,
    duplicate_diagnostics: Optional[dict],
) -> dict:
    data = _project_payload_for_history(payload)
    if duplicate_diagnostics:
        data["duplicateDiagnostics"] = duplicate_diagnostics
    return data


def _record_failed_project_operation(
    *,
    user_id: int,
    actor_user_id: int,
    operation: str,
    error_message: str,
    via_impersonation: bool,
    project_id: Optional[int] = None,
    project_name: Optional[str] = None,
    request_payload: Optional[dict] = None,
    error_code: Optional[str] = None,
) -> None:
    try:
        with SessionLocal() as s:  # type: Session
            crud.record_project_operation_failed(
                s,
                user_id=user_id,
                actor_user_id=actor_user_id,
                operation=operation,
                project_id=project_id,
                project_name=project_name,
                request_payload=request_payload,
                error_message=error_message,
                error_code=error_code,
                via_impersonation=via_impersonation,
            )
    except Exception:
        logging.getLogger("app").warning(
            "Failed to record project operation failure: user_id=%s project_id=%s operation=%s",
            user_id,
            project_id,
            operation,
            exc_info=True,
        )


def _project_operation_conflict(exc: project_operations.ActiveProjectOperationError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "PROJECT_OPERATION_IN_PROGRESS",
            "message": "Для этого клиента уже выполняется операция с проектами.",
            "operationId": exc.operation_id,
        },
    )


def _ensure_project_operation_access(
    db_sess: Session,
    current_user: models.User,
    client_id: int,
) -> None:
    if crud.is_admin_user(current_user) or crud.is_agent_user(current_user):
        _ensure_manager_client_access(db_sess, current_user, int(client_id))
        return
    if int(current_user.id) != int(client_id):
        raise HTTPException(status_code=404, detail="Operation not found")


def _launch_bulk_project_operation(
    db_sess: Session,
    *,
    payload: schemas.ProjectBulkOperationIn,
    current_user: models.User,
    manager_mode: bool,
) -> models.ProjectOperationJob:
    project_ids = list(dict.fromkeys(int(value) for value in payload.projectIds))
    if manager_mode and payload.action == "delete" and not crud.is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Удаление проектов доступно только администратору.")
    if payload.action == "update" and str(payload.patch.get("status") or "") == "Удалён":
        raise HTTPException(
            status_code=422,
            detail="Статус «Удалён» выполняется только отдельной операцией удаления.",
        )
    rows = db_sess.execute(
        select(models.Project).where(models.Project.id.in_(project_ids)).order_by(models.Project.id.asc())
    ).scalars().all()
    if len(rows) != len(project_ids):
        raise HTTPException(status_code=404, detail="Один или несколько проектов не найдены.")
    if any(_is_pixel_project(project) for project in rows):
        raise HTTPException(status_code=422, detail="Pixel-проекты не поддерживаются этой массовой операцией.")
    owner_ids = {int(project.user_id or 0) for project in rows}
    if len(owner_ids) != 1 or 0 in owner_ids:
        raise HTTPException(status_code=422, detail="Все проекты операции должны принадлежать одному клиенту.")
    client_id = next(iter(owner_ids))
    client = db_sess.get(models.User, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if manager_mode:
        _ensure_manager_client_access(db_sess, current_user, client_id)
        for project in rows:
            if not crud.manager_can_access_project(db_sess, current_user, int(project.id)):
                raise HTTPException(status_code=403, detail="Нет доступа к одному или нескольким проектам.")
    elif int(current_user.id) != client_id:
        raise HTTPException(status_code=404, detail="Один или несколько проектов не найдены.")
    if bool(getattr(client, "projects_mutation_locked", False)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECTS_LOCKED_BY_ADMIN",
                "message": "Изменение проектов временно заблокировано администратором.",
            },
        )

    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    status_only_value = (
        str(payload.patch.get("status") or "").strip()
        if payload.action == "update" and set(payload.patch.keys()) == {"status"}
        else ""
    )
    status_only_operation = status_only_value in {"Активен", "На паузе"}
    items: List[dict] = []
    for project in rows:
        if project.status == "Удалён":
            raise HTTPException(status_code=409, detail=f'Проект {project.id} уже удалён.')
        item_payload: dict = {
            "action": payload.action,
            "actorUserId": actor_user_id,
            "adminUpdate": bool(manager_mode),
            "viaImpersonation": via_impersonation,
        }
        provider_status_only = (
            status_only_operation
            and str(getattr(project, "collection_source", "") or "").strip() != "СМС"
        )
        if provider_status_only:
            item_payload["action"] = "status"
            item_payload["status"] = status_only_value
            item_payload["parallelSafe"] = (
                status_only_value == "На паузе"
                or not bool(getattr(client, "auto_limit_control_enabled", False))
            )
        elif payload.action == "update":
            try:
                item_payload["update"] = _project_update_data_for_operation(
                    project,
                    payload.patch,
                    admin=manager_mode,
                )
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        items.append(
            {
                "projectId": int(project.id),
                "providerProjectId": project.provider_project_id,
                "projectName": project.name,
                "stateSnapshot": _snapshot_limit_control_project(project),
                "payloadSnapshot": item_payload,
            }
        )
    try:
        return crud.create_project_operation(
            db_sess,
            client_id=client_id,
            operation_type=f"bulk_{payload.action}",
            actor_user_id=actor_user_id,
            actor_role=crud.get_user_role(current_user),
            items=items,
            payload_snapshot={"action": payload.action, "patch": payload.patch},
        )
    except project_operations.ActiveProjectOperationError as exc:
        raise _project_operation_conflict(exc) from exc


@app.get("/project-operations/active", response_model=schemas.ProjectOperationOut)
def active_project_operation(
    clientId: Optional[int] = None,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    client_id = int(clientId) if clientId is not None else int(current_user.id)
    _ensure_project_operation_access(db_sess, current_user, client_id)
    operation = crud.get_active_project_operation(db_sess, client_id)
    if not operation:
        raise HTTPException(status_code=404, detail="Active operation not found")
    return crud.project_operation_to_view(
        db_sess,
        operation,
        include_technical=crud.is_admin_user(current_user),
    )


@app.get("/project-operations/{operation_id}", response_model=schemas.ProjectOperationOut)
def project_operation_status(
    operation_id: int,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    operation = crud.get_project_operation(db_sess, operation_id)
    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")
    _ensure_project_operation_access(db_sess, current_user, int(operation.client_id))
    return crud.project_operation_to_view(
        db_sess,
        operation,
        include_technical=crud.is_admin_user(current_user),
    )


@app.post("/project-operations/bulk", response_model=schemas.ProjectOperationLaunchOut, status_code=202)
def create_client_project_operation(
    payload: schemas.ProjectBulkOperationIn,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    operation = _launch_bulk_project_operation(
        db_sess,
        payload=payload,
        current_user=current_user,
        manager_mode=False,
    )
    return schemas.ProjectOperationLaunchOut(
        operation=crud.project_operation_to_view(db_sess, operation, include_technical=False)
    )


@app.post("/admin/project-operations/bulk", response_model=schemas.ProjectOperationLaunchOut, status_code=202)
def create_admin_project_operation(
    payload: schemas.ProjectBulkOperationIn,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    operation = _launch_bulk_project_operation(
        db_sess,
        payload=payload,
        current_user=current_manager,
        manager_mode=True,
    )
    return schemas.ProjectOperationLaunchOut(
        operation=crud.project_operation_to_view(
            db_sess,
            operation,
            include_technical=crud.is_admin_user(current_manager),
        )
    )


@app.get("/me", response_model=schemas.SelfProfileOut)
def get_me(current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    profile = db_sess.execute(
        select(models.ClientProfile).where(models.ClientProfile.user_id == current_user.id)
    ).scalar_one_or_none()
    return schemas.SelfProfileOut(
        id=current_user.id,
        login=current_user.login,
        name=(profile.name if profile else (getattr(current_user, "display_name", None) or None)),
        role=crud.get_user_role(current_user),  # type: ignore[arg-type]
        ownerAgentId=(int(current_user.owner_agent_id) if getattr(current_user, "owner_agent_id", None) is not None else None),
        viaImpersonation=bool(getattr(current_user, "_via_impersonation", False)),
        impersonatorUserId=(int(getattr(current_user, "_actor_user_id", 0) or 0) if bool(getattr(current_user, "_via_impersonation", False)) else None),
        isDisabled=bool(getattr(current_user, "is_disabled", False)),
        projectsMutationLocked=bool(getattr(current_user, "projects_mutation_locked", False)),
        projectsMutationLockedAt=current_user.projects_mutation_locked_at.isoformat() if getattr(current_user, "projects_mutation_locked_at", None) else None,
        projectsMutationLockedBy=(int(current_user.projects_mutation_locked_by) if getattr(current_user, "projects_mutation_locked_by", None) is not None else None),
        projectsMutationLockReason=(current_user.projects_mutation_lock_reason or None),
        autoLimitControlEnabled=bool(getattr(current_user, "auto_limit_control_enabled", False)),
        telegramNotificationsChatId=(getattr(current_user, "telegram_notifications_chat_id", None) or None),
        telegramAutoPauseEnabled=bool(getattr(current_user, "telegram_auto_pause_enabled", False)),
        uniqueProjectNamesEnabled=bool(getattr(current_user, "unique_project_names_enabled", False)),
        pixelTableUrl=(
            str(getattr(profile, "pixel_table_url", "") or "").strip() or None
            if profile is not None
            else None
        ),
    )


@app.get("/dashboard", response_model=schemas.ClientDashboardOut)
def client_dashboard(
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    sources: Optional[str] = None,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    if crud.get_user_role(current_user) != crud.ROLE_CLIENT:
        raise HTTPException(status_code=403, detail="Client access required")

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today = datetime.now(tz).date()
    if not fromDate:
        fromDate = today.isoformat()
    if not toDate:
        toDate = today.isoformat()

    try:
        y, m, d = [int(x) for x in fromDate.split("-")]
        y2, m2, d2 = [int(x) for x in toDate.split("-")]
        start_date = datetime(y, m, d, 0, 0, 0, tzinfo=tz).date()
        end_date = datetime(y2, m2, d2, 0, 0, 0, tzinfo=tz).date()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Некорректный формат дат. Используйте YYYY-MM-DD.") from exc

    if end_date < start_date:
        raise HTTPException(status_code=422, detail="Дата окончания не может быть раньше даты начала.")

    def day_start(value):
        return datetime(value.year, value.month, value.day, 0, 0, 0, tzinfo=tz).replace(tzinfo=None)

    src_list: Optional[List[str]] = None
    if sources:
        src_list = [s.strip().upper() for s in sources.split(",") if s.strip()]
        src_list = [s for s in src_list if s in {"B1", "B2", "B3", "B4"}]
        if not src_list:
            src_list = None

    return crud.client_dashboard(
        db_sess,
        client_id=int(current_user.id),
        start_local=day_start(start_date),
        end_local=day_start(end_date + timedelta(days=1)),
        today_start=day_start(today),
        today_end=day_start(today + timedelta(days=1)),
        last7_start=day_start(today - timedelta(days=6)),
        last30_start=day_start(today - timedelta(days=29)),
        chart_start=day_start(today - timedelta(days=29)),
        chart_end=day_start(today + timedelta(days=1)),
        sources=src_list,
    )


@app.get("/projects", response_model=schemas.ProjectListOut)
def list_projects(
    offset: int = 0,
    limit: int = 50,
    q: str | None = None,
    sources: Optional[str] = None,
    collectionSources: Optional[str] = None,
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
    includeDeleted: bool = False,
    includeArchived: bool = False,
    projectStatus: Optional[schemas.ProjectStatus] = None,
    dailyLimitReached: bool = False,
    isTop: bool = False,
    sortBy: Optional[str] = None,
    sortDir: Optional[str] = None,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    limit = max(1, min(1000, limit))
    offset = max(0, offset)

    # Диапазон дат по аналогии с /leads
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today_msk = datetime.now(tz).date()
    if not fromDate:
        fromDate = today_msk.isoformat()
    if not toDate:
        toDate = today_msk.isoformat()

    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz)

    start_naive = start_local.replace(tzinfo=None)
    end_naive = end_local.replace(tzinfo=None)

    src_list: Optional[List[str]] = None
    if sources:
        src_list = [s.strip().upper() for s in sources.split(",") if s.strip()]
        src_list = [s for s in src_list if s in {"B1", "B2", "B3", "B4"}]
        if not src_list:
            src_list = None

    collection_src_list: Optional[List[str]] = None
    if collectionSources:
        collection_src_list = [s.strip() for s in collectionSources.split(",") if s.strip()]
        if not collection_src_list:
            collection_src_list = None

    return crud.list_projects_paginated(
        db_sess,
        offset=offset,
        limit=limit,
        q=q,
        user_id=current_user.id,
        sources=src_list,
        collection_sources=collection_src_list,
        start_local=start_naive,
        end_local=end_naive,
        include_deleted=includeDeleted,
        include_archived=includeArchived,
        project_status=projectStatus,
        daily_limit_reached=dailyLimitReached,
        is_top=isTop,
        sort_by=sortBy,
        sort_dir=sortDir,
    )


def _create_projects_with_unique_names(
    *,
    db_sess: Session,
    items: List[schemas.CreateProjectItem],
    current_user: models.User,
    actor_user_id: int,
    via_impersonation: bool,
) -> tuple[List[schemas.ProjectOut], Optional[str]]:
    notices: List[str] = []
    created: List[schemas.ProjectOut] = []
    batch_id = secrets.token_hex(8)
    logger = logging.getLogger("app")
    db_sess.rollback()

    for item in items:
        provider_id_value: Optional[str] = None
        success_notice: Optional[str] = None
        try:
            result = prostats.create_project(item)
            provider_id_value = str(result.get("provider_id") or "").strip() or None

            missing_items = result.get("missing_items") or []
            target_type = result.get("target_type")
            if missing_items and target_type in ("hosts", "calls"):
                duplicates = _find_duplicates_with_new_session(
                    missing_items,
                    target_type,
                    user_id=current_user.id,
                    provider_project_id=provider_id_value,
                )
                success_notice = prostats._build_partial_warning(
                    item.name,
                    target_type,
                    missing_items,
                    duplicates,
                    action="создан",
                )
                if target_type == "hosts":
                    item.sites = [s for s in (item.sites or []) if s not in missing_items]
                else:
                    item.phones = [p for p in (item.phones or []) if p not in missing_items]
            else:
                success_notice = f'Проект "{item.name}" создан.'
        except prostats.ProstatsError as exc:
            target_type = prostats._type_from_collection(item.collectionSource)
            message = exc.message
            if prostats._should_check_duplicates(exc.message, target_type):
                message = "Данные номера/сайты используются в других проектах. Подробности недоступны."
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation="create",
                project_name=item.name,
                request_payload=_project_payload_for_history(item),
                error_message=message,
                error_code=str(exc.status_code) if exc.status_code else None,
                via_impersonation=via_impersonation,
            )
            notices.append(f'Проект "{item.name}" не создан: {message}')
            continue

        project_row = crud.build_project_model_from_create_item(
            item,
            user_id=current_user.id,
            provider_id=provider_id_value,
            unique_name_applied=False,
        )
        pending_name = _build_pending_unique_project_name(item)
        project_row.name = pending_name
        project_row.tag = pending_name
        db_sess.add(project_row)
        try:
            db_sess.flush()
        except IntegrityError as exc:
            db_sess.rollback()
            if _is_project_name_unique_violation(exc):
                if provider_id_value:
                    try:
                        prostats.delete_project(str(provider_id_value), project_row)
                    except prostats.ProstatsError:
                        logger.warning(
                            "Failed to cleanup provider project after local name conflict: provider_project_id=%s",
                            provider_id_value,
                            exc_info=True,
                        )
                _record_failed_project_operation(
                    user_id=current_user.id,
                    actor_user_id=actor_user_id,
                    operation="create",
                    project_name=item.name,
                    request_payload=_project_payload_for_history(item),
                    error_message=crud.PROJECT_NAME_UNAVAILABLE_MESSAGE,
                    error_code="PROJECT_NAME_UNAVAILABLE",
                    via_impersonation=via_impersonation,
                )
                notices.append(f'Проект "{item.name}" не создан: {crud.PROJECT_NAME_UNAVAILABLE_MESSAGE}')
                continue
            raise

        final_name = _build_unique_project_name(item.dataSourceCode, int(project_row.id), item.name)
        if crud.active_project_name_exists(db_sess, final_name, exclude_project_id=int(project_row.id)):
            if provider_id_value:
                try:
                    prostats.delete_project(str(provider_id_value), project_row)
                except prostats.ProstatsError:
                    logger.warning(
                        "Failed to cleanup provider project after final name conflict: provider_project_id=%s",
                        provider_id_value,
                        exc_info=True,
                    )
            db_sess.rollback()
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation="create",
                project_name=item.name,
                request_payload=_project_payload_for_history(item),
                error_message=crud.PROJECT_NAME_UNAVAILABLE_MESSAGE,
                error_code="PROJECT_NAME_UNAVAILABLE",
                via_impersonation=via_impersonation,
            )
            notices.append(f'Проект "{item.name}" не создан: {crud.PROJECT_NAME_UNAVAILABLE_MESSAGE}')
            continue
        rename_payload = _build_project_update_from_create_item(item, name=final_name, tag=final_name)

        try:
            _rename_project_in_prostats_with_retries(
                str(provider_id_value or ""),
                project_row,
                rename_payload,
            )
        except prostats.ProstatsError as exc:
            db_sess.rollback()
            cleanup_ok = False
            cleanup_error = ""
            retryable_failure = _should_retry_prostats_error(exc)
            if provider_id_value and not retryable_failure:
                try:
                    prostats.delete_project(str(provider_id_value), project_row)
                    cleanup_ok = True
                except prostats.ProstatsError as cleanup_exc:
                    cleanup_error = cleanup_exc.message
            logger.error(
                "Failed to finalize unique project name: user_id=%s provider_project_id=%s original_name=%s cleanup_ok=%s cleanup_error=%s",
                current_user.id,
                provider_id_value,
                item.name,
                cleanup_ok,
                cleanup_error,
                exc_info=True,
            )
            _notify_unique_project_name_failure(
                client_id=current_user.id,
                project_name=item.name,
                provider_project_id=provider_id_value,
                message=(
                    f"{exc.message}; retryable={retryable_failure}; cleanup_ok={cleanup_ok}; cleanup_error={cleanup_error}"
                ),
            )
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation="create",
                project_name=item.name,
                request_payload=_project_payload_for_history(item),
                error_message=exc.message,
                error_code=str(exc.status_code) if exc.status_code else None,
                via_impersonation=via_impersonation,
            )
            if retryable_failure:
                notices.append(
                    f'Проект "{item.name}" не создан: провайдер долго подтверждает уникальное имя. Подождите и проверьте результат позже.'
                )
            elif cleanup_ok:
                notices.append(
                    f'Проект "{item.name}" не создан: не удалось применить уникальное имя у поставщика. Попробуйте ещё раз позже.'
                )
            else:
                notices.append(
                    f'Проект "{item.name}" не создан: ошибка применения уникального имени у поставщика. Нужна ручная проверка администратора.'
                )
            continue

        project_row.name = final_name
        project_row.tag = final_name
        project_row.unique_name_applied = True
        project_row.updated_at = now_msk()
        after = crud._project_to_out(project_row).dict()
        crud.add_project_audit_event(
            db_sess,
            project=project_row,
            user_id=current_user.id,
            actor_user_id=actor_user_id or current_user.id,
            batch_id=batch_id,
            action='create',
            before=None,
            after=after,
            changed_fields=list(after.keys()),
            via_impersonation=via_impersonation,
        )
        try:
            db_sess.commit()
        except IntegrityError as exc:
            db_sess.rollback()
            if _is_project_name_unique_violation(exc):
                _record_failed_project_operation(
                    user_id=current_user.id,
                    actor_user_id=actor_user_id,
                    operation="create",
                    project_name=item.name,
                    request_payload=_project_payload_for_history(item),
                    error_message=crud.PROJECT_NAME_UNAVAILABLE_MESSAGE,
                    error_code="PROJECT_NAME_UNAVAILABLE",
                    via_impersonation=via_impersonation,
                )
                notices.append(f'Проект "{item.name}" не создан: {crud.PROJECT_NAME_UNAVAILABLE_MESSAGE}')
                continue
            raise
        db_sess.refresh(project_row)
        created.append(crud._project_to_out(project_row))
        if success_notice:
            notices.append(success_notice)

    warning_text = "\n\n".join(notices) if notices else None
    return created, warning_text


@app.post("/projects", response_model=schemas.CreateProjectsOut)
def create_projects(payload: schemas.CreateProjectsPayload, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    try:
        _assert_projects_mutation_allowed(current_user)
    except HTTPException as exc:
        message = _http_error_message(exc.detail)
        code = _http_error_code(exc.detail)
        for item in payload.items:
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation="create",
                project_name=item.name,
                request_payload=_project_payload_for_history(item),
                error_message=message,
                error_code=code,
                via_impersonation=via_impersonation,
            )
        raise
    for item in payload.items:
        try:
            _validate_create_project_item_name(item)
        except HTTPException as exc:
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation="create",
                project_name=item.name,
                request_payload=_project_payload_for_history(item),
                error_message=_http_error_message(exc.detail),
                error_code=_http_error_code(exc.detail),
                via_impersonation=via_impersonation,
            )
            raise

    if any(_is_pixel_collection_source(item.collectionSource) for item in payload.items):
        if not _client_has_pixel_table_url(db_sess, int(current_user.id)):
            detail = {
                "message": "Чтобы создать Пиксель-проект, сначала заполните «Таблица клиента: Пиксель» в карточке клиента."
            }
            for item in payload.items:
                if _is_pixel_collection_source(item.collectionSource):
                    _record_failed_project_operation(
                        user_id=current_user.id,
                        actor_user_id=actor_user_id,
                        operation="create",
                        project_name=item.name,
                        request_payload=_project_payload_for_history(item),
                        error_message=detail["message"],
                        error_code="pixel_table_url_required",
                        via_impersonation=via_impersonation,
                    )
            raise HTTPException(status_code=422, detail=detail)

    client_internal_prefix: Optional[str] = None
    if bool(getattr(current_user, "unique_project_names_enabled", False)):
        client_internal_prefix = _client_internal_prefix_for_user(db_sess, int(current_user.id))
        if client_internal_prefix:
            for item in payload.items:
                if _is_pixel_collection_source(item.collectionSource):
                    continue
                item.name = _build_project_name_with_client_internal_prefix(
                    item.dataSourceCode,
                    item.name,
                    client_internal_prefix,
                )
                item.tag = item.name

    _ensure_create_project_names_available(
        db_sess,
        payload.items,
        user_id=current_user.id,
        actor_user_id=actor_user_id,
        via_impersonation=via_impersonation,
    )

    # 1) Создаём проекты у поставщика (частичный успех допустим)
    notices: List[str] = []
    adjusted_items: List[schemas.CreateProjectItem] = []
    provider_ids: List[Optional[str]] = []
    duplicate_diagnostics: List[Optional[dict]] = []
    db_sess.rollback()
    for item in payload.items:
        notice_name = _project_name_for_client_message(item.name, item.dataSourceCode, client_internal_prefix)
        if _is_pixel_collection_source(item.collectionSource):
            provider_ids.append(None)
            adjusted_items.append(item)
            duplicate_diagnostics.append(None)
            notices.append(f'Проект "{item.name}" создан локально.')
            continue
        try:
            result = prostats.create_project(item)
            provider_id_value = str(result.get("provider_id") or "").strip() or None
            provider_ids.append(provider_id_value)
            adjusted_items.append(item)
            item_duplicate_diagnostics: Optional[dict] = None

            missing_items = result.get("missing_items") or []
            target_type = result.get("target_type")
            if missing_items and target_type in ("hosts", "calls"):
                item_duplicate_diagnostics = _build_admin_duplicate_diagnostics_with_new_session(
                    missing_items,
                    target_type,
                    reason="provider_missing_items",
                    mark_external_provider_only=True,
                )
                duplicates = _find_duplicates_with_new_session(
                    missing_items,
                    target_type,
                    user_id=current_user.id,
                    provider_project_id=provider_id_value,
                )
                notices.append(
                    prostats._build_partial_warning(
                        notice_name,
                        target_type,
                        missing_items,
                        duplicates,
                        action="создан",
                    ),
                )
                if target_type == "hosts":
                    item.sites = [s for s in (item.sites or []) if s not in missing_items]
                else:
                    item.phones = [p for p in (item.phones or []) if p not in missing_items]
            else:
                notices.append(f'Проект "{notice_name}" создан.')
            duplicate_diagnostics.append(item_duplicate_diagnostics)
        except prostats.ProstatsError as exc:
            target_type = prostats._type_from_collection(item.collectionSource)
            message = exc.message
            item_duplicate_diagnostics = None
            if prostats._should_check_duplicates(exc.message, target_type):
                message = "Данные номера/сайты используются в других проектах. Подробности недоступны."
                items_for_diagnostics = item.sites if target_type == "hosts" else item.phones
                item_duplicate_diagnostics = _build_admin_duplicate_diagnostics_with_new_session(
                    items_for_diagnostics or [],
                    target_type,
                    reason="provider_duplicate_error",
                    summary=(
                        "Провайдер отклонил набор как занятый, но среди проектов нашего ЛК совпадения не найдены. "
                        "Вероятно занято в ЛК провайдера."
                    ),
                )
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation="create",
                project_name=item.name,
                request_payload=_project_payload_for_history_with_duplicate_diagnostics(item, item_duplicate_diagnostics),
                error_message=message,
                error_code=str(exc.status_code) if exc.status_code else None,
                via_impersonation=via_impersonation,
            )
            notices.append(f'Проект "{notice_name}" не создан: {message}')

    # 2) Если все успешны — сохраняем у нас
    if not adjusted_items:
        warning_text = "\n\n".join(notices) if notices else "Не удалось создать проекты."
        raise HTTPException(status_code=422, detail={"message": warning_text})

    with SessionLocal() as write_sess:  # type: Session
        try:
            created = crud.create_projects(
                write_sess,
                adjusted_items,
                user_id=current_user.id,
                provider_ids=provider_ids,
                actor_user_id=actor_user_id,
                via_impersonation=via_impersonation,
                client_internal_prefix=client_internal_prefix,
                duplicate_diagnostics=duplicate_diagnostics,
            )
        except IntegrityError as exc:
            write_sess.rollback()
            if _is_project_name_unique_violation(exc):
                raise HTTPException(status_code=409, detail=_project_name_unavailable_detail())
            raise
    _run_limit_control_for_client_in_new_session(current_user.id, trigger="projects_create")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    for project in created:
        if project.collectionSource == "СМС":
            _queue_sms_project_notification(
                project_id=int(project.id),
                action="create",
                actor_user_id=actor_user_id,
            )
    warning_text = "\n\n".join(notices) if notices else None
    return schemas.CreateProjectsOut(items=created, warning=warning_text)


@app.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: int, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    project = crud.get_project(db_sess, project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}/top", response_model=schemas.ProjectOut)
def set_project_top(
    project_id: int,
    payload: schemas.ProjectTopUpdate,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    project_row = db_sess.get(models.Project, project_id)
    if not project_row or project_row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if project_row.status == "Удалён":
        raise HTTPException(status_code=409, detail="Удалённый проект нельзя отмечать как топ.")
    updated = crud.set_project_top(
        db_sess,
        project_id,
        is_top=payload.isTop,
        expose_internal_name=False,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return updated


@app.get("/projects/{project_id}/chart", response_model=schemas.ProjectChartOut)
def project_chart(
    project_id: int,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    project = db_sess.get(models.Project, int(project_id))
    if not project or int(project.user_id or 0) != int(current_user.id):
        raise HTTPException(status_code=404, detail="Project not found")
    start_local, end_local = _parse_date_range_in_settings_tz(fromDate, toDate)
    return crud.project_leads_chart(
        db_sess,
        project=project,
        start_local=start_local,
        end_local=end_local,
    )


@app.patch("/projects/{project_id}", response_model=schemas.UpdateProjectOut)
def update_project(project_id: int, payload: schemas.ProjectUpdate, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    operation = "delete" if payload.status == "Удалён" else "update"
    try:
        _assert_projects_mutation_allowed(current_user)
    except HTTPException as exc:
        _record_failed_project_operation(
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            operation=operation,
            project_id=project_id,
            project_name=payload.name,
            request_payload=_project_payload_for_history(payload),
            error_message=_http_error_message(exc.detail),
            error_code=_http_error_code(exc.detail),
            via_impersonation=via_impersonation,
        )
        raise
    project_row = db_sess.get(models.Project, project_id)
    if not project_row or project_row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    skip_provider_sync = _should_skip_provider_sync_for_status_change(project_row, payload.status)
    if project_row.status == "Удалён":
        _record_failed_project_operation(
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            operation=operation,
            project_id=project_id,
            project_name=project_row.name,
            request_payload=_project_payload_for_history(payload),
            error_message="Проект удалён. Редактирование запрещено.",
            error_code="PROJECT_DELETED",
            via_impersonation=via_impersonation,
        )
        raise HTTPException(status_code=409, detail="Проект удалён. Редактирование запрещено.")
    if not skip_provider_sync and not project_row.provider_project_id:
        _record_failed_project_operation(
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            operation=operation,
            project_id=project_id,
            project_name=project_row.name,
            request_payload=_project_payload_for_history(payload),
            error_message="Project is not linked to provider",
            error_code="PROVIDER_LINK_MISSING",
            via_impersonation=via_impersonation,
        )
        raise HTTPException(status_code=409, detail="Project is not linked to provider")
    try:
        payload.name = _validate_project_name_update(
            project_row,
            payload.name,
            allow_client_internal_prefix_restore=True,
        )
    except HTTPException as exc:
        _record_failed_project_operation(
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            operation=operation,
            project_id=project_id,
            project_name=project_row.name,
            request_payload=_project_payload_for_history(payload),
            error_message=_http_error_message(exc.detail),
            error_code=_http_error_code(exc.detail),
            via_impersonation=via_impersonation,
        )
        raise
    if payload.status != "Удалён":
        try:
            _ensure_project_name_available(db_sess, payload.name, exclude_project_id=project_id)
        except HTTPException:
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation=operation,
                project_id=project_id,
                project_name=project_row.name,
                request_payload=_project_payload_for_history(payload),
                error_message=crud.PROJECT_NAME_UNAVAILABLE_MESSAGE,
                error_code="PROJECT_NAME_UNAVAILABLE",
                via_impersonation=via_impersonation,
            )
            raise
    if _project_client_internal_prefix(project_row) or bool(getattr(project_row, "unique_name_applied", False)):
        payload.tag = payload.name
    if payload.status == "Активен" and project_row.status != "Активен":
        can_activate, reason = crud.can_activate_project_under_limit_control(
            db_sess,
            project_id=project_id,
            projected_data_limit=int(payload.dataLimit),
        )
        if not can_activate:
            detail = {
                "code": "LIMIT_CONTROL_BLOCK",
                "message": reason or "Нельзя включить проект из-за ограничения по лимитам клиента.",
            }
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation=operation,
                project_id=project_id,
                project_name=project_row.name,
                request_payload=_project_payload_for_history(payload),
                error_message=_http_error_message(detail),
                error_code=_http_error_code(detail),
                via_impersonation=via_impersonation,
            )
            raise HTTPException(
                status_code=409,
                detail=detail,
            )
    warning_text = None
    duplicate_diagnostics: Optional[dict] = None
    if not skip_provider_sync:
        project_snapshot = _project_snapshot_for_prostats(project_row)
        db_sess.rollback()
        try:
            if payload.status == "Удалён":
                prostats.delete_project(str(project_snapshot.provider_project_id), project_snapshot)
            else:
                result = prostats.update_project(str(project_snapshot.provider_project_id), project_snapshot, payload)
                missing_items = result.get("missing_items") or []
                target_type = result.get("target_type")
                if missing_items and target_type in ("hosts", "calls"):
                    duplicate_diagnostics = _build_admin_duplicate_diagnostics_with_new_session(
                        missing_items,
                        target_type,
                        reason="provider_missing_items",
                        exclude_project_id=project_snapshot.id,
                        mark_external_provider_only=True,
                    )
                    duplicates = _find_duplicates_with_new_session(
                        missing_items,
                        target_type,
                        user_id=current_user.id,
                        provider_project_id=project_snapshot.provider_project_id,
                        exclude_project_id=project_snapshot.id,
                    )
                    warning_text = prostats._build_partial_warning(
                        payload.name,
                        target_type,
                        missing_items,
                        duplicates,
                        action="обновлён",
                    )
                    if target_type == "hosts":
                        payload.sites = [s for s in (payload.sites or []) if s not in missing_items]
                    else:
                        payload.phones = [p for p in (payload.phones or []) if p not in missing_items]
        except prostats.ProstatsError as exc:
            detail = _prostats_http_detail(exc)
            if payload.status != "Удалён":
                target_type = prostats._type_from_collection(project_snapshot.collection_source)
                if prostats._should_check_duplicates(exc.message, target_type) and target_type in ("hosts", "calls"):
                    items = payload.sites if target_type == "hosts" else payload.phones
                    duplicate_error_diagnostics = _build_admin_duplicate_diagnostics_with_new_session(
                        items or [],
                        target_type,
                        reason="provider_duplicate_error",
                        exclude_project_id=project_snapshot.id,
                        summary=(
                            "Провайдер отклонил набор как занятый, но среди проектов нашего ЛК совпадения не найдены. "
                            "Вероятно занято в ЛК провайдера."
                        ),
                    )
                    duplicates = _find_duplicates_with_new_session(
                        items or [],
                        target_type,
                        user_id=current_user.id,
                        provider_project_id=project_snapshot.provider_project_id,
                        exclude_project_id=project_snapshot.id,
                    )
                    if duplicates:
                        detail["duplicates"] = duplicates
                        detail["message"] = (
                            "Домены уже используются в наших проектах."
                            if target_type == "hosts"
                            else "Номера уже используются в наших проектах."
                        )
                        _record_failed_project_operation(
                            user_id=current_user.id,
                            actor_user_id=actor_user_id,
                            operation=operation,
                            project_id=project_id,
                            project_name=project_snapshot.name,
                            request_payload=_project_payload_for_history_with_duplicate_diagnostics(
                                payload,
                                duplicate_error_diagnostics,
                            ),
                            error_message=_http_error_message(detail),
                            error_code=str(exc.status_code) if exc.status_code else None,
                            via_impersonation=via_impersonation,
                        )
                        raise HTTPException(status_code=422, detail=detail)
                    detail["message"] = "Данные номера/сайты используются в других проектах. Подробности недоступны."
                    _record_failed_project_operation(
                        user_id=current_user.id,
                        actor_user_id=actor_user_id,
                        operation=operation,
                        project_id=project_id,
                        project_name=project_snapshot.name,
                        request_payload=_project_payload_for_history_with_duplicate_diagnostics(
                            payload,
                            duplicate_error_diagnostics,
                        ),
                        error_message=_http_error_message(detail),
                        error_code=str(exc.status_code) if exc.status_code else None,
                        via_impersonation=via_impersonation,
                    )
                    raise HTTPException(status_code=422, detail=detail)
            _record_failed_project_operation(
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                operation=operation,
                project_id=project_id,
                project_name=project_snapshot.name,
                request_payload=_project_payload_for_history(payload),
                error_message=_http_error_message(detail),
                error_code=str(exc.status_code) if exc.status_code else None,
                via_impersonation=via_impersonation,
            )
            raise HTTPException(status_code=exc.status_code, detail=detail)

    if payload.status == "Удалён":
        with SessionLocal() as write_sess:  # type: Session
            ok = crud.delete_project(
                write_sess,
                project_id,
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                via_impersonation=via_impersonation,
            )
            if not ok:
                raise HTTPException(status_code=404, detail="Project not found")
            updated = crud.get_project(write_sess, project_id, user_id=current_user.id)
            if not updated:
                raise HTTPException(status_code=404, detail="Project not found")
    else:
        with SessionLocal() as write_sess:  # type: Session
            try:
                updated = crud.update_project(
                    write_sess,
                    project_id,
                    payload,
                    user_id=current_user.id,
                    actor_user_id=actor_user_id,
                    via_impersonation=via_impersonation,
                    duplicate_diagnostics=duplicate_diagnostics,
                )
            except IntegrityError as exc:
                write_sess.rollback()
                if _is_project_name_unique_violation(exc):
                    raise HTTPException(status_code=409, detail=_project_name_unavailable_detail())
                raise
            if not updated:
                raise HTTPException(status_code=404, detail="Project not found")
        _run_limit_control_for_client_in_new_session(current_user.id, trigger="project_update")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    if payload.status != "Удалён" and updated.collectionSource == "СМС":
        _queue_sms_project_notification(
            project_id=int(updated.id),
            action="update",
            actor_user_id=actor_user_id,
        )
    return schemas.UpdateProjectOut(project=updated, warning=warning_text)


@app.get("/projects/{project_id}/history", response_model=List[schemas.ProjectHistoryItem])
def project_history(
    project_id: int,
    limit: int = 100,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    """
    История изменений проекта.

    Сейчас клиент видит только свои изменения: фильтрация по user_id происходит в crud.list_project_history.
    """
    # Сначала убеждаемся, что проект принадлежит пользователю (или доступен ему)
    project = crud.get_project(db_sess, project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return crud.list_project_history(db_sess, project_id=project_id, user_id=current_user.id, limit=limit)


@app.delete("/projects/{project_id}")
def delete_project(project_id: int, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    try:
        _assert_projects_mutation_allowed(current_user)
    except HTTPException as exc:
        _record_failed_project_operation(
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            operation="delete",
            project_id=project_id,
            request_payload={"projectId": project_id},
            error_message=_http_error_message(exc.detail),
            error_code=_http_error_code(exc.detail),
            via_impersonation=via_impersonation,
        )
        raise
    project_row = db_sess.get(models.Project, project_id)
    if not project_row or project_row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if _is_pixel_project(project_row):
        db_sess.rollback()
        with SessionLocal() as write_sess:  # type: Session
            ok = crud.delete_project(
                write_sess,
                project_id,
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                via_impersonation=via_impersonation,
            )
            if not ok:
                raise HTTPException(status_code=404, detail="Project not found")
        _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
        return {"deleted": True}
    if not project_row.provider_project_id:
        _record_failed_project_operation(
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            operation="delete",
            project_id=project_id,
            project_name=project_row.name,
            request_payload={"projectId": project_id},
            error_message="Проект не связан с Prostats. Удаление запрещено.",
            error_code="PROVIDER_LINK_MISSING",
            via_impersonation=via_impersonation,
        )
        raise HTTPException(status_code=409, detail="Проект не связан с Prostats. Удаление запрещено.")
    try:
        project_snapshot = _project_snapshot_for_prostats(project_row)
        db_sess.rollback()
        prostats.delete_project(str(project_snapshot.provider_project_id), project_snapshot)
    except prostats.ProstatsError as exc:
        detail = _prostats_http_detail(exc)
        _record_failed_project_operation(
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            operation="delete",
            project_id=project_id,
            project_name=project_snapshot.name,
            request_payload={"projectId": project_id},
            error_message=_http_error_message(detail),
            error_code=str(exc.status_code) if exc.status_code else None,
            via_impersonation=via_impersonation,
        )
        raise HTTPException(status_code=exc.status_code, detail=detail)

    with SessionLocal() as write_sess:  # type: Session
        ok = crud.delete_project(
            write_sess,
            project_id,
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            via_impersonation=via_impersonation,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Project not found")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    return {"deleted": True}


@app.post("/login")
@limiter.limit("5/minute")
def login(request: Request, payload: dict, db_sess: Session = Depends(get_db)):
    login_str = str(payload.get("login") or payload.get("username") or "")
    password = str(payload.get("password", ""))
    if not login_str or not password:
        raise HTTPException(status_code=400, detail="login and password are required")
    user = crud.get_user_by_login(db_sess, login_str)
    if not user or not auth.verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Bad credentials")
    # Админом является пользователь с id=1
    is_admin = user.id == 1
    if crud.is_agent_user(user) and crud.user_is_disabled(user):
        raise HTTPException(status_code=403, detail="Агент отключён.")
    role = crud.get_user_role(user)
    token = auth.create_access_token(user.id, is_admin=is_admin, role=role)
    return {"access_token": token, "is_admin": is_admin, "role": role}


@app.post("/auth/logout")
def logout():
    # Для JWT на клиенте — серверного state нет. Возвращаем ok.
    return {"ok": True}


@app.post("/support-message", response_model=schemas.QueuedNotificationOut)
def support_message(
    payload: schemas.SupportMessageIn,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    """
    Сообщение в поддержку из ЛК. Backend ставит его в Telegram outbox,
    а внешний worker доставляет его в Telegram.
    """
    chat_id = settings["TELEGRAM_CHAT_ID"]

    # Форматируем сообщение для оператора
    text = (
        "<b>[ЛК | Сообщение от клиента]</b>\n"
        f"Пользователь: <code>{html.escape(str(current_user.login))}</code> (id={current_user.id})\n"
        f"Телефон: <code>{html.escape(str(payload.phone))}</code>\n\n"
        f"{html.escape(str(payload.text))}"
    )

    db_sess.rollback()
    result = notifications.send_telegram_notification(
        db_sess=db_sess,
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        kind="support",
        metadata={"user_id": int(current_user.id), "phone": payload.phone},
    )
    if not result.delivered:
        if result.reason == "telegram_disabled":
            raise HTTPException(status_code=503, detail="Telegram уведомления глобально отключены")
        raise HTTPException(status_code=500, detail="Не удалось поставить сообщение в очередь Telegram")
    return {"ok": True, "status": "queued", "notificationId": result.notification_id}


# ----------------------- Лиды -----------------------
@app.get("/leads", response_model=schemas.LeadsListOut)
def list_leads(
    projectIds: Optional[str] = None,  # "1,2,3"; если нет — все
    sources: Optional[str] = None,     # "B1,B2"; если нет — все
    collectionSources: Optional[str] = None,  # "Пиксель,Сайты"; если нет — все
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
    q: Optional[str] = None,         # поиск по телефону и источникам
    offset: int = 0,
    limit: int = 50,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    # Границы дат в локальной TZ; в БД храним локальные naive
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        # Фолбэк для окружений без tzdata (Windows) — фиксированный +03:00
        tz = timezone(timedelta(hours=3))
    today_msk = datetime.now(tz).date()
    if not fromDate:
        fromDate = today_msk.isoformat()
    if not toDate:
        toDate = today_msk.isoformat()

    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz)

    start_naive = start_local.replace(tzinfo=None)
    end_naive = end_local.replace(tzinfo=None)

    proj_ids: Optional[List[int]] = None
    if projectIds:
        try:
            proj_ids = [int(x) for x in projectIds.split(',') if x.strip()]
            if not proj_ids:
                proj_ids = None
        except Exception:
            proj_ids = None

    src_list: Optional[List[str]] = None
    if sources:
        src_list = [s.strip() for s in sources.split(",") if s.strip()]
        if not src_list:
            src_list = None

    collection_src_list: Optional[List[str]] = None
    if collectionSources:
        collection_src_list = [s.strip() for s in collectionSources.split(",") if s.strip()]
        if not collection_src_list:
            collection_src_list = None

    limit = max(1, min(1000, limit))
    offset = max(0, offset)
    return crud.list_provider_leads_paginated(
        db_sess,
        project_ids=proj_ids,
        start_local=start_naive,
        end_local=end_naive,
        offset=offset,
        limit=limit,
        sources=src_list,
        collection_sources=collection_src_list,
        search_query=q,
        user_id=current_user.id,
    )


@app.get("/leads/export")
def export_leads(
    request: Request,
    projectIds: Optional[str] = None,
    sources: Optional[str] = None,
    collectionSources: Optional[str] = None,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    format: Optional[str] = "csv",  # csv | xlsx
    source: Optional[str] = "leads",
    clientId: Optional[int] = None,
    db_sess: Session = Depends(get_db),
):
    # Проверяем аутентификацию (cookies или headers)
    token = None

    # Сначала проверяем Authorization header
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    else:
        # Проверяем cookies
        token = request.cookies.get("access_token")
        if not token:
            # Fallback для совместимости - query parameter (deprecated)
            token = request.query_params.get("token")

    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = auth.decode_access_token(token or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    current_user = db_sess.get(models.User, int(user_id))
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if crud.is_agent_user(current_user) and crud.user_is_disabled(current_user):
        raise HTTPException(status_code=403, detail="Агент отключён.")

    # Границы дат локальные (MSK)
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))
    today = datetime.now(tz).date()
    if not fromDate:
        fromDate = today.isoformat()
    if not toDate:
        toDate = today.isoformat()
    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0).replace(tzinfo=None)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59).replace(tzinfo=None)

    use_provider_leads = True
    user_role = crud.get_user_role(current_user)
    is_admin = crud.is_admin_user(current_user)
    is_agent = crud.is_agent_user(current_user)

    effective_client_id: Optional[int]
    if is_admin:
        effective_client_id = clientId
    elif is_agent:
        if clientId is None:
            raise HTTPException(status_code=400, detail="clientId is required for agent export")
        _ensure_manager_client_access(db_sess, current_user, clientId)
        effective_client_id = clientId
    else:
        effective_client_id = current_user.id

    # Разрешённые проекты (для фильтрации provider_leads у клиентов/админа с clientId)
    allowed_ids = set(crud.get_user_project_ids(db_sess, effective_client_id or current_user.id))
    if not is_admin and not allowed_ids:
        empty_headers = {"Content-Disposition": f'attachment; filename="leads_empty.{format or "csv"}"'}
        if (format or "csv").lower() == "xlsx":
            return Response(content=b"", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=empty_headers)
        return Response(content="", media_type="text/csv", headers=empty_headers)
    logging.getLogger("app").info(
        "export_leads: user=%s clientId=%s allowed_ids=%s raw_projectIds=%s",
        current_user.id,
        clientId,
        sorted(list(allowed_ids)),
        projectIds,
    )
    if not use_provider_leads and not allowed_ids:
        empty_headers = {"Content-Disposition": f'attachment; filename="leads_empty.{format or "csv"}"'}
        if (format or "csv").lower() == "xlsx":
            return Response(content=b"", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=empty_headers)
        return Response(content="", media_type="text/csv", headers=empty_headers)

    proj_ids: Optional[List[int]] = None
    provider_allowed_ids: Optional[set[int]] = None
    if not is_admin:
        provider_allowed_ids = allowed_ids
    elif clientId is not None and clientId != 1:
        provider_allowed_ids = set(crud.get_user_project_ids(db_sess, clientId))
    if provider_allowed_ids is not None and not provider_allowed_ids:
        empty_headers = {"Content-Disposition": f'attachment; filename="leads_empty.{format or "csv"}"'}
        if (format or "csv").lower() == "xlsx":
            return Response(content=b"", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=empty_headers)
        return Response(content="", media_type="text/csv", headers=empty_headers)

    if projectIds:
        try:
            proj_ids = [int(x) for x in projectIds.split(',') if x.strip()]
            if not proj_ids:
                proj_ids = None
            else:
                if provider_allowed_ids is not None:
                    proj_ids = [pid for pid in proj_ids if pid in provider_allowed_ids]
                    if proj_ids is not None and len(proj_ids) == 0:
                        empty_headers = {"Content-Disposition": f'attachment; filename="leads_empty.{format or "csv"}"'}
                        if (format or "csv").lower() == "xlsx":
                            return Response(content=b"", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=empty_headers)
                        return Response(content="", media_type="text/csv", headers=empty_headers)
                else:
                    proj_ids = None
        except Exception:
            proj_ids = None
    else:
        if provider_allowed_ids is not None:
            proj_ids = sorted(provider_allowed_ids)

    src_list: Optional[List[str]] = None
    if sources:
        src_list = [s.strip() for s in sources.split(",") if s.strip()]
        if not src_list:
            src_list = None

    collection_src_list: Optional[List[str]] = None
    if collectionSources:
        collection_src_list = [s.strip() for s in collectionSources.split(",") if s.strip()]
        if not collection_src_list:
            collection_src_list = None

    # Логируем экспорт отчёта (для вкладки "Отчёты").
    # Повторные скачивания из раздела "Отчёты" помечаем source=reports и не логируем,
    # чтобы не плодить дубли.
    if (source or "leads") != "reports":
        try:
            crud.log_report_export(
                db_sess,
                user_id=current_user.id,
                client_id=effective_client_id,
                from_date=fromDate,
                to_date=toDate,
                project_ids=proj_ids,
                fmt=(format or "csv"),
            )
        except Exception:
            # Не блокируем выгрузку, если логирование по какой-то причине не удалось
            logging.getLogger("app").exception("Failed to log report export")
    
    # Максимум строк в одном экспорте отчета /leads/export (CSV/XLSX).
    try:
        max_rows = int(os.getenv("EXPORT_MAX_ROWS", "15000"))
    except ValueError:
        logging.getLogger("app").warning("Invalid EXPORT_MAX_ROWS value, fallback to 15000")
        max_rows = 15000
    max_rows = max(1, max_rows)
    # Для менеджерского отчёта по всем клиентам клиент определяется по проекту каждой строки.
    export_user_info = crud._get_user_info(db_sess, effective_client_id) if effective_client_id is not None else None
    if export_user_info is None and not is_admin:
        export_user_info = crud._get_user_info(db_sess, current_user.id)
    export_started = time.perf_counter()
    include_client_column = is_admin or is_agent

    filename = f"leads_{fromDate}_{toDate}.{format}"

    def iter_export_rows():
        yield from crud.iter_provider_leads_for_export(
            db_sess,
            project_ids=proj_ids,
            start_local=start_local,
            end_local=end_local,
            max_rows=max_rows,
            sources=src_list,
            collection_sources=collection_src_list,
            user_info=export_user_info,
            expose_internal_names=(is_admin or is_agent),
        )

    if (format or "csv").lower() == "csv":
        def gen():
            rows_count = 0
            try:
                headers = ["Дата", "Телефон", "Канал", "Источники", "Проект", "lk id"]
                if include_client_column:
                    headers.extend(["ext_id", "Клиент"])
                yield _csv_export_line(headers).encode('utf-8-sig')
                for r in iter_export_rows():
                    row = [
                        r["imported_at"],
                        r["phone"],
                        _source_code_for_display(r["source"]),
                        _source_text_for_display(r["utm_campaign"]),
                        _project_name_for_display(r["project_name"]),
                        r["lk_id"],
                    ]
                    if include_client_column:
                        row.extend([r["ext_id"], r["user_name"]])
                    rows_count += 1
                    yield _csv_export_line(row).encode('utf-8')
            finally:
                duration_ms = int((time.perf_counter() - export_started) * 1000)
                logging.getLogger("app").info(
                    "export_leads_done format=csv rows=%s max_rows=%s user=%s clientId=%s ms=%s",
                    rows_count,
                    max_rows,
                    current_user.id,
                    clientId,
                    duration_ms,
                )
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(gen(), media_type="text/csv; charset=utf-8", headers=headers)
    else:
        # XLSX пишем в temp-файл и отдаём как FileResponse.
        from openpyxl import Workbook

        tmp = tempfile.NamedTemporaryFile(prefix="leads_export_", suffix=".xlsx", delete=False)
        tmp_path = tmp.name
        tmp.close()

        wb = None
        rows_count = 0
        try:
            wb = Workbook(write_only=True)
            ws = wb.create_sheet(title="leads")
            headers = ["Дата", "Телефон", "Канал", "Источники", "Проект", "lk id"]
            if include_client_column:
                headers.extend(["ext_id", "Клиент"])
            ws.append(headers)

            for r in iter_export_rows():
                row = [
                    r["imported_at"],
                    r["phone"],
                    _source_code_for_display(r["source"]),
                    _source_text_for_display(r["utm_campaign"]),
                    _project_name_for_display(r["project_name"]),
                    r["lk_id"],
                ]
                if include_client_column:
                    row.extend([r["ext_id"], r["user_name"]])
                ws.append(row)
                rows_count += 1

            wb.save(tmp_path)
            duration_ms = int((time.perf_counter() - export_started) * 1000)
            logging.getLogger("app").info(
                "export_leads_done format=xlsx rows=%s max_rows=%s user=%s clientId=%s ms=%s",
                rows_count,
                max_rows,
                current_user.id,
                clientId,
                duration_ms,
            )
        except Exception:
            duration_ms = int((time.perf_counter() - export_started) * 1000)
            logging.getLogger("app").exception(
                "export_leads_failed format=xlsx rows=%s max_rows=%s user=%s clientId=%s ms=%s",
                rows_count,
                max_rows,
                current_user.id,
                clientId,
                duration_ms,
            )
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass
            _cleanup_temp_file(tmp_path)
            raise
        finally:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass

        return FileResponse(
            path=tmp_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
            background=BackgroundTask(_cleanup_temp_file, tmp_path),
        )


@app.get("/reports", response_model=schemas.ReportListOut)
def list_reports(
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,  # YYYY-MM-DD — фильтр по дате создания отчёта (начало дня)
    toDate: Optional[str] = None,    # YYYY-MM-DD — фильтр по дате создания отчёта (конец дня)
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    """
    Вкладка «Отчёты»: история всех экспортов отчётов текущего пользователя.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    limit = max(1, min(500, limit))
    offset = max(0, offset)

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today_msk = datetime.now(tz).date()
    # По умолчанию — отчёты за последние 90 дней (по дате создания)
    if not fromDate:
        fromDate = (today_msk - timedelta(days=90)).isoformat()
    if not toDate:
        toDate = today_msk.isoformat()

    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz)

    start_naive = start_local.replace(tzinfo=None)
    end_naive = end_local.replace(tzinfo=None)

    return crud.list_reports_paginated(
        db_sess,
        user_id=current_user.id,
        offset=offset,
        limit=limit,
        start_local=start_naive,
        end_local=end_naive,
    )


@app.post("/reports", response_model=schemas.ReportOut)
def create_report(
    payload: schemas.ClientCreateReportIn,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    """
    Логирует запрос на экспорт отчёта для текущего клиента.
    """
    proj_ids = payload.projectIds or None
    row = crud.log_report_export(
        db_sess,
        user_id=current_user.id,
        client_id=current_user.id,
        from_date=payload.fromDate,
        to_date=payload.toDate,
        project_ids=proj_ids,
        fmt=payload.format,
    )
    return schemas.ReportOut(
        id=row.id,
        createdAt=row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        fromDate=row.from_date,
        toDate=row.to_date,
        projectIds=row.project_ids,
        format=row.format,
    )


@app.get("/activity/events", response_model=schemas.ActivityEventListOut)
def list_client_activity_events(
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,  # YYYY-MM-DD (optional)
    toDate: Optional[str] = None,    # YYYY-MM-DD (optional)
    entities: Optional[str] = None,  # project,blacklist,balance,report
    q: Optional[str] = None,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    """
    Единая история действий по текущему аккаунту клиента.

    Источники данных:
    - audit_events (проекты и чёрный список),
    - client_balance_operations (начисления/списания),
    - report_exports (создание отчётов).
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    limit = max(1, min(500, limit))
    offset = max(0, offset)

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    start_naive: Optional[datetime] = None
    end_naive: Optional[datetime] = None
    if fromDate or toDate:
        if not fromDate and toDate:
            fromDate = toDate
        if not toDate and fromDate:
            toDate = fromDate
        try:
            y, m, d = [int(x) for x in (fromDate or "").split("-")]
            start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
            y2, m2, d2 = [int(x) for x in (toDate or "").split("-")]
            end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid fromDate/toDate format, expected YYYY-MM-DD")
        start_naive = start_local.replace(tzinfo=None)
        end_naive = end_local.replace(tzinfo=None)

    entities_list = [e.strip() for e in (entities or "").split(",") if e.strip()] or None
    return crud.list_client_activity_events(
        db_sess,
        client_id=current_user.id,
        offset=offset,
        limit=limit,
        start_local=start_naive,
        end_local=end_naive,
        entities=entities_list,
        q=q,
    )


@app.get("/activity/events/{event_id}", response_model=schemas.HistoryEventDetailOut)
def get_activity_event_detail(
    event_id: str,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    detail = crud.get_history_event_detail(db_sess, event_id=event_id, viewer=current_user)
    if not detail:
        raise HTTPException(status_code=404, detail="History event not found")
    return detail


@app.get("/admin/activity/events", response_model=schemas.ActivityEventListOut)
def list_admin_activity_events(
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    clientId: Optional[int] = None,
    entities: Optional[str] = None,
    q: Optional[str] = None,
    status: Optional[str] = None,  # all|success|failed|pending|done
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """
    Единая история действий по клиентам, доступным текущему manager-пользователю.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    limit = max(1, min(500, limit))
    offset = max(0, offset)

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    start_naive: Optional[datetime] = None
    end_naive: Optional[datetime] = None
    if fromDate or toDate:
        if not fromDate and toDate:
            fromDate = toDate
        if not toDate and fromDate:
            toDate = fromDate
        try:
            y, m, d = [int(x) for x in (fromDate or "").split("-")]
            start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
            y2, m2, d2 = [int(x) for x in (toDate or "").split("-")]
            end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid fromDate/toDate format, expected YYYY-MM-DD")
        start_naive = start_local.replace(tzinfo=None)
        end_naive = end_local.replace(tzinfo=None)

    if clientId is not None:
        _ensure_manager_client_access(db_sess, current_manager, int(clientId))
        client_ids = [int(clientId)]
    else:
        client_ids = crud.get_accessible_client_ids_for_manager(db_sess, current_manager)

    entities_list = [e.strip() for e in (entities or "").split(",") if e.strip()] or None
    status_value = (status or "all").strip().lower()
    if status_value not in ("all", "success", "failed", "pending", "done"):
        status_value = "all"

    return crud.list_activity_events_for_clients(
        db_sess,
        client_ids=client_ids,
        offset=offset,
        limit=limit,
        start_local=start_naive,
        end_local=end_naive,
        entities=entities_list,
        q=q,
        status=status_value,
        include_client=True,
    )

# ----------------------- Черный список -----------------------
@app.get("/blacklist", response_model=schemas.BlacklistListOut)
def list_blacklist(offset: int = 0, limit: int = 50, q: str | None = None, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.list_blacklist_paginated(db_sess, user_id=current_user.id, offset=offset, limit=limit, q=q)


@app.post("/blacklist", response_model=List[schemas.BlacklistPhoneOut])
def add_blacklist(payload: schemas.BlacklistAddIn, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    items = crud.add_to_blacklist(
        db_sess,
        current_user.id,
        payload.phones,
        actor_user_id=actor_user_id,
        via_impersonation=via_impersonation,
    )
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return items


@app.delete("/blacklist/{row_id}")
def delete_blacklist(row_id: int, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    ok = crud.delete_from_blacklist(
        db_sess,
        current_user.id,
        row_id,
        actor_user_id=actor_user_id,
        via_impersonation=via_impersonation,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return {"deleted": True}


# =====================================================
# =================== ADMIN ENDPOINTS =================
# =====================================================

def require_admin(request: Request, db_sess: Session = Depends(get_db)):
    """
    Проверяет, что текущий пользователь — админ (id=1).
    """
    token = None

    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    else:
        token = request.query_params.get("token")

    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = auth.decode_access_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = db_sess.get(models.User, int(user_id))
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Проверяем, что это админ (id=1)
    if user.id != 1:
        raise HTTPException(status_code=403, detail="Admin access required")

    return user


app.include_router(build_analytics_router(
    require_admin, get_db,
    _source_code_for_display, _source_text_for_display, _project_name_for_display,
))

# Machine authentication is isolated from human admin/JWT authorization.
from .agent_api.router import build_app as build_agent_app

app.mount("/agent/v1", build_agent_app(get_db, settings))


@app.post("/admin/operator-block-check/run", response_model=schemas.OperatorBlockCheckOut)
def admin_run_operator_block_check(
    current_admin: models.User = Depends(require_admin),
):
    return _run_operator_block_check(trigger=f"manual:{current_admin.id}")


@app.get("/admin/dashboard", response_model=schemas.AdminDashboardOut)
def admin_dashboard(
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    clientId: Optional[int] = None,
    sources: Optional[str] = None,
    includeAgentClients: bool = True,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today = datetime.now(tz).date()
    yesterday = today - timedelta(days=1)
    if not fromDate:
        fromDate = yesterday.isoformat()
    if not toDate:
        toDate = yesterday.isoformat()

    try:
        y, m, d = [int(x) for x in fromDate.split("-")]
        y2, m2, d2 = [int(x) for x in toDate.split("-")]
        start_date = datetime(y, m, d, 0, 0, 0, tzinfo=tz).date()
        end_date = datetime(y2, m2, d2, 0, 0, 0, tzinfo=tz).date()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Некорректный формат дат. Используйте YYYY-MM-DD.") from exc

    if end_date < start_date:
        raise HTTPException(status_code=422, detail="Дата окончания не может быть раньше даты начала.")

    def day_start(value):
        return datetime(value.year, value.month, value.day, 0, 0, 0, tzinfo=tz).replace(tzinfo=None)

    selected_start = day_start(start_date)
    selected_end = day_start(end_date + timedelta(days=1))
    today_start = day_start(today)
    today_end = day_start(today + timedelta(days=1))
    yesterday_start = day_start(yesterday)
    yesterday_end = day_start(today)
    last7_start = day_start(today - timedelta(days=6))
    last30_start = day_start(today - timedelta(days=29))
    chart_start = day_start(today - timedelta(days=29))
    chart_end = today_end

    src_list: Optional[List[str]] = None
    if sources:
        src_list = [s.strip().upper() for s in sources.split(",") if s.strip()]
        src_list = [s for s in src_list if s in {"B1", "B2", "B3", "B4"}]
        if not src_list:
            src_list = None

    return crud.admin_dashboard(
        db_sess,
        start_local=selected_start,
        end_local=selected_end,
        today_start=today_start,
        today_end=today_end,
        yesterday_start=yesterday_start,
        yesterday_end=yesterday_end,
        last7_start=last7_start,
        last30_start=last30_start,
        chart_start=chart_start,
        chart_end=chart_end,
        client_id=clientId,
        sources=src_list,
        include_agent_clients=bool(includeAgentClients),
    )


@app.post("/admin/provider-leads-import/preview", response_model=schemas.AdminProviderLeadsImportPreviewOut)
async def admin_preview_provider_leads_import(
    file: UploadFile = File(...),
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Файл не выбран")

    try:
        file_bytes = await file.read()
        result = provider_leads_import.create_preview(
            db_sess,
            admin_user_id=int(current_admin.id),
            file_name=filename,
            file_bytes=file_bytes,
        )
        return result
    except provider_leads_import.ProviderLeadsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@app.post("/admin/provider-leads-import/commit", response_model=schemas.AdminProviderLeadsImportCommitOut)
def admin_commit_provider_leads_import(
    payload: schemas.AdminProviderLeadsImportCommitIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    try:
        return provider_leads_import.commit_preview(
            db_sess,
            preview_id=payload.previewId,
            admin_user_id=int(current_admin.id),
        )
    except provider_leads_import.ProviderLeadsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@app.post("/admin/pixel-leads-import/preview", response_model=schemas.AdminProviderLeadsImportPreviewOut)
async def admin_preview_pixel_leads_import(
    file: UploadFile = File(...),
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Файл не выбран")

    try:
        file_bytes = await file.read()
        result = provider_leads_import.create_pixel_preview(
            db_sess,
            admin_user_id=int(current_admin.id),
            file_name=filename,
            file_bytes=file_bytes,
        )
        return result
    except provider_leads_import.ProviderLeadsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@app.post("/admin/pixel-leads-import/commit", response_model=schemas.AdminProviderLeadsImportCommitOut)
def admin_commit_pixel_leads_import(
    payload: schemas.AdminProviderLeadsImportCommitIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    try:
        return provider_leads_import.commit_pixel_preview(
            db_sess,
            preview_id=payload.previewId,
            admin_user_id=int(current_admin.id),
        )
    except provider_leads_import.ProviderLeadsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def create_impersonation_token(
    target_user_id: int,
    impersonator_user_id: int,
    role: str = "client",
    ttl_minutes: int = 1440,
    allow_disabled_target: bool = False,
) -> str:
    """Генерируем JWT для служебного входа под target-пользователем без manager-прав."""
    from datetime import timedelta

    extra_claims = {"impersonator_user_id": int(impersonator_user_id)}
    if allow_disabled_target:
        extra_claims["allow_disabled_target"] = True
    return auth.create_access_token(
        user_id=target_user_id,
        is_admin=False,
        role=str(role or "client"),
        expires_delta=timedelta(minutes=max(1, ttl_minutes)),
        extra_claims=extra_claims,
    )


@app.post("/admin/clients", response_model=schemas.AdminClientCreateOut)
def admin_create_client(
    payload: schemas.AdminClientCreateIn,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    try:
        return crud.admin_create_client(
            db_sess,
            name=payload.name,
            inn=payload.inn,
            phone=payload.phone,
            contact=payload.contact,
            login=payload.login,
            password=payload.password,
            auto_limit_control_enabled=bool(payload.autoLimitControlEnabled or False) if crud.is_admin_user(current_manager) else False,
            telegram_notifications_chat_id=(payload.telegramNotificationsChatId if crud.is_admin_user(current_manager) else None),
            telegram_auto_pause_enabled=bool(payload.telegramAutoPauseEnabled or False) if crud.is_admin_user(current_manager) else False,
            unique_project_names_enabled=bool(payload.uniqueProjectNamesEnabled or False) if crud.is_admin_user(current_manager) else False,
            internal_client_id=(payload.internalClientId if crud.is_admin_user(current_manager) else None),
            table_url=(payload.tableUrl if crud.is_admin_user(current_manager) else None),
            pixel_table_url=(payload.pixelTableUrl if crud.is_admin_user(current_manager) else None),
            owner_agent_id=(payload.ownerAgentId if crud.is_admin_user(current_manager) else int(current_manager.id)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/admin/clients/{client_id}/impersonate")
def admin_impersonate_client(
    client_id: int,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """
    Выдаёт токен (24 часа) для входа в ЛК клиента.
    Токен имеет payload user_id клиента, без is_admin.
    """
    client_user = db_sess.get(models.User, client_id)
    if not client_user or not crud.is_client_user(client_user):
        raise HTTPException(status_code=404, detail="Client not found")
    _ensure_manager_client_access(db_sess, current_manager, client_id)

    token = create_impersonation_token(
        target_user_id=client_user.id,
        impersonator_user_id=int(current_manager.id),
        role="client",
        ttl_minutes=1440,
    )
    return {"access_token": token, "ttl_minutes": 1440}


@app.post("/admin/agents/{agent_id}/impersonate")
def admin_impersonate_agent(
    agent_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """
    Выдаёт токен (24 часа) для служебного входа в ЛК агента.
    Даже если агент отключён, администратору такой вход разрешён.
    """
    agent_user = db_sess.get(models.User, agent_id)
    if not agent_user or not crud.is_agent_user(agent_user):
        raise HTTPException(status_code=404, detail="Agent not found")

    token = create_impersonation_token(
        target_user_id=agent_user.id,
        impersonator_user_id=int(current_admin.id),
        role="agent",
        ttl_minutes=1440,
        allow_disabled_target=True,
    )
    return {"access_token": token, "ttl_minutes": 1440}


@app.patch("/admin/clients/{client_id}", response_model=schemas.AdminClientUpdateOut)
def admin_update_client(
    client_id: int,
    payload: schemas.AdminClientUpdateIn,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    try:
        existing_user = db_sess.get(models.User, client_id)
        if not existing_user or not crud.is_client_user(existing_user):
            raise HTTPException(status_code=404, detail="Client not found")
        if crud.is_agent_user(current_manager) and not crud.manager_can_access_client(db_sess, current_manager, client_id):
            raise HTTPException(status_code=403, detail="Нет доступа к этому клиенту")

        prev_enabled = bool(getattr(existing_user, "telegram_auto_pause_enabled", False))
        prev_chat_id = str(getattr(existing_user, "telegram_notifications_chat_id", "") or "").strip()
        next_enabled = (
            bool(payload.telegramAutoPauseEnabled)
            if payload.telegramAutoPauseEnabled is not None and crud.is_admin_user(current_manager)
            else prev_enabled
        )
        next_chat_id = (
            str(payload.telegramNotificationsChatId or "").strip()
            if payload.telegramNotificationsChatId is not None and crud.is_admin_user(current_manager)
            else prev_chat_id
        )
        should_send_test = _should_send_auto_pause_test_message(
            prev_enabled=prev_enabled,
            prev_chat_id=prev_chat_id,
            next_enabled=next_enabled,
            next_chat_id=next_chat_id,
        )

        update_kwargs = dict(
            client_id=client_id,
            name=payload.name,
            inn=payload.inn,
            phone=payload.phone,
            contact=payload.contact,
            login=payload.login,
            password=payload.password,
            auto_limit_control_enabled=(payload.autoLimitControlEnabled if crud.is_admin_user(current_manager) else None),
            telegram_notifications_chat_id=(payload.telegramNotificationsChatId if crud.is_admin_user(current_manager) else None),
            telegram_auto_pause_enabled=(payload.telegramAutoPauseEnabled if crud.is_admin_user(current_manager) else None),
            unique_project_names_enabled=(payload.uniqueProjectNamesEnabled if crud.is_admin_user(current_manager) else None),
            internal_client_id=(payload.internalClientId if crud.is_admin_user(current_manager) else None),
            table_url=(payload.tableUrl if crud.is_admin_user(current_manager) else None),
            pixel_table_url=(payload.pixelTableUrl if crud.is_admin_user(current_manager) else None),
            owner_agent_id=None,
        )
        test_message = _build_auto_pause_test_message(existing_user)
        db_sess.rollback()

        with SessionLocal() as write_sess:  # type: Session
            result = crud.admin_update_client(
                write_sess,
                **update_kwargs,
                commit=True,
            )
            if should_send_test and crud.is_admin_user(current_manager):
                test_result = notifications.send_telegram_notification(
                    db_sess=write_sess,
                    chat_id=next_chat_id,
                    text=test_message,
                    parse_mode="HTML",
                    kind="route_test",
                    metadata={"client_id": client_id},
                )
                if test_result.delivered:
                    result.telegramTestStatus = "queued"
                    result.telegramTestNotificationId = test_result.notification_id
                else:
                    result.telegramTestStatus = "failed"
                    logging.getLogger("app").warning(
                        "Failed to queue Telegram route test for client_id=%s reason=%s",
                        client_id,
                        test_result.reason,
                    )
        if payload.autoLimitControlEnabled is True and crud.is_admin_user(current_manager):
            _run_limit_control_for_client_in_new_session(client_id=client_id, trigger="admin_toggle_on")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        db_sess.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/admin/clients/{client_id}/work-status", response_model=schemas.AdminClientWorkStatusUpdateOut)
def admin_update_client_work_status(
    client_id: int,
    payload: schemas.AdminClientWorkStatusUpdateIn,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    _ensure_manager_client_access(db_sess, current_manager, client_id)
    try:
        return crud.admin_update_client_work_status(
            db_sess,
            client_id=client_id,
            actor_user_id=int(current_manager.id),
            work_status=payload.workStatus,
        )
    except ValueError as exc:
        db_sess.rollback()
        message = str(exc)
        if "не найден" in message.lower():
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)


@app.get("/admin/agents", response_model=schemas.AdminAgentsListOut)
def admin_list_agents(
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    return crud.list_agents_summary(db_sess)


@app.post("/admin/agents", response_model=schemas.AdminAgentCreateOut)
def admin_create_agent(
    payload: schemas.AdminAgentCreateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    try:
        return crud.admin_create_agent(
            db_sess,
            name=payload.name,
            inn=payload.inn,
            phone=payload.phone,
            login=payload.login,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/admin/agents/{agent_id}", response_model=schemas.AdminAgentUpdateOut)
def admin_update_agent(
    agent_id: int,
    payload: schemas.AdminAgentUpdateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    try:
        existing_agent = db_sess.get(models.User, int(agent_id))
        if not existing_agent or not crud.is_agent_user(existing_agent):
            raise HTTPException(status_code=404, detail="Agent not found")
        was_disabled = bool(getattr(existing_agent, "is_disabled", False))
        agent_disable_lock_reason = "Агент отключён администратором"
        owned_client_ids = crud.get_accessible_client_ids_for_manager(db_sess, existing_agent)
        result = crud.admin_update_agent(
            db_sess,
            agent_id=agent_id,
            name=payload.name,
            inn=payload.inn,
            phone=payload.phone,
            login=payload.login,
            password=payload.password,
            is_disabled=payload.isDisabled,
        )
        if payload.isDisabled is True and not was_disabled:
            for client_id in owned_client_ids:
                _pause_and_lock_client_projects_by_admin(
                    client_id=int(client_id),
                    admin_user_id=int(current_admin.id),
                    reason=agent_disable_lock_reason,
                )
        if payload.isDisabled is False and was_disabled:
            for client_id in owned_client_ids:
                client_user = db_sess.get(models.User, int(client_id))
                if not client_user:
                    continue
                if not bool(getattr(client_user, "projects_mutation_locked", False)):
                    continue
                if (getattr(client_user, "projects_mutation_lock_reason", None) or None) != agent_disable_lock_reason:
                    continue
                crud.admin_set_client_projects_mutation_lock(
                    db_sess,
                    client_id=int(client_id),
                    locked=False,
                    admin_user_id=int(current_admin.id),
                    reason=None,
                )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/admin/agents/{agent_id}/balance", response_model=schemas.ClientBalanceSummaryOut)
def admin_agent_balance_summary(
    agent_id: int,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    agent = db_sess.get(models.User, int(agent_id))
    if not agent or not crud.is_agent_user(agent):
        raise HTTPException(status_code=404, detail="Agent not found")
    start_local, end_local = _parse_balance_date_range(fromDate, toDate)
    return crud.get_agent_balance_summary(db_sess, agent_id=agent_id, start_local=start_local, end_local=end_local)


@app.get("/admin/agents/{agent_id}/balance/ops", response_model=schemas.ClientBalanceOpsListOut)
def admin_agent_balance_ops(
    agent_id: int,
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    agent = db_sess.get(models.User, int(agent_id))
    if not agent or not crud.is_agent_user(agent):
        raise HTTPException(status_code=404, detail="Agent not found")
    start_local, end_local = _parse_balance_date_range(fromDate, toDate)
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.list_agent_client_balance_operations(
        db_sess,
        agent_id=agent_id,
        offset=offset,
        limit=limit,
        start_local=start_local,
        end_local=end_local,
    )


@app.post("/admin/agents/{agent_id}/balance/ops", response_model=schemas.BalanceOperationOut)
def admin_create_agent_balance_op(
    agent_id: int,
    payload: schemas.BalanceOperationCreateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    raise HTTPException(status_code=410, detail="Прямые операции баланса агента отключены.")


@app.get("/admin/agents/{agent_id}/tariffs", response_model=schemas.ClientTariffListOut)
def admin_agent_tariffs(
    agent_id: int,
    offset: int = 0,
    limit: int = 50,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    raise HTTPException(status_code=410, detail="Тарифы агентов отключены.")


@app.post("/admin/agents/{agent_id}/tariffs", response_model=schemas.ClientTariffOut)
def admin_create_agent_tariff(
    agent_id: int,
    payload: schemas.ClientTariffCreateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    raise HTTPException(status_code=410, detail="Тарифы агентов отключены.")


@app.post("/admin/clients/{client_id}/owner", response_model=schemas.ClientOwnerTransferOut)
def admin_transfer_client_owner(
    client_id: int,
    payload: schemas.ClientOwnerTransferIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    if payload.ownerType == "agent" and payload.agentId is None:
        raise HTTPException(status_code=400, detail="agentId is required for agent owner")
    try:
        return crud.transfer_client_to_owner(
            db_sess,
            client_id=client_id,
            owner_type=payload.ownerType,
            agent_id=payload.agentId,
            admin_user_id=int(current_admin.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/admin/users", response_model=List[schemas.UserInfo])
def admin_list_users(
    includeAgents: bool = False,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Список доступных клиентов; для админа по флагу можно добавить агентов."""
    users = crud.get_all_users(db_sess)
    if crud.is_admin_user(current_manager):
        allowed_roles = {"client"}
        if includeAgents:
            allowed_roles.add("agent")
        return [user for user in users if (user.role or "client") in allowed_roles]
    return [
        user
        for user in users
        if (user.role or "client") == "client" and int(user.ownerAgentId or 0) == int(current_manager.id)
    ]


@app.get("/admin/projects", response_model=schemas.AdminProjectListOut)
def admin_list_projects(
    offset: int = 0,
    limit: int = 50,
    q: str | None = None,
    userId: int | None = None,
    sources: Optional[str] = None,
    collectionSources: Optional[str] = None,
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
    includeDeleted: bool = False,
    includeArchived: bool = False,
    projectStatus: Optional[schemas.ProjectStatus] = None,
    dailyLimitReached: bool = False,
    isTop: bool = False,
    sortBy: Optional[str] = None,
    sortDir: Optional[str] = None,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Список всех проектов всех клиентов с поддержкой периода для подсчёта лидов."""
    limit = max(1, min(1000, limit))
    offset = max(0, offset)

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today_msk = datetime.now(tz).date()
    if not fromDate:
        fromDate = today_msk.isoformat()
    if not toDate:
        toDate = today_msk.isoformat()

    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz)

    start_naive = start_local.replace(tzinfo=None)
    end_naive = end_local.replace(tzinfo=None)

    if crud.is_agent_user(current_manager):
        if userId is None:
            return schemas.AdminProjectListOut(items=[], total=0)
        _ensure_manager_client_access(db_sess, current_manager, userId)

    src_list: Optional[List[str]] = None
    if sources:
        src_list = [s.strip().upper() for s in sources.split(",") if s.strip()]
        src_list = [s for s in src_list if s in {"B1", "B2", "B3", "B4"}]
        if not src_list:
            src_list = None

    collection_src_list: Optional[List[str]] = None
    if collectionSources:
        collection_src_list = [s.strip() for s in collectionSources.split(",") if s.strip()]
        if not collection_src_list:
            collection_src_list = None

    return crud.admin_list_all_projects(
        db_sess,
        offset=offset,
        limit=limit,
        q=q,
        user_id_filter=userId,
        sources=src_list,
        collection_sources=collection_src_list,
        start_local=start_naive,
        end_local=end_naive,
        include_deleted=includeDeleted,
        include_archived=includeArchived,
        project_status=projectStatus,
        daily_limit_reached=dailyLimitReached,
        is_top=isTop,
        sort_by=sortBy,
        sort_dir=sortDir,
    )


# =====================================================
# ===== «Лимит группы проектов на день» (admin-only) ==
# =====================================================

def _daily_limit_group_to_out(
    db_sess: Session,
    group: models.ProjectDailyExportLimitGroup,
    *,
    quota: daily_export_limit_groups.DailyExportLimitQuota,
    project_ids: List[int],
) -> schemas.DailyExportLimitGroupOut:
    return schemas.DailyExportLimitGroupOut(
        id=int(group.id),
        clientId=int(group.client_id),
        name=str(group.name),
        dailyLimit=int(group.daily_limit),
        projectIds=project_ids,
        projectCount=len(project_ids),
        exportedToday=int(quota.exported_today),
        remainingToday=int(quota.remaining_today),
        pendingTotal=int(quota.pending_total),
        limitReached=bool(quota.limit_reached),
        createdAt=group.created_at,
        updatedAt=group.updated_at,
    )


def _require_daily_limit_group_client(
    db_sess: Session,
    manager_user: models.User,
    client_id: int,
) -> models.User:
    """
    Группы лимита меняет только администратор (user.id == 1).

    Агент не может ни создавать, ни редактировать эти настройки, даже если
    клиент ему доступен: правило следует существующей модели прав проекта,
    где агент ограничен своими клиентами, а системные настройки — админские.
    """
    if not crud.is_admin_user(manager_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return _ensure_manager_client_access(db_sess, manager_user, client_id)


def _parse_int_list_param(value: Optional[str]) -> Optional[List[int]]:
    """Разбирает ``projectIds=1,2,3`` в список int.  None = фильтр не задан."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return []
    out: List[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            out.append(int(item))
        except ValueError:
            continue
    return out


@app.get("/admin/clients/{client_id}/daily-export-limit-groups", response_model=schemas.DailyExportLimitGroupListOut)
def admin_list_daily_export_limit_groups(
    client_id: int,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    _require_daily_limit_group_client(db_sess, current_manager, client_id)
    groups = daily_export_limit_groups.list_groups(db_sess, client_id)
    if not groups:
        # Групп нет — ограничение не настроено вовсе, предупреждать не о чем.
        return schemas.DailyExportLimitGroupListOut(items=[])
    members = daily_export_limit_groups.list_group_project_ids(
        db_sess, [int(group.id) for group in groups]
    )
    items = []
    for group in groups:
        project_ids = members.get(int(group.id), [])
        quota = daily_export_limit_groups.get_group_quota(
            db_sess, int(group.id), project_ids=project_ids
        )
        items.append(
            _daily_limit_group_to_out(
                db_sess, group, quota=quota, project_ids=project_ids
            )
        )
    return schemas.DailyExportLimitGroupListOut(
        items=items,
        unassignedProjectsCount=crud.count_client_projects_outside_limit_groups(
            db_sess, client_id=client_id
        ),
        unassignedProjectsTotal=crud.count_client_projects_for_limit_group(
            db_sess, client_id=client_id
        ),
    )


@app.get(
    "/admin/clients/{client_id}/daily-export-limit-groups/projects",
    response_model=schemas.DailyExportLimitGroupProjectListOut,
)
def admin_list_daily_export_limit_group_projects(
    client_id: int,
    q: Optional[str] = None,
    limit: int = 50,
    projectIds: Optional[str] = None,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """
    Проекты клиента, доступные для группы.

    Поиск работает по части названия и по LR-коду (``LR223``, ``[LR223]``).
    Для каждого проекта сразу отдаём группу, в которую он уже входит,
    чтобы UI мог заблокировать checkbox и показать её название.

    ``projectIds`` позволяет получить конкретные проекты по id — этим
    редактор группы подтягивает названия текущих участников, не загружая
    весь список проектов клиента.
    """
    _require_daily_limit_group_client(db_sess, current_manager, client_id)
    requested_ids = _parse_int_list_param(projectIds)
    projects = crud.search_client_projects_for_limit_group(
        db_sess, client_id=client_id, q=q, limit=limit, project_ids=requested_ids
    )
    project_ids = [int(project.id) for project in projects]
    owner_names = daily_export_limit_groups.list_group_names_by_project_ids(
        db_sess, project_ids
    )
    group_ids = daily_export_limit_groups.project_to_group_map(db_sess)

    items = [
        schemas.DailyExportLimitGroupProjectOut(
            id=int(project.id),
            name=str(project.name),
            tag=str(project.tag or "") or None,
            dataSourceCode=project.data_source_code,  # type: ignore[arg-type]
            collectionSource=project.collection_source,  # type: ignore[arg-type]
            status=project.status,  # type: ignore[arg-type]
            limitGroupId=group_ids.get(int(project.id)),
            limitGroupName=owner_names.get(int(project.id)),
        )
        for project in projects
    ]
    # total — реальное число совпадений, а не len(items): UI должен видеть,
    # что выдача обрезана, и не обещать "выбрано всё", выбрав часть.
    if requested_ids:
        total = len(items)
    else:
        total = crud.count_client_projects_for_limit_group(db_sess, client_id=client_id, q=q)
    return schemas.DailyExportLimitGroupProjectListOut(
        items=items, total=total, truncated=len(items) < total
    )


@app.post(
    "/admin/clients/{client_id}/daily-export-limit-groups",
    response_model=schemas.DailyExportLimitGroupOut,
)
def admin_create_daily_export_limit_group(
    client_id: int,
    payload: schemas.DailyExportLimitGroupCreateIn,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    _require_daily_limit_group_client(db_sess, current_manager, client_id)
    try:
        group = daily_export_limit_groups.create_group(
            db_sess,
            client_id=client_id,
            name=payload.name,
            daily_limit=payload.dailyLimit,
            project_ids=payload.projectIds,
        )
    except daily_export_limit_groups.DailyExportLimitGroupError as exc:
        db_sess.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    project_ids = daily_export_limit_groups.list_group_project_ids(
        db_sess, [int(group.id)]
    ).get(int(group.id), [])
    quota = daily_export_limit_groups.get_group_quota(
        db_sess, int(group.id), project_ids=project_ids
    )
    return _daily_limit_group_to_out(db_sess, group, quota=quota, project_ids=project_ids)


@app.patch(
    "/admin/daily-export-limit-groups/{group_id}",
    response_model=schemas.DailyExportLimitGroupOut,
)
def admin_update_daily_export_limit_group(
    group_id: int,
    payload: schemas.DailyExportLimitGroupUpdateIn,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """
    Изменение группы.

    Увеличение лимита сразу добавляет доступные места сегодня: уже
    выгруженные строки не трогаем, а следующий запуск provider export
    продолжит FIFO-очередь до нового потолка.

    Уменьшение лимита применяется сразу, но уже выгруженное за день
    назад не удаляется: если лимит стал меньше расхода, группа сегодня
    просто ничего не отправляет, а завтра квота уже новая.
    """
    if not crud.is_admin_user(current_manager):
        raise HTTPException(status_code=403, detail="Admin access required")
    group = daily_export_limit_groups.get_group(db_sess, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    client_id = int(group.client_id)
    _ensure_manager_client_access(db_sess, current_manager, client_id)

    try:
        updated = daily_export_limit_groups.update_group(
            db_sess,
            group_id=group_id,
            client_id=client_id,
            name=payload.name,
            daily_limit=payload.dailyLimit,
            project_ids=payload.projectIds,
        )
    except daily_export_limit_groups.DailyExportLimitGroupError as exc:
        db_sess.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    project_ids = daily_export_limit_groups.list_group_project_ids(
        db_sess, [int(updated.id)]
    ).get(int(updated.id), [])
    quota = daily_export_limit_groups.get_group_quota(
        db_sess, int(updated.id), project_ids=project_ids
    )
    return _daily_limit_group_to_out(db_sess, updated, quota=quota, project_ids=project_ids)


@app.delete(
    "/admin/daily-export-limit-groups/{group_id}",
    response_model=schemas.DailyExportLimitGroupDeleteOut,
)
def admin_delete_daily_export_limit_group(
    group_id: int,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """
    Удаление группы освобождает её проекты: они становятся обычными
    негруппированными проектами, и их pending-лиды снова выгружаются без
    группового ограничения.  Никакие лиды не удаляются.
    """
    if not crud.is_admin_user(current_manager):
        raise HTTPException(status_code=403, detail="Admin access required")
    group = daily_export_limit_groups.get_group(db_sess, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    _ensure_manager_client_access(db_sess, current_manager, int(group.client_id))

    try:
        daily_export_limit_groups.delete_group(
            db_sess, group_id=group_id, client_id=int(group.client_id)
        )
    except daily_export_limit_groups.DailyExportLimitGroupError as exc:
        db_sess.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return schemas.DailyExportLimitGroupDeleteOut(groupId=int(group_id), deleted=True)


@app.get("/admin/projects/{project_id}", response_model=schemas.AdminProjectOut)
def admin_get_project(
    project_id: int,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Получить проект по id (для админа)."""
    _ensure_manager_project_access(db_sess, current_manager, project_id)
    project = crud.admin_get_project(db_sess, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/admin/projects/{project_id}/top", response_model=schemas.AdminProjectOut)
def admin_set_project_top(
    project_id: int,
    payload: schemas.ProjectTopUpdate,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Переключить ручную отметку Топ без синхронизации с поставщиком и audit history."""
    project_row = _ensure_manager_project_access(db_sess, current_manager, project_id)
    if project_row.status == "Удалён":
        raise HTTPException(status_code=409, detail="Удалённый проект нельзя отмечать как топ.")
    updated = crud.set_project_top(db_sess, project_id, is_top=payload.isTop)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    admin_project = crud.admin_get_project(db_sess, project_id)
    if not admin_project:
        raise HTTPException(status_code=404, detail="Project not found")
    return admin_project


@app.get("/admin/projects/{project_id}/history", response_model=schemas.AdminProjectHistoryListOut)
def admin_project_history(
    project_id: int,
    limit: int = 100,
    userId: int | None = None,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    status: Optional[str] = None,  # pending|done|all
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    _ensure_manager_project_access(db_sess, current_manager, project_id)
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today_msk = datetime.now(tz).date()
    if not fromDate:
        fromDate = today_msk.isoformat()
    if not toDate:
        toDate = today_msk.isoformat()

    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz).replace(tzinfo=None)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz).replace(tzinfo=None)

    status = status or "all"
    if status not in ("pending", "done", "all"):
        status = "all"

    history = crud.admin_list_project_history(
        db_sess,
        project_id=project_id,
        limit=limit,
        user_id_filter=userId,
        start_local=start_local,
        end_local=end_local,
        status=status if status != "all" else None,
    )
    if crud.is_agent_user(current_manager):
        history.items = [item for item in history.items if item.user is None or crud.manager_can_access_client(db_sess, current_manager, int(item.user.id))]
        for item in history.items:
            item.projectSnapshot = crud.strip_duplicate_diagnostics_from_snapshot(item.projectSnapshot)
        history.total = len(history.items)
    return history


@app.get("/admin/projects/{project_id}/chart", response_model=schemas.ProjectChartOut)
def admin_project_chart(
    project_id: int,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    project = _ensure_manager_project_access(db_sess, current_admin, project_id)
    start_local, end_local = _parse_date_range_in_settings_tz(fromDate, toDate)
    return crud.project_leads_chart(
        db_sess,
        project=project,
        start_local=start_local,
        end_local=end_local,
    )


@app.patch("/admin/projects/{project_id}", response_model=schemas.AdminUpdateProjectOut)
def admin_update_project(
    project_id: int,
    payload: schemas.AdminProjectUpdate,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Обновить проект (включая delivery_status)."""
    project_row = _ensure_manager_project_access(db_sess, current_manager, project_id)
    operation = "delete" if payload.status == "Удалён" else "update"
    owner_user_id = int(project_row.user_id) if project_row.user_id else None
    _assert_manager_project_mutation_allowed(db_sess, owner_user_id)
    if operation == "delete" and not crud.is_admin_user(current_manager):
        raise HTTPException(status_code=403, detail="Удаление проекта доступно только администратору.")
    skip_provider_sync = _should_skip_provider_sync_for_status_change(project_row, payload.status)
    if project_row.status == "Удалён":
        if owner_user_id:
            _record_failed_project_operation(
                user_id=owner_user_id,
                actor_user_id=current_manager.id,
                operation=operation,
                project_id=project_id,
                project_name=project_row.name,
                request_payload=_project_payload_for_history(payload),
                error_message="Проект удалён. Редактирование запрещено.",
                error_code="PROJECT_DELETED",
                via_impersonation=False,
            )
        raise HTTPException(status_code=409, detail="Проект удалён. Редактирование запрещено.")
    if not skip_provider_sync and not project_row.provider_project_id:
        if owner_user_id:
            _record_failed_project_operation(
                user_id=owner_user_id,
                actor_user_id=current_manager.id,
                operation=operation,
                project_id=project_id,
                project_name=project_row.name,
                request_payload=_project_payload_for_history(payload),
                error_message="Project is not linked to provider",
                error_code="PROVIDER_LINK_MISSING",
                via_impersonation=False,
            )
        raise HTTPException(status_code=409, detail="Project is not linked to provider")
    if crud.is_agent_user(current_manager):
        payload.deliveryStatus = project_row.delivery_status  # type: ignore[assignment]
    try:
        payload.name = _validate_project_name_update(project_row, payload.name)
    except HTTPException as exc:
        if owner_user_id:
            _record_failed_project_operation(
                user_id=owner_user_id,
                actor_user_id=current_manager.id,
                operation=operation,
                project_id=project_id,
                project_name=project_row.name,
                request_payload=_project_payload_for_history(payload),
                error_message=_http_error_message(exc.detail),
                error_code=_http_error_code(exc.detail),
                via_impersonation=False,
            )
        raise
    if payload.status != "Удалён":
        try:
            _ensure_project_name_available(db_sess, payload.name, exclude_project_id=project_id)
        except HTTPException:
            if owner_user_id:
                _record_failed_project_operation(
                    user_id=owner_user_id,
                    actor_user_id=current_manager.id,
                    operation=operation,
                    project_id=project_id,
                    project_name=project_row.name,
                    request_payload=_project_payload_for_history(payload),
                    error_message=crud.PROJECT_NAME_UNAVAILABLE_MESSAGE,
                    error_code="PROJECT_NAME_UNAVAILABLE",
                    via_impersonation=False,
                )
            raise
    if _project_client_internal_prefix(project_row) or bool(getattr(project_row, "unique_name_applied", False)):
        payload.tag = payload.name
    if payload.status == "Активен" and project_row.status != "Активен":
        can_activate, reason = crud.can_activate_project_under_limit_control(
            db_sess,
            project_id=project_id,
            projected_data_limit=int(payload.dataLimit),
        )
        if not can_activate:
            detail = {
                "code": "LIMIT_CONTROL_BLOCK",
                "message": reason or "Нельзя включить проект из-за ограничения по лимитам клиента.",
            }
            if owner_user_id:
                _record_failed_project_operation(
                    user_id=owner_user_id,
                    actor_user_id=current_manager.id,
                    operation=operation,
                    project_id=project_id,
                    project_name=project_row.name,
                    request_payload=_project_payload_for_history(payload),
                    error_message=_http_error_message(detail),
                    error_code=_http_error_code(detail),
                    via_impersonation=False,
                )
            raise HTTPException(
                status_code=409,
                detail=detail,
            )
    warning_text = None
    duplicate_diagnostics: Optional[dict] = None
    if not skip_provider_sync:
        project_snapshot = _project_snapshot_for_prostats(project_row)
        db_sess.rollback()
        try:
            if payload.status == "Удалён":
                prostats.delete_project(str(project_snapshot.provider_project_id), project_snapshot)
            else:
                result = prostats.update_project(str(project_snapshot.provider_project_id), project_snapshot, payload)
                missing_items = result.get("missing_items") or []
                target_type = result.get("target_type")
                if missing_items and target_type in ("hosts", "calls"):
                    duplicate_diagnostics = _build_admin_duplicate_diagnostics_with_new_session(
                        missing_items,
                        target_type,
                        reason="provider_missing_items",
                        exclude_project_id=project_snapshot.id,
                        mark_external_provider_only=True,
                    )
                    duplicates = _find_duplicates_with_new_session(
                        missing_items,
                        target_type,
                        user_id=owner_user_id or 0,
                        provider_project_id=project_snapshot.provider_project_id,
                        exclude_project_id=project_snapshot.id,
                    )
                    warning_text = prostats._build_partial_warning(
                        payload.name,
                        target_type,
                        missing_items,
                        duplicates,
                        action="обновлён",
                    )
                    if target_type == "hosts":
                        payload.sites = [s for s in (payload.sites or []) if s not in missing_items]
                    else:
                        payload.phones = [p for p in (payload.phones or []) if p not in missing_items]
        except prostats.ProstatsError as exc:
            detail = _prostats_http_detail(exc)
            if payload.status != "Удалён":
                target_type = prostats._type_from_collection(project_snapshot.collection_source)
                if prostats._should_check_duplicates(exc.message, target_type) and target_type in ("hosts", "calls"):
                    items = payload.sites if target_type == "hosts" else payload.phones
                    duplicate_error_diagnostics = _build_admin_duplicate_diagnostics_with_new_session(
                        items or [],
                        target_type,
                        reason="provider_duplicate_error",
                        exclude_project_id=project_snapshot.id,
                        summary=(
                            "Провайдер отклонил набор как занятый, но среди проектов нашего ЛК совпадения не найдены. "
                            "Вероятно занято в ЛК провайдера."
                        ),
                    )
                    duplicates = _find_duplicates_with_new_session(
                        items or [],
                        target_type,
                        user_id=owner_user_id or 0,
                        provider_project_id=project_snapshot.provider_project_id,
                        exclude_project_id=project_snapshot.id,
                    )
                    if duplicates:
                        detail["duplicates"] = duplicates
                        detail["message"] = (
                            "Домены уже используются в наших проектах."
                            if target_type == "hosts"
                            else "Номера уже используются в наших проектах."
                        )
                        if owner_user_id:
                            _record_failed_project_operation(
                                user_id=owner_user_id,
                                actor_user_id=current_manager.id,
                                operation=operation,
                                project_id=project_id,
                                project_name=project_snapshot.name,
                                request_payload=_project_payload_for_history_with_duplicate_diagnostics(
                                    payload,
                                    duplicate_error_diagnostics,
                                ),
                                error_message=_http_error_message(detail),
                                error_code=str(exc.status_code) if exc.status_code else None,
                                via_impersonation=False,
                            )
                        raise HTTPException(status_code=422, detail=detail)
                    detail["message"] = "Данные номера/сайты используются в других проектах. Подробности недоступны."
                    if owner_user_id:
                        _record_failed_project_operation(
                            user_id=owner_user_id,
                            actor_user_id=current_manager.id,
                            operation=operation,
                            project_id=project_id,
                            project_name=project_snapshot.name,
                            request_payload=_project_payload_for_history_with_duplicate_diagnostics(
                                payload,
                                duplicate_error_diagnostics,
                            ),
                            error_message=_http_error_message(detail),
                            error_code=str(exc.status_code) if exc.status_code else None,
                            via_impersonation=False,
                        )
                    raise HTTPException(status_code=422, detail=detail)
            if owner_user_id:
                _record_failed_project_operation(
                    user_id=owner_user_id,
                    actor_user_id=current_manager.id,
                    operation=operation,
                    project_id=project_id,
                    project_name=project_snapshot.name,
                    request_payload=_project_payload_for_history(payload),
                    error_message=_http_error_message(detail),
                    error_code=str(exc.status_code) if exc.status_code else None,
                    via_impersonation=False,
                )
            raise HTTPException(status_code=exc.status_code, detail=detail)

    if payload.status == "Удалён":
        with SessionLocal() as write_sess:  # type: Session
            ok = crud.admin_delete_project(write_sess, project_id, admin_user_id=current_manager.id)
            if not ok:
                raise HTTPException(status_code=404, detail="Project not found")
            updated = crud.admin_get_project(write_sess, project_id)
            if not updated:
                raise HTTPException(status_code=404, detail="Project not found")
    else:
        with SessionLocal() as write_sess:  # type: Session
            try:
                updated = crud.admin_update_project(
                    write_sess,
                    project_id,
                    payload,
                    admin_user_id=current_manager.id,
                    duplicate_diagnostics=duplicate_diagnostics,
                )
            except IntegrityError as exc:
                write_sess.rollback()
                if _is_project_name_unique_violation(exc):
                    raise HTTPException(status_code=409, detail=_project_name_unavailable_detail())
                raise
            if not updated:
                raise HTTPException(status_code=404, detail="Project not found")
        if owner_user_id:
            _run_limit_control_for_client_in_new_session(client_id=owner_user_id, trigger="admin_project_update")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    if payload.status != "Удалён" and updated.collectionSource == "СМС":
        _queue_sms_project_notification(
            project_id=int(updated.id),
            action="update",
            actor_user_id=current_manager.id,
        )
    return schemas.AdminUpdateProjectOut(project=updated, warning=warning_text)


@app.delete("/admin/projects/{project_id}")
def admin_delete_project(
    project_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Удалить проект (для админа)."""
    project_row = _ensure_manager_project_access(db_sess, current_admin, project_id)
    owner_user_id = int(project_row.user_id) if project_row.user_id else None
    _assert_manager_project_mutation_allowed(db_sess, owner_user_id)
    if _is_pixel_project(project_row):
        db_sess.rollback()
        with SessionLocal() as write_sess:  # type: Session
            ok = crud.admin_delete_project(write_sess, project_id, admin_user_id=current_admin.id)
            if not ok:
                raise HTTPException(status_code=404, detail="Project not found")
        _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
        return {"deleted": True}
    if not project_row.provider_project_id:
        if owner_user_id:
            _record_failed_project_operation(
                user_id=owner_user_id,
                actor_user_id=current_admin.id,
                operation="delete",
                project_id=project_id,
                project_name=project_row.name,
                request_payload={"projectId": project_id},
                error_message="Проект не связан с Prostats. Удаление запрещено.",
                error_code="PROVIDER_LINK_MISSING",
                via_impersonation=False,
            )
        raise HTTPException(status_code=409, detail="Проект не связан с Prostats. Удаление запрещено.")
    project_snapshot = _project_snapshot_for_prostats(project_row)
    db_sess.rollback()
    try:
        prostats.delete_project(str(project_snapshot.provider_project_id), project_snapshot)
    except prostats.ProstatsError as exc:
        detail = _prostats_http_detail(exc)
        if owner_user_id:
            _record_failed_project_operation(
                user_id=owner_user_id,
                actor_user_id=current_admin.id,
                operation="delete",
                project_id=project_id,
                project_name=project_snapshot.name,
                request_payload={"projectId": project_id},
                error_message=_http_error_message(detail),
                error_code=str(exc.status_code) if exc.status_code else None,
                via_impersonation=False,
            )
        raise HTTPException(status_code=exc.status_code, detail=detail)

    with SessionLocal() as write_sess:  # type: Session
        ok = crud.admin_delete_project(write_sess, project_id, admin_user_id=current_admin.id)
        if not ok:
            raise HTTPException(status_code=404, detail="Project not found")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    return {"deleted": True}


@app.get("/admin/leads", response_model=schemas.AdminLeadsListOut)
def admin_list_leads(
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    userId: int | None = None,
    projectIds: Optional[str] = None,
    sources: Optional[str] = None,
    collectionSources: Optional[str] = None,
    unlinked: bool = False,
    offset: int = 0,
    limit: int = 50,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Список всех лидов (для админа)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today_msk = datetime.now(tz).date()
    if not fromDate:
        fromDate = today_msk.isoformat()
    if not toDate:
        toDate = today_msk.isoformat()

    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0).replace(tzinfo=None)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59).replace(tzinfo=None)

    proj_ids: Optional[List[int]] = None
    if projectIds:
        try:
            proj_ids = [int(x) for x in projectIds.split(',') if x.strip()]
            if not proj_ids:
                proj_ids = None
        except Exception:
            proj_ids = None

    src_list: Optional[List[str]] = None
    if sources:
        src_list = [s.strip() for s in sources.split(",") if s.strip()]
        if not src_list:
            src_list = None

    collection_src_list: Optional[List[str]] = None
    if collectionSources:
        collection_src_list = [s.strip() for s in collectionSources.split(",") if s.strip()]
        if not collection_src_list:
            collection_src_list = None

    limit = max(1, min(1000, limit))
    offset = max(0, offset)

    if crud.is_agent_user(current_manager):
        if userId is None:
            return schemas.AdminLeadsListOut(items=[], total=0)
        _ensure_manager_client_access(db_sess, current_manager, userId)

    if userId is not None:
        return crud.admin_list_provider_leads(
            db_sess,
            start_local=start_local,
            end_local=end_local,
            offset=offset,
            limit=limit,
            user_id_filter=userId,
            project_ids_filter=proj_ids,
            sources_filter=src_list,
            collection_sources_filter=collection_src_list,
        )

    return crud.admin_list_all_leads(
        db_sess,
        start_local=start_local,
        end_local=end_local,
        offset=offset,
        limit=limit,
        user_id_filter=userId,
        project_ids_filter=proj_ids,
        sources_filter=src_list,
        collection_sources_filter=collection_src_list,
        unlinked_only=bool(unlinked),
    )


@app.get("/admin/blacklist", response_model=schemas.AdminBlacklistListOut)
def admin_list_blacklist(
    offset: int = 0,
    limit: int = 50,
    q: str | None = None,
    userId: int | None = None,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Список всех записей черного списка (для админа)."""
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    if crud.is_agent_user(current_manager):
        if userId is not None:
            _ensure_manager_client_access(db_sess, current_manager, userId)
            return crud.admin_list_all_blacklist(db_sess, offset=offset, limit=limit, q=q, user_id_filter=userId)
        allowed_ids = crud.get_accessible_client_ids_for_manager(db_sess, current_manager)
        return crud.admin_list_all_blacklist(db_sess, offset=offset, limit=limit, q=q, user_ids_filter=allowed_ids)
    return crud.admin_list_all_blacklist(db_sess, offset=offset, limit=limit, q=q, user_id_filter=userId)


@app.get("/admin/reports", response_model=schemas.AdminReportListOut)
def admin_list_reports(
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,  # YYYY-MM-DD — фильтр по дате создания
    toDate: Optional[str] = None,    # YYYY-MM-DD — фильтр по дате создания
    clientId: Optional[int] = None,  # фильтр по целевому клиенту
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Список всех отчётов (для админа)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    limit = max(1, min(500, limit))
    offset = max(0, offset)

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today_msk = datetime.now(tz).date()
    # По умолчанию — отчёты за последние 90 дней (по дате создания)
    if not fromDate:
        fromDate = (today_msk - timedelta(days=90)).isoformat()
    if not toDate:
        toDate = today_msk.isoformat()

    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz)

    start_naive = start_local.replace(tzinfo=None)
    end_naive = end_local.replace(tzinfo=None)

    if clientId is not None and crud.is_agent_user(current_manager):
        _ensure_manager_client_access(db_sess, current_manager, clientId)

    # Показываем только отчёты, сформированные текущим админом
    return crud.admin_list_all_reports(
        db_sess,
        offset=offset,
        limit=limit,
        user_id_filter=current_manager.id,
        start_local=start_naive,
        end_local=end_naive,
        target_client_id=clientId,
    )


@app.post("/admin/reports", response_model=schemas.AdminReportOut)
def admin_create_report(
    payload: schemas.AdminCreateReportIn,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    client_id = payload.clientId
    if client_id is None:
        if not crud.is_admin_user(current_manager):
            raise HTTPException(status_code=403, detail="Only admin can create report for all clients")
        proj_ids = None
        client_user = None
    else:
        proj_ids = payload.projectIds or None
        _ensure_manager_client_access(db_sess, current_manager, client_id)
        client_user = db_sess.get(models.User, client_id)
    row = crud.log_report_export(
        db_sess,
        user_id=current_manager.id,
        client_id=client_id,
        from_date=payload.fromDate,
        to_date=payload.toDate,
        project_ids=proj_ids,
        fmt=payload.format,
    )
    return schemas.AdminReportOut(
        id=row.id,
        createdAt=row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        fromDate=row.from_date,
        toDate=row.to_date,
        projectIds=row.project_ids,
        format=row.format,
        user=schemas.UserInfo(id=current_manager.id, login=current_manager.login, role=crud.get_user_role(current_manager)),
        client=schemas.UserInfo(id=client_id, login=client_user.login if client_user else str(client_id)) if client_id is not None else None,
    )


@app.get("/admin/changes/summary", response_model=schemas.AdminClientChangesSummaryListOut)
def admin_changes_summary(
    actions: Optional[str] = None,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """
    Краткая сводка по количеству необработанных изменений по клиентам.
    Можно отфильтровать по списку действий через query param actions=update,delete.
    """
    actions_list = [a.strip() for a in (actions or "").split(",") if a.strip()] or None
    items = crud.admin_list_client_changes_summary(db_sess, actions=actions_list)
    if crud.is_agent_user(current_manager):
        allowed_ids = set(crud.get_accessible_client_ids_for_manager(db_sess, current_manager))
        items = [item for item in items if int(item.user.id) in allowed_ids]
    return schemas.AdminClientChangesSummaryListOut(items=items)


def _parse_balance_date_range(from_date: Optional[str], to_date: Optional[str]) -> tuple[Optional[datetime], Optional[datetime]]:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    start_local: Optional[datetime] = None
    end_local: Optional[datetime] = None

    if from_date:
        y, m, d = [int(x) for x in from_date.split("-")]
        start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz).replace(tzinfo=None)
    if to_date:
        y, m, d = [int(x) for x in to_date.split("-")]
        end_local = datetime(y, m, d, 23, 59, 59, tzinfo=tz).replace(tzinfo=None)

    return start_local, end_local


@app.get("/balance", response_model=schemas.ClientBalanceSummaryOut)
def client_balance_summary(
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    start_local, end_local = _parse_balance_date_range(fromDate, toDate)

    return crud.get_client_balance_summary(
        db_sess,
        client_id=current_user.id,
        start_local=start_local,
        end_local=end_local,
    )


@app.get("/balance/ops", response_model=schemas.ClientBalanceOpsListOut)
def client_balance_ops(
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    start_local, end_local = _parse_balance_date_range(fromDate, toDate)

    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.list_client_balance_operations(
        db_sess,
        client_id=current_user.id,
        offset=offset,
        limit=limit,
        start_local=start_local,
        end_local=end_local,
    )


@app.get("/admin/clients/summary", response_model=schemas.AdminClientsSummaryOut)
def admin_clients_summary(
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(settings["SHEETS_TZ"])
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))

    today_msk = datetime.now(tz).date()
    if not fromDate:
        fromDate = today_msk.isoformat()
    if not toDate:
        toDate = today_msk.isoformat()

    y, m, d = [int(x) for x in fromDate.split("-")]
    start_local = datetime(y, m, d, 0, 0, 0, tzinfo=tz).replace(tzinfo=None)
    y2, m2, d2 = [int(x) for x in toDate.split("-")]
    end_local = datetime(y2, m2, d2, 23, 59, 59, tzinfo=tz).replace(tzinfo=None)

    summary = crud.admin_clients_summary(
        db_sess,
        start_local=start_local,
        end_local=end_local,
    )
    if crud.is_agent_user(current_manager):
        allowed_ids = set(crud.get_accessible_client_ids_for_manager(db_sess, current_manager))
        filtered_items = [item for item in summary.items if int(item.user.id) in allowed_ids]
        summary.items = filtered_items
        summary.totals = schemas.AdminClientSummaryTotals(
            clients=len(filtered_items),
            projects=sum(int(item.projectCount) for item in filtered_items),
            totalLimit=sum(int(item.totalLimit) for item in filtered_items),
            usedTotal=sum(int(item.usedTotal) for item in filtered_items),
            usedPeriod=sum(int(item.usedPeriod) for item in filtered_items),
            remaining=sum(int(item.remaining) for item in filtered_items),
            pendingCreates=sum(int(item.pendingCreates) for item in filtered_items),
            pendingChanges=sum(int(item.pendingChanges) for item in filtered_items),
        )
    return summary


def _ensure_admin_client_exists(db_sess: Session, client_id: int) -> models.User:
    user = db_sess.get(models.User, client_id)
    if not user:
        raise HTTPException(status_code=404, detail="Client not found")
    return user


def _pause_and_lock_client_projects_by_admin(client_id: int, admin_user_id: int, reason: str) -> None:
    with SessionLocal() as operation_sess:  # type: Session
        admin_user = operation_sess.get(models.User, int(admin_user_id))
        if not admin_user:
            logging.getLogger("app").error(
                "Cannot enqueue pause during agent disable: admin_id=%s client_id=%s",
                admin_user_id,
                client_id,
            )
            return
        try:
            _launch_collection_project_operation(
                operation_sess,
                client_id=int(client_id),
                action="pause",
                current_admin=admin_user,
            )
        except HTTPException as exc:
            # Уже заблокированный клиент или существующая job не должны
            # отменять отключение агента; состояние остаётся видимым админу.
            logging.getLogger("app").warning(
                "Cannot enqueue pause during agent disable: client_id=%s detail=%s",
                client_id,
                _http_error_message(exc.detail),
            )
            return
        client_user = operation_sess.get(models.User, int(client_id))
        if client_user:
            client_user.projects_mutation_lock_reason = reason
            operation_sess.add(client_user)
            operation_sess.commit()


def _assert_projects_mutation_allowed(current_user: models.User) -> None:
    if bool(getattr(current_user, "projects_mutation_locked", False)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECTS_LOCKED_BY_ADMIN",
                "message": "Изменение проектов временно заблокировано администратором.",
            },
        )
    with SessionLocal() as operation_sess:  # type: Session
        active = crud.get_active_project_operation(operation_sess, int(current_user.id))
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECT_OPERATION_IN_PROGRESS",
                "message": "Для этого клиента уже выполняется операция с проектами.",
                "operationId": int(active.id),
            },
        )


def _assert_manager_project_mutation_allowed(db_sess: Session, client_id: Optional[int]) -> None:
    if client_id is None:
        return
    client = db_sess.get(models.User, int(client_id))
    if client and bool(getattr(client, "projects_mutation_locked", False)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECTS_LOCKED_BY_ADMIN",
                "message": "Изменение проектов клиента временно заблокировано.",
            },
        )
    active = crud.get_active_project_operation(db_sess, int(client_id))
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECT_OPERATION_IN_PROGRESS",
                "message": "Для этого клиента уже выполняется операция с проектами.",
                "operationId": int(active.id),
            },
        )


@app.get("/admin/clients/{client_id}/collection-state", response_model=schemas.AdminClientCollectionStateOut)
def admin_client_collection_state(
    client_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    _ensure_admin_client_exists(db_sess, client_id)
    return crud.admin_get_collection_state(db_sess, client_id=client_id)


def _launch_collection_project_operation(
    db_sess: Session,
    *,
    client_id: int,
    action: str,
    current_admin: models.User,
) -> models.ProjectOperationJob:
    client = _ensure_admin_client_exists(db_sess, client_id)
    active = crud.get_active_project_operation(db_sess, client_id)
    if active:
        raise _project_operation_conflict(
            project_operations.ActiveProjectOperationError(client_id, active.id)
        )
    if action == "pause" and bool(getattr(client, "projects_mutation_locked", False)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECTS_ALREADY_PAUSED",
                "message": "Проекты клиента уже заблокированы. Используйте возобновление.",
            },
        )

    if action == "pause":
        projects = db_sess.execute(
            select(models.Project).where(
                models.Project.user_id == client_id,
                models.Project.status == "Активен",
            ).order_by(models.Project.id.asc())
        ).scalars().all()
        projects = [project for project in projects if not _is_pixel_project(project)]
        stale_snapshot = db_sess.execute(
            select(models.ClientProjectPauseSnapshot).where(
                models.ClientProjectPauseSnapshot.client_id == client_id
            )
        ).scalar_one_or_none()
        if stale_snapshot:
            stale_snapshot.project_ids = []
            stale_snapshot.paused_by = int(current_admin.id)
            stale_snapshot.updated_at = now_msk()
            db_sess.add(stale_snapshot)
        else:
            # Пустая строка создаётся до запуска worker. Параллельно
            # подтвердившиеся items затем сериализуют добавление id через
            # SELECT FOR UPDATE и не теряют изменения общего JSON snapshot.
            db_sess.add(
                models.ClientProjectPauseSnapshot(
                    client_id=int(client_id),
                    project_ids=[],
                    paused_by=int(current_admin.id),
                    created_at=now_msk(),
                    updated_at=now_msk(),
                )
            )
        client.projects_mutation_locked = True
        client.projects_mutation_locked_at = now_msk()
        client.projects_mutation_locked_by = int(current_admin.id)
        client.projects_mutation_lock_reason = "Проекты во временной блокировке"
        desired_status = "На паузе"
    elif action == "resume":
        state = crud.admin_get_collection_state(db_sess, client_id=client_id)
        snapshot_ids = [int(item.id) for item in state.snapshotProjects]
        projects = (
            db_sess.execute(
                select(models.Project).where(
                    models.Project.user_id == client_id,
                    models.Project.id.in_(snapshot_ids),
                ).order_by(models.Project.id.asc())
            ).scalars().all()
            if snapshot_ids
            else []
        )
        projects = [
            project
            for project in projects
            if not _is_pixel_project(project) and project.status != "Удалён"
        ]
        valid_snapshot_ids = [int(project.id) for project in projects]
        snapshot_row = db_sess.execute(
            select(models.ClientProjectPauseSnapshot).where(
                models.ClientProjectPauseSnapshot.client_id == client_id
            )
        ).scalar_one_or_none()
        if snapshot_row:
            if valid_snapshot_ids:
                snapshot_row.project_ids = valid_snapshot_ids
                snapshot_row.updated_at = now_msk()
                db_sess.add(snapshot_row)
            else:
                db_sess.delete(snapshot_row)
        desired_status = "Активен"
        if not projects:
            client.projects_mutation_locked = False
            client.projects_mutation_locked_at = None
            client.projects_mutation_locked_by = None
            client.projects_mutation_lock_reason = None
    else:
        raise HTTPException(status_code=400, detail="Unsupported collection operation")

    db_sess.add(client)
    items = [
        {
            "projectId": int(project.id),
            "providerProjectId": project.provider_project_id,
            "projectName": project.name,
            "stateSnapshot": _snapshot_limit_control_project(project),
            "payloadSnapshot": {
                "action": "status",
                "status": desired_status,
                "collectionAction": action,
                "actorUserId": int(current_admin.id),
                "adminUpdate": True,
                "parallelSafe": (
                    desired_status == "На паузе"
                    or not bool(getattr(client, "auto_limit_control_enabled", False))
                ),
            },
        }
        for project in projects
    ]
    try:
        return crud.create_project_operation(
            db_sess,
            client_id=client_id,
            operation_type=f"collection_{action}",
            actor_user_id=int(current_admin.id),
            actor_role="admin",
            items=items,
            payload_snapshot={"action": action, "desiredStatus": desired_status},
        )
    except project_operations.ActiveProjectOperationError as exc:
        raise _project_operation_conflict(exc) from exc


@app.post(
    "/admin/clients/{client_id}/collection/pause",
    response_model=schemas.ProjectOperationLaunchOut,
    status_code=202,
)
def admin_pause_client_projects(
    client_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    operation = _launch_collection_project_operation(
        db_sess,
        client_id=client_id,
        action="pause",
        current_admin=current_admin,
    )
    return schemas.ProjectOperationLaunchOut(
        operation=crud.project_operation_to_view(db_sess, operation, include_technical=True)
    )


@app.post(
    "/admin/clients/{client_id}/collection/resume",
    response_model=schemas.ProjectOperationLaunchOut,
    status_code=202,
)
def admin_resume_client_projects(
    client_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    operation = _launch_collection_project_operation(
        db_sess,
        client_id=client_id,
        action="resume",
        current_admin=current_admin,
    )
    return schemas.ProjectOperationLaunchOut(
        operation=crud.project_operation_to_view(db_sess, operation, include_technical=True)
    )


@app.get("/admin/clients/{client_id}/balance", response_model=schemas.ClientBalanceSummaryOut)
def admin_client_balance_summary(
    client_id: int,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    _ensure_manager_client_access(db_sess, current_manager, client_id)
    start_local, end_local = _parse_balance_date_range(fromDate, toDate)

    return crud.get_client_balance_summary(db_sess, client_id=client_id, start_local=start_local, end_local=end_local)


@app.get("/admin/clients/{client_id}/balance/ops", response_model=schemas.ClientBalanceOpsListOut)
def admin_client_balance_ops(
    client_id: int,
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    _ensure_manager_client_access(db_sess, current_manager, client_id)
    start_local, end_local = _parse_balance_date_range(fromDate, toDate)

    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.list_client_balance_operations(
        db_sess,
        client_id=client_id,
        offset=offset,
        limit=limit,
        start_local=start_local,
        end_local=end_local,
    )


@app.get("/admin/clients/{client_id}/tariffs", response_model=schemas.ClientTariffListOut)
def admin_client_tariffs(
    client_id: int,
    offset: int = 0,
    limit: int = 50,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    _ensure_manager_client_access(db_sess, current_manager, client_id)
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.list_client_tariffs(
        db_sess,
        client_id=client_id,
        offset=offset,
        limit=limit,
    )


@app.post("/admin/clients/{client_id}/tariffs", response_model=schemas.ClientTariffOut)
def admin_create_client_tariff(
    client_id: int,
    payload: schemas.ClientTariffCreateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    client = db_sess.get(models.User, int(client_id))
    if not client or not crud.is_client_user(client):
        raise HTTPException(status_code=404, detail="Client not found")
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    try:
        tariff = crud.create_client_tariff(
            db_sess,
            client_id=client_id,
            admin_id=current_admin.id,
            amount=payload.amount,
            comment=payload.comment,
            signal1=payload.signal1,
            signal2=payload.signal2,
            signal3=payload.signal3,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _queue_client_tariff_operation_notification(
        client_id=client_id,
        tariff_id=int(tariff.id),
        action="create",
        amount=int(payload.amount),
        comment=payload.comment,
    )
    _run_limit_control_for_client(db_sess, client_id=client_id, trigger="tariff_create")
    return tariff


@app.patch("/admin/tariffs/{tariff_id}", response_model=schemas.ClientTariffOut)
def admin_update_client_tariff(
    tariff_id: int,
    payload: schemas.ClientTariffUpdateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    existing_tariff = crud.get_client_tariff(db_sess, tariff_id=tariff_id)
    if not existing_tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    try:
        result = crud.update_client_tariff(
            db_sess,
            tariff_id=tariff_id,
            admin_id=current_admin.id,
            amount=payload.amount,
            comment=payload.comment,
            signal1=payload.signal1,
            signal2=payload.signal2,
            signal3=payload.signal3,
        )
    except ValueError as exc:
        message = str(exc)
        if message == "Tariff not found":
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)
    amount_delta = int(result.currentAmount) - int(existing_tariff.currentAmount)
    if amount_delta != 0:
        _queue_client_tariff_operation_notification(
            client_id=int(existing_tariff.clientId),
            tariff_id=int(tariff_id),
            action=("credit" if amount_delta > 0 else "debit"),
            amount=abs(amount_delta),
            comment=payload.comment,
        )
    target_user = db_sess.get(models.User, int(existing_tariff.clientId))
    if target_user and crud.is_client_user(target_user):
        _run_limit_control_for_client(db_sess, client_id=int(target_user.id), trigger="tariff_update")
    return result


@app.get("/admin/tariffs/{tariff_id}", response_model=schemas.ClientTariffOut)
def admin_get_client_tariff(
    tariff_id: int,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    return _ensure_manager_tariff_access(db_sess, current_manager, tariff_id)


@app.get("/admin/tariffs/{tariff_id}/ops", response_model=schemas.ClientTariffOperationsListOut)
def admin_list_client_tariff_operations(
    tariff_id: int,
    offset: int = 0,
    limit: int = 50,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    _ensure_manager_tariff_access(db_sess, current_manager, tariff_id)
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.list_client_tariff_operations(
        db_sess,
        tariff_id=tariff_id,
        offset=offset,
        limit=limit,
    )


@app.post("/admin/tariffs/{tariff_id}/ops", response_model=schemas.ClientTariffOperationOut)
def admin_create_client_tariff_operation(
    tariff_id: int,
    payload: schemas.ClientTariffOperationCreateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    tariff = crud.get_client_tariff(db_sess, tariff_id=tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    if payload.type not in ("credit", "debit"):
        raise HTTPException(status_code=400, detail="type must be credit or debit")
    comment = (payload.comment or "").strip()
    if not comment:
        raise HTTPException(status_code=400, detail="comment is required")
    try:
        result = crud.create_client_tariff_operation(
            db_sess,
            tariff_id=tariff_id,
            admin_id=current_admin.id,
            amount=payload.amount,
            op_type=payload.type,
            comment=comment,
        )
    except ValueError as exc:
        message = str(exc)
        if message == "Tariff not found":
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)
    _queue_client_tariff_operation_notification(
        client_id=int(tariff.clientId),
        tariff_id=int(tariff_id),
        action=result.type,
        amount=int(result.amount),
        comment=result.comment,
    )
    target_user = db_sess.get(models.User, int(tariff.clientId))
    if target_user and crud.is_client_user(target_user):
        _run_limit_control_for_client(db_sess, client_id=int(target_user.id), trigger="tariff_operation")
    return result


@app.post("/admin/clients/{client_id}/balance/ops", response_model=schemas.BalanceOperationOut)
def admin_create_balance_op(
    client_id: int,
    payload: schemas.BalanceOperationCreateIn,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    raise HTTPException(status_code=410, detail="Прямые операции баланса клиента отключены. Используйте тарифы.")


@app.get("/admin/changes/{client_id}", response_model=schemas.AdminClientChangesOut)
def admin_client_changes(
    client_id: int,
    actions: Optional[str] = None,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """
    Подробный список необработанных изменений конкретного клиента.
    Дополнительно можно фильтровать по actions (create,update,delete).
    """
    _ensure_manager_client_access(db_sess, current_manager, client_id)
    actions_list = [a.strip() for a in (actions or "").split(",") if a.strip()] or None
    result = crud.admin_list_client_changes(db_sess, client_id=client_id, actions=actions_list)
    if not crud.is_admin_user(current_manager):
        for item in result.items:
            item.projectSnapshot = crud.strip_duplicate_diagnostics_from_snapshot(item.projectSnapshot)
            item.beforeSnapshot = crud.strip_duplicate_diagnostics_from_snapshot(item.beforeSnapshot)
    return result


@app.post("/admin/changes/{event_id}/resolve")
def admin_resolve_change(
    event_id: int,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """
    Помечает одно событие аудита как обработанное админом.
    """
    ev = db_sess.get(models.AuditEvent, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Change not found")
    target_client_id = int(ev.user_id) if getattr(ev, "user_id", None) is not None else None
    if target_client_id is not None and not crud.is_admin_user(current_manager):
        _ensure_manager_client_access(db_sess, current_manager, target_client_id)
    if ev.batch_id:
        count = crud.admin_mark_batch_processed(db_sess, batch_id=ev.batch_id, admin_user_id=current_manager.id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Change not found")
        return {"ok": True, "processed": count, "batch": ev.batch_id}
    ok = crud.admin_mark_change_processed(db_sess, event_id=event_id, admin_user_id=current_manager.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Change not found")
    return {"ok": True, "processed": 1}
