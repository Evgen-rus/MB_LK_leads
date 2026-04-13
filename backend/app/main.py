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
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import inspect, select, text
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect

from . import db, models, schemas, crud, telegram, notify_worker, logging_setup, auth
from . import provider_leads_xlsx_import as provider_leads_import
from .time_utils import now_msk
from .providers import prostats


def get_settings():
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        "DEBOUNCE_WINDOW_MINUTES": int(os.getenv("DEBOUNCE_WINDOW_MINUTES", "30")),
        "AUTO_LIMIT_CHECK_SECONDS": int(os.getenv("AUTO_LIMIT_CHECK_SECONDS", "300")),
        "DB_POOL_SIZE": int(os.getenv("DB_POOL_SIZE", "10")),
        "DB_MAX_OVERFLOW": int(os.getenv("DB_MAX_OVERFLOW", "20")),
        "DB_POOL_TIMEOUT": int(os.getenv("DB_POOL_TIMEOUT", "30")),
        "DB_POOL_RECYCLE": int(os.getenv("DB_POOL_RECYCLE", "1800")),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
        "SHEETS_TZ": os.getenv("SHEETS_TZ", "Europe/Moscow"),
    }


load_dotenv()
logging_setup.setup_logging()
settings = get_settings()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is not set")

provider_webhook_logger = logging_setup.setup_provider_webhook_logger()

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


def _ensure_project_unique_name_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "projects" not in tables:
        return

    columns = {col.get("name") for col in inspector.get_columns("projects")}
    with engine.begin() as conn:
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


_ensure_project_provider_leads_grace_columns()


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


def _required_project_name_prefix(project: models.Project) -> str:
    prefix = _provider_prefix_for_code(str(getattr(project, "data_source_code", "") or ""))
    if bool(getattr(project, "unique_name_applied", False)):
        return f"{prefix}[MB{int(project.id)}] "
    return prefix


def _validate_create_project_item_name(item: schemas.CreateProjectItem) -> None:
    expected_prefix = _provider_prefix_for_code(item.dataSourceCode)
    raw_name = str(item.name or "").strip()
    if not raw_name.startswith(expected_prefix):
        raise HTTPException(
            status_code=422,
            detail={"message": f'Название проекта должно начинаться с префикса "{expected_prefix}"'},
        )
    if not _extract_project_display_name(raw_name):
        raise HTTPException(status_code=422, detail={"message": "Название проекта не может быть пустым."})


def _validate_project_name_update(project: models.Project, proposed_name: str) -> str:
    raw_name = str(proposed_name or "").strip()
    required_prefix = _required_project_name_prefix(project)
    if not raw_name.startswith(required_prefix):
        if bool(getattr(project, "unique_name_applied", False)):
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


def _build_unique_project_name(data_source_code: str, project_id: int, raw_name: str) -> str:
    base_name = _extract_project_display_name(raw_name)
    if not base_name:
        raise HTTPException(status_code=422, detail={"message": "Название проекта не может быть пустым."})
    return f'{_provider_prefix_for_code(data_source_code)}[MB{int(project_id)}] {base_name}'


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
cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
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
            "bot_token": settings["TELEGRAM_BOT_TOKEN"],
            "chat_id": settings["TELEGRAM_CHAT_ID"],
            "sleep_seconds": 60,
        },
        daemon=True,
        name="notify-worker-thread",
    )
    worker.start()

    # Периодический контроль лимитов клиентов (пер-клиентный флаг в users.auto_limit_control_enabled).
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


def run_limit_control_loop(SessionLocal, sleep_seconds: int = 300) -> None:
    while True:
        try:
            with SessionLocal() as s:  # type: Session
                client_ids = crud.list_clients_with_auto_limit_control(s)
                for client_id in client_ids:
                    _run_limit_control_for_client(s, client_id=int(client_id), trigger="schedule")
        except Exception:
            logging.getLogger("app").warning("limit-control loop failed", exc_info=True)
        time.sleep(max(30, int(sleep_seconds)))


def _run_limit_control_for_client(db_sess: Session, client_id: int, trigger: str) -> dict:
    user = db_sess.get(models.User, int(client_id))
    if not user:
        return {"paused": 0, "errors": [], "skipped": 0}

    user_snapshot = _snapshot_limit_control_user(user)
    remaining = crud.get_client_remaining_numbers(db_sess, client_id=int(client_id))
    active_projects = db_sess.execute(
        select(models.Project).where(
            models.Project.user_id == int(client_id),
            models.Project.status == "Активен",
            models.Project.provider_project_id.is_not(None),
        )
    ).scalars().all()
    active_project_snapshots = [_snapshot_limit_control_project(project) for project in active_projects]
    active_sum = sum(int(p.data_limit or 0) for p in active_projects)

    # Закрываем текущую транзакцию перед сетевыми вызовами, чтобы не держать
    # соединение из пула БД во время ожидания Telegram/Prostats.
    db_sess.rollback()

    _sync_client_tariff_signal_alert(
        db_sess,
        user_snapshot=user_snapshot,
        remaining=remaining,
    )

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
        if not project["provider_project_id"]:
            skipped += 1
            continue
        try:
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


