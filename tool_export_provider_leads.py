"""
Экспорт лидов провайдера из БД в Google Sheets.

Берём только записи за последние N дней (LEADS_EXPORT_LOOKBACK_DAYS).
Заполняем по позициям A..G:
 A Created At -> prov_created_at
 B id        -> vid
 C Phone     -> первый телефон
 D Unused    -> пусто
 E Project Tag -> project_name
 F GCK Tag     -> "ГЦК " + project_name без префикса B1_/B2_/B3_/B4_
 G Check_mark  -> subdomain
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Callable, Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select

from backend.app import db, models, logging_setup
from backend.app.time_utils import now_msk


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
MIN_REQUEST_INTERVAL_SEC = 0.5
RETRY_DELAYS_SEC = [5, 15, 45]


def _get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _build_sheets_client(credentials_file: str):
    creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _strip_provider_channel(name: str) -> str:
    for prefix in ("B1_", "B2_", "B3_", "B4_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _to_gck_tag(project_name: Optional[str]) -> str:
    if not project_name:
        return ""
    base = _strip_provider_channel(project_name).strip()
    return f"ГЦК {base}" if base else ""


def _format_dt(value: Optional[datetime]) -> str:
    if not value:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _extract_first_phone(row: models.ProviderLead) -> str:
    if isinstance(row.phones_raw, list) and row.phones_raw:
        first = str(row.phones_raw[0]).strip()
        if first:
            return first
    if row.phone:
        return str(row.phone).split(",")[0].strip()
    return ""


def _get_sheet_meta(service, spreadsheet_id: str, sheet_name: str) -> tuple[int, int]:
    resp = _safe_google_call(lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute())
    for sheet in resp.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name:
            sheet_id = int(props.get("sheetId"))
            row_count = int(props.get("gridProperties", {}).get("rowCount", 0))
            return sheet_id, row_count
    raise RuntimeError(f"Sheet not found: {sheet_name}")


def _get_existing_ids(service, spreadsheet_id: str, sheet_name: str) -> set[str]:
    rng = f"{sheet_name}!B2:B"
    resp = _safe_google_call(
        lambda: service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=rng, majorDimension="ROWS"
        ).execute()
    )
    values = resp.get("values", [])
    return {str(row[0]).strip() for row in values if row and str(row[0]).strip()}


def _ensure_rows(service, spreadsheet_id: str, sheet_id: int, required_rows: int, current_rows: int) -> int:
    if required_rows <= current_rows:
        return 0
    to_add = required_rows - current_rows
    added_total = 0
    while to_add > 0:
        chunk = min(500, to_add)
        body = {
            "requests": [
                {
                    "appendDimension": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "length": chunk,
                    }
                }
            ]
        }
        _safe_google_call(
            lambda: service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        )
        added_total += chunk
        to_add -= chunk
    return added_total


def _fetch_recent_provider_leads(db_sess, days: int) -> List[models.ProviderLead]:
    threshold = now_msk() - timedelta(days=days)
    threshold = threshold.replace(tzinfo=None)
    stmt = (
        select(models.ProviderLead)
        .where(models.ProviderLead.prov_created_at.isnot(None))
        .where(models.ProviderLead.prov_created_at >= threshold)
        .order_by(models.ProviderLead.prov_created_at.asc())
    )
    return db_sess.execute(stmt).scalars().all()


_last_call_ts: Optional[float] = None


def _safe_google_call(call: Callable[[], Any]):
    global _last_call_ts
    if _last_call_ts is not None:
        elapsed = time.time() - _last_call_ts
        if elapsed < MIN_REQUEST_INTERVAL_SEC:
            time.sleep(MIN_REQUEST_INTERVAL_SEC - elapsed)

    for idx, delay in enumerate([0] + RETRY_DELAYS_SEC):
        if delay:
            time.sleep(delay)
        try:
            result = call()
            _last_call_ts = time.time()
            return result
        except HttpError as exc:
            status = getattr(exc, "status_code", None)
            if status is None and hasattr(exc, "resp"):
                status = getattr(exc.resp, "status", None)
            if status in (429,) or (isinstance(status, int) and status >= 500):
                if idx < len(RETRY_DELAYS_SEC):
                    continue
            raise

def export_provider_leads():
    log = logging.getLogger("app.export_provider")

    credentials_file = _get_env("GOOGLE_CREDENTIALS_FILE")
    sheet_id = _get_env("GOOGLE_SHEET_ID")
    sheet_name = _get_env("GOOGLE_SHEET_NAME")
    lookback_days = int(_get_env("LEADS_EXPORT_LOOKBACK_DAYS", "3"))
    database_url = _get_env("DATABASE_URL", "sqlite:///./app.db")

    if not credentials_file or not os.path.exists(credentials_file):
        log.error("GOOGLE_CREDENTIALS_FILE not found: %s", credentials_file)
        return
    if not sheet_id or not sheet_name:
        log.error("GOOGLE_SHEET_ID or GOOGLE_SHEET_NAME is empty")
        return

    service = _build_sheets_client(credentials_file)

    engine, SessionLocal = db.init_engine_and_session(database_url)
    models.Base.metadata.create_all(bind=engine)

    with SessionLocal() as s:
        rows = _fetch_recent_provider_leads(s, lookback_days)

    if not rows:
        log.info("No provider leads to export for last %d days", lookback_days)
        return

    errors_count = 0
    existing_ids = _get_existing_ids(service, sheet_id, sheet_name)
    existing_rows_count = len(existing_ids)

    out_rows: List[List[str]] = []
    skipped_count = 0
    for r in rows:
        if str(r.vid).strip() in existing_ids:
            skipped_count += 1
            continue
        out_rows.append(
            [
                _format_dt(r.prov_created_at),
                str(r.vid),
                _extract_first_phone(r),
                "",
                r.project_name or "",
                _to_gck_tag(r.project_name),
                r.subdomain or "",
            ]
        )

    if not out_rows:
        log.info(
            "Экспорт пропущен: новых строк нет. Всего кандидатов=%d, пропущено (дубликаты)=%d",
            len(rows),
            skipped_count,
        )
        return

    sheet_id_num, row_count = _get_sheet_meta(service, sheet_id, sheet_name)
    required_rows = 1 + existing_rows_count + len(out_rows)
    added_rows = _ensure_rows(service, sheet_id, sheet_id_num, required_rows, row_count)

    body = {"values": out_rows}
    try:
        _safe_google_call(
            lambda: service.spreadsheets().values().append(
                spreadsheetId=sheet_id,
                range=f"{sheet_name}!A:G",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()
        )
    except Exception:
        errors_count += 1
        log.exception("Ошибка при записи данных в Google Sheets")
        return

    log.info(
        "Экспорт завершен. Отправлено=%d, пропущено (дубликаты)=%d, ошибок=%d, добавлено строк=%d",
        len(out_rows),
        skipped_count,
        errors_count,
        added_rows,
    )


def main():
    load_dotenv()
    logging_setup.setup_logging()
    logging_setup.setup_provider_export_logger()
    logging.getLogger().setLevel(logging.INFO)
    export_provider_leads()


if __name__ == "__main__":
    main()
