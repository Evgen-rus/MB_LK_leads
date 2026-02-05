"""
Скрипт запрашивает список проектов у Prostats и сохраняет ответ в JSON-файл.
"""

import json
import os
import sys
from datetime import datetime

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"


def require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return v


def main() -> None:
    load_dotenv()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")

    payload = {"token": token, "command": "gck_projects"}

    r = requests.post(api_url, json=payload, timeout=60)
    r.raise_for_status()

    # сохраняем и raw текст (на всякий)
    raw_text = r.text
    try:
        data = r.json()
    except Exception:
        out = {
            "error": "non_json_response",
            "http_status": r.status_code,
            "text": raw_text[:5000],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_file = sys.argv[1] if len(sys.argv) > 1 else f"gck_projects_{ts}.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Saved projects to: {out_file}")

    # полезный вывод
    result = data.get("result") or []
    print("Projects count:", len(result))
    if result:
        print("First project example:", result[0])


if __name__ == "__main__":
    main()
