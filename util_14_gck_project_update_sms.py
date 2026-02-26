"""
Обновляет SMS-проект у провайдера (gck_project_update).

Зачем:
- Все изменяемые поля вынесены в начало файла.
- Удобно вручную подставлять новые значения перед запуском.

Запуск:
    python util_14_gck_project_update_sms.py
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"

# === ПАРАМЕТРЫ ОБНОВЛЕНИЯ (МЕНЯЙТЕ ЭТИ ПЕРЕМЕННЫЕ) ===
PROVIDER_PROJECT_ID = "11400044"
NAME = "B2_тест смс2"
TAG = "тест смс2"
LIMIT = 100
STATUS = 0  # 1 = активен, 0 = на паузе
WORKDAYS = "1234567"  # 1..7 (Пн..Вс)
CONTENT = "71111213432"  # sender для sms-проекта

# Регионы:
# - None: не передавать регионы в update (оставить как есть на стороне провайдера)
# - []: очистить регионы
# - [77, 78]: передать конкретные коды регионов
REGIONS: Optional[list[int]] = None
REGIONS_REVERSE = 0  # 0 = include, 1 = exclude

# Некоторые инсталляции API могут требовать type/src в update.
# По умолчанию не отправляем (как в базовом util_07).
SEND_TYPE_AND_SRC = True
PROJECT_TYPE = "sms"  # sms / calls / hosts / complex
PROJECT_SRC = "bl"    # B2=bl, B3=mt, B1=rt, B4=mg


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return value


def _get_api_url() -> str:
    return os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT


def _post(payload: dict) -> Tuple[int, str, Optional[dict]]:
    response = requests.post(_get_api_url(), json=payload, timeout=60)
    raw_text = response.text
    parsed = None
    try:
        parsed = response.json()
    except Exception:
        parsed = None
    return response.status_code, raw_text, parsed


def _mask_token(payload: dict) -> dict:
    safe = dict(payload)
    token = safe.get("token")
    if token:
        safe["token"] = "****"
    return safe


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    token = require_env("PROSTATS_TOKEN")
    project_id = str(PROVIDER_PROJECT_ID).strip()
    if not project_id:
        raise SystemExit("PROVIDER_PROJECT_ID is required")

    payload = {
        "token": token,
        "command": "gck_project_update",
        "id": project_id,
        "name": str(NAME).strip(),
        "tag": str(TAG).strip() or str(NAME).strip(),
        "limit": int(LIMIT),
        "content": str(CONTENT).strip(),
        "status": int(STATUS),
        "is_crm": 0,
        "workdays": str(WORKDAYS).strip(),
    }

    if REGIONS is not None:
        payload["regions"] = REGIONS
        payload["regions_reverse"] = int(REGIONS_REVERSE)

    if SEND_TYPE_AND_SRC:
        payload["type"] = str(PROJECT_TYPE).strip()
        payload["src"] = str(PROJECT_SRC).strip()

    status_code, raw_text, parsed = _post(payload)

    logger.info("REQUEST %s", json.dumps(_mask_token(payload), ensure_ascii=False))
    logger.info("RESPONSE_HTTP_STATUS %s", status_code)
    logger.info("RESPONSE_RAW %s", raw_text)
    logger.info(
        "RESPONSE_JSON %s",
        json.dumps(parsed, ensure_ascii=False) if parsed is not None else "not a json response",
    )

    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise SystemExit("Update failed. Check RESPONSE above for details.")

    logger.info("Update finished with status=success.")


if __name__ == "__main__":
    main()

