"""
Экспорт Пиксель-идентификаций из БД напрямую в Google Sheets клиентов.

Берём только provider_leads.lead_source = "pixel" и только строки, где
client_sheet_exported_at IS NULL. После успешной записи проставляем
client_sheet_exported_at, чтобы повторный cron не трогал строку.

Лист выбирается по provider_leads.imported_at: "Июнь 2026", "Июль 2026" и т.д.
Если листа нет, создаём его и копируем первую строку из листа предыдущего месяца,
а если его нет — из первого листа таблицы.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import inspect, select, text, update

from backend.app import db, logging_setup, models
from backend.app.provider_lead_ids import format_provider_lead_lk_id
from backend.app.time_utils import now_msk


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
MIN_REQUEST_INTERVAL_SEC = 0.5
RETRY_DELAYS_SEC = [5, 15, 45]
SHEET_COLUMNS_COUNT = 21  # A:U
SHEET_ROW_RESERVE = 500
DROPDOWN_COLUMN_INDEX = 2  # C, zero-based Google Sheets API index
MONTH_NAMES_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}
PIXEL_QUERY_PARAMS = (
    "utm_term",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "yclid",
    "aviclid",
    "utm_referrer",
    "utm_type",
    "utm_domain",
)


@dataclass(frozen=True)
class PixelLeadExportRow:
    id: int
    phone: str
    project_name: str
    prov_created_at: Optional[datetime]
    imported_at: datetime
    pixel_url: str
    client_id: int
    pixel_table_url: str

    @property
    def lk_id(self) -> str:
        return format_provider_lead_lk_id(self.id)


def _get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _build_sheets_client(credentials_file: str):
    creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _format_dt(value: Optional[datetime]) -> str:
    if not value:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _month_title(value: datetime) -> str:
    return f"{MONTH_NAMES_RU[int(value.month)]} {int(value.year)}"


def _previous_month_title(value: datetime) -> str:
    year = int(value.year)
    month = int(value.month) - 1
    if month == 0:
        month = 12
        year -= 1
    return f"{MONTH_NAMES_RU[month]} {year}"


def _quote_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def _range(sheet_name: str, a1: str) -> str:
    return f"{_quote_sheet_name(sheet_name)}!{a1}"


def _extract_spreadsheet_id(value: str) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", raw):
        return raw
    return None


def _parse_url_params(url: str) -> Dict[str, str]:
    try:
        query = urlsplit(url or "").query
        parsed = parse_qs(query, keep_blank_values=True)
    except Exception:
        return {key: "" for key in PIXEL_QUERY_PARAMS}
    out: Dict[str, str] = {}
    for key in PIXEL_QUERY_PARAMS:
        values = parsed.get(key) or []
        out[key] = str(values[0]).strip() if values else ""
    return out


def _row_values(row: PixelLeadExportRow) -> List[str]:
    params = _parse_url_params(row.pixel_url)
    return [
        _format_dt(row.prov_created_at),  # A
        row.phone,  # B
        "",  # C
        "",  # D
        "",  # E
        params["utm_term"],  # F
        params["utm_source"],  # G
        params["utm_medium"],  # H
        params["utm_campaign"],  # I
        params["utm_content"],  # J
        params["yclid"],  # K
        params["aviclid"],  # L
        params["utm_referrer"],  # M
        params["utm_type"],  # N
        params["utm_domain"],  # O
        row.pixel_url,  # P
        "",  # Q
        row.lk_id,  # R
        row.project_name,  # S
        _format_dt(row.prov_created_at),  # T
        _format_dt(row.imported_at),  # U
    ]


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


def _spreadsheet_sheets(service, spreadsheet_id: str) -> List[dict]:
    resp = _safe_google_call(
        lambda: service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,index,gridProperties(rowCount,columnCount)))",
        )
        .execute()
    )
    return resp.get("sheets", [])


def _sheet_props_by_title(service, spreadsheet_id: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for sheet in _spreadsheet_sheets(service, spreadsheet_id):
        props = sheet.get("properties", {})
        title = str(props.get("title") or "")
        if title:
            out[title] = props
    return out


def _ensure_month_sheet(service, spreadsheet_id: str, sheet_name: str, imported_at: datetime) -> dict:
    props_by_title = _sheet_props_by_title(service, spreadsheet_id)
    if sheet_name in props_by_title:
        return props_by_title[sheet_name]

    source_title = _previous_month_title(imported_at)
    source_props = props_by_title.get(source_title)
    if source_props is None:
        sheets_sorted = sorted(props_by_title.values(), key=lambda item: int(item.get("index", 0)))
        source_props = sheets_sorted[0] if sheets_sorted else None

    body = {
        "requests": [
            {
                "addSheet": {
                    "properties": {
                        "title": sheet_name,
                        "gridProperties": {
                            "rowCount": 1 + SHEET_ROW_RESERVE,
                            "columnCount": SHEET_COLUMNS_COUNT,
                        },
                    }
                }
            }
        ]
    }
    resp = _safe_google_call(
        lambda: service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    )
    created_props = resp.get("replies", [{}])[0].get("addSheet", {}).get("properties", {})

    if source_props is not None and created_props.get("sheetId") is not None:
        copy_body = {
            "requests": [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": int(source_props["sheetId"]),
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": SHEET_COLUMNS_COUNT,
                        },
                        "destination": {
                            "sheetId": int(created_props["sheetId"]),
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": SHEET_COLUMNS_COUNT,
                        },
                        "pasteType": "PASTE_NORMAL",
                    }
                }
            ]
        }
        _safe_google_call(
            lambda: service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=copy_body).execute()
        )

    return created_props


def _get_existing_lk_ids(service, spreadsheet_id: str, sheet_name: str) -> set[str]:
    resp = _safe_google_call(
        lambda: service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=_range(sheet_name, "R2:R"), majorDimension="ROWS")
        .execute()
    )
    values = resp.get("values", [])
    return {str(row[0]).strip() for row in values if row and str(row[0]).strip()}


def _get_existing_values_count(service, spreadsheet_id: str, sheet_name: str) -> int:
    resp = _safe_google_call(
        lambda: service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=_range(sheet_name, "A:U"), majorDimension="ROWS")
        .execute()
    )
    return len(resp.get("values", []))


def _ensure_rows(service, spreadsheet_id: str, sheet_id: int, required_rows: int, current_rows: int) -> int:
    if required_rows <= current_rows:
        return 0
    to_add = required_rows - current_rows
    body = {
        "requests": [
            {
                "appendDimension": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "length": to_add,
                }
            }
        ]
    }
    _safe_google_call(
        lambda: service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    )
    return to_add


def _parse_updated_rows(updated_range: str) -> Optional[Tuple[int, int]]:
    a1_range = str(updated_range or "").rsplit("!", 1)[-1]
    rows = [int(value) for value in re.findall(r"[A-Z]+(\d+)", a1_range)]
    if not rows:
        return None
    start_row = rows[0]
    end_row = rows[-1]
    if end_row < start_row:
        start_row, end_row = end_row, start_row
    return start_row, end_row


def _copy_dropdown_validation_to_rows(
    service,
    spreadsheet_id: str,
    sheet_id: int,
    start_row: int,
    end_row: int,
) -> None:
    if start_row <= 1:
        logging.getLogger("pixel.client_sheet_export").warning(
            "Dropdown validation copy skipped: no previous row for spreadsheet_id=%s sheet_id=%s rows=%s:%s",
            spreadsheet_id,
            sheet_id,
            start_row,
            end_row,
        )
        return

    body = {
        "requests": [
            {
                "copyPaste": {
                    "source": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row - 2,
                        "endRowIndex": start_row - 1,
                        "startColumnIndex": DROPDOWN_COLUMN_INDEX,
                        "endColumnIndex": DROPDOWN_COLUMN_INDEX + 1,
                    },
                    "destination": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row,
                        "startColumnIndex": DROPDOWN_COLUMN_INDEX,
                        "endColumnIndex": DROPDOWN_COLUMN_INDEX + 1,
                    },
                    "pasteType": "PASTE_DATA_VALIDATION",
                }
            }
        ]
    }
    _safe_google_call(
        lambda: service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    )


def _ensure_db_columns(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "client_profiles" in tables:
            columns = {col.get("name") for col in inspector.get_columns("client_profiles")}
            if "pixel_table_url" not in columns:
                conn.execute(text("ALTER TABLE client_profiles ADD COLUMN pixel_table_url VARCHAR"))
        if "provider_leads" in tables:
            columns = {col.get("name") for col in inspector.get_columns("provider_leads")}
            if "client_sheet_exported_at" not in columns:
                conn.execute(text("ALTER TABLE provider_leads ADD COLUMN client_sheet_exported_at TIMESTAMP"))
            if engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_provider_leads_client_sheet_exported_at "
                        "ON provider_leads (client_sheet_exported_at)"
                    )
                )


def _fetch_pending_rows(SessionLocal, batch_limit: int) -> List[PixelLeadExportRow]:
    with SessionLocal() as session:
        stmt = (
            select(
                models.ProviderLead.id,
                models.ProviderLead.phone,
                models.ProviderLead.project_name,
                models.ProviderLead.prov_created_at,
                models.ProviderLead.imported_at,
                models.ProviderLead.pixel_url,
                models.Project.user_id,
                models.ClientProfile.pixel_table_url,
            )
            .join(models.Project, models.Project.id == models.ProviderLead.project_id)
            .outerjoin(models.ClientProfile, models.ClientProfile.user_id == models.Project.user_id)
            .where(models.ProviderLead.lead_source == "pixel")
            .where(models.ProviderLead.client_sheet_exported_at.is_(None))
            .where(models.ProviderLead.imported_at.isnot(None))
            .order_by(models.ProviderLead.imported_at.asc(), models.ProviderLead.id.asc())
            .limit(batch_limit)
        )
        rows = session.execute(stmt).all()

    out: List[PixelLeadExportRow] = []
    for row in rows:
        pixel_table_url = str(row.pixel_table_url or "").strip()
        if not pixel_table_url:
            continue
        out.append(
            PixelLeadExportRow(
                id=int(row.id),
                phone=str(row.phone or "").strip(),
                project_name=str(row.project_name or "").strip(),
                prov_created_at=row.prov_created_at,
                imported_at=row.imported_at,
                pixel_url=str(row.pixel_url or "").strip(),
                client_id=int(row.user_id),
                pixel_table_url=pixel_table_url,
            )
        )
    return out


def _count_pending_without_table(SessionLocal) -> int:
    with SessionLocal() as session:
        stmt = (
            select(models.ProviderLead.id)
            .join(models.Project, models.Project.id == models.ProviderLead.project_id)
            .outerjoin(models.ClientProfile, models.ClientProfile.user_id == models.Project.user_id)
            .where(models.ProviderLead.lead_source == "pixel")
            .where(models.ProviderLead.client_sheet_exported_at.is_(None))
            .where(models.ProviderLead.imported_at.isnot(None))
            .where((models.ClientProfile.pixel_table_url.is_(None)) | (models.ClientProfile.pixel_table_url == ""))
            .limit(1)
        )
        return 1 if session.execute(stmt).first() else 0


def _mark_exported(SessionLocal, lead_ids: Sequence[int]) -> int:
    ids = sorted({int(lead_id) for lead_id in lead_ids})
    if not ids:
        return 0
    with SessionLocal() as session:
        result = session.execute(
            update(models.ProviderLead)
            .where(models.ProviderLead.id.in_(ids))
            .where(models.ProviderLead.client_sheet_exported_at.is_(None))
            .values(client_sheet_exported_at=now_msk())
        )
        session.commit()
        return int(result.rowcount or 0)


def _group_rows(rows: Iterable[PixelLeadExportRow]) -> Dict[Tuple[str, str], List[PixelLeadExportRow]]:
    grouped: Dict[Tuple[str, str], List[PixelLeadExportRow]] = {}
    for row in rows:
        spreadsheet_id = _extract_spreadsheet_id(row.pixel_table_url)
        if not spreadsheet_id:
            continue
        sheet_name = _month_title(row.imported_at)
        grouped.setdefault((spreadsheet_id, sheet_name), []).append(row)
    return grouped


def export_pixel_leads_to_client_sheets() -> None:
    log = logging.getLogger("pixel.client_sheet_export")

    credentials_file = _get_env("GOOGLE_CREDENTIALS_FILE")
    database_url = _get_env("DATABASE_URL", "sqlite:///./app.db")
    batch_limit = int(_get_env("PIXEL_LEADS_EXPORT_BATCH_LIMIT", "1000"))

    if not credentials_file or not os.path.exists(credentials_file):
        log.error("GOOGLE_CREDENTIALS_FILE not found: %s", credentials_file)
        return

    service = _build_sheets_client(credentials_file)
    engine, SessionLocal = db.init_engine_and_session(database_url)
    models.Base.metadata.create_all(bind=engine)
    _ensure_db_columns(engine)

    rows = _fetch_pending_rows(SessionLocal, batch_limit=batch_limit)
    missing_table_exists = _count_pending_without_table(SessionLocal)
    if not rows:
        if missing_table_exists:
            log.warning("Нет строк для выгрузки: есть pending Pixel-лиды без pixel_table_url у клиента")
        else:
            log.info("Нет pending Pixel-лидов для выгрузки")
        return

    invalid_url_count = 0
    for row in rows:
        if _extract_spreadsheet_id(row.pixel_table_url) is None:
            invalid_url_count += 1
    grouped = _group_rows(rows)

    inserted_count = 0
    duplicate_count = 0
    marked_count = 0
    error_count = 0

    for (spreadsheet_id, sheet_name), group_rows in grouped.items():
        try:
            sample = group_rows[0]
            sheet_props = _ensure_month_sheet(service, spreadsheet_id, sheet_name, sample.imported_at)
            sheet_id = int(sheet_props["sheetId"])
            current_row_count = int(sheet_props.get("gridProperties", {}).get("rowCount", 0) or 0)
            existing_count = _get_existing_values_count(service, spreadsheet_id, sheet_name)
            existing_lk_ids = _get_existing_lk_ids(service, spreadsheet_id, sheet_name)

            rows_to_insert: List[PixelLeadExportRow] = []
            duplicate_ids: List[int] = []
            for row in group_rows:
                if row.lk_id in existing_lk_ids:
                    duplicate_ids.append(row.id)
                else:
                    rows_to_insert.append(row)

            if duplicate_ids:
                duplicate_count += len(duplicate_ids)
                marked_count += _mark_exported(SessionLocal, duplicate_ids)

            if not rows_to_insert:
                continue

            required_rows = max(1, existing_count) + len(rows_to_insert) + SHEET_ROW_RESERVE
            _ensure_rows(service, spreadsheet_id, sheet_id, required_rows, current_row_count)

            body = {"values": [_row_values(row) for row in rows_to_insert]}
            append_resp = _safe_google_call(
                lambda: service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=_range(sheet_name, "A:U"),
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body=body,
                )
                .execute()
            )
            updated_rows = _parse_updated_rows((append_resp or {}).get("updates", {}).get("updatedRange", ""))
            if updated_rows is None:
                start_row = max(1, existing_count) + 1
                end_row = start_row + len(rows_to_insert) - 1
            else:
                start_row, end_row = updated_rows

            try:
                _copy_dropdown_validation_to_rows(service, spreadsheet_id, sheet_id, start_row, end_row)
            except Exception:
                log.exception(
                    "Не удалось скопировать dropdown validation в колонку C: spreadsheet_id=%s sheet=%s rows=%d:%d",
                    spreadsheet_id,
                    sheet_name,
                    start_row,
                    end_row,
                )

            inserted_count += len(rows_to_insert)
            marked_count += _mark_exported(SessionLocal, [row.id for row in rows_to_insert])
        except Exception:
            error_count += len(group_rows)
            log.exception(
                "Ошибка выгрузки Pixel-лидов в Google Sheets: spreadsheet_id=%s sheet=%s rows=%d",
                spreadsheet_id,
                sheet_name,
                len(group_rows),
            )

    if invalid_url_count:
        log.warning("Pixel-лиды с некорректной ссылкой pixel_table_url пропущены: %d", invalid_url_count)

    log.info(
        "Pixel export finished. inserted=%d duplicates_marked=%d marked=%d invalid_url=%d errors=%d groups=%d",
        inserted_count,
        duplicate_count,
        marked_count,
        invalid_url_count,
        error_count,
        len(grouped),
    )


def main() -> None:
    load_dotenv()
    logging_setup.setup_logging()
    logging_setup.setup_pixel_client_sheet_export_logger()
    logging.getLogger().setLevel(logging.INFO)
    export_pixel_leads_to_client_sheets()


if __name__ == "__main__":
    main()
