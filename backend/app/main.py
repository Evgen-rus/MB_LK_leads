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
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from . import db, models, schemas, crud, telegram, notify_worker, logging_setup, auth


def get_settings():
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        "DEBOUNCE_WINDOW_MINUTES": int(os.getenv("DEBOUNCE_WINDOW_MINUTES", "30")),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
        "SHEETS_TZ": os.getenv("SHEETS_TZ", "Europe/Moscow"),
    }


load_dotenv()
logging_setup.setup_logging()
settings = get_settings()

engine, SessionLocal = db.init_engine_and_session(settings["DATABASE_URL"]) 
models.Base.metadata.create_all(bind=engine)


def get_db():
    db_sess = SessionLocal()
    try:
        yield db_sess
    finally:
        db_sess.close()


app = FastAPI(title="LK Projects API")
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
        # Ensure single row state exists
        crud.ensure_notify_state(s, settings["DEBOUNCE_WINDOW_MINUTES"])
        # Ensure users from .env exist and projects have user_id column
        crud.ensure_users_from_env(s)
        crud.ensure_projects_user_id_column(s)
        crud.ensure_audit_user_id_column(s)
        crud.ensure_blacklist_user_id_column(s)
        crud.ensure_audit_admin_columns(s)
        crud.ensure_report_client_id_column(s)
        crud.ensure_client_profile_contact_column(s)

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


