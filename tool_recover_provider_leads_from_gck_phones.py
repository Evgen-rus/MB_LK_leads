"""
Дозагрузка потерянных лидов провайдера из API gck_phones в provider_leads.

Логика специально упрощена и повторяет семантику tool_compare_provider_api_vs_db.py:
- сравнение выполняется по уникальному телефону в рамках project + day;
- если один и тот же телефон встречался у провайдера несколько раз за день,
  это считается одним событием;
- по умолчанию скрипт сразу пишет новые строки в БД;
- для проверки без записи есть --dry-run.

Примеры запуска:
    python tool_recover_provider_leads_from_gck_phones.py
    python tool_recover_provider_leads_from_gck_phones.py --dry-run
    python tool_recover_provider_leads_from_gck_phones.py --project-id 10951952
    python tool_recover_provider_leads_from_gck_phones.py --project-id 10951952 --date 2026-03-25
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
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.app import db as db_mod
from backend.app import models
from backend.app.time_utils import now_msk


API_URL_DEFAULT = "https://prostats.info/api/index.php"
DEFAULT_DB_URL = "sqlite:///./app.db"
REQUEST_TIMEOUT_SEC = 60
PAGE_SIZE_HINT = 1000
REQUEST_DELAY_SEC = 0.3
DB_PROJECTS_CHUNK_SIZE = 500
SETTLE_SECONDS = 15
RECOVERY_VID_TOTAL_LEN = 10
RECOVERY_VID_SEQ_LEN = 5


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
class RecoveryCandidate:
    project_id: int
    provider_project_id: str
    project_name: str
    phone: str
    day: str
    prov_created_at: Optional[datetime]
    api_created_at_values: List[str]
    synthetic_vid: Optional[str] = None


@dataclass
class ProjectRecoveryResult:
    project_id: int
    provider_project_id: str
    name: str
    status: str
    api_stats: Dict[str, int]
    api_time_ms: int
    api_unique_keys: int
    db_unique_keys: int
    missing_keys: List[LeadKey]
    recovery_candidates: List[RecoveryCandidate]
    inserted_rows: int
    skipped_after_recheck: int
    grace_until: Optional[str] = None


@dataclass
class ProjectRecoveryError:
    project_id: int
    provider_project_id: str
    name: str
    status: str
    error: str
    grace_until: Optional[str] = None


@dataclass
class PreloadedDbProjectData:
    rows_by_project_id: Dict[int, List[DbLeadRow]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Дозагрузить потерянные лиды из API провайдера в provider_leads по project + phone + day"
    )
    parser.add_argument(
        "--project-id",
        dest="provider_project_id",
        help="Обработать только один provider_project_id из таблицы projects",
    )
    parser.add_argument(
        "--date",
        dest="date_str",
        default=now_msk().date().isoformat(),
        help="Дата в формате YYYY-MM-DD (по умолчанию сегодня)",
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL", DEFAULT_DB_URL),
        help="DATABASE_URL для подключения (по умолчанию из env или sqlite:///./app.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Проверка без записи в БД",
    )
    parser.add_argument(
        "--show-empty",
        action="store_true",
        help="Печатать также проекты без найденных missing-лидов",
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


def parse_page_parts(page: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not page:
        return None, None
    parts = page.split("_")
    prov_chanel = parts[0].strip() if parts else None
    prov_source = None
    if len(parts) >= 3:
        prov_source = "_".join(parts[2:]).strip() or None
    return prov_chanel, prov_source


def build_recovery_vid(day: str, seq_num: int) -> str:
    day_digits = "".join(ch for ch in str(day) if ch.isdigit())
    if len(day_digits) != 8:
        raise ValueError(f"Invalid recovery day for vid generation: {day!r}")
    if seq_num < 0:
        raise ValueError(f"Invalid recovery sequence value: {seq_num}")
    seq_str = str(seq_num).zfill(RECOVERY_VID_SEQ_LEN)
    if len(seq_str) > RECOVERY_VID_SEQ_LEN:
        raise ValueError(
            f"Recovery vid sequence overflow for day={day_digits}. "
            f"Maximum per day is {10 ** RECOVERY_VID_SEQ_LEN - 1}."
        )
    vid = f"{day_digits[-1]}{day_digits[4:]}{seq_str}"
    if len(vid) != RECOVERY_VID_TOTAL_LEN:
        raise ValueError(f"Unexpected recovery vid length: {vid}")
    return vid


def get_recovery_vid_prefix(day: str) -> str:
    day_digits = "".join(ch for ch in str(day) if ch.isdigit())
    if len(day_digits) != 8:
        raise ValueError(f"Invalid recovery day prefix: {day!r}")
    return f"{day_digits[-1]}{day_digits[4:]}"


def get_max_recovery_seq_for_day(db_sess, day: str) -> int:
    prefix = get_recovery_vid_prefix(day)
    rows = db_sess.execute(
        select(models.ProviderLead.vid).where(models.ProviderLead.vid.like(f"{prefix}%"))
    ).all()

    max_seq = 0
    for (vid_value,) in rows:
        vid = str(vid_value or "").strip()
        if len(vid) != RECOVERY_VID_TOTAL_LEN:
            continue
        if not vid.isdigit():
            continue
        if not vid.startswith(prefix):
            continue
        try:
            seq_num = int(vid[len(prefix):])
        except ValueError:
            continue
        if seq_num > max_seq:
            max_seq = seq_num
    return max_seq


def assign_recovery_vids(candidates: List[RecoveryCandidate], *, next_seq: int) -> int:
    current_seq = next_seq
    for candidate in candidates:
        current_seq += 1
        candidate.synthetic_vid = build_recovery_vid(candidate.day, current_seq)
    return current_seq


def fetch_gck_phones_page(
    session: requests.Session,
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
    response = session.post(api_url, json=payload, timeout=REQUEST_TIMEOUT_SEC)
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
    session: requests.Session,
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
        response = fetch_gck_phones_page(session, api_url, token, provider_project_id, date_str, page)
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
        raise SystemExit("Ambiguous provider_project_id in DB. Recovery stopped.")

    return projects[0]


def get_projects_for_recovery(db_sess, provider_project_id: Optional[str]) -> List[models.Project]:
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

    if not project_ids:
        return PreloadedDbProjectData(rows_by_project_id=rows_by_project_id)

    for chunk in chunked_ints(project_ids):
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
            if not phone or row.prov_created_at is None:
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

    return PreloadedDbProjectData(rows_by_project_id=rows_by_project_id)


def load_db_project_rows_for_day(
    db_sess,
    *,
    project_id: int,
    target_day: date,
) -> List[DbLeadRow]:
    rows: List[DbLeadRow] = []
    rows_stmt: Select[tuple[models.ProviderLead]] = (
        select(models.ProviderLead)
        .where(
            models.ProviderLead.project_id == project_id,
            models.ProviderLead.prov_created_at.is_not(None),
            func.date(models.ProviderLead.prov_created_at) == target_day,
        )
        .order_by(
            models.ProviderLead.prov_created_at.asc(),
            models.ProviderLead.id.asc(),
        )
    )
    for row in db_sess.execute(rows_stmt).scalars():
        if row.project_id is None or row.prov_created_at is None:
            continue
        phone = normalize_phone(row.phone)
        if not phone:
            continue
        rows.append(
            DbLeadRow(
                id=int(row.id),
                vid=str(row.vid),
                phone=phone,
                prov_created_at=row.prov_created_at,
                day=row.prov_created_at.date().isoformat(),
            )
        )
    return rows


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


def build_recovery_candidates(
    *,
    project: models.Project,
    missing_keys: List[LeadKey],
    api_grouped: Dict[LeadKey, List[ApiLeadRow]],
) -> List[RecoveryCandidate]:
    candidates: List[RecoveryCandidate] = []
    for key in missing_keys:
        api_rows = api_grouped[key]
        created_at_values = sorted({row.created_at for row in api_rows})
        first_created_at = parse_api_created_at(created_at_values[0]) if created_at_values else None
        candidates.append(
            RecoveryCandidate(
                project_id=int(project.id),
                provider_project_id=str(project.provider_project_id or "").strip(),
                project_name=str(project.name or ""),
                phone=key.phone,
                day=key.day,
                prov_created_at=first_created_at,
                api_created_at_values=created_at_values,
            )
        )
    return candidates


def compare_project_for_recovery(
    *,
    session: requests.Session,
    project: models.Project,
    api_url: str,
    token: str,
    date_str: str,
    db_rows: List[DbLeadRow],
) -> ProjectRecoveryResult:
    provider_project_id = str(project.provider_project_id or "").strip()
    api_started_at = time.perf_counter()
    api_rows, api_stats = fetch_api_rows(session, api_url, token, provider_project_id, date_str)
    api_time_ms = int((time.perf_counter() - api_started_at) * 1000)

    api_grouped = group_api_rows(row for row in api_rows if row.day == date_str)
    db_grouped = group_db_rows(row for row in db_rows if row.day == date_str)
    missing_keys = sorted(
        (set(api_grouped.keys()) - set(db_grouped.keys())),
        key=lambda item: (item.day, item.phone),
    )
    recovery_candidates = build_recovery_candidates(
        project=project,
        missing_keys=missing_keys,
        api_grouped=api_grouped,
    )

    return ProjectRecoveryResult(
        project_id=int(project.id),
        provider_project_id=provider_project_id,
        name=str(project.name or ""),
        status=str(project.status or ""),
        api_stats=api_stats,
        api_time_ms=api_time_ms,
        api_unique_keys=len(api_grouped),
        db_unique_keys=len(db_grouped),
        missing_keys=missing_keys,
        recovery_candidates=recovery_candidates,
        inserted_rows=0,
        skipped_after_recheck=0,
        grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
    )


def filter_candidates_after_recheck(
    *,
    candidates: List[RecoveryCandidate],
    db_rows: List[DbLeadRow],
    date_str: str,
) -> Tuple[List[RecoveryCandidate], int]:
    if not candidates:
        return [], 0

    db_grouped = group_db_rows(row for row in db_rows if row.day == date_str)
    still_missing: List[RecoveryCandidate] = []
    skipped = 0

    for candidate in candidates:
        key = LeadKey(phone=candidate.phone, day=candidate.day)
        if key in db_grouped:
            skipped += 1
            continue
        still_missing.append(candidate)

    return still_missing, skipped


def insert_recovery_candidates(db_sess, candidates: List[RecoveryCandidate]) -> int:
    if not candidates:
        return 0

    to_insert: List[models.ProviderLead] = []
    for item in candidates:
        prov_chanel, prov_source = parse_page_parts(item.project_name)
        if not item.synthetic_vid:
            raise ValueError(
                f"Recovery candidate has no synthetic_vid: project_id={item.project_id} phone={item.phone} day={item.day}"
            )
        to_insert.append(
            models.ProviderLead(
                vid=item.synthetic_vid,
                phone=item.phone,
                phones_raw=[item.phone],
                project_name=item.project_name,
                prov_created_at=item.prov_created_at,
                prov_chanel=prov_chanel,
                prov_source=prov_source,
                subdomain=None,
                project_id=item.project_id,
            )
        )

    db_sess.add_all(to_insert)
    db_sess.commit()
    return len(to_insert)


def print_run_header(
    projects: List[models.Project],
    date_str: str,
    provider_project_id: Optional[str],
    dry_run: bool,
) -> None:
    mode = "single_project" if provider_project_id else "all_projects"
    print("")
    print("[RUN]")
    print(f"mode={mode}")
    print(f"date={date_str}")
    print(f"projects_selected={len(projects)}")
    print(f"dry_run={dry_run}")
    if provider_project_id:
        print(f"provider_project_id={provider_project_id}")


def print_project_result(result: ProjectRecoveryResult) -> None:
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
                "api_time_ms": result.api_time_ms,
                "api_pages_fetched": result.api_stats["pages_fetched"],
                "api_raw_items": result.api_stats["raw_items"],
                "api_invalid_phone": result.api_stats["invalid_phone"],
                "api_invalid_created_at": result.api_stats["invalid_created_at"],
                "api_unique_keys": result.api_unique_keys,
                "db_unique_keys": result.db_unique_keys,
                "missing_in_db": len(result.missing_keys),
                "inserted_rows": result.inserted_rows,
                "skipped_after_recheck": result.skipped_after_recheck,
            },
            ensure_ascii=False,
        )
    )


def print_recovery_rows(result: ProjectRecoveryResult) -> None:
    print("")
    print("[RECOVERY_ROWS]")
    if not result.recovery_candidates:
        print("No recovery rows.")
        return

    for item in result.recovery_candidates:
        print(
            json.dumps(
                {
                    "project_id": item.project_id,
                    "provider_project_id": item.provider_project_id,
                    "project_name": item.project_name,
                    "phone": item.phone,
                    "day": item.day,
                    "api_created_at": item.api_created_at_values,
                    "synthetic_vid": item.synthetic_vid,
                },
                ensure_ascii=False,
            )
        )


def print_project_error(error: ProjectRecoveryError) -> None:
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


def should_print_project_result(result: ProjectRecoveryResult, *, show_empty: bool) -> bool:
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
    results: List[ProjectRecoveryResult],
    errors: List[ProjectRecoveryError],
    dry_run: bool,
    elapsed_ms: int,
) -> None:
    print("")
    print("[OVERALL_SUMMARY]")
    print(f"date={date_str}")
    print(f"projects_selected={projects_selected}")
    print(f"projects_checked={len(results)}")
    print(f"projects_failed={len(errors)}")
    print(f"projects_with_api_data={sum(1 for item in results if item.api_stats['raw_items'] > 0)}")
    print(f"projects_with_missing={sum(1 for item in results if item.missing_keys)}")
    print(f"total_missing_in_db={sum(len(item.missing_keys) for item in results)}")
    print(f"total_inserted_rows={sum(item.inserted_rows for item in results)}")
    print(f"total_skipped_after_recheck={sum(item.skipped_after_recheck for item in results)}")
    print(f"total_api_raw_items={sum(item.api_stats['raw_items'] for item in results)}")
    print(f"total_api_unique_keys={sum(item.api_unique_keys for item in results)}")
    print(f"api_total_ms={sum(item.api_time_ms for item in results)}")
    print(f"total_db_unique_keys={sum(item.db_unique_keys for item in results)}")
    print(f"dry_run={dry_run}")
    print(f"elapsed_ms={elapsed_ms}")


def main() -> None:
    load_dotenv()
    args = parse_args()
    started_at = time.perf_counter()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")
    date_str = parse_target_date(args.date_str)
    target_day = datetime.strptime(date_str, "%Y-%m-%d").date()

    _, session_local = db_mod.init_engine_and_session(args.db_url)

    with session_local() as read_sess:
        projects = get_projects_for_recovery(read_sess, args.provider_project_id)
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
            print("total_inserted_rows=0")
            print(f"dry_run={args.dry_run}")
            print(f"elapsed_ms={int((time.perf_counter() - started_at) * 1000)}")
            return

        db_data = preload_db_project_data_for_day(
            read_sess,
            project_ids=[int(project.id) for project in projects],
            target_day=target_day,
        )
        current_recovery_seq = get_max_recovery_seq_for_day(read_sess, date_str)

    print_run_header(projects, date_str, args.provider_project_id, bool(args.dry_run))

    results: List[ProjectRecoveryResult] = []
    errors: List[ProjectRecoveryError] = []
    needs_settle_pause = False

    with requests.Session() as http_session:
        for project in projects:
            try:
                result = compare_project_for_recovery(
                    session=http_session,
                    project=project,
                    api_url=api_url,
                    token=token,
                    date_str=date_str,
                    db_rows=db_data.rows_by_project_id.get(int(project.id), []),
                )
                if result.recovery_candidates:
                    current_recovery_seq = assign_recovery_vids(
                        result.recovery_candidates,
                        next_seq=current_recovery_seq,
                    )
                    if not args.dry_run:
                        needs_settle_pause = True

                results.append(result)

            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else "unknown"
                error = ProjectRecoveryError(
                    project_id=int(project.id),
                    provider_project_id=str(project.provider_project_id or "").strip(),
                    name=str(project.name or ""),
                    status=str(project.status or ""),
                    grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
                    error=f"Provider API HTTP error: status={status_code}",
                )
                errors.append(error)
                print_project_error(error)
            except requests.RequestException as exc:
                error = ProjectRecoveryError(
                    project_id=int(project.id),
                    provider_project_id=str(project.provider_project_id or "").strip(),
                    name=str(project.name or ""),
                    status=str(project.status or ""),
                    grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
                    error=f"Provider API request failed: {exc}",
                )
                errors.append(error)
                print_project_error(error)
            except IntegrityError as exc:
                error = ProjectRecoveryError(
                    project_id=int(project.id),
                    provider_project_id=str(project.provider_project_id or "").strip(),
                    name=str(project.name or ""),
                    status=str(project.status or ""),
                    grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
                    error=f"DB integrity error: {exc}",
                )
                errors.append(error)
                print_project_error(error)
            except RuntimeError as exc:
                error = ProjectRecoveryError(
                    project_id=int(project.id),
                    provider_project_id=str(project.provider_project_id or "").strip(),
                    name=str(project.name or ""),
                    status=str(project.status or ""),
                    grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
                    error=str(exc),
                )
                errors.append(error)
                print_project_error(error)
            except SQLAlchemyError as exc:
                error = ProjectRecoveryError(
                    project_id=int(project.id),
                    provider_project_id=str(project.provider_project_id or "").strip(),
                    name=str(project.name or ""),
                    status=str(project.status or ""),
                    grace_until=format_dt(getattr(project, "provider_leads_grace_until", None)),
                    error=f"Database error: {exc}",
                )
                errors.append(error)
                print_project_error(error)

    if needs_settle_pause and SETTLE_SECONDS > 0:
        print("")
        print("[SETTLE]")
        print(f"sleep_seconds={SETTLE_SECONDS}")
        time.sleep(SETTLE_SECONDS)

    if not args.dry_run:
        for result in results:
            if not result.recovery_candidates:
                continue
            with session_local() as write_sess:
                try:
                    fresh_db_rows = load_db_project_rows_for_day(
                        write_sess,
                        project_id=result.project_id,
                        target_day=target_day,
                    )
                    fresh_candidates, skipped_after_recheck = filter_candidates_after_recheck(
                        candidates=result.recovery_candidates,
                        db_rows=fresh_db_rows,
                        date_str=date_str,
                    )
                    result.skipped_after_recheck = skipped_after_recheck
                    result.recovery_candidates = fresh_candidates
                    if result.recovery_candidates:
                        result.inserted_rows = insert_recovery_candidates(write_sess, result.recovery_candidates)
                except Exception:
                    write_sess.rollback()
                    raise

    for result in results:
        if should_print_project_result(result, show_empty=bool(args.show_empty)):
            print_project_result(result)
            if result.recovery_candidates:
                print_recovery_rows(result)

    print_overall_summary(
        date_str=date_str,
        projects_selected=len(projects),
        results=results,
        errors=errors,
        dry_run=bool(args.dry_run),
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
    )


if __name__ == "__main__":
    main()
