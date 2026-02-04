"""
Импорт лидов из Google Sheets в таблицу `leads`.

Источник: лист "Данные" со столбцами:
  A=ID, B=Дата (MSK), C=Номера (phone), D=Источник (source), E=UTM_CAMPAIGN (optional)

Окно импорта: последние N дней (LEADS_IMPORT_LOOKBACK_DAYS, по умолчанию 3).
Дедупликация: по ext_id (уникальный внешний ID из столбца A).

Запуск из cron:
  python -m backend.app.sheets_import
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from .time_utils import now_msk
from typing import Dict, Iterable, List, Tuple

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from . import db, models, logging_setup
from sqlalchemy import select


SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _parse_map(value: str) -> Dict[str, int]:
    """Парсит строку вида "spreadsheetId1:1,spreadsheetId2:2" -> dict.
    Пробелы допускаются и игнорируются.
    """
    result: Dict[str, int] = {}
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        sid, pid = part.split(":", 1)
        sid = sid.strip()
        try:
            result[sid] = int(pid.strip())
        except ValueError:
            continue
    return result


def _build_sheets_client(credentials_file: str):
    creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _parse_msk_datetime(s: str, tz_name: str) -> datetime | None:
    """Парсинг строки вида 'YYYY-MM-DD H:MM:SS' (час может быть без нуля) в ЛОКАЛЬНОЕ (MSK) время.
    Возвращает naive datetime в часовом поясе таблицы (без tzinfo),
    чтобы хранить в БД ровно то время, что в Google.
    """
    s = s.strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})$", s)
    if not m:
        return None
    date_part, hh, mm, ss = m.group(1), m.group(2), m.group(3), m.group(4)
    hh = hh.zfill(2)
    try:
        from zoneinfo import ZoneInfo

        # Создаём aware в нужной TZ, затем убираем tzinfo → получаем naive локальное
        tz = ZoneInfo(tz_name)
        dt_local = datetime.fromisoformat(f"{date_part} {hh}:{mm}:{ss}").replace(tzinfo=tz)
        return dt_local.replace(tzinfo=None)
    except Exception:
        return None


def _fetch_rows(service, spreadsheet_id: str, sheet_name: str) -> List[List[str]]:
    # Берём со второй строки, чтобы пропустить заголовок
    rng = f"{sheet_name}!A2:E"
    resp = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=rng, majorDimension="ROWS").execute()
    values = resp.get("values", [])
    return values


def _filter_recent(rows: Iterable[List[str]], tz_name: str, days: int) -> List[Tuple[int, datetime, str, str | None, str | None]]:
    """Оставляем строки за последние `days` дней.
    Возвращаем кортежи: (ext_id, created_at_utc, phone, source, utm)
    """
    from zoneinfo import ZoneInfo
    now_local = datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    threshold_local = now_local - timedelta(days=days)
    out: List[Tuple[int, datetime, str, str | None, str | None]] = []
    for row in rows:
        # Ожидаем как минимум A, B, C
        if len(row) < 3:
            continue
        ext_id_str = str(row[0]).strip()
        dt_str = str(row[1]).strip()
        phone = str(row[2]).strip()
        source = (str(row[3]).strip() if len(row) >= 4 and str(row[3]).strip() != "" else None)
        utm = (str(row[4]).strip() if len(row) >= 5 and str(row[4]).strip() != "" else None)

        if not ext_id_str or not dt_str or not phone:
            continue
        try:
            ext_id = int(ext_id_str)
        except ValueError:
            continue
        dt_local = _parse_msk_datetime(dt_str, tz_name)
        if not dt_local:
            continue
        if dt_local < threshold_local:
            continue
        out.append((ext_id, dt_local, phone, source, utm))
    return out


def _load_existing_ext_ids(db_sess, ext_ids: List[int]) -> set[int]:
    if not ext_ids:
        return set()
    rows = db_sess.execute(select(models.Lead.ext_id).where(models.Lead.ext_id.in_(ext_ids))).all()
    return {int(r[0]) for r in rows}


def _load_project_map(db_sess) -> Dict[tuple[int, str | None], int]:
    """
    Читает таблицу project_id_map: (external_id, source) -> project_id.
    source может быть NULL; в словаре используется ключ (external_id, None).
    """
    mapping: Dict[tuple[int, str | None], int] = {}
    try:
        rows = db_sess.execute(
            select(
                models.ProjectIdMap.external_id,
                models.ProjectIdMap.source,
                models.ProjectIdMap.project_id,
            )
        ).all()
        for external_id, source, project_id in rows:
            src = source if source is not None else None
            mapping[(int(external_id), src)] = int(project_id)
    except Exception:
        # Если таблицы нет/не совпадает схема — вернём пустой маппинг
        return {}
    return mapping


def import_all():
    log = logging.getLogger("app.import")

    credentials_file = _get_env("GOOGLE_CREDENTIALS_FILE")
    tz_name = _get_env("SHEETS_TZ", "Europe/Moscow")
    sheet_name = _get_env("SHEETS_SHEET_NAMES", "Данные").split(",")[0].strip() or "Данные"
    lookback_days = int(_get_env("LEADS_IMPORT_LOOKBACK_DAYS", "3"))
    mapping = _parse_map(_get_env("SHEETS_MAP", ""))
    fallback_project_id = int(_get_env("UNMAPPED_PROJECT_ID", "0") or "0") or None

    if not credentials_file or not os.path.exists(credentials_file):
        log.error("GOOGLE_CREDENTIALS_FILE not found: %s", credentials_file)
        return
    if not mapping:
        log.error("SHEETS_MAP is empty")
        return

    service = _build_sheets_client(credentials_file)

    engine, SessionLocal = db.init_engine_and_session(os.getenv("DATABASE_URL", "sqlite:///./app.db"))
    models.Base.metadata.create_all(bind=engine)

    total_created = 0
    total_skipped_unmapped = 0
    total_fallback = 0
    with SessionLocal() as s:
        project_map = _load_project_map(s)
        for spreadsheet_id, project_id in mapping.items():
            try:
                rows = _fetch_rows(service, spreadsheet_id, sheet_name)
                filtered = _filter_recent(rows, tz_name, lookback_days)
                ext_ids = [e for (e, _, _, _, _) in filtered]
                existing = _load_existing_ext_ids(s, ext_ids)
                to_insert_raw = [t for t in filtered if t[0] not in existing]

                resolved: List[models.Lead] = []
                # Время импорта фиксируем в MSK (aware)
                imported_at_local = now_msk()

                for ext_id, created_at_utc, phone, source, utm in to_insert_raw:
                    # Ищем внутренний project_id по маппингу (external_id из env mapping)
                    internal_pid = project_map.get((project_id, source)) or project_map.get((project_id, None))
                    if internal_pid is None:
                        if fallback_project_id:
                            internal_pid = fallback_project_id
                            total_fallback += 1
                            log.info(
                                "Fallback lead ext_id=%s: external project_id=%s, source=%s -> fallback project_id=%s",
                                ext_id,
                                project_id,
                                source,
                                fallback_project_id,
                            )
                        else:
                            total_skipped_unmapped += 1
                            log.warning(
                                "Skip lead ext_id=%s: no mapping for external project_id=%s, source=%s",
                                ext_id,
                                project_id,
                                source,
                            )
                            continue
                    lead = models.Lead(
                        ext_id=ext_id,
                        project_id=internal_pid,
                        external_project_id=project_id,
                        created_at=created_at_utc,
                        phone=phone,
                        source=source,
                        utm_campaign=utm,
                        spreadsheet_id=spreadsheet_id,
                        sheet_name=sheet_name,
                        imported_at=imported_at_local,
                    )
                    resolved.append(lead)

                s.add_all(resolved)
                s.commit()
                total_created += len(resolved)
                log.info(
                    "Imported %d new leads (sheet=%s, external_project=%d)",
                    len(resolved),
                    spreadsheet_id,
                    project_id,
                )
            except Exception:
                # Глотаем и продолжаем, логируем ошибку
                log.exception("Import failed for sheet %s (project %d)", spreadsheet_id, project_id)

    log.info(
        "Total new leads: %d (fallback used: %d, skipped unmapped: %d)",
        total_created,
        total_fallback,
        total_skipped_unmapped,
    )


def main():
    # Подхватить .env и настроить файл логов
    load_dotenv()
    logging_setup.setup_logging()
    logging.getLogger().setLevel(logging.INFO)
    import_all()


if __name__ == "__main__":
    main()


