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
from dotenv import load_dotenv
import logging
import secrets
import uuid
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response, FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import inspect, select, text
from starlette.background import BackgroundTask

from . import db, models, schemas, crud, telegram, notify_worker, logging_setup, auth
from .time_utils import now_msk
from .providers import prostats


def get_settings():
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        "DEBOUNCE_WINDOW_MINUTES": int(os.getenv("DEBOUNCE_WINDOW_MINUTES", "30")),
        "AUTO_LIMIT_CHECK_SECONDS": int(os.getenv("AUTO_LIMIT_CHECK_SECONDS", "300")),
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

engine, SessionLocal = db.init_engine_and_session(settings["DATABASE_URL"]) 
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
        if "telegram_balance_alert_level" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN telegram_balance_alert_level INTEGER"))

    # На старых БД гарантируем не-null значение для bool-флагов.
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET projects_mutation_locked = FALSE WHERE projects_mutation_locked IS NULL"))
        conn.execute(text("UPDATE users SET auto_limit_control_enabled = FALSE WHERE auto_limit_control_enabled IS NULL"))
        conn.execute(text("UPDATE users SET telegram_auto_pause_enabled = FALSE WHERE telegram_auto_pause_enabled IS NULL"))


_ensure_user_projects_lock_columns()


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
                    _run_limit_control_for_client(s, client_id=client_id, trigger="schedule")
        except Exception:
            logging.getLogger("app").warning("limit-control loop failed", exc_info=True)
        time.sleep(max(30, int(sleep_seconds)))


def _run_limit_control_for_client(db_sess: Session, client_id: int, trigger: str) -> dict:
    user = db_sess.get(models.User, int(client_id))
    if not user:
        return {"paused": 0, "errors": [], "skipped": 0}

    remaining = crud.get_client_remaining_numbers(db_sess, client_id=int(client_id))
    active_projects = db_sess.execute(
        select(models.Project).where(
            models.Project.user_id == int(client_id),
            models.Project.status == "Активен",
            models.Project.provider_project_id.is_not(None),
        )
    ).scalars().all()
    active_sum = sum(int(p.data_limit or 0) for p in active_projects)

    _sync_client_balance_alert(
        db_sess,
        user=user,
        remaining=remaining,
        active_limit_sum=active_sum,
    )

    if not bool(getattr(user, "auto_limit_control_enabled", False)):
        return {"paused": 0, "errors": [], "skipped": 0}
    if active_sum <= remaining:
        return {"paused": 0, "errors": [], "skipped": 0}

    pause_candidates = sorted(
        active_projects,
        key=lambda p: (int(p.data_limit or 0), int(p.id or 0)),
        reverse=True,
    )
    paused_projects: List[models.Project] = []
    skipped = 0
    errors: List[str] = []

    for project in pause_candidates:
        if active_sum <= remaining:
            break
        if not project.provider_project_id:
            skipped += 1
            continue
        try:
            prostats.update_project_status(str(project.provider_project_id), project, "На паузе")
            ok = crud.update_project_status_with_audit(
                db_sess,
                project_id=int(project.id),
                status="На паузе",
                event_user_id=int(client_id),
                actor_user_id=None,
                audit_reason=(
                    f"Автопауза по лимитам ({trigger}): сумма активных лимитов была {active_sum}, остаток {remaining}."
                ),
            )
            if ok:
                paused_projects.append(project)
                active_sum -= int(project.data_limit or 0)
        except prostats.ProstatsError as exc:
            errors.append(f'Проект {project.id} "{project.name}": {exc.message}')

    if paused_projects:
        crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
        _notify_auto_limit_pause(
            user=user,
            remaining=remaining,
            sum_before=sum(int(p.data_limit or 0) for p in active_projects),
            paused_projects=paused_projects,
            trigger=trigger,
            errors=errors,
        )
    return {"paused": len(paused_projects), "errors": errors, "skipped": skipped}