def _snapshot_limit_control_user(user: models.User) -> dict:
    return {
        "id": int(getattr(user, "id", 0) or 0),
        "login": str(getattr(user, "login", "") or ""),
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


def _run_limit_control_for_client_in_new_session(client_id: int, trigger: str) -> dict:
    with SessionLocal() as s:  # type: Session
        return _run_limit_control_for_client(s, client_id=client_id, trigger=trigger)


def _schedule_debounce_in_new_session(minutes: int) -> None:
    with SessionLocal() as s:  # type: Session
        crud.schedule_debounce(s, minutes=minutes)


def _normalize_tariff_signal_level(value: Any) -> int:
    try:
        return max(0, min(3, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _resolve_tariff_signal_level(remaining: int, tariff: Optional[schemas.ClientTariffOut]) -> Optional[int]:
    if not tariff:
        return None
    if tariff.signal1 is None or tariff.signal2 is None or tariff.signal3 is None:
        return None
    signal1 = int(tariff.signal1)
    signal2 = int(tariff.signal2)
    signal3 = int(tariff.signal3)
    if signal3 <= 0 or signal2 <= signal3 or signal1 <= signal2 or signal1 >= int(tariff.currentAmount):
        return None
    if remaining <= signal3:
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
    user_snapshot: dict,
    remaining: int,
    tariff: schemas.ClientTariffOut,
    signal_level: int,
) -> str:
    signal_value_map = {
        1: int(tariff.signal1 or 0),
        2: int(tariff.signal2 or 0),
        3: int(tariff.signal3 or 0),
    }
    signal_value = signal_value_map.get(signal_level, 0)
    return (
        f"<b>[ЛК | Сигнал остатка тарифа {signal_level}]</b>\n"
        f'Клиент: <code>{html.escape(str(user_snapshot.get("login", "") or ""))}</code> '
        f'(id={html.escape(str(user_snapshot.get("id", "") or ""))})\n'
        f"Текущий остаток: <b>{html.escape(str(int(remaining)))}</b>\n"
        f"Достигнут порог: <b>{html.escape(str(signal_value))}</b>\n"
        f"Текущий тариф: <b>{html.escape(str(int(tariff.currentAmount)))}</b>\n"
        f"Сигналы: {html.escape(str(int(tariff.signal1 or 0)))} / "
        f"{html.escape(str(int(tariff.signal2 or 0)))} / "
        f"{html.escape(str(int(tariff.signal3 or 0)))}"
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

    bot_token = str(settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = _get_client_telegram_chat_id_for_notifications(user_snapshot)
    if not bot_token or not chat_id or not latest_tariff:
        return None

    text = _build_client_tariff_signal_message(
        user_snapshot=user_snapshot,
        remaining=remaining,
        tariff=latest_tariff,
        signal_level=next_level,
    )
    if not telegram.send_text(bot_token, chat_id, text, parse_mode="HTML"):
        logging.getLogger("app").warning(
            "Failed to send tariff signal alert to Telegram for client_id=%s level=%s",
            user_snapshot.get("id"),
            next_level,
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
    bot_token = settings.get("TELEGRAM_BOT_TOKEN") or ""
    chat_id = _get_client_telegram_chat_id_for_notifications(user_snapshot)
    if not bot_token or not chat_id:
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
    telegram.send_text(bot_token, chat_id, text, parse_mode="HTML")


def _get_client_telegram_chat_id_for_notifications(user: models.User) -> str:
    """
    Определяет, куда отправлять системные Telegram-уведомления по клиенту.

    Поведение специально безопасное:
    - если у клиента включён персональный Telegram-маршрут и задан chat id,
      отправляем туда;
    - иначе используем общий TELEGRAM_CHAT_ID из env, чтобы не потерять уведомление.
    """
    if isinstance(user, dict):
        use_client_route = bool(user.get("telegram_auto_pause_enabled", False))
        client_chat_id = str(user.get("telegram_notifications_chat_id", "") or "").strip()
    else:
        use_client_route = bool(getattr(user, "telegram_auto_pause_enabled", False))
        client_chat_id = str(getattr(user, "telegram_notifications_chat_id", "") or "").strip()
    return _resolve_notification_chat_id(use_client_route, client_chat_id)


def _resolve_notification_chat_id(use_client_route: bool, client_chat_id: str) -> str:
    if use_client_route and client_chat_id:
        return client_chat_id
    return str(settings.get("TELEGRAM_CHAT_ID") or "").strip()


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
    client_login = html.escape(str(getattr(user, "login", "") or ""))
    client_id = html.escape(str(getattr(user, "id", "") or ""))
    return (
        "<b>[ЛК | Тест Telegram-маршрута]</b>\n"
        "Это тестовое сообщение подтверждает, что персональный Telegram-чат для уведомлений настроен корректно.\n\n"
        f"Клиент: <code>{client_login}</code> (id={client_id})\n"
        "Дальше сюда будут приходить уведомления из личного кабинета"
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
    bot_token = str(settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not bot_token or not chat_id:
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

    if not telegram.send_text(bot_token, chat_id, text, parse_mode="HTML"):
        logging.getLogger("app").warning(
            "Failed to send Telegram notification about ambiguous provider lead: vid=%s page=%s",
            vid,
            project_name,
        )


def _notify_unique_project_name_failure(
    *,
    client_id: int,
    project_name: str,
    provider_project_id: Optional[str],
    message: str,
) -> None:
    bot_token = str(settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if not bot_token or not chat_id:
        return
    text = (
        "<b>[ЛК | Ошибка финализации уникального имени проекта]</b>\n"
        f"client_id: <code>{html.escape(str(client_id))}</code>\n"
        f"project_name: <code>{html.escape(project_name)}</code>\n"
        f"provider_project_id: <code>{html.escape(str(provider_project_id or ''))}</code>\n"
        f"details: {html.escape(message)}"
    )
    if not telegram.send_text(bot_token, chat_id, text, parse_mode="HTML"):
        logging.getLogger("app").warning(
            "Failed to send Telegram notification about unique project rename failure: client_id=%s provider_project_id=%s",
            client_id,
            provider_project_id,
        )


@app.get("/health")
def health() -> dict:
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


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
    )


@app.get("/projects", response_model=schemas.ProjectListOut)
def list_projects(
    offset: int = 0,
    limit: int = 50,
    q: str | None = None,
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
    includeDeleted: bool = False,
    projectStatus: Optional[schemas.ProjectStatus] = None,
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

    return crud.list_projects_paginated(
        db_sess,
        offset=offset,
        limit=limit,
        q=q,
        user_id=current_user.id,
        start_local=start_naive,
        end_local=end_naive,
        include_deleted=includeDeleted,
        project_status=projectStatus,
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
            notices.append(f'Проект "{item.name}" не создан: {message}')
            continue

        project_row = crud.build_project_model_from_create_item(
            item,
            user_id=current_user.id,
            provider_id=provider_id_value,
            unique_name_applied=False,
        )
        db_sess.add(project_row)
        db_sess.flush()

        final_name = _build_unique_project_name(item.dataSourceCode, int(project_row.id), item.name)
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
        db_sess.add(models.AuditEvent(
            user_id=current_user.id,
            actor_user_id=actor_user_id or current_user.id,
            project_id=project_row.id,
            batch_id=batch_id,
            action='create',
            before=None,
            after=after,
            changed_fields=list(after.keys()),
            via_impersonation=via_impersonation,
        ))
        db_sess.commit()
        db_sess.refresh(project_row)
        created.append(crud._project_to_out(project_row))
        if success_notice:
            notices.append(success_notice)

    warning_text = "\n\n".join(notices) if notices else None
    return created, warning_text


@app.post("/projects", response_model=schemas.CreateProjectsOut)
def create_projects(payload: schemas.CreateProjectsPayload, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    _assert_projects_mutation_allowed(current_user)
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    for item in payload.items:
        _validate_create_project_item_name(item)

    if bool(getattr(current_user, "unique_project_names_enabled", False)):
        created, warning_text = _create_projects_with_unique_names(
            db_sess=db_sess,
            items=payload.items,
            current_user=current_user,
            actor_user_id=actor_user_id,
            via_impersonation=via_impersonation,
        )
        if not created:
            raise HTTPException(status_code=422, detail={"message": warning_text or "Не удалось создать проекты."})
        _run_limit_control_for_client(db_sess, client_id=current_user.id, trigger="projects_create")
        crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
        return schemas.CreateProjectsOut(items=created, warning=warning_text)

    # 1) Создаём проекты у поставщика (частичный успех допустим)
    notices: List[str] = []
    adjusted_items: List[schemas.CreateProjectItem] = []
    provider_ids: List[Optional[str]] = []
    db_sess.rollback()
    for item in payload.items:
        try:
            result = prostats.create_project(item)
            provider_id_value = str(result.get("provider_id") or "").strip() or None
            provider_ids.append(provider_id_value)
            adjusted_items.append(item)

            missing_items = result.get("missing_items") or []
            target_type = result.get("target_type")
            if missing_items and target_type in ("hosts", "calls"):
                duplicates = _find_duplicates_with_new_session(
                    missing_items,
                    target_type,
                    user_id=current_user.id,
                    provider_project_id=provider_id_value,
                )
                notices.append(
                    prostats._build_partial_warning(
                        item.name,
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
                notices.append(f'Проект "{item.name}" создан.')
        except prostats.ProstatsError as exc:
            target_type = prostats._type_from_collection(item.collectionSource)
            message = exc.message
            if prostats._should_check_duplicates(exc.message, target_type):
                message = "Данные номера/сайты используются в других проектах. Подробности недоступны."
            notices.append(f'Проект "{item.name}" не создан: {message}')

    # 2) Если все успешны — сохраняем у нас
    if not adjusted_items:
        warning_text = "\n\n".join(notices) if notices else "Не удалось создать проекты."
        raise HTTPException(status_code=422, detail={"message": warning_text})

    with SessionLocal() as write_sess:  # type: Session
        created = crud.create_projects(
            write_sess,
            adjusted_items,
            user_id=current_user.id,
            provider_ids=provider_ids,
            actor_user_id=actor_user_id,
            via_impersonation=via_impersonation,
        )
    _run_limit_control_for_client_in_new_session(current_user.id, trigger="projects_create")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    warning_text = "\n\n".join(notices) if notices else None
    return schemas.CreateProjectsOut(items=created, warning=warning_text)


@app.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: int, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    project = crud.get_project(db_sess, project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}", response_model=schemas.UpdateProjectOut)
def update_project(project_id: int, payload: schemas.ProjectUpdate, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    _assert_projects_mutation_allowed(current_user)
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    project_row = db_sess.get(models.Project, project_id)
    if not project_row or project_row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if project_row.status == "Удалён":
        raise HTTPException(status_code=409, detail="Проект удалён. Редактирование запрещено.")
    if not project_row.provider_project_id:
        raise HTTPException(status_code=409, detail="Project is not linked to provider")
    payload.name = _validate_project_name_update(project_row, payload.name)
    if bool(getattr(project_row, "unique_name_applied", False)):
        payload.tag = payload.name
    if payload.status == "Активен" and project_row.status != "Активен":
        can_activate, reason = crud.can_activate_project_under_limit_control(
            db_sess,
            project_id=project_id,
            projected_data_limit=int(payload.dataLimit),
        )
        if not can_activate:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LIMIT_CONTROL_BLOCK",
                    "message": reason or "Нельзя включить проект из-за ограничения по лимитам клиента.",
                },
            )
    warning_text = None
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
        detail = {"message": exc.message, **(exc.details or {})}
        if payload.status != "Удалён":
            target_type = prostats._type_from_collection(project_snapshot.collection_source)
            if prostats._should_check_duplicates(exc.message, target_type) and target_type in ("hosts", "calls"):
                items = payload.sites if target_type == "hosts" else payload.phones
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
                    raise HTTPException(status_code=422, detail=detail)
                detail["message"] = "Данные номера/сайты используются в других проектах. Подробности недоступны."
                raise HTTPException(status_code=422, detail=detail)
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
            updated = crud.update_project(
                write_sess,
                project_id,
                payload,
                user_id=current_user.id,
                actor_user_id=actor_user_id,
                via_impersonation=via_impersonation,
            )
            if not updated:
                raise HTTPException(status_code=404, detail="Project not found")
        _run_limit_control_for_client_in_new_session(current_user.id, trigger="project_update")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
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
    _assert_projects_mutation_allowed(current_user)
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    project_row = db_sess.get(models.Project, project_id)
    if not project_row or project_row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project_row.provider_project_id:
        raise HTTPException(status_code=409, detail="Проект не связан с Prostats. Удаление запрещено.")
    try:
        project_snapshot = _project_snapshot_for_prostats(project_row)
        db_sess.rollback()
        prostats.delete_project(str(project_snapshot.provider_project_id), project_snapshot)
    except prostats.ProstatsError as exc:
        detail = {"message": exc.message, **(exc.details or {})}
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


@app.post("/support-message")
def support_message(
    payload: schemas.SupportMessageIn,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    """
    Сообщение в поддержку из ЛК. Сейчас просто пересылаем текст в тот же Telegram-чат,
    куда приходят уведомления об изменениях проектов.
    """
    bot_token = settings["TELEGRAM_BOT_TOKEN"]
    chat_id = settings["TELEGRAM_CHAT_ID"]

    # Форматируем сообщение для оператора
    text = (
        "<b>[ЛК | Сообщение от клиента]</b>\n"
        f"Пользователь: <code>{current_user.login}</code> (id={current_user.id})\n"
        f"Телефон: <code>{payload.phone}</code>\n\n"
        f"{payload.text}"
    )

    db_sess.rollback()
    ok = telegram.send_text(bot_token, chat_id, text)
    if not ok:
        raise HTTPException(status_code=500, detail="Не удалось отправить сообщение в Telegram")
    return {"ok": True}


# ----------------------- Лиды -----------------------
@app.get("/leads", response_model=schemas.LeadsListOut)
def list_leads(
    projectIds: Optional[str] = None,  # "1,2,3"; если нет — все
    sources: Optional[str] = None,     # "B1,B2"; если нет — все
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
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
        user_id=current_user.id,
    )


@app.get("/leads/export")
def export_leads(
    request: Request,
    projectIds: Optional[str] = None,
    sources: Optional[str] = None,
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
    user_info = crud._get_user_info(db_sess, effective_client_id or current_user.id)
    export_started = time.perf_counter()

    filename = f"leads_{fromDate}_{toDate}.{format}"

    def iter_export_rows():
        yield from crud.iter_provider_leads_for_export(
            db_sess,
            project_ids=proj_ids,
            start_local=start_local,
            end_local=end_local,
            max_rows=max_rows,
            sources=src_list,
            user_info=user_info,
        )

    if (format or "csv").lower() == "csv":
        def gen():
            rows_count = 0
            try:
                if is_admin:
                    yield ("ext_id;project_id;project_name;channel;imported_at;phone;source;user_login;user_id\n").encode('utf-8-sig')
                else:
                    yield ("project_id;project_name;channel;imported_at;phone;source;user_login;user_id\n").encode('utf-8-sig')
                for r in iter_export_rows():
                    utm = r["utm_campaign"] or ""
                    if is_admin:
                        line = f"{r['ext_id']};{r['project_id']};{r['project_name']};{r['source'] or ''};{r['imported_at']};{r['phone']};{utm};{r['user_login']};{r['user_id']}\n"
                    else:
                        line = f"{r['project_id']};{r['project_name']};{r['source'] or ''};{r['imported_at']};{r['phone']};{utm};{r['user_login']};{r['user_id']}\n"
                    rows_count += 1
                    yield line.encode('utf-8')
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
            if is_admin:
                ws.append(["ext_id", "project_id", "project_name", "channel", "imported_at", "phone", "source", "user_login", "user_id"])
            else:
                ws.append(["project_id", "project_name", "channel", "imported_at", "phone", "source", "user_login", "user_id"])

            for r in iter_export_rows():
                row = [
                    r["project_id"],
                    r["project_name"],
                    r["source"] or "",
                    r["imported_at"],
                    r["phone"],
                    r["utm_campaign"] or "",
                    r["user_login"],
                    r["user_id"],
                ]
                if is_admin:
                    row.insert(0, r["ext_id"])
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
            owner_agent_id=None,
        )
        test_message = _build_auto_pause_test_message(existing_user)
        db_sess.rollback()

        if should_send_test and crud.is_admin_user(current_manager):
            with SessionLocal() as validate_sess:  # type: Session
                crud.admin_update_client(
                    validate_sess,
                    **update_kwargs,
                    commit=False,
                )
                validate_sess.rollback()
            bot_token = str(settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
            test_ok = telegram.send_text(
                bot_token,
                next_chat_id,
                test_message,
                parse_mode="HTML",
            )
            if not test_ok:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Не удалось отправить тестовое сообщение в Telegram. "
                        "Проверьте chat id и убедитесь, что бот добавлен в группу."
                    ),
                )

        with SessionLocal() as write_sess:  # type: Session
            result = crud.admin_update_client(
                write_sess,
                **update_kwargs,
                commit=True,
            )
        if payload.autoLimitControlEnabled is True and crud.is_admin_user(current_manager):
            _run_limit_control_for_client_in_new_session(client_id=client_id, trigger="admin_toggle_on")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        db_sess.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


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
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
    includeDeleted: bool = False,
    projectStatus: Optional[schemas.ProjectStatus] = None,
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

    return crud.admin_list_all_projects(
        db_sess,
        offset=offset,
        limit=limit,
        q=q,
        user_id_filter=userId,
        start_local=start_naive,
        end_local=end_naive,
        include_deleted=includeDeleted,
        project_status=projectStatus,
    )


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
        history.total = len(history.items)
    return history


@app.patch("/admin/projects/{project_id}", response_model=schemas.AdminUpdateProjectOut)
def admin_update_project(
    project_id: int,
    payload: schemas.AdminProjectUpdate,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Обновить проект (включая delivery_status)."""
    project_row = _ensure_manager_project_access(db_sess, current_manager, project_id)
    if project_row.status == "Удалён":
        raise HTTPException(status_code=409, detail="Проект удалён. Редактирование запрещено.")
    if not project_row.provider_project_id:
        raise HTTPException(status_code=409, detail="Project is not linked to provider")
    if crud.is_agent_user(current_manager):
        payload.deliveryStatus = project_row.delivery_status  # type: ignore[assignment]
    payload.name = _validate_project_name_update(project_row, payload.name)
    if bool(getattr(project_row, "unique_name_applied", False)):
        payload.tag = payload.name
    if payload.status == "Активен" and project_row.status != "Активен":
        can_activate, reason = crud.can_activate_project_under_limit_control(
            db_sess,
            project_id=project_id,
            projected_data_limit=int(payload.dataLimit),
        )
        if not can_activate:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LIMIT_CONTROL_BLOCK",
                    "message": reason or "Нельзя включить проект из-за ограничения по лимитам клиента.",
                },
            )
    warning_text = None
    project_snapshot = _project_snapshot_for_prostats(project_row)
    owner_user_id = int(project_row.user_id) if project_row.user_id else None
    db_sess.rollback()
    try:
        if payload.status == "Удалён":
            prostats.delete_project(str(project_snapshot.provider_project_id), project_snapshot)
        else:
            result = prostats.update_project(str(project_snapshot.provider_project_id), project_snapshot, payload)
            missing_items = result.get("missing_items") or []
            target_type = result.get("target_type")
            if missing_items and target_type in ("hosts", "calls"):
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
        detail = {"message": exc.message, **(exc.details or {})}
        if payload.status != "Удалён":
            target_type = prostats._type_from_collection(project_snapshot.collection_source)
            if prostats._should_check_duplicates(exc.message, target_type) and target_type in ("hosts", "calls"):
                items = payload.sites if target_type == "hosts" else payload.phones
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
                    raise HTTPException(status_code=422, detail=detail)
                detail["message"] = "Данные номера/сайты используются в других проектах. Подробности недоступны."
                raise HTTPException(status_code=422, detail=detail)
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
            updated = crud.admin_update_project(write_sess, project_id, payload, admin_user_id=current_manager.id)
            if not updated:
                raise HTTPException(status_code=404, detail="Project not found")
        if owner_user_id:
            _run_limit_control_for_client_in_new_session(client_id=owner_user_id, trigger="admin_project_update")
    _schedule_debounce_in_new_session(settings["DEBOUNCE_WINDOW_MINUTES"])
    return schemas.AdminUpdateProjectOut(project=updated, warning=warning_text)


@app.delete("/admin/projects/{project_id}")
def admin_delete_project(
    project_id: int,
    current_manager: models.User = Depends(require_manager),
    db_sess: Session = Depends(get_db),
):
    """Удалить проект (для админа)."""
    project_row = _ensure_manager_project_access(db_sess, current_manager, project_id)
    if not project_row.provider_project_id:
        raise HTTPException(status_code=409, detail="Проект не связан с Prostats. Удаление запрещено.")
    project_snapshot = _project_snapshot_for_prostats(project_row)
    owner_user_id = int(project_row.user_id) if project_row.user_id else None
    db_sess.rollback()
    try:
        prostats.delete_project(str(project_snapshot.provider_project_id), project_snapshot)
    except prostats.ProstatsError as exc:
        detail = {"message": exc.message, **(exc.details or {})}
        raise HTTPException(status_code=exc.status_code, detail=detail)

    with SessionLocal() as write_sess:  # type: Session
        ok = crud.admin_delete_project(write_sess, project_id, admin_user_id=current_manager.id)
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
    proj_ids = payload.projectIds or None
    client_id = payload.clientId
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
        client=schemas.UserInfo(id=client_id, login=client_user.login if client_user else str(client_id)),
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
    with SessionLocal() as read_sess:  # type: Session
        active_projects = read_sess.execute(
            select(models.Project).where(
                models.Project.user_id == client_id,
                models.Project.status == "Активен",
            ).order_by(models.Project.id.asc())
        ).scalars().all()
        active_project_snapshots = [_project_snapshot_for_prostats(project) for project in active_projects]

    paused_ids: List[int] = []
    for project_snapshot in active_project_snapshots:
        if project_snapshot.status == "Удалён" or not project_snapshot.provider_project_id:
            continue
        try:
            prostats.update_project_status(str(project_snapshot.provider_project_id), project_snapshot, "На паузе")
            with SessionLocal() as write_sess:  # type: Session
                ok = crud.admin_update_project_status_only(
                    write_sess,
                    project_id=int(project_snapshot.id),
                    status="На паузе",
                    admin_user_id=admin_user_id,
                )
            if ok:
                paused_ids.append(int(project_snapshot.id))
        except prostats.ProstatsError:
            logging.getLogger("app").warning(
                "Failed to pause project during agent disable: client_id=%s project_id=%s",
                client_id,
                getattr(project_snapshot, "id", None),
                exc_info=True,
            )

    with SessionLocal() as finalize_sess:  # type: Session
        crud.admin_replace_pause_snapshot(
            finalize_sess,
            client_id=client_id,
            project_ids=paused_ids,
            admin_user_id=admin_user_id,
        )
        crud.admin_set_client_projects_mutation_lock(
            finalize_sess,
            client_id=client_id,
            locked=True,
            admin_user_id=admin_user_id,
            reason=reason,
        )
        if paused_ids:
            crud.schedule_debounce(finalize_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
def _assert_projects_mutation_allowed(current_user: models.User) -> None:
    if bool(getattr(current_user, "projects_mutation_locked", False)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROJECTS_LOCKED_BY_ADMIN",
                "message": "Изменение проектов временно заблокировано администратором.",
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


@app.post("/admin/clients/{client_id}/collection/pause", response_model=schemas.AdminClientCollectionActionOut)
def admin_pause_client_projects(
    client_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    _ensure_admin_client_exists(db_sess, client_id)
    prev_state = crud.admin_get_collection_state(db_sess, client_id=client_id)
    had_snapshot = len(prev_state.snapshotProjects) > 0

    active_projects = db_sess.execute(
        select(models.Project).where(
            models.Project.user_id == client_id,
            models.Project.status == "Активен",
        ).order_by(models.Project.id.asc())
    ).scalars().all()
    active_project_snapshots = [_project_snapshot_for_prostats(project) for project in active_projects]

    paused_ids: List[int] = []
    skipped_count = 0
    failed_count = 0
    errors: List[str] = []

    for p in active_project_snapshots:
        if p.status == "Удалён":
            skipped_count += 1
            continue
        if not p.provider_project_id:
            skipped_count += 1
            errors.append(f'Проект {p.id} "{p.name}": не связан с Prostats, пропущен.')
            continue
        try:
            db_sess.rollback()
            prostats.update_project_status(str(p.provider_project_id), p, "На паузе")
            with SessionLocal() as write_sess:  # type: Session
                ok = crud.admin_update_project_status_only(
                    write_sess,
                    project_id=int(p.id),
                    status="На паузе",
                    admin_user_id=current_admin.id,
                )
            if ok:
                paused_ids.append(int(p.id))
        except prostats.ProstatsError as exc:
            failed_count += 1
            errors.append(f'Проект {p.id} "{p.name}": {exc.message}')

    with SessionLocal() as finalize_sess:  # type: Session
        crud.admin_replace_pause_snapshot(
            finalize_sess,
            client_id=client_id,
            project_ids=paused_ids,
            admin_user_id=current_admin.id,
        )
        crud.admin_set_client_projects_mutation_lock(
            finalize_sess,
            client_id=client_id,
            locked=True,
            admin_user_id=current_admin.id,
            reason="Проекты во временной блокировке",
        )
        if paused_ids:
            crud.schedule_debounce(finalize_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
        state = crud.admin_get_collection_state(finalize_sess, client_id=client_id)
    if paused_ids and failed_count == 0 and skipped_count == 0:
        message = "Все активные проекты поставлены на паузу."
    elif paused_ids:
        message = (
            f"Пауза применена частично: поставлено на паузу {len(paused_ids)}, "
            f"пропущено {skipped_count}, ошибок {failed_count}."
        )
    else:
        message = "Активных синхронизированных проектов для паузы не найдено."

    if had_snapshot:
        message += " Снимок ранее поставленных на паузу проектов перезаписан."

    return schemas.AdminClientCollectionActionOut(
        state=state,
        message=message,
        pausedCount=len(paused_ids),
        resumedCount=0,
        skippedCount=skipped_count,
        failedCount=failed_count,
        errors=errors,
    )


@app.post("/admin/clients/{client_id}/collection/resume", response_model=schemas.AdminClientCollectionActionOut)
def admin_resume_client_projects(
    client_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    _ensure_admin_client_exists(db_sess, client_id)
    prev_state = crud.admin_get_collection_state(db_sess, client_id=client_id)
    snapshot_ids = [int(item.id) for item in prev_state.snapshotProjects]
    if not snapshot_ids:
        crud.admin_set_client_projects_mutation_lock(
            db_sess,
            client_id=client_id,
            locked=False,
            admin_user_id=current_admin.id,
            reason=None,
        )
        state = crud.admin_get_collection_state(db_sess, client_id=client_id)
        return schemas.AdminClientCollectionActionOut(
            state=state,
            message="Сохранённых проектов для восстановления нет. Блокировка раздела проектов снята.",
            pausedCount=0,
            resumedCount=0,
            skippedCount=0,
            failedCount=0,
            errors=[],
        )

    proj_rows = db_sess.execute(
        select(models.Project).where(
            models.Project.user_id == client_id,
            models.Project.id.in_(snapshot_ids),
        )
    ).scalars().all()
    by_id = {int(p.id): p for p in proj_rows}
    project_snapshots = {int(p.id): _project_snapshot_for_prostats(p) for p in proj_rows}

    resumed_ids: List[int] = []
    skipped_count = 0
    failed_count = 0
    errors: List[str] = []

    for pid in snapshot_ids:
        p = by_id.get(pid)
        if not p:
            skipped_count += 1
            errors.append(f"Проект {pid}: не найден, пропущен.")
            continue
        if p.status == "Удалён":
            skipped_count += 1
            continue
        if not p.provider_project_id:
            skipped_count += 1
            errors.append(f'Проект {p.id} "{p.name}": не связан с Prostats, пропущен.')
            continue
        if p.status == "Активен":
            skipped_count += 1
            errors.append(f'Проект {p.id} "{p.name}": уже активен, пропущен.')
            continue
        if p.status != "На паузе":
            skipped_count += 1
            errors.append(f'Проект {p.id} "{p.name}": неожиданный статус "{p.status}", пропущен.')
            continue
        can_activate, reason = crud.can_activate_project_under_limit_control(db_sess, project_id=int(p.id))
        if not can_activate:
            skipped_count += 1
            errors.append(f'Проект {p.id} "{p.name}": {reason or "ограничение по лимитам клиента"}.')
            continue
        try:
            project_snapshot = project_snapshots[int(p.id)]
            db_sess.rollback()
            prostats.update_project_status(str(project_snapshot.provider_project_id), project_snapshot, "Активен")
            with SessionLocal() as write_sess:  # type: Session
                ok = crud.admin_update_project_status_only(
                    write_sess,
                    project_id=int(p.id),
                    status="Активен",
                    admin_user_id=current_admin.id,
                )
            if ok:
                resumed_ids.append(int(p.id))
        except prostats.ProstatsError as exc:
            failed_count += 1
            errors.append(f'Проект {p.id} "{p.name}": {exc.message}')

    with SessionLocal() as finalize_sess:  # type: Session
        fresh_rows = finalize_sess.execute(
            select(models.Project).where(
                models.Project.user_id == client_id,
                models.Project.id.in_(snapshot_ids),
            )
        ).scalars().all()
        fresh_by_id = {int(project.id): project for project in fresh_rows}

        # Оставляем в снимке только те проекты, которые всё ещё на паузе и могут быть восстановлены позже.
        next_snapshot_ids: List[int] = []
        for pid in snapshot_ids:
            p = fresh_by_id.get(pid)
            if not p:
                continue
            if p.status == "Удалён":
                continue
            if not p.provider_project_id:
                continue
            if p.status != "Активен":
                next_snapshot_ids.append(int(pid))

        crud.admin_replace_pause_snapshot(
            finalize_sess,
            client_id=client_id,
            project_ids=next_snapshot_ids,
            admin_user_id=current_admin.id,
        )
        crud.admin_set_client_projects_mutation_lock(
            finalize_sess,
            client_id=client_id,
            locked=False,
            admin_user_id=current_admin.id,
            reason=None,
        )
        if resumed_ids:
            crud.schedule_debounce(finalize_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
        state = crud.admin_get_collection_state(finalize_sess, client_id=client_id)
    if resumed_ids and failed_count == 0 and skipped_count == 0 and not next_snapshot_ids:
        message = "Проекты восстановлены."
    elif resumed_ids:
        message = (
            f"Восстановление выполнено частично: включено {len(resumed_ids)}, "
            f"пропущено {skipped_count}, ошибок {failed_count}."
        )
    else:
        message = "Не удалось восстановить проекты из сохранённого снимка."

    return schemas.AdminClientCollectionActionOut(
        state=state,
        message=message,
        pausedCount=0,
        resumedCount=len(resumed_ids),
        skippedCount=skipped_count,
        failedCount=failed_count,
        errors=errors,
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
    return crud.admin_list_client_changes(db_sess, client_id=client_id, actions=actions_list)


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
