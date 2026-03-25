"""
Сверка лидов провайдера из API с нашей таблицей provider_leads.

Первая версия утилиты:
- работает только как ручной CLI-инструмент;
- спрашивает id проекта у провайдера и дату YYYY-MM-DD;
- ничего не пишет в БД;
- ничего не сохраняет в файлы;
- печатает в терминал только сводку и лиды, которые есть в API, но нет у нас.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app import db as db_mod
from backend.app import models


API_URL_DEFAULT = "https://prostats.info/api/index.php"
DEFAULT_DB_URL = "sqlite:///./app.db"
REQUEST_TIMEOUT_SEC = 60
PAGE_SIZE_HINT = 1000
REQUEST_DELAY_SEC = 0.3


@dataclass(frozen=True)
class LeadKey:
    phone: str
    day: str


@dataclass
class ApiLeadRow:
    phone: str
    created_at: str
    day: str
    raw: Dict[str, Any]


@dataclass
class DbLeadRow:
    id: int
    vid: str
    phone: str
    prov_created_at: datetime
    day: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Сверить лиды из API провайдера с provider_leads по project_id и дню"
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL", DEFAULT_DB_URL),
        help="DATABASE_URL для подключения (по умолчанию из env или sqlite:///./app.db)",
    )
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return value


def prompt_provider_project_id() -> str:
    value = input("Введите id проекта Prostats: ").strip()
    if not value:
        raise SystemExit("Project id is empty. Provide a valid id.")
    return value


def prompt_date() -> str:
    value = input("Введите дату в формате YYYY-MM-DD: ").strip()
    if not value:
        raise SystemExit("Date is empty. Provide a valid date.")

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("Invalid date format. Use YYYY-MM-DD.") from exc
    return value


def normalize_phone(value: Any) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    return digits or None


def parse_api_created_at(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None

    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def fetch_gck_phones_page(
    api_url: str,
    token: str,
    provider_project_id: str,
    date_str: str,
    page: int,
) -> requests.Response:
    payload = {
        "token": token,
        "command": "gck_phones",
        "id": provider_project_id,
        "date": date_str,
        "page": page,
    }
    response = requests.post(api_url, json=payload, timeout=REQUEST_TIMEOUT_SEC)
    response.raise_for_status()
    return response


def extract_page_items(data: Any) -> Tuple[List[Any], str]:
    if not isinstance(data, dict):
        return [], "top_level_not_dict"

    result = data.get("result")
    if isinstance(result, list):
        return result, "result"

    if isinstance(result, dict):
        for key in ("items", "data", "phones", "rows", "list"):
            value = result.get(key)
            if isinstance(value, list):
                return value, f"result.{key}"
        return [result], "result_object_fallback"

    return [], "result_missing_or_unsupported"


def fetch_api_rows(api_url: str, token: str, provider_project_id: str, date_str: str) -> Tuple[List[ApiLeadRow], Dict[str, int]]:
    rows: List[ApiLeadRow] = []
    stats = {
        "pages_fetched": 0,
        "raw_items": 0,
        "invalid_phone": 0,
        "invalid_created_at": 0,
    }

    page = 1
    while True:
        response = fetch_gck_phones_page(api_url, token, provider_project_id, date_str, page)
        try:
            data = response.json()
        except Exception as exc:
            raise SystemExit(f"Provider API returned non-JSON response on page {page}.") from exc

        page_items, _ = extract_page_items(data)
        stats["pages_fetched"] += 1
        stats["raw_items"] += len(page_items)

        for item in page_items:
            if not isinstance(item, dict):
                stats["invalid_phone"] += 1
                continue

            phone = normalize_phone(item.get("phone"))
            if not phone:
                stats["invalid_phone"] += 1
                continue

            created_at = parse_api_created_at(item.get("created_at"))
            if not created_at:
                stats["invalid_created_at"] += 1
                continue

            rows.append(
                ApiLeadRow(
                    phone=phone,
                    created_at=created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    day=created_at.date().isoformat(),
                    raw=item,
                )
            )

        if len(page_items) == 0:
            break
        if len(page_items) < PAGE_SIZE_HINT:
            break

        page += 1
        if REQUEST_DELAY_SEC > 0:
            import time

            time.sleep(REQUEST_DELAY_SEC)

    return rows, stats


def get_single_project_by_provider_id(db_sess, provider_project_id: str) -> models.Project:
    stmt: Select[tuple[models.Project]] = (
        select(models.Project)
        .where(models.Project.provider_project_id == str(provider_project_id).strip())
        .order_by(models.Project.id.asc())
    )
    projects = list(db_sess.execute(stmt).scalars())

    if not projects:
        raise SystemExit(f"Project with provider_project_id={provider_project_id} not found in DB.")

    if len(projects) > 1:
        print("")
        print("[ERROR] Найдено несколько проектов с одинаковым provider_project_id:")
        for project in projects:
            print(
                json.dumps(
                    {
                        "id": int(project.id),
                        "name": project.name,
                        "status": project.status,
                        "provider_project_id": project.provider_project_id,
                    },
                    ensure_ascii=False,
                )
            )
        raise SystemExit("Ambiguous provider_project_id in DB. Comparison stopped.")

    return projects[0]


def fetch_db_rows_for_day(db_sess, project_id: int, target_day: date) -> Tuple[List[DbLeadRow], int]:
    null_count_stmt = select(func.count(models.ProviderLead.id)).where(
        models.ProviderLead.project_id == project_id,
        models.ProviderLead.prov_created_at.is_(None),
    )
    null_count = int(db_sess.execute(null_count_stmt).scalar_one() or 0)

    rows_stmt: Select[tuple[models.ProviderLead]] = (
        select(models.ProviderLead)
        .where(
            models.ProviderLead.project_id == project_id,
            models.ProviderLead.prov_created_at.is_not(None),
            func.date(models.ProviderLead.prov_created_at) == target_day,
        )
        .order_by(models.ProviderLead.prov_created_at.asc(), models.ProviderLead.id.asc())
    )
    db_rows = list(db_sess.execute(rows_stmt).scalars())

    normalized_rows = [
        DbLeadRow(
            id=int(row.id),
            vid=str(row.vid),
            phone=normalize_phone(row.phone) or "",
            prov_created_at=row.prov_created_at,
            day=row.prov_created_at.date().isoformat(),
        )
        for row in db_rows
        if normalize_phone(row.phone)
    ]
    return normalized_rows, null_count


def group_api_rows(rows: Iterable[ApiLeadRow]) -> DefaultDict[LeadKey, List[ApiLeadRow]]:
    grouped: DefaultDict[LeadKey, List[ApiLeadRow]] = defaultdict(list)
    for row in rows:
        grouped[LeadKey(phone=row.phone, day=row.day)].append(row)
    return grouped


def group_db_rows(rows: Iterable[DbLeadRow]) -> DefaultDict[LeadKey, List[DbLeadRow]]:
    grouped: DefaultDict[LeadKey, List[DbLeadRow]] = defaultdict(list)
    for row in rows:
        grouped[LeadKey(phone=row.phone, day=row.day)].append(row)
    return grouped


def print_header(provider_project_id: str, date_str: str, project: models.Project) -> None:
    print("")
    print("[INPUT]")
    print(f"provider_project_id={provider_project_id}")
    print(f"date={date_str}")
    print("")
    print("[PROJECT]")
    print(f"project_id={project.id}")
    print(f"name={project.name}")
    print(f"status={project.status}")


def print_summary(
    api_stats: Dict[str, int],
    api_grouped: Dict[LeadKey, List[ApiLeadRow]],
    db_grouped: Dict[LeadKey, List[DbLeadRow]],
    db_rows_without_prov_created_at: int,
    missing_keys: List[LeadKey],
) -> None:
    print("")
    print("[SUMMARY]")
    print(f"api_pages_fetched={api_stats['pages_fetched']}")
    print(f"api_raw_items={api_stats['raw_items']}")
    print(f"api_invalid_phone={api_stats['invalid_phone']}")
    print(f"api_invalid_created_at={api_stats['invalid_created_at']}")
    print(f"api_unique_keys={len(api_grouped)}")
    print(f"db_unique_keys={len(db_grouped)}")
    print(f"db_rows_without_prov_created_at_excluded={db_rows_without_prov_created_at}")
    print(f"missing_in_db={len(missing_keys)}")


def print_missing_rows(
    missing_keys: List[LeadKey],
    api_grouped: Dict[LeadKey, List[ApiLeadRow]],
) -> None:
    print("")
    print("[MISSING_IN_DB]")
    if not missing_keys:
        print("No missing leads found.")
        return

    for key in missing_keys:
        api_rows = api_grouped[key]
        created_at_values = sorted({row.created_at for row in api_rows})
        print(
            json.dumps(
                {
                    "phone": key.phone,
                    "day": key.day,
                    "api_occurrences": len(api_rows),
                    "api_created_at": created_at_values,
                },
                ensure_ascii=False,
            )
        )


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")
    provider_project_id = prompt_provider_project_id()
    date_str = prompt_date()
    target_day = datetime.strptime(date_str, "%Y-%m-%d").date()

    _, session_local = db_mod.init_engine_and_session(args.db_url)
    db_sess = session_local()

    try:
        project = get_single_project_by_provider_id(db_sess, provider_project_id)
        api_rows, api_stats = fetch_api_rows(api_url, token, provider_project_id, date_str)
        db_rows, db_rows_without_prov_created_at = fetch_db_rows_for_day(db_sess, int(project.id), target_day)

        api_grouped = group_api_rows(row for row in api_rows if row.day == date_str)
        db_grouped = group_db_rows(row for row in db_rows if row.day == date_str)
        missing_keys = sorted(
            (set(api_grouped.keys()) - set(db_grouped.keys())),
            key=lambda item: (item.day, item.phone),
        )

        print_header(provider_project_id, date_str, project)
        print_summary(
            api_stats=api_stats,
            api_grouped=api_grouped,
            db_grouped=db_grouped,
            db_rows_without_prov_created_at=db_rows_without_prov_created_at,
            missing_keys=missing_keys,
        )
        print_missing_rows(missing_keys, api_grouped)

    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        raise SystemExit(f"Provider API HTTP error: status={status_code}") from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Provider API request failed: {exc}") from exc
    except SQLAlchemyError as exc:
        raise SystemExit(f"Database error: {exc}") from exc
    finally:
        db_sess.close()


if __name__ == "__main__":
    main()
