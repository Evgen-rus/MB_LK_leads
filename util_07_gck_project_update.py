"""
Обновляет проект у провайдера по provider_id.

Запуск:
python util_07_gck_project_update.py
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"

# === ПАРАМЕТРЫ ОБНОВЛЕНИЯ (ПРИМЕРЫ) ===
PROVIDER_PROJECT_ID = "10976296"  # id проекта у провайдера (обязательно)
# Если NAME/TAG пустые, возьмём текущие значения у провайдера
NAME = ""
TAG = ""
LIMIT = "10"
STATUS = 0  # 1 = активен, 0 = на паузе
WORKDAYS = "23456"  # Пн..Пт

# Для calls/hosts: контент — список через запятую
CONTENT = "79231111116,79231111117"

# Регионы можно оставить None (не изменять), либо задать список кодов.
REGIONS = [77, 78]  # пример: [77, 78]
REGIONS_REVERSE = 0  # 0 = include, 1 = exclude


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


def get_project_detail(project_id: str) -> Optional[dict]:
    token = require_env("PROSTATS_TOKEN")
    payload = {"token": token, "command": "gck_project", "id": project_id}
    status_code, raw_text, parsed = _post(payload)
    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise SystemExit(f"Failed to load project detail. Status={status_code} Raw={raw_text}")
    return parsed.get("result") or None


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
    if not PROVIDER_PROJECT_ID.strip():
        raise SystemExit("PROVIDER_PROJECT_ID is required")

    provider_name = NAME.strip()
    provider_tag = TAG.strip()
    if not provider_name or not provider_tag:
        detail = get_project_detail(PROVIDER_PROJECT_ID.strip())
        if not detail:
            raise SystemExit("Provider project not found.")
        if not provider_name:
            provider_name = str(detail.get("name") or "").strip()
        if not provider_tag:
            provider_tag = str(detail.get("tag") or provider_name or "").strip()

    payload = {
        "token": token,
        "command": "gck_project_update",
        "id": PROVIDER_PROJECT_ID.strip(),
        "name": provider_name,
        "tag": provider_tag or provider_name,
        "limit": int(LIMIT),
        "content": CONTENT.strip(),
        "status": int(STATUS),
        "is_crm": 0,
        "workdays": WORKDAYS.strip(),
    }

    if REGIONS is not None:
        payload["regions"] = REGIONS
        payload["regions_reverse"] = int(REGIONS_REVERSE)

    status_code, raw_text, parsed = _post(payload)

    logger.info("REQUEST %s", json.dumps(_mask_token(payload), ensure_ascii=True))
    logger.info("RESPONSE_HTTP_STATUS %s", status_code)
    logger.info("RESPONSE_RAW %s", raw_text)
    logger.info(
        "RESPONSE_JSON %s",
        json.dumps(parsed, ensure_ascii=True)
        if parsed is not None
        else "not a json response",
    )

    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise SystemExit("Update failed. Check RESPONSE above for details.")

    logger.info("Update finished with status=success.")


if __name__ == "__main__":
    main()
