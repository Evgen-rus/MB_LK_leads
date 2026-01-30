import json
import logging
import os
import sys
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"


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
    """
    Usage:
      python util_05_gck_project_delete.py <project_id>
    """
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    logger = logging.getLogger(__name__)

    token = require_env("PROSTATS_TOKEN")
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python util_05_gck_project_delete.py <project_id>")

    project_id = sys.argv[1].strip()
    if not project_id:
        raise SystemExit("project_id is required")

    payload = {
        "token": token,
        "command": "gck_project_delete",
        "id": project_id,
    }

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
        raise SystemExit("Delete failed. Check RESPONSE above for details.")

    logger.info("Delete finished with status=success.")


if __name__ == "__main__":
    main()
