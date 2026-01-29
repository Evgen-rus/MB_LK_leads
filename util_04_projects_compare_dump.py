import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return value


def get_db_path() -> str:
    db_url = os.getenv("DATABASE_URL", "sqlite:///./app.db").strip()
    if db_url.startswith("sqlite:///"):
        raw_path = db_url[len("sqlite:///"):] or "./app.db"
        return os.path.abspath(raw_path)
    raise SystemExit("Only sqlite DATABASE_URL is supported in this script.")


def parse_json_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return value
    return value


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    for key in ("regions", "sites", "phones"):
        data[key] = parse_json_cell(data.get(key))
    return data


def fetch_projects(conn: sqlite3.Connection, project_id: Optional[str]) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    if project_id:
        cur = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cur.fetchone()
        if row:
            return [row_to_dict(row)]
        cur = conn.execute(
            "SELECT * FROM projects WHERE provider_project_id = ?",
            (project_id,),
        )
        row = cur.fetchone()
        if row:
            return [row_to_dict(row)]
        raise SystemExit(f"Project not found by id or provider_project_id: {project_id}")
    cur = conn.execute(
        "SELECT * FROM projects WHERE provider_project_id IS NOT NULL AND TRIM(provider_project_id) != ''"
    )
    return [row_to_dict(row) for row in cur.fetchall()]


def fetch_prostats_project(api_url: str, token: str, provider_id: str) -> Dict[str, Any]:
    payload = {"token": token, "command": "gck_project", "id": provider_id}
    response = requests.post(api_url, json=payload, timeout=60)
    raw_text = response.text
    try:
        parsed = response.json()
    except Exception:
        parsed = None
    return {
        "http_status": response.status_code,
        "json": parsed,
        "raw_text": raw_text[:5000],
    }


def main() -> None:
    """
    Usage:
      python util_04_projects_compare_dump.py [project_id] [out_file]

    - Без project_id: проходит по всем проектам с provider_project_id в нашей БД.
    - С project_id: ищет по projects.id, если нет — по provider_project_id.
    """
    load_dotenv()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")
    project_id = sys.argv[1].strip() if len(sys.argv) > 1 else None

    db_path = get_db_path()
    if not os.path.exists(db_path):
        raise SystemExit(f"Database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        local_items = fetch_projects(conn, project_id)

    items: List[Dict[str, Any]] = []
    for local in local_items:
        provider_id = str(local.get("provider_project_id") or "").strip() or None
        if provider_id:
            prostats = fetch_prostats_project(api_url, token, provider_id)
        else:
            prostats = {"error": "missing_provider_project_id"}
        items.append(
            {
                "local": local,
                "provider_project_id": provider_id,
                "prostats": prostats,
            }
        )

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    default_name = (
        f"gck_projects_compare_{ts}.json"
        if not project_id
        else f"gck_project_compare_{project_id}_{ts}.json"
    )
    out_file = sys.argv[2] if len(sys.argv) > 2 else default_name

    output = {
        "meta": {
            "generated_at": ts,
            "db_path": db_path,
            "items_count": len(items),
        },
        "items": items,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Saved report to: {out_file}")
    print(f"Projects processed: {len(items)}")


if __name__ == "__main__":
    main()