@app.get("/health")
def health() -> dict:
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


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

    user_id = auth.decode_access_token(token or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = db_sess.get(models.User, int(user_id))
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


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


@app.post("/projects", response_model=List[schemas.ProjectOut])
def create_projects(payload: schemas.CreateProjectsPayload, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    created = crud.create_projects(db_sess, payload.items, user_id=current_user.id)
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return created


@app.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: int, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    project = crud.get_project(db_sess, project_id, user_id=current_user.id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: int, payload: schemas.ProjectUpdate, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    updated = crud.update_project(db_sess, project_id, payload, user_id=current_user.id)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return updated


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
    ok = crud.delete_project(db_sess, project_id, user_id=current_user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return {"deleted": True}


@app.post("/login")
def login(payload: dict, db_sess: Session = Depends(get_db)):
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

    # Разрешённые проекты текущего пользователя
    allowed_ids = set(crud.get_user_project_ids(db_sess, current_user.id))

    proj_ids: Optional[List[int]] = None
    if projectIds:
        try:
            proj_ids = [int(x) for x in projectIds.split(',') if x.strip()]
            if not proj_ids:
                proj_ids = None
        except Exception:
            proj_ids = None

    # Ограничиваем проектами пользователя (для клиентского ЛК)
    allowed_ids = set(crud.get_user_project_ids(db_sess, current_user.id))
    if proj_ids is None:
        proj_ids = list(allowed_ids)
    else:
        proj_ids = [pid for pid in proj_ids if pid in allowed_ids]

    # Пересекаем с разрешёнными
    if proj_ids is None:
        proj_ids = list(allowed_ids)
    else:
        proj_ids = [pid for pid in proj_ids if pid in allowed_ids]

    src_list: Optional[List[str]] = None
    if sources:
        src_list = [s.strip() for s in sources.split(",") if s.strip()]
        if not src_list:
            src_list = None

    limit = max(1, min(1000, limit))
    offset = max(0, offset)
    return crud.list_leads_paginated(
        db_sess,
        project_ids=proj_ids,
        start_local=start_naive,
        end_local=end_naive,
        offset=offset,
        limit=limit,
        sources=src_list,
    )


@app.get("/leads/export")
def export_leads(
    projectIds: Optional[str] = None,
    sources: Optional[str] = None,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    format: Optional[str] = "csv",  # csv | xlsx
    source: Optional[str] = "leads",
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
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

    # Логируем экспорт отчёта (для вкладки "Отчёты").
    # Повторные скачивания из раздела "Отчёты" помечаем source=reports и не логируем,
    # чтобы не плодить дубли.
    if (source or "leads") != "reports":
        try:
            crud.log_report_export(
                db_sess,
                user_id=current_user.id,
                from_date=fromDate,
                to_date=toDate,
                project_ids=proj_ids,
                fmt=(format or "csv"),
            )
        except Exception:
            # Не блокируем выгрузку, если логирование по какой-то причине не удалось
            logging.getLogger("app").exception("Failed to log report export")

    max_rows = int(os.getenv("EXPORT_MAX_ROWS", "200000"))
    rows = crud.fetch_leads_for_export(
        db_sess,
        project_ids=proj_ids,
        start_local=start_local,
        end_local=end_local,
        max_rows=max_rows,
        sources=src_list,
        current_user_id=current_user.id,
    )

    filename = f"leads_{fromDate}_{toDate}.{format}"
    is_admin = current_user.id == 1
    if (format or "csv").lower() == "csv":
        def gen():
            if is_admin:
                yield ("ext_id;project_id;project_name;source;imported_at;phone;utm_campaign;user_login;user_id\n").encode('utf-8-sig')
            else:
                yield ("project_id;project_name;source;imported_at;phone;utm_campaign;user_login;user_id\n").encode('utf-8-sig')
            for r in rows:
                utm = r["utm_campaign"] or ""
                if is_admin:
                    line = f"{r['ext_id']};{r['project_id']};{r['project_name']};{r['source'] or ''};{r['imported_at']};{r['phone']};{utm};{r['user_login']};{r['user_id']}\n"
                else:
                    line = f"{r['project_id']};{r['project_name']};{r['source'] or ''};{r['imported_at']};{r['phone']};{utm};{r['user_login']};{r['user_id']}\n"
                yield line.encode('utf-8')
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(gen(), media_type="text/csv; charset=utf-8", headers=headers)
    else:
        # XLSX через openpyxl в память
        import io
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "leads"
        if is_admin:
            ws.append(["ext_id", "project_id", "project_name", "source", "imported_at", "phone", "utm_campaign", "user_login", "user_id"])
        else:
            ws.append(["project_id", "project_name", "source", "imported_at", "phone", "utm_campaign", "user_login", "user_id"])
        for r in rows:
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
        bio = io.BytesIO()
        wb.save(bio)
        data = bio.getvalue()
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


@app.get("/reports", response_model=schemas.ReportListOut)
def list_reports(
    offset: int = 0,
    limit: int = 50,
    current_user: models.User = Depends(require_auth),
    db_sess: Session = Depends(get_db),
):
    """
    Вкладка «Отчёты»: история всех экспортов отчётов текущего пользователя.
    """
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.list_reports_paginated(db_sess, user_id=current_user.id, offset=offset, limit=limit)


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

# ----------------------- Черный список -----------------------
@app.get("/blacklist", response_model=schemas.BlacklistListOut)
def list_blacklist(offset: int = 0, limit: int = 50, q: str | None = None, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return crud.list_blacklist_paginated(db_sess, user_id=current_user.id, offset=offset, limit=limit, q=q)


@app.post("/blacklist", response_model=List[schemas.BlacklistPhoneOut])
def add_blacklist(payload: schemas.BlacklistAddIn, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    items = crud.add_to_blacklist(db_sess, current_user.id, payload.phones)
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return items


@app.delete("/blacklist/{row_id}")
def delete_blacklist(row_id: int, current_user: models.User = Depends(require_auth), db_sess: Session = Depends(get_db)):
    ok = crud.delete_from_blacklist(db_sess, current_user.id, row_id)
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


def create_impersonation_token(client_user_id: int, ttl_minutes: int = 15) -> str:
    """Генерируем короткоживущий JWT для входа под клиентом (без флага is_admin)."""
    from datetime import timedelta

    return auth.create_access_token(
        user_id=client_user_id,
        is_admin=False,
        expires_delta=timedelta(minutes=max(1, ttl_minutes)),
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
    Выдаёт короткоживущий токен (15 минут) для входа в ЛК клиента.
    Токен имеет payload user_id клиента, без is_admin.
    """
    client_user = db_sess.get(models.User, client_id)
    if not client_user:
        raise HTTPException(status_code=404, detail="Client not found")

    token = create_impersonation_token(client_user.id, ttl_minutes=15)
    return {"access_token": token, "ttl_minutes": 15}


@app.patch("/admin/clients/{client_id}", response_model=schemas.AdminClientUpdateOut)
def admin_update_client(
    client_id: int,
    payload: schemas.AdminClientUpdateIn,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    try:
        return crud.admin_update_client(
            db_sess,
            client_id=client_id,
            name=payload.name,
            inn=payload.inn,
            phone=payload.phone,
            contact=payload.contact,
            login=payload.login,
            password=payload.password,
        )
    except ValueError as exc:
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


@app.patch("/admin/projects/{project_id}", response_model=schemas.AdminProjectOut)
def admin_update_project(
    project_id: int,
    payload: schemas.AdminProjectUpdate,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Обновить проект (включая delivery_status)."""
    updated = crud.admin_update_project(db_sess, project_id, payload, admin_user_id=current_admin.id)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return updated


@app.delete("/admin/projects/{project_id}")
def admin_delete_project(
    project_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Удалить проект (для админа)."""
    ok = crud.admin_delete_project(db_sess, project_id, admin_user_id=current_admin.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
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
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """Список всех отчётов (для админа)."""
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    # Показываем только отчёты, сформированные текущим админом
    return crud.admin_list_all_reports(db_sess, offset=offset, limit=limit, user_id_filter=current_admin.id)


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
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """
    Краткая сводка по количеству необработанных изменений по клиентам.
    """
    items = crud.admin_list_client_changes_summary(db_sess)
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
    return crud.create_client_balance_operation(
        db_sess,
        client_id=client_id,
        admin_id=current_admin.id,
        amount=payload.amount,
        op_type=payload.type,
        comment=payload.comment,
    )


@app.get("/admin/changes/{client_id}", response_model=schemas.AdminClientChangesOut)
def admin_client_changes(
    client_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """
    Подробный список необработанных изменений конкретного клиента.
    """
    return crud.admin_list_client_changes(db_sess, client_id=client_id)


@app.post("/admin/changes/{event_id}/resolve")
def admin_resolve_change(
    event_id: int,
    current_admin: models.User = Depends(require_admin),
    db_sess: Session = Depends(get_db),
):
    """
    Помечает одно событие аудита как обработанное админом.
    """
    ok = crud.admin_mark_change_processed(db_sess, event_id=event_id, admin_user_id=current_admin.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Change not found")
    return {"ok": True}