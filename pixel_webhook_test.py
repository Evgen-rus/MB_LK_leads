"""
Минимальный FastAPI-сервер для приёма тестовых вебхуков Пикселя.
Не использует БД, только пишет входящие данные в отдельный лог и отвечает 200 OK.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()


LOG_PATH = Path("logs/pixel_webhook.log")

PIXEL_WEBHOOK_SECRET = os.getenv("PIXEL_WEBHOOK_SECRET", "").strip()
if not PIXEL_WEBHOOK_SECRET:
    raise RuntimeError("PIXEL_WEBHOOK_SECRET is not set")


def _setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pixel_webhook")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(message)s")

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = _setup_logger()
app = FastAPI(title="Pixel Webhook Test")


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}

    filename = getattr(value, "filename", None)
    if filename is not None:
        return {
            "filename": filename,
            "content_type": getattr(value, "content_type", None),
        }

    return str(value)


async def _read_body(request: Request) -> Tuple[Dict[str, Any], str]:
    """
    Возвращает кортеж: (данные, формат).
    Поддерживает JSON, form-data/URL-encoded и raw body.
    """
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return {
                "raw": raw.decode("utf-8", errors="replace"),
                "json_error": str(exc),
            }, "invalid_json"
        if isinstance(payload, dict):
            return _jsonable(payload), "json"
        return {"raw": _jsonable(payload)}, "json"

    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        payload: Dict[str, Any] = {}
        for key in form.keys():
            values = [_jsonable(item) for item in form.getlist(key)]
            payload[str(key)] = values if len(values) > 1 else (values[0] if values else None)
        return payload, "form"

    raw = await request.body()
    return {"raw": raw.decode("utf-8", errors="replace")}, "raw"


def _phones_count(payload: Dict[str, Any]) -> int:
    phones = payload.get("phones")
    if isinstance(phones, list):
        return len([phone for phone in phones if str(phone or "").strip()])
    if isinstance(phones, str):
        return 1 if phones.strip() else 0
    return 0


def _build_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "vid": payload.get("vid"),
        "site": payload.get("site"),
        "page": payload.get("page"),
        "phones_count": _phones_count(payload),
        "ip": payload.get("ip"),
        "device": payload.get("device"),
        "browser": payload.get("browser"),
        "platform": payload.get("platform"),
    }


@app.post("/api/pixel-webhook/{secret}")
async def pixel_webhook(secret: str, request: Request) -> JSONResponse:
    if secret != PIXEL_WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        payload, fmt = await _read_body(request)
    except Exception as exc:
        payload, fmt = {"read_error": str(exc)}, "read_error"

    log_record = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.url.path.replace(secret, "<secret>", 1),
        "headers": dict(request.headers),
        "format": fmt,
        "summary": _build_summary(payload),
        "payload": payload,
    }
    logger.info(json.dumps(log_record, ensure_ascii=False))

    return JSONResponse({"ok": True})
