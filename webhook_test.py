"""
Минимальный FastAPI-сервер для приёма тестовых вебхуков.
Не использует БД, только пишет входящие данные в лог и отвечает 200 OK.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dotenv import load_dotenv

load_dotenv()


LOG_PATH = Path("logs/provider_webhook.log")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET is not set")


def _setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("provider_webhook")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
    )

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = _setup_logger()
app = FastAPI()


async def _read_body(request: Request) -> Tuple[Dict[str, Any], str]:
    """
    Возвращает кортеж: (данные, формат).
    Поддерживает JSON и form-data/URL-encoded.
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        return payload, "json"

    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return dict(form), "form"

    raw = await request.body()
    return {"raw": raw.decode("utf-8", errors="replace")}, "raw"


@app.post(f"/api/provider-test/{WEBHOOK_SECRET}")
async def provider_test(request: Request) -> JSONResponse:
    payload, fmt = await _read_body(request)
    logger.info(
        json.dumps(
            {
                "format": fmt,
                "headers": dict(request.headers),
                "payload": payload,
            },
            ensure_ascii=False,
        ),
    )
    return JSONResponse({"ok": True})
