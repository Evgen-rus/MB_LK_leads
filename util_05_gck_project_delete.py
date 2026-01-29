import json
import os
import sys
from typing import Any, Optional, Tuple

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


def _print_block(title: str, content: Any) -> None:
    print("=" * 80)
    print(title)
    print("-" * 80)
    if isinstance(content, (dict, list)):
        print(json.dumps(content, ensure_ascii=True, indent=2))
    else:
        print(content)


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

    _print_block("REQUEST", _mask_token(payload))
    _print_block("RESPONSE: HTTP STATUS", status_code)
    _print_block("RESPONSE: RAW", raw_text[:5000])
    _print_block("RESPONSE: JSON", parsed if parsed is not None else "not a json response")

    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise SystemExit("Delete failed. Check RESPONSE above for details.")

    print("=" * 80)
    print("Delete finished with status=success.")


if __name__ == "__main__":
    main()
