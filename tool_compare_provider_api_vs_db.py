"""
Сверка лидов провайдера из API с нашей таблицей provider_leads.

Текущая версия утилиты:
- по умолчанию проверяет все релевантные проекты из таблицы projects;
- датой по умолчанию берёт текущий день в локальной TZ проекта;
- не пишет ничего ни в БД, ни в файлы;
- печатает в терминал summary по каждому проекту и общий итог;
- по желанию умеет ограничить проверку одним provider_project_id через --project-id.

примеры запуска:
    python tool_compare_provider_api_vs_db.py  (все проекты и сегодняшнюю дату)
    python tool_compare_provider_api_vs_db.py --project-id 1234567890 (только один проект)
    python tool_compare_provider_api_vs_db.py --project-id 1234567890 --date 2026-03-25 (только один проект и конкретная дата)
    python tool_compare_provider_api_vs_db.py --date 2026-03-25 (все проекты и конкретная дата)
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app import db as db_mod
from backend.app import models
from backend.app.time_utils import now_msk


API_URL_DEFAULT = "https://prostats.info/api/index.php"
DEFAULT_DB_URL = "sqlite:///./app.db"
REQUEST_TIMEOUT_SEC = 60
PAGE_SIZE_HINT = 1000
REQUEST_DELAY_SEC = 0.3
DB_PROJECTS_CHUNK_SIZE = 500


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


@dataclass
class ProjectCheckResult:
    project_id: int
    provider_project_id: str
    name: str
    status: str
    api_stats: Dict[str, int]
    api_unique_keys: int
    db_unique_keys: int
    db_rows_without_prov_created_at: int
    missing_keys: List[LeadKey]
    api_grouped: Dict[LeadKey, List[ApiLeadRow]]
    grace_until: Optional[str] = None


@dataclass
class ProjectCheckError:
    project_id: int
    provider_project_id: str
    name: str
    status: str
    error: str
    grace_until: Optional[str] = None


@dataclass
class PreloadedDbProjectData:
    rows_by_project_id: Dict[int, List[DbLeadRow]]
    null_counts_by_project_id: Dict[int, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Сверить лиды из API провайдера с provider_leads по проектам и дню"
    )
    parser.add_argument(
        "--project-id",
        dest="provider_project_id",
        help="Проверить только один provider_project_id из таблицы projects",
    )
    parser.add_argument(
        "--date",
        dest="date_str",
        default=now_msk().date().isoformat(),
        help="Дата сверки в формате YYYY-MM-DD (по умолчанию сегодня)",
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL", DEFAULT_DB_URL),
        help="DATABASE_URL для подключения (по умолчанию из env или sqlite:///./app.db)",
    )
    parser.add_argument(
        "--show-empty",
        action="store_true",
        help="Печатать также проекты без лидов и без расхождений",
    )
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return value


def parse_target_date(value: str) -> str:
    date_str = str(value or "").strip()
    if not date_str:
        raise SystemExit("Date is empty. Provide a valid date.")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("Invalid date format. Use YYYY-MM-DD.") from exc
    return date_str


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


def format_dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def chunked_ints(values: List[int], size: int = DB_PROJECTS_CHUNK_SIZE) -> Iterable[List[int]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


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


def fetch_api_rows(
    api_url: str,
    token: str,
    provider_project_id: str,
    date_str: str,
) -> Tuple[List[ApiLeadRow], Dict[str, int]]:
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
            raise RuntimeError(f"Provider API returned non-JSON response on page {page}.") from exc

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
                        "provider_leads_grace_until": format_dt(getattr(project, "provider_leads_grace_until", None)),
                    },
                    ensure_ascii=False,
                )
            )
        raise SystemExit("Ambiguous provider_project_id in DB. Comparison stopped.")

    return projects[0]


def get_projects_for_comparison(db_sess, provider_project_id: Optional[str]) -> List[models.Project]:
    if provider_project_id:
        return [get_single_project_by_provider_id(db_sess, provider_project_id)]

    now = now_msk()
    stmt: Select[tuple[models.Project]] = (
        select(models.Project)
        .where(
            models.Project.provider_project_id.is_not(None),
            func.trim(models.Project.provider_project_id) != "",
            or_(
                models.Project.status != "Удалён",
                and_(
                    models.Project.status == "Удалён",
                    models.Project.provider_leads_grace_until.is_not(None),
                    models.Project.provider_leads_grace_until >= now,
                ),
            ),
        )
        .order_by(models.Project.status.asc(), models.Project.user_id.asc(), models.Project.id.asc())
    )
    return list(db_sess.execute(stmt).scalars())


def preload_db_project_data_for_day(
    db_sess,
    project_ids: List[int],
    target_day: date,
) -> PreloadedDbProjectData:
    rows_by_project_id: Dict[int, List[DbLeadRow]] = {int(project_id): [] for project_id in project_ids}
    null_counts_by_project_id: Dict[int, int] = {int(project_id): 0 for project_id in project_ids}

    if not project_ids:
        return PreloadedDbProjectData(
            rows_by_project_id=rows_by_project_id,
            null_counts_by_project_id=null_counts_by_project_id,
        )

    for chunk in chunked_ints(project_ids):
        null_count_stmt = (
            select(models.ProviderLead.project_id, func.count(models.ProviderLead.id))
            .where(
                models.ProviderLead.project_id.in_(chunk),
                models.ProviderLead.prov_created_at.is_(None),
            )
            .group_by(models.ProviderLead.project_id)
        )
        for project_id, count_value in db_sess.execute(null_count_stmt).all():
            if project_id is None:
                continue
            null_counts_by_project_id[int(project_id)] = int(count_value or 0)

        rows_stmt: Select[tuple[models.ProviderLead]] = (
            select(models.ProviderLead)
            .where(
                models.ProviderLead.project_id.in_(chunk),
                models.ProviderLead.prov_created_at.is_not(None),
                func.date(models.ProviderLead.prov_created_at) == target_day,
            )
            .order_by(
                models.ProviderLead.project_id.asc(),
                models.ProviderLead.prov_created_at.asc(),
                models.ProviderLead.id.asc(),
            )
        )
        for row in db_sess.execute(rows_stmt).scalars():
            if row.project_id is None:
                continue
            phone = normalize_phone(row.phone)
            if not phone:
                continue
            project_id = int(row.project_id)
            rows_by_project_id.setdefault(project_id, []).append(
                DbLeadRow(
                    id=int(row.id),
                    vid=str(row.vid),
                    phone=phone,
                    prov_created_at=row.prov_created_at,
                    day=row.prov_created_at.date().isoformat(),
                )
            )

    return PreloadedDbProjectData(
        rows_by_project_id=rows_by_project_id,
        null_counts_by_project_id=null_counts_by_project_id,
    )


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


def compare_project(
    *,
    project: models.Project,
    api_url: str,
    token: str,
    date_str: str,
    db_rows: List[DbLeadRow],
    db_rows_without_prov_created_at: int,
) -> ProjectCheckResult:
    provider_project_id = str(project.provider_project_id or "").strip()
    api_rows, api_stats = fetch_api_rows(api_url, token, provider_project_id, date_str)

    api_grouped = group_api_rows(row for row in api_rows if row.day == date_str)
    db_grouped = group_db_rows(row for row in db_rows if row.day == date_str)
    missing_keys = sorted(
        (set(api_grouped.keys()) - set(db_grouped.keys())),
        key=lambda item: (item.day, item.phone),
    )

    return ProjectCheckResult(
        project_id=int(project.id),
        provider_project_id=provider_project_id,
        name=project.name,
        status=project.status,
        api_stats=api_stats,
        api_unique_keys=len(api_grouped),
        db_unique_keys=len(db_grouped),
        db_rows_without_prov_created_at=db_rows_without_prov_created_at,
        missing_keys=missing_keys,
        api_grouped=api_grouped,
        grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
    )


def print_run_header(projects: List[models.Project], date_str: str, provider_project_id: Optional[str]) -> None:
    mode = "single_project" if provider_project_id else "all_projects"
    print("")
    print("[RUN]")
    print(f"mode={mode}")
    print(f"date={date_str}")
    print(f"projects_selected={len(projects)}")
    if provider_project_id:
        print(f"provider_project_id={provider_project_id}")


def print_project_result(result: ProjectCheckResult) -> None:
    print("")
    print("[PROJECT_RESULT]")
    print(
        json.dumps(
            {
                "project_id": result.project_id,
                "provider_project_id": result.provider_project_id,
                "name": result.name,
                "status": result.status,
                "provider_leads_grace_until": result.grace_until,
                "api_pages_fetched": result.api_stats["pages_fetched"],
                "api_raw_items": result.api_stats["raw_items"],
                "api_invalid_phone": result.api_stats["invalid_phone"],
                "api_invalid_created_at": result.api_stats["invalid_created_at"],
                "api_unique_keys": result.api_unique_keys,
                "db_unique_keys": result.db_unique_keys,
                "db_rows_without_prov_created_at_excluded": result.db_rows_without_prov_created_at,
                "missing_in_db": len(result.missing_keys),
            },
            ensure_ascii=False,
        )
    )


def print_missing_rows(result: ProjectCheckResult) -> None:
    print("")
    print("[MISSING_IN_DB]")
    if not result.missing_keys:
        print("No missing leads found.")
        return

    for key in result.missing_keys:
        api_rows = result.api_grouped[key]
        created_at_values = sorted({row.created_at for row in api_rows})
        print(
            json.dumps(
                {
                    "project_id": result.project_id,
                    "provider_project_id": result.provider_project_id,
                    "name": result.name,
                    "phone": key.phone,
                    "day": key.day,
                    "api_occurrences": len(api_rows),
                    "api_created_at": created_at_values,
                },
                ensure_ascii=False,
            )
        )


def print_project_error(error: ProjectCheckError) -> None:
    print("")
    print("[PROJECT_ERROR]")
    print(
        json.dumps(
            {
                "project_id": error.project_id,
                "provider_project_id": error.provider_project_id,
                "name": error.name,
                "status": error.status,
                "provider_leads_grace_until": error.grace_until,
                "error": error.error,
            },
            ensure_ascii=False,
        )
    )


def should_print_project_result(result: ProjectCheckResult, *, show_empty: bool) -> bool:
    if show_empty:
        return True
    if result.missing_keys:
        return True
    if result.api_stats["raw_items"] > 0:
        return True
    if result.db_unique_keys > 0:
        return True
    return False


def print_overall_summary(
    *,
    date_str: str,
    projects_selected: int,
    results: List[ProjectCheckResult],
    errors: List[ProjectCheckError],
) -> None:
    print("")
    print("[OVERALL_SUMMARY]")
    print(f"date={date_str}")
    print(f"projects_selected={projects_selected}")
    print(f"projects_checked={len(results)}")
    print(f"projects_failed={len(errors)}")
    print(f"projects_with_missing={sum(1 for item in results if item.missing_keys)}")
    print(f"total_missing_in_db={sum(len(item.missing_keys) for item in results)}")
    print(f"total_api_raw_items={sum(item.api_stats['raw_items'] for item in results)}")
    print(f"total_api_unique_keys={sum(item.api_unique_keys for item in results)}")
    print(f"total_db_unique_keys={sum(item.db_unique_keys for item in results)}")
    print(
        "total_db_rows_without_prov_created_at_excluded="
        f"{sum(item.db_rows_without_prov_created_at for item in results)}"
    )


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")
    date_str = parse_target_date(args.date_str)
    target_day = datetime.strptime(date_str, "%Y-%m-%d").date()

    _, session_local = db_mod.init_engine_and_session(args.db_url)
    db_sess = session_local()

    try:
        projects = get_projects_for_comparison(db_sess, args.provider_project_id)
        if not projects:
            print("")
            print("[RUN]")
            print(f"date={date_str}")
            print("projects_selected=0")
            print("")
            print("[OVERALL_SUMMARY]")
            print(f"date={date_str}")
            print("projects_selected=0")
            print("projects_checked=0")
            print("projects_failed=0")
            print("projects_with_missing=0")
            print("total_missing_in_db=0")
            return

        print_run_header(projects, date_str, args.provider_project_id)
        db_data = preload_db_project_data_for_day(
            db_sess,
            project_ids=[int(project.id) for project in projects],
            target_day=target_day,
        )

        results: List[ProjectCheckResult] = []
        errors: List[ProjectCheckError] = []

        for project in projects:
            try:
                result = compare_project(
                    project=project,
                    api_url=api_url,
                    token=token,
                    date_str=date_str,
                    db_rows=db_data.rows_by_project_id.get(int(project.id), []),
                    db_rows_without_prov_created_at=db_data.null_counts_by_project_id.get(int(project.id), 0),
                )
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else "unknown"
                error = ProjectCheckError(
                    project_id=int(project.id),
                    provider_project_id=str(project.provider_project_id or "").strip(),
                    name=project.name,
                    status=project.status,
                    grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
                    error=f"Provider API HTTP error: status={status_code}",
                )
                errors.append(error)
                print_project_error(error)
                continue
            except requests.RequestException as exc:
                error = ProjectCheckError(
                    project_id=int(project.id),
                    provider_project_id=str(project.provider_project_id or "").strip(),
                    name=project.name,
                    status=project.status,
                    grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
                    error=f"Provider API request failed: {exc}",
                )
                errors.append(error)
                print_project_error(error)
                continue
            except RuntimeError as exc:
                error = ProjectCheckError(
                    project_id=int(project.id),
                    provider_project_id=str(project.provider_project_id or "").strip(),
                    name=project.name,
                    status=project.status,
                    grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
                    error=str(exc),
                )
                errors.append(error)
                print_project_error(error)
                continue

            results.append(result)
            if should_print_project_result(result, show_empty=bool(args.show_empty)):
                print_project_result(result)
                if result.missing_keys:
                    print_missing_rows(result)

        print_overall_summary(
            date_str=date_str,
            projects_selected=len(projects),
            results=results,
            errors=errors,
        )

    except SQLAlchemyError as exc:
        raise SystemExit(f"Database error: {exc}") from exc
    finally:
        db_sess.close()


if __name__ == "__main__":
    main()
