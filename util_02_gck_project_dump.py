"""
Скрипт запрашивает один проект у Prostats по id и сохраняет JSON + raw ответ.
"""

import json
import os
import sys
from datetime import datetime

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return value


def require_project_id() -> str:
    if len(sys.argv) < 2:
        raise SystemExit("Missing project id. Usage: python 02_gck_project_dump.py <id> [out_file]")
    project_id = str(sys.argv[1]).strip()
    if not project_id:
        raise SystemExit("Project id is empty. Provide a valid id.")
    return project_id


def main() -> None:
    load_dotenv()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")
    project_id = require_project_id()

    payload = {"token": token, "command": "gck_project", "id": project_id}

    response = requests.post(api_url, json=payload, timeout=60)

    # сохраняем и raw текст (на всякий)
    raw_text = response.text

    if not response.ok:
        print(f"HTTP error: {response.status_code} for url: {response.url}")
        print("API response (first 2000 chars):")
        print(raw_text[:2000] if raw_text else "<empty response body>")

        try:
            error_data = response.json()
            print("Parsed API error JSON:")
            print(json.dumps(error_data, ensure_ascii=False, indent=2)[:4000])
        except Exception:
            print("Response is not valid JSON.")
        return

    try:
        data = response.json()
    except Exception:
        out = {
            "error": "non_json_response",
            "http_status": response.status_code,
            "text": raw_text[:5000],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    default_name = f"gck_project_{project_id}_{ts}.json"
    out_file = sys.argv[2] if len(sys.argv) > 2 else default_name
    raw_out_file = f"gck_project_{project_id}_{ts}_raw.txt"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Saved project to: {out_file}")
    with open(raw_out_file, "w", encoding="utf-8") as f:
        f.write(raw_text)
    print(f"Saved raw response to: {raw_out_file}")

    # полезный вывод
    result = data.get("result") or {}
    if result:
        print("Project id:", result.get("id"))
        print("Project name:", result.get("name"))
        print("Project status:", result.get("status"))


if __name__ == "__main__":
    main()
