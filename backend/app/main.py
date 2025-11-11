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


def require_auth(request: Request):
    cookie = request.cookies.get("session")
    user = auth.verify_session(cookie or "") if cookie else None
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


@app.get("/projects", response_model=List[schemas.ProjectOut])
def list_projects(_: str = Depends(require_auth), db_sess: Session = Depends(get_db)):
    return crud.list_projects(db_sess)


@app.post("/projects", response_model=List[schemas.ProjectOut])
def create_projects(payload: schemas.CreateProjectsPayload, _: str = Depends(require_auth), db_sess: Session = Depends(get_db)):
    created = crud.create_projects(db_sess, payload.items)
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return created


@app.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: int, _: str = Depends(require_auth), db_sess: Session = Depends(get_db)):
    project = crud.get_project(db_sess, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: int, payload: schemas.ProjectUpdate, _: str = Depends(require_auth), db_sess: Session = Depends(get_db)):
    updated = crud.update_project(db_sess, project_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return updated


@app.delete("/projects/{project_id}")
def delete_project(project_id: int, _: str = Depends(require_auth), db_sess: Session = Depends(get_db)):
    ok = crud.delete_project(db_sess, project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return {"deleted": True}


@app.post("/auth/login")
def login(payload: dict, request: Request):
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    u_ok = secrets.compare_digest(username, os.getenv("AUTH_USER", ""))
    p_ok = secrets.compare_digest(password, os.getenv("AUTH_PASSWORD", ""))
    if not (u_ok and p_ok):
        raise HTTPException(status_code=401, detail="Bad credentials")
    token = auth.sign_session(username)
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    secure = os.getenv("COOKIE_SECURE", "0").lower() in ("1","true")
    resp.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=86400,
    )
    return resp


@app.post("/auth/logout")
def logout():
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session", path="/")
    return resp


# ----------------------- Лиды -----------------------
@app.get("/leads", response_model=List[schemas.LeadOut])
def list_leads(
    projectIds: Optional[str] = None,  # "1,2,3"; если нет — все
    fromDate: Optional[str] = None,  # YYYY-MM-DD
    toDate: Optional[str] = None,    # YYYY-MM-DD
    _: str = Depends(require_auth),
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

    return crud.list_leads(db_sess, project_ids=proj_ids, start_local=start_naive, end_local=end_naive)


@app.get("/leads/export")
def export_leads(
    projectIds: Optional[str] = None,
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    format: Optional[str] = "csv",  # csv | xlsx
    _: str = Depends(require_auth),
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

    max_rows = int(os.getenv("EXPORT_MAX_ROWS", "200000"))
    rows = crud.fetch_leads_for_export(db_sess, project_ids=proj_ids, start_local=start_local, end_local=end_local, max_rows=max_rows)

    filename = f"leads_{fromDate}_{toDate}.{format}"
    if (format or "csv").lower() == "csv":
        def gen():
            # заголовки
            yield ("ext_id;project_id;created_at;phone;utm_campaign\n").encode('utf-8-sig')
            for r in rows:
                utm = r.utm_campaign or ""
                line = f"{r.ext_id};{r.project_id};{r.created_at.strftime('%Y-%m-%d %H:%M:%S')};{r.phone};{utm}\n"
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
        ws.append(["ext_id", "project_id", "created_at", "phone", "utm_campaign"])
        for r in rows:
            ws.append([r.ext_id, r.project_id, r.created_at.strftime('%Y-%m-%d %H:%M:%S'), r.phone, r.utm_campaign or ""])
        bio = io.BytesIO()
        wb.save(bio)
        data = bio.getvalue()
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


# ----------------------- Черный список -----------------------
@app.get("/blacklist", response_model=List[schemas.BlacklistPhoneOut])
def list_blacklist(_: str = Depends(require_auth), db_sess: Session = Depends(get_db)):
    return crud.list_blacklist(db_sess)


@app.post("/blacklist", response_model=List[schemas.BlacklistPhoneOut])
def add_blacklist(payload: schemas.BlacklistAddIn, _: str = Depends(require_auth), db_sess: Session = Depends(get_db)):
    return crud.add_to_blacklist(db_sess, payload.phones)


@app.delete("/blacklist/{row_id}")
def delete_blacklist(row_id: int, _: str = Depends(require_auth), db_sess: Session = Depends(get_db)):
    ok = crud.delete_from_blacklist(db_sess, row_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}