def _notify_auto_limit_pause(
    user: models.User,
    remaining: int,
    sum_before: int,
    paused_projects: List[models.Project],
    trigger: str,
    errors: List[str],
) -> None:
    bot_token = settings.get("TELEGRAM_BOT_TOKEN") or ""
    chat_id = _get_client_telegram_chat_id_for_notifications(user)
    if not bot_token or not chat_id:
        return
    paused_lines = [f'- {p.name} (id: {p.id}, лимит: {int(p.data_limit or 0)})' for p in paused_projects]
    details = "\n".join(paused_lines)
    text = (
        "<b>[ЛК | Автопауза по лимитам]</b>\n"
        f"Клиент: <code>{user.login}</code> (id={user.id})\n"
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
    use_client_route = bool(getattr(user, "telegram_auto_pause_enabled", False))
    client_chat_id = str(getattr(user, "telegram_notifications_chat_id", "") or "").strip()
    if use_client_route and client_chat_id:
        return client_chat_id
    return str(settings.get("TELEGRAM_CHAT_ID") or "").strip()


def _resolve_client_balance_alert_level(remaining: int, active_limit_sum: int) -> Optional[int]:
    if active_limit_sum <= 0:
        return None
    if remaining <= 0:
        return 0

    days_left = float(remaining) / float(active_limit_sum)
    if days_left <= 1:
        return 1
    if days_left <= 2:
        return 2
    if days_left <= 3:
        return 3
    return None


def _format_client_balance_days_left(remaining: int, active_limit_sum: int) -> str:
    if active_limit_sum <= 0:
        return "inf"
    if remaining <= 0:
        return "0.00"
    days_left = float(remaining) / float(active_limit_sum)
    return f"{days_left:.2f}"


def _build_client_balance_alert_message(
    user: models.User,
    remaining: int,
    active_limit_sum: int,
    alert_level: int,
) -> str:
    login = html.escape(str(getattr(user, "login", "") or ""))
    user_id = html.escape(str(getattr(user, "id", "") or ""))
    remaining_text = html.escape(str(int(remaining)))
    active_sum_text = html.escape(str(int(active_limit_sum)))
    days_left_text = html.escape(_format_client_balance_days_left(remaining, active_limit_sum))

    if alert_level == 0:
        title = "Остаток клиента закончился"
        status_line = "Остаток достиг нуля или ушёл в минус."
    else:
        suffix = "день" if alert_level == 1 else ("дня" if alert_level in (2, 3) else "дней")
        title = f"Остаток клиента: {alert_level} {suffix}"
        status_line = f"Расчётно остатка хватит примерно на <b>{alert_level} {suffix}</b> или меньше."

    return (
        f"<b>[ЛК | {title}]</b>\n"
        f"Клиент: <code>{login}</code> (id={user_id})\n"
        f"Текущий остаток: <b>{remaining_text}</b>\n"
        f"Сумма лимитов активных проектов: <b>{active_sum_text}</b>\n"
        f"Расчётный горизонт: <b>{days_left_text}</b> дн.\n\n"
        f"{status_line}\n"
        "Рекомендуем пополнить баланс и/или снизить лимиты активных проектов."
    )


def _sync_client_balance_alert(
    db_sess: Session,
    user: models.User,
    remaining: int,
    active_limit_sum: int,
) -> Optional[int]:
    saved_level_raw = getattr(user, "telegram_balance_alert_level", None)
    try:
        saved_level = int(saved_level_raw) if saved_level_raw is not None else None
    except (TypeError, ValueError):
        saved_level = None

    next_level = _resolve_client_balance_alert_level(remaining, active_limit_sum)
    if next_level is None:
        if saved_level is not None:
            user.telegram_balance_alert_level = None
            db_sess.add(user)
        return None

    if saved_level == next_level:
        return next_level

    bot_token = str(settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = _get_client_telegram_chat_id_for_notifications(user)
    if not bot_token or not chat_id:
        return None

    text = _build_client_balance_alert_message(
        user=user,
        remaining=remaining,
        active_limit_sum=active_limit_sum,
        alert_level=next_level,
    )
    if not telegram.send_text(bot_token, chat_id, text, parse_mode="HTML"):
        logging.getLogger("app").warning(
            "Failed to send balance alert to Telegram for client_id=%s level=%s",
            getattr(user, "id", None),
            next_level,
        )
        return None

    user.telegram_balance_alert_level = next_level
    db_sess.add(user)
    return next_level


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


@app.get("/health")
def health() -> dict:
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/provider-test/{secret}")
async def provider_webhook(secret: str, request: Request, db_sess: Session = Depends(get_db)):
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")

    payload, fmt = await _read_webhook_body(request)
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
    project_id = crud.get_project_id_by_name(db_sess, page) if page else None

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

    if project_id:
        proj = db_sess.get(models.Project, int(project_id))
        if proj and proj.user_id:
            _run_limit_control_for_client(db_sess, client_id=int(proj.user_id), trigger="provider_lead")

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

    if impersonator_id and impersonator_id != user.id:
        setattr(user, "_actor_user_id", impersonator_id)
        setattr(user, "_via_impersonation", True)
    else:
        setattr(user, "_actor_user_id", user.id)
        setattr(user, "_via_impersonation", False)
    return user


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
        name=profile.name if profile else None,
        projectsMutationLocked=bool(getattr(current_user, "projects_mutation_locked", False)),
        projectsMutationLockedAt=current_user.projects_mutation_locked_at.isoformat() if getattr(current_user, "projects_mutation_locked_at", None) else None,
        projectsMutationLockedBy=(int(current_user.projects_mutation_locked_by) if getattr(current_user, "projects_mutation_locked_by", None) is not None else None),
        projectsMutationLockReason=(current_user.projects_mutation_lock_reason or None),
        autoLimitControlEnabled=bool(getattr(current_user, "auto_limit_control_enabled", False)),
        telegramNotificationsChatId=(getattr(current_user, "telegram_notifications_chat_id", None) or None),
        telegramAutoPauseEnabled=bool(getattr(current_user, "telegram_auto_pause_enabled", False)),
    )


@app.get("/projects", response_model=schemas.ProjectListOut)
def list_projects(
    offset: int = 0,
    limit: int = 50,
    q: str | None = None,
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
    includeDeleted: bool = False,
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
    )


@app.post("/projects", response_model=schemas.CreateProjectsOut)
def create_projects(payload: schemas.CreateProjectsPayload, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    _assert_projects_mutation_allowed(current_user)
    actor_user_id, via_impersonation = _audit_actor_context(current_user)
    # 1) Создаём проекты у поставщика (частичный успех допустим)
    notices: List[str] = []
    adjusted_items: List[schemas.CreateProjectItem] = []
    provider_ids: List[Optional[str]] = []
    for item in payload.items:
        try:
            result = prostats.create_project(item)
            provider_id_value = str(result.get("provider_id") or "").strip() or None
            provider_ids.append(provider_id_value)
            adjusted_items.append(item)

            missing_items = result.get("missing_items") or []
            target_type = result.get("target_type")
            if missing_items and target_type in ("hosts", "calls"):
                duplicates = crud.find_duplicates_in_projects(
                    db_sess,
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

    created = crud.create_projects(
        db_sess,
        adjusted_items,
        user_id=current_user.id,
        provider_ids=provider_ids,
        actor_user_id=actor_user_id,
        via_impersonation=via_impersonation,
    )
    _run_limit_control_for_client(db_sess, client_id=current_user.id, trigger="projects_create")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
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
    try:
        if payload.status == "Удалён":
            prostats.delete_project(str(project_row.provider_project_id), project_row)
        else:
            result = prostats.update_project(str(project_row.provider_project_id), project_row, payload)
            missing_items = result.get("missing_items") or []
            target_type = result.get("target_type")
            if missing_items and target_type in ("hosts", "calls"):
                duplicates = crud.find_duplicates_in_projects(
                    db_sess,
                    missing_items,
                    target_type,
                    user_id=current_user.id,
                    provider_project_id=project_row.provider_project_id,
                    exclude_project_id=project_row.id,
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
            target_type = prostats._type_from_collection(project_row.collection_source)
            if prostats._should_check_duplicates(exc.message, target_type) and target_type in ("hosts", "calls"):
                items = payload.sites if target_type == "hosts" else payload.phones
                duplicates = crud.find_duplicates_in_projects(
                    db_sess,
                    items or [],
                    target_type,
                    user_id=current_user.id,
                    provider_project_id=project_row.provider_project_id,
                    exclude_project_id=project_row.id,
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
        ok = crud.delete_project(
            db_sess,
            project_id,
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            via_impersonation=via_impersonation,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Project not found")
        updated = crud.get_project(db_sess, project_id, user_id=current_user.id)
        if not updated:
            raise HTTPException(status_code=404, detail="Project not found")
        _sync_client_balance_alert(
            db_sess,
            user=current_user,
            remaining=crud.get_client_remaining_numbers(db_sess, client_id=current_user.id),
            active_limit_sum=crud.get_client_active_projects_limit_sum(db_sess, client_id=current_user.id),
        )
    else:
        updated = crud.update_project(
            db_sess,
            project_id,
            payload,
            user_id=current_user.id,
            actor_user_id=actor_user_id,
            via_impersonation=via_impersonation,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Project not found")
        _run_limit_control_for_client(db_sess, client_id=current_user.id, trigger="project_update")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
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
        prostats.delete_project(str(project_row.provider_project_id), project_row)
    except prostats.ProstatsError as exc:
        detail = {"message": exc.message, **(exc.details or {})}
        raise HTTPException(status_code=exc.status_code, detail=detail)

    ok = crud.delete_project(
        db_sess,
        project_id,
        user_id=current_user.id,
        actor_user_id=actor_user_id,
        via_impersonation=via_impersonation,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    _sync_client_balance_alert(
        db_sess,
        user=current_user,
        remaining=crud.get_client_remaining_numbers(db_sess, client_id=current_user.id),
        active_limit_sum=crud.get_client_active_projects_limit_sum(db_sess, client_id=current_user.id),
    )
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
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
    token = auth.create_access_token(user.id, is_admin=is_admin)
    return {"access_token": token, "is_admin": is_admin}


@app.post("/auth/logout")
def logout():
    # Для JWT на клиенте — серверного state нет. Возвращаем ok.
    return {"ok": True}


@app.post("/support-message")
def support_message(
    payload: schemas.SupportMessageIn,
    current_user: models.User = Depends(require_auth),
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

    # Разрешённые проекты (для фильтрации provider_leads у клиентов/админа с clientId)
    allowed_ids = set(crud.get_user_project_ids(db_sess, current_user.id))
    if current_user.id != 1 and not allowed_ids:
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
    if current_user.id != 1:
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
                client_id=clientId if clientId is not None and current_user.id == 1 else current_user.id,
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
    user_info = crud._get_user_info(db_sess, clientId or current_user.id)
    export_started = time.perf_counter()

    filename = f"leads_{fromDate}_{toDate}.{format}"
    is_admin = current_user.id == 1

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


def create_impersonation_token(client_user_id: int, admin_user_id: int, ttl_minutes: int = 1440) -> str:
    """Генерируем JWT для входа под клиентом (без флага is_admin)."""
    from datetime import timedelta

    return auth.create_access_token(
        user_id=client_user_id,
        is_admin=False,
        expires_delta=timedelta(minutes=max(1, ttl_minutes)),
        extra_claims={"impersonator_user_id": int(admin_user_id)},
    )


@app.post("/admin/clients", response_model=schemas.AdminClientCreateOut)
def admin_create_client(
    payload: schemas.AdminClientCreateIn,
    current_admin: models.User = Depends(require_admin),
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
            auto_limit_control_enabled=bool(payload.autoLimitControlEnabled or False),
            telegram_notifications_chat_id=payload.telegramNotificationsChatId,
            telegram_auto_pause_enabled=bool(payload.telegramAutoPauseEnabled or False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/admin/clients/{client_id}/impersonate")
def admin_impersonate_client(
    client_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """
    Выдаёт токен (24 часа) для входа в ЛК клиента.
    Токен имеет payload user_id клиента, без is_admin.
    """
    client_user = db_sess.get(models.User, client_id)
    if not client_user:
        raise HTTPException(status_code=404, detail="Client not found")

    token = create_impersonation_token(client_user.id, admin_user_id=current_admin.id, ttl_minutes=1440)
    return {"access_token": token, "ttl_minutes": 1440}


@app.patch("/admin/clients/{client_id}", response_model=schemas.AdminClientUpdateOut)
def admin_update_client(
    client_id: int,
    payload: schemas.AdminClientUpdateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    try:
        existing_user = db_sess.get(models.User, client_id)
        if not existing_user:
            raise HTTPException(status_code=404, detail="Client not found")

        prev_enabled = bool(getattr(existing_user, "telegram_auto_pause_enabled", False))
        prev_chat_id = str(getattr(existing_user, "telegram_notifications_chat_id", "") or "").strip()
        next_enabled = (
            bool(payload.telegramAutoPauseEnabled)
            if payload.telegramAutoPauseEnabled is not None
            else prev_enabled
        )
        next_chat_id = (
            str(payload.telegramNotificationsChatId or "").strip()
            if payload.telegramNotificationsChatId is not None
            else prev_chat_id
        )
        should_send_test = _should_send_auto_pause_test_message(
            prev_enabled=prev_enabled,
            prev_chat_id=prev_chat_id,
            next_enabled=next_enabled,
            next_chat_id=next_chat_id,
        )

        result = crud.admin_update_client(
            db_sess,
            client_id=client_id,
            name=payload.name,
            inn=payload.inn,
            phone=payload.phone,
            contact=payload.contact,
            login=payload.login,
            password=payload.password,
            auto_limit_control_enabled=payload.autoLimitControlEnabled,
            telegram_notifications_chat_id=payload.telegramNotificationsChatId,
            telegram_auto_pause_enabled=payload.telegramAutoPauseEnabled,
            commit=False,
        )

        if should_send_test:
            bot_token = str(settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
            test_ok = telegram.send_text(
                bot_token,
                next_chat_id,
                _build_auto_pause_test_message(existing_user),
                parse_mode="HTML",
            )
            if not test_ok:
                db_sess.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Не удалось отправить тестовое сообщение в Telegram. "
                        "Проверьте chat id и убедитесь, что бот добавлен в группу."
                    ),
                )

        db_sess.commit()
        if payload.autoLimitControlEnabled is True:
            _run_limit_control_for_client(db_sess, client_id=client_id, trigger="admin_toggle_on")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        db_sess.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/admin/users", response_model=List[schemas.UserInfo])
def admin_list_users(
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Список всех пользователей (для фильтра по клиенту)."""
    return crud.get_all_users(db_sess)


@app.get("/admin/projects", response_model=schemas.AdminProjectListOut)
def admin_list_projects(
    offset: int = 0,
    limit: int = 50,
    q: str | None = None,
    userId: int | None = None,
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
    includeDeleted: bool = False,
    current_admin: models.User = Depends(require_admin),
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

    return crud.admin_list_all_projects(
        db_sess,
        offset=offset,
        limit=limit,
        q=q,
        user_id_filter=userId,
        start_local=start_naive,
        end_local=end_naive,
        include_deleted=includeDeleted,
    )


@app.get("/admin/projects/{project_id}", response_model=schemas.AdminProjectOut)
def admin_get_project(
    project_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Получить проект по id (для админа)."""
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
    current_admin: models.User = Depends(require_admin),
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

    status = status or "all"
    if status not in ("pending", "done", "all"):
        status = "all"

    return crud.admin_list_project_history(
        db_sess,
        project_id=project_id,
        limit=limit,
        user_id_filter=userId,
        start_local=start_local,
        end_local=end_local,
        status=status if status != "all" else None,
    )


@app.patch("/admin/projects/{project_id}", response_model=schemas.AdminUpdateProjectOut)
def admin_update_project(
    project_id: int,
    payload: schemas.AdminProjectUpdate,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Обновить проект (включая delivery_status)."""
    project_row = db_sess.get(models.Project, project_id)
    if not project_row:
        raise HTTPException(status_code=404, detail="Project not found")
    if project_row.status == "Удалён":
        raise HTTPException(status_code=409, detail="Проект удалён. Редактирование запрещено.")
    if not project_row.provider_project_id:
        raise HTTPException(status_code=409, detail="Project is not linked to provider")
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
    try:
        if payload.status == "Удалён":
            prostats.delete_project(str(project_row.provider_project_id), project_row)
        else:
            result = prostats.update_project(str(project_row.provider_project_id), project_row, payload)
            missing_items = result.get("missing_items") or []
            target_type = result.get("target_type")
            if missing_items and target_type in ("hosts", "calls"):
                duplicates = crud.find_duplicates_in_projects(
                    db_sess,
                    missing_items,
                    target_type,
                    user_id=project_row.user_id,
                    provider_project_id=project_row.provider_project_id,
                    exclude_project_id=project_row.id,
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
            target_type = prostats._type_from_collection(project_row.collection_source)
            if prostats._should_check_duplicates(exc.message, target_type) and target_type in ("hosts", "calls"):
                items = payload.sites if target_type == "hosts" else payload.phones
                duplicates = crud.find_duplicates_in_projects(
                    db_sess,
                    items or [],
                    target_type,
                    user_id=project_row.user_id,
                    provider_project_id=project_row.provider_project_id,
                    exclude_project_id=project_row.id,
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
        ok = crud.admin_delete_project(db_sess, project_id, admin_user_id=current_admin.id)
        if not ok:
            raise HTTPException(status_code=404, detail="Project not found")
        updated = crud.admin_get_project(db_sess, project_id)
        if not updated:
            raise HTTPException(status_code=404, detail="Project not found")
        if project_row.user_id:
            user = db_sess.get(models.User, int(project_row.user_id))
            if user:
                _sync_client_balance_alert(
                    db_sess,
                    user=user,
                    remaining=crud.get_client_remaining_numbers(db_sess, client_id=int(project_row.user_id)),
                    active_limit_sum=crud.get_client_active_projects_limit_sum(db_sess, client_id=int(project_row.user_id)),
                )
    else:
        updated = crud.admin_update_project(db_sess, project_id, payload, admin_user_id=current_admin.id)
        if not updated:
            raise HTTPException(status_code=404, detail="Project not found")
        if project_row.user_id:
            _run_limit_control_for_client(db_sess, client_id=int(project_row.user_id), trigger="admin_project_update")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return schemas.AdminUpdateProjectOut(project=updated, warning=warning_text)


@app.delete("/admin/projects/{project_id}")
def admin_delete_project(
    project_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Удалить проект (для админа)."""
    project_row = db_sess.get(models.Project, project_id)
    if not project_row:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project_row.provider_project_id:
        raise HTTPException(status_code=409, detail="Проект не связан с Prostats. Удаление запрещено.")
    try:
        prostats.delete_project(str(project_row.provider_project_id), project_row)
    except prostats.ProstatsError as exc:
        detail = {"message": exc.message, **(exc.details or {})}
        raise HTTPException(status_code=exc.status_code, detail=detail)

    ok = crud.admin_delete_project(db_sess, project_id, admin_user_id=current_admin.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    if project_row.user_id:
        user = db_sess.get(models.User, int(project_row.user_id))
        if user:
            _sync_client_balance_alert(
                db_sess,
                user=user,
                remaining=crud.get_client_remaining_numbers(db_sess, client_id=int(project_row.user_id)),
                active_limit_sum=crud.get_client_active_projects_limit_sum(db_sess, client_id=int(project_row.user_id)),
            )
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
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
    current_admin: models.User = Depends(require_admin),
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
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Список всех записей черного списка (для админа)."""
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.admin_list_all_blacklist(db_sess, offset=offset, limit=limit, q=q, user_id_filter=userId)


@app.get("/admin/reports", response_model=schemas.AdminReportListOut)
def admin_list_reports(
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,  # YYYY-MM-DD — фильтр по дате создания
    toDate: Optional[str] = None,    # YYYY-MM-DD — фильтр по дате создания
    clientId: Optional[int] = None,  # фильтр по целевому клиенту
    current_admin: models.User = Depends(require_admin),
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

    # Показываем только отчёты, сформированные текущим админом
    return crud.admin_list_all_reports(
        db_sess,
        offset=offset,
        limit=limit,
        user_id_filter=current_admin.id,
        start_local=start_naive,
        end_local=end_naive,
        target_client_id=clientId,
    )


@app.post("/admin/reports", response_model=schemas.AdminReportOut)
def admin_create_report(
    payload: schemas.AdminCreateReportIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    proj_ids = payload.projectIds or None
    client_id = payload.clientId
    client_user = db_sess.get(models.User, client_id)
    row = crud.log_report_export(
        db_sess,
        user_id=current_admin.id,
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
        user=schemas.UserInfo(id=current_admin.id, login=current_admin.login),
        client=schemas.UserInfo(id=client_id, login=client_user.login if client_user else str(client_id)),
    )


@app.get("/admin/changes/summary", response_model=schemas.AdminClientChangesSummaryListOut)
def admin_changes_summary(
    actions: Optional[str] = None,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """
    Краткая сводка по количеству необработанных изменений по клиентам.
    Можно отфильтровать по списку действий через query param actions=update,delete.
    """
    actions_list = [a.strip() for a in (actions or "").split(",") if a.strip()] or None
    items = crud.admin_list_client_changes_summary(db_sess, actions=actions_list)
    return schemas.AdminClientChangesSummaryListOut(items=items)


@app.get("/balance", response_model=schemas.ClientBalanceSummaryOut)
def client_balance_summary(
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_user: models.User = Depends(require_auth),
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
    current_admin: models.User = Depends(require_admin),
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

    return crud.admin_clients_summary(
        db_sess,
        start_local=start_local,
        end_local=end_local,
    )


def _ensure_admin_client_exists(db_sess: Session, client_id: int) -> models.User:
    user = db_sess.get(models.User, client_id)
    if not user:
        raise HTTPException(status_code=404, detail="Client not found")
    return user


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

    paused_ids: List[int] = []
    skipped_count = 0
    failed_count = 0
    errors: List[str] = []

    for p in active_projects:
        if p.status == "Удалён":
            skipped_count += 1
            continue
        if not p.provider_project_id:
            skipped_count += 1
            errors.append(f'Проект {p.id} "{p.name}": не связан с Prostats, пропущен.')
            continue
        try:
            prostats.update_project_status(str(p.provider_project_id), p, "На паузе")
            ok = crud.admin_update_project_status_only(
                db_sess,
                project_id=int(p.id),
                status="На паузе",
                admin_user_id=current_admin.id,
            )
            if ok:
                paused_ids.append(int(p.id))
        except prostats.ProstatsError as exc:
            failed_count += 1
            errors.append(f'Проект {p.id} "{p.name}": {exc.message}')

    crud.admin_replace_pause_snapshot(
        db_sess,
        client_id=client_id,
        project_ids=paused_ids,
        admin_user_id=current_admin.id,
    )
    crud.admin_set_client_projects_mutation_lock(
        db_sess,
        client_id=client_id,
        locked=True,
        admin_user_id=current_admin.id,
        reason="Проекты во временной блокировке",
    )

    if paused_ids:
        crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    user = db_sess.get(models.User, client_id)
    if user:
        _sync_client_balance_alert(
            db_sess,
            user=user,
            remaining=crud.get_client_remaining_numbers(db_sess, client_id=client_id),
            active_limit_sum=crud.get_client_active_projects_limit_sum(db_sess, client_id=client_id),
        )

    state = crud.admin_get_collection_state(db_sess, client_id=client_id)

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
            prostats.update_project_status(str(p.provider_project_id), p, "Активен")
            ok = crud.admin_update_project_status_only(
                db_sess,
                project_id=int(p.id),
                status="Активен",
                admin_user_id=current_admin.id,
            )
            if ok:
                resumed_ids.append(int(p.id))
        except prostats.ProstatsError as exc:
            failed_count += 1
            errors.append(f'Проект {p.id} "{p.name}": {exc.message}')

    # Оставляем в снимке только те проекты, которые всё ещё на паузе и могут быть восстановлены позже.
    next_snapshot_ids: List[int] = []
    for pid in snapshot_ids:
        p = by_id.get(pid)
        if not p:
            continue
        if p.status == "Удалён":
            continue
        if not p.provider_project_id:
            continue
        if p.status != "Активен":
            next_snapshot_ids.append(int(pid))

    crud.admin_replace_pause_snapshot(
        db_sess,
        client_id=client_id,
        project_ids=next_snapshot_ids,
        admin_user_id=current_admin.id,
    )
    crud.admin_set_client_projects_mutation_lock(
        db_sess,
        client_id=client_id,
        locked=False,
        admin_user_id=current_admin.id,
        reason=None,
    )

    if resumed_ids:
        crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    user = db_sess.get(models.User, client_id)
    if user:
        _sync_client_balance_alert(
            db_sess,
            user=user,
            remaining=crud.get_client_remaining_numbers(db_sess, client_id=client_id),
            active_limit_sum=crud.get_client_active_projects_limit_sum(db_sess, client_id=client_id),
        )

    state = crud.admin_get_collection_state(db_sess, client_id=client_id)

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
    current_admin: models.User = Depends(require_admin),
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

    return crud.get_client_balance_summary(db_sess, client_id=client_id, start_local=start_local, end_local=end_local)


@app.get("/admin/clients/{client_id}/balance/ops", response_model=schemas.ClientBalanceOpsListOut)
def admin_client_balance_ops(
    client_id: int,
    offset: int = 0,
    limit: int = 50,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    current_admin: models.User = Depends(require_admin),
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


@app.post("/admin/clients/{client_id}/balance/ops", response_model=schemas.BalanceOperationOut)
def admin_create_balance_op(
    client_id: int,
    payload: schemas.BalanceOperationCreateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    if payload.type not in ("credit", "debit"):
        raise HTTPException(status_code=400, detail="type must be credit or debit")
    op = crud.create_client_balance_operation(
        db_sess,
        client_id=client_id,
        admin_id=current_admin.id,
        amount=payload.amount,
        op_type=payload.type,
        comment=payload.comment,
    )
    _run_limit_control_for_client(db_sess, client_id=client_id, trigger="balance_operation")
    return op


@app.get("/admin/changes/{client_id}", response_model=schemas.AdminClientChangesOut)
def admin_client_changes(
    client_id: int,
    actions: Optional[str] = None,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """
    Подробный список необработанных изменений конкретного клиента.
    Дополнительно можно фильтровать по actions (create,update,delete).
    """
    actions_list = [a.strip() for a in (actions or "").split(",") if a.strip()] or None
    return crud.admin_list_client_changes(db_sess, client_id=client_id, actions=actions_list)


@app.post("/admin/changes/{event_id}/resolve")
def admin_resolve_change(
    event_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """
    Помечает одно событие аудита как обработанное админом.
    """
    ev = db_sess.get(models.AuditEvent, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Change not found")
    if ev.batch_id:
        count = crud.admin_mark_batch_processed(db_sess, batch_id=ev.batch_id, admin_user_id=current_admin.id)
        if count == 0:
            raise HTTPException(status_code=404, detail="Change not found")
        return {"ok": True, "processed": count, "batch": ev.batch_id}
    ok = crud.admin_mark_change_processed(db_sess, event_id=event_id, admin_user_id=current_admin.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Change not found")
    return {"ok": True, "processed": 1}
