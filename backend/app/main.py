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
import uuid
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import db, models, schemas, crud, telegram, notify_worker, logging_setup


def get_settings():
    return {
        "DATABASE_URL": os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        "DEBOUNCE_WINDOW_MINUTES": int(os.getenv("DEBOUNCE_WINDOW_MINUTES", "30")),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.get("/projects", response_model=List[schemas.ProjectOut])
def list_projects(db_sess: Session = Depends(get_db)):
    return crud.list_projects(db_sess)


@app.post("/projects", response_model=List[schemas.ProjectOut])
def create_projects(payload: schemas.CreateProjectsPayload, db_sess: Session = Depends(get_db)):
    created = crud.create_projects(db_sess, payload.items)
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return created


@app.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: int, db_sess: Session = Depends(get_db)):
    project = crud.get_project(db_sess, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: int, payload: schemas.ProjectUpdate, db_sess: Session = Depends(get_db)):
    updated = crud.update_project(db_sess, project_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return updated


@app.delete("/projects/{project_id}")
def delete_project(project_id: int, db_sess: Session = Depends(get_db)):
    ok = crud.delete_project(db_sess, project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    crud.schedule_debounce(db_sess, minutes=settings["DEBOUNCE_WINDOW_MINUTES"])
    return {"deleted": True}


