"""
Экспорт лидов провайдера из БД в Google Sheets.

Очередь выгрузки хранится в БД, а не вычисляется через lookback:
 ``provider_leads.provider_sheet_exported_at IS NULL``  — строка ещё не выгружена,
 datetime — успешно выгружена. Поэтому лиды, не попавшие в промежуточную
 таблицу сегодня (например, из-за дневного лимита группы), не теряются
 и уходят в следующие дни по FIFO: ``imported_at ASC, id ASC``.

Граница «начало очереди» — скользящее окно в ``LEADS_EXPORT_LOOKBACK_DAYS``
 (по умолчанию 5) дней, считаное по ``imported_at``.  Окно намеренно
 оставлено как ограничитель: чистка Google-таблицы удаляет ``vid``, и любая
 сверка по нему после чистки вернула бы в таблицу десятки тысяч уже
 распределённых строк.  Одноразовый backfill закрывает историю до деплоя.

Дневной лимит группы проектов применяется только к project_id, реально
состоящим в ``project_daily_export_limit_group_projects``.  Ни LR-код,
ни regex имени проекта, ни ``client_internal_prefix`` в рантайме не читаются.

Заполняем по позициям A..H:
 A Created At   -> prov_created_at
 B id           -> vid
 C Phone        -> первый телефон
 D Unused       -> пусто
 E Project Tag  -> project_name (+ "_first_subdomain" только для B4_, если subdomain есть)
 F GCK Tag      -> "ГЦК " + project_name без префикса B1_/B2_/B3_/B4_
                   (+ "_first_subdomain" только для B4_, если subdomain есть)
 G Check_mark   -> subdomain
 H lk id        -> 30100000 + id
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Callable, Any

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select, text, update

from backend.app import daily_export_limit_groups as limit_groups
from backend.app import db, models, logging_setup
from backend.app.provider_lead_ids import format_provider_lead_lk_id
from backend.app.time_utils import now_msk, now_msk_naive


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
MIN_REQUEST_INTERVAL_SEC = 0.5
RETRY_DELAYS_SEC = [5, 15, 45]

# Максимум строк за один запуск, чтобы не упираться в лимиты Google Sheets API
# и в длинные транзакции.  Квота групп режется ДО похода в Google, поэтому
# превысить лимит даже на одну строку этот ограничитель не может.
MAX_ROWS_PER_RUN = int(os.getenv("LEADS_EXPORT_MAX_ROWS_PER_RUN", "5000") or "5000")


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


def _is_b4_project_name(project_name: Optional[str]) -> bool:
    return bool(project_name and project_name.startswith("B4_"))


def _build_project_tag(project_name: Optional[str], subdomain: Optional[str]) -> str:
    base = (project_name or "").strip()
    if not base:
        return ""
    if _is_b4_project_name(project_name):
        return _append_subdomain_suffix(project_name, subdomain)
    return base


def _build_gck_tag(project_name: Optional[str], subdomain: Optional[str]) -> str:
    gck_base = _to_gck_tag(project_name)
    if not gck_base:
        return ""
    if _is_b4_project_name(project_name):
        return _append_subdomain_suffix(gck_base, subdomain)
    return gck_base


def _append_subdomain_suffix(value: Optional[str], subdomain: Optional[str]) -> str:
    base = (value or "").strip()
    raw_suffix = (subdomain or "").strip()
    suffix = raw_suffix.split(";", 1)[0].strip() if raw_suffix else ""
    if not base:
        return ""
    if not suffix:
        return base
    return f"{base}_{suffix}"


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


def _ensure_provider_export_state(engine, lookback_days: int) -> None:
    """
    Добавляет колонку ``provider_sheet_exported_at``, создаёт таблицы групп
    и один раз заполняет колонку для истории (см. _backfill_provider_export_state).

    Backfill НЕ сверяется с Google Sheet намеренно: чистка таблицы удаляет
    ``vid``, и сверка по нему вернула бы в таблицу десятки тысяч уже
    распределённых строк.  Вместо этого история закрывается по окну.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "provider_leads" in tables:
            columns = {col.get("name") for col in inspector.get_columns("provider_leads")}
            if "provider_sheet_exported_at" not in columns:
                conn.execute(
                    text("ALTER TABLE provider_leads ADD COLUMN provider_sheet_exported_at TIMESTAMP")
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_provider_leads_provider_sheet_exported_at "
                    "ON provider_leads (provider_sheet_exported_at)"
                )
            )

        models.ProjectDailyExportLimitGroup.__table__.create(bind=engine, checkfirst=True)
        models.ProjectDailyExportLimitGroupProject.__table__.create(bind=engine, checkfirst=True)

        _backfill_provider_export_state(conn, lookback_days=lookback_days)


def _backfill_provider_export_state(conn, *, lookback_days: int) -> int:
    """
    Одноразовый backfill: помечает выгруженными ВСЁ, что было в БД до деплоя,
    не глядя в Google.

    Логика безопасная именно потому, что НЕ сверяется с таблицей.  Сверка
    по ``vid`` здесь была бы вредной: чистка таблицы удаляет ``vid``, и
    отсутствие строки перестаёт означать «не выгружено» — сверка вернула бы
    в таблицу десятки тысяч уже распределённых строк.

    Почему помечается ВСЁ, а не только то, что вне окна:

    - строки СТАРШЕ окна в таблицу никогда не попадали — их надо закрыть,
      иначе они всплывут и зальют таблицу;
    - строки ВНУТРИ окна уже были отправлены старой выгрузкой (окно в
      LEADS_EXPORT_LOOKBACK_DAYS было её единственным фильтром).  Если их не
      закрыть, они вернутся в очередь и продублируются.

    Открытыми остаются только строки, пришедшие ПОСЛЕ деплоя: у них
    ``imported_at`` новее момента backfill, и до этого запуска их в БД не было.

    Идемпотентно: трогаем только NULL и помечаем служебной строкой-меткой,
    поэтому повторный запуск ничего не меняет.
    """
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS provider_export_backfill_state ("
            "name VARCHAR PRIMARY KEY, applied_at TIMESTAMP, queue_started_at TIMESTAMP)"
        )
    )
    already = conn.execute(
        text("SELECT name FROM provider_export_backfill_state WHERE name = :name"),
        {"name": "provider_sheet_exported_at_v1"},
    ).first()
    if already is not None:
        return 0

    # Всё, что было в БД на момент деплоя: imported_at строго раньше метки.
    # applied_at — момент выполнения backfill, он же граница «до деплоя».
    deployed_at = now_msk_naive()
    window_days = max(int(lookback_days), 1)
    queue_started_at = deployed_at - timedelta(days=window_days)

    marked = conn.execute(
        text(
            "UPDATE provider_leads "
            "SET provider_sheet_exported_at = imported_at "
            "WHERE lead_source = 'provider' "
            "AND provider_sheet_exported_at IS NULL "
            "AND imported_at < :deployed_at"
        ),
        {"deployed_at": deployed_at},
    ).rowcount or 0

    conn.execute(
        text(
            "INSERT INTO provider_export_backfill_state (name, applied_at, queue_started_at) "
            "VALUES (:name, :applied_at, :queue_started_at) "
            "ON CONFLICT(name) DO NOTHING"
        ),
        {"name": "provider_sheet_exported_at_v1", "applied_at": deployed_at, "queue_started_at": queue_started_at},
    )

    logging.getLogger("provider.export").info(
        "Backfill provider_sheet_exported_at выполнен: помечено строк=%d, "
        "очередь ограничена окном в %d дней",
        int(marked),
        window_days,
    )
    return int(marked)


def _load_queue_started_at(engine) -> datetime:
    """
    Базовая граница — скользящее окно в ``LEADS_EXPORT_LOOKBACK_DAYS`` (по
    умолчанию 5) дней, считаное по ``imported_at``.  Скользящее, а не
    постоянное: поэтому лид, переживший вчерашний лимит, автоматически
    попадает в очередь сегодня — это и есть перенос остатка на следующие дни.

    Если в базе есть невыгруженные строки СТАРШЕ этого окна (например,
    группа переполнялась дольше N дней), окно автоматически расширяется до
    самой старой такой строки.  В стационарном режиме это ничего не меняет:
    backfill закрывает всю историю, поэтому старых невыгруженных строк
    не бывает.  Но при реальном переполнении лиды не отсекаются навсегда,
    а дорабатываются — то есть лимит ограничивает поток, но не обрезает
    историю.

    Зачем вообще окно.  Единственный носитель знания «эта строка уже была
    в таблице» — колонка ``provider_sheet_exported_at`` в БД, а дедуп по
    ``vid`` смотрит в саму Google-таблицу.  Как только вы чистите таблицу,
    эти два источника расходятся: отсутствие ``vid`` в колонке B перестаёт
    означать «не выгружено».  Сверка по ``vid`` в такой момент вернула бы
    в таблицу десятки тысяч уже распределённых строк.  Окно — простой и
    предсказуемый ограничитель против этого.

    Расширение окна не ослабляет защиту от повторов: история закрыта
    backfill'ом (см. _backfill_provider_export_state) и в очередь не
    попадает никогда.
    """
    lookback_days = max(int(_get_env("LEADS_EXPORT_LOOKBACK_DAYS", "5")), 1)
    now = now_msk_naive()
    base = now - timedelta(days=lookback_days)

    # Если за базовым окном осталось что-то невыгруженное, не отрезаем:
    # сдвигаем границу к самой старой такой строке.
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT MIN(imported_at) FROM provider_leads "
                    "WHERE lead_source = 'provider' "
                    "AND provider_sheet_exported_at IS NULL "
                    "AND imported_at < :base"
                ),
                {"base": base},
            ).first()
    except Exception:
        # База ещё не инициализирована или недоступна: работаем по базовому
        # окну, следующий запуск уточнит.
        logging.getLogger("provider.export").debug(
            "Не удалось проверить наличие невыгруженных старше окна", exc_info=True
        )
        return base

    oldest = row[0] if row is not None else None
    if oldest is None:
        return base
    if isinstance(oldest, str):
        try:
            oldest = datetime.fromisoformat(oldest)
        except ValueError:
            return base
    if not isinstance(oldest, datetime) or oldest >= base:
        return base

    logging.getLogger("provider.export").info(
        "Окно очереди расширено из-за невыгруженных старше %d дней: с %s",
        lookback_days,
        oldest.isoformat(sep=" "),
    )
    return oldest



# -------------------------------------------------------------------
# Защита от параллельного запуска cron
# -------------------------------------------------------------------

@contextlib.contextmanager
def _single_runner_guard():
    """
    Минимально безопасная защита от двойного запуска cron.

    Ключ блокировки строится из целевой таблицы и БД — без PID, иначе каждый
    процесс получил бы свой собственный файл и взаимной блокировки не было бы.
    Два процесса, стартовавшие одновременно, не смогут оба решить
    «осталось 20 мест»: второй увидит занятую блокировку и выйдет.
    """
    import hashlib
    import tempfile

    identity = "|".join(
        [
            _get_env("GOOGLE_SHEET_ID", ""),
            _get_env("GOOGLE_SHEET_NAME", ""),
            _get_env("DATABASE_URL", "sqlite:///./app.db"),
        ]
    )
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    lock_path = os.path.join(tempfile.gettempdir(), f"mb_lk_provider_export_{key}.lock")

    try:
        lock_file = open(lock_path, "a+", encoding="utf-8")
    except OSError:
        # Не смогли создать lock-файл.  Не блокируем выгрузку полностью,
        # но и не выдаём mutex, который не удерживаем.
        logging.getLogger("provider.export").warning(
            "Не удалось создать lock-файл %s; запуск продолжится без защиты от параллельного запуска",
            lock_path,
        )
        yield False
        return

    try:
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        with contextlib.suppress(Exception):
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        with contextlib.suppress(Exception):
            lock_file.close()


# -------------------------------------------------------------------
# Очередь выгрузки
# -------------------------------------------------------------------

def _fetch_pending_provider_leads(
    db_sess, limit: int, *, queue_started_at: Optional[datetime] = None
) -> List[models.ProviderLead]:
    """
    Неотправленные provider-лиды в FIFO-порядке: imported_at ASC, id ASC.

    Это ДОПОЛНЕНИЕ к дневным квотам групп, а не замена lookback: лид, не
    отправленный вчера из-за лимита, остаётся в этой очереди бессрочно —
    ему не нужно «просвеживать» дату.

    ``queue_started_at`` — аварийный ограничитель на случай, когда сверка с
    Google Sheet не удалась.  Он фильтрует по ``imported_at``, а не по
    ``prov_created_at``: поздно пришедший лид провайдера с давним
    ``prov_created_at`` не должен выпадать из очереди навсегда.
    """
    stmt = (
        select(models.ProviderLead)
        .where(models.ProviderLead.lead_source == "provider")
        .where(models.ProviderLead.prov_created_at.isnot(None))
        .where(models.ProviderLead.provider_sheet_exported_at.is_(None))
    )
    if queue_started_at is not None:
        stmt = stmt.where(models.ProviderLead.imported_at >= queue_started_at)
    stmt = stmt.order_by(
        models.ProviderLead.imported_at.asc(), models.ProviderLead.id.asc()
    ).limit(max(1, int(limit)))
    return list(db_sess.execute(stmt).scalars().all())


def _mark_provider_exported(db_sess, lead_ids: List[int]) -> int:
    """
    Помечает строки выгруженными.

    Вызывается ТОЛЬКО после подтверждённого успешного ответа Google API.
    Дополнительно перепроверяем NULL, чтобы повторный cron не трогал
    уже выгруженные строки и не сдвигал их метку.
    """
    ids = sorted({int(value) for value in lead_ids})
    if not ids:
        return 0
    result = db_sess.execute(
        update(models.ProviderLead)
        .where(models.ProviderLead.id.in_(ids))
        .where(models.ProviderLead.provider_sheet_exported_at.is_(None))
        .values(provider_sheet_exported_at=now_msk_naive())
    )
    db_sess.commit()
    return int(result.rowcount or 0)


def _notify_groups_limit_reached(db_sess, group_ids: Iterable[int], *, day=None) -> int:
    """
    Ставит дедуплицированные Telegram-уведомления в существующий outbox.

    Квоты читаются ЗДЕСЬ, а не берутся из снапшота до Google-записи.
    Иначе сценарий «выгрузили ровно до лимита» не дал бы уведомления:
    снапшот видел бы 400/500, а в БД уже 500/500.

    Кандидаты — объединение двух источников:

    1. Группы текущей пачки, включая группы с ``remaining_today == 0``:
       их нет в ``ExportPlan.group_ids()``, потому что их лиды не разрешены,
       но именно из-за исчерпанной квоты они и не выгружаются.
    2. Группы, достигшие лимита сегодня, но без успешно поставленного
       уведомления.  Нужны, потому что у группы может не быть собственных
       pending-лидов (всё выгружено) — тогда она не видна ни в плане, ни в
       ветке пустой очереди, и уведомление не повторилось бы никогда.

    Второй источник — один дешёвый SELECT с фильтром по самой таблице групп:
    уже уведомлённые за (день, лимит) отсекаются без единого запроса по
    лидам.  Поэтому повторных сообщений не бывает, а пустой результат стоит
    одного запроса.
    """
    try:
        awaiting = set(limit_groups.find_groups_awaiting_limit_notification(db_sess, day=day))
    except Exception:
        logging.getLogger("provider.export").warning(
            "Не удалось найти группы, ожидающие уведомления о лимите", exc_info=True
        )
        awaiting = set()

    ids = {int(value) for value in (group_ids or []) if value is not None} | awaiting
    if not ids:
        return 0
    chat_id = _get_env("TELEGRAM_CHAT_ID")
    if not chat_id:
        return 0
    queued = 0
    for group_id in sorted(ids):
        group = limit_groups.get_group(db_sess, group_id)
        if group is None:
            continue
        try:
            # Свежая квота = фактическое состояние БД после этой выгрузки.
            quota = limit_groups.get_group_quota(db_sess, group_id, day=day)
            if not quota.limit_reached:
                continue
            if limit_groups.notify_limit_reached(
                db_sess, group=group, quota=quota, chat_id=chat_id, day=day
            ):
                queued += 1
        except Exception:
            logging.getLogger("provider.export").warning(
                "Не удалось поставить уведомление о лимите группы %s", group_id, exc_info=True
            )
    return queued


def export_provider_leads():
    log = logging.getLogger("provider.export")

    credentials_file = _get_env("GOOGLE_CREDENTIALS_FILE")
    sheet_id = _get_env("GOOGLE_SHEET_ID")
    sheet_name = _get_env("GOOGLE_SHEET_NAME")
    lookback_days = int(_get_env("LEADS_EXPORT_LOOKBACK_DAYS", "5"))
    database_url = _get_env("DATABASE_URL", "sqlite:///./app.db")

    if not credentials_file or not os.path.exists(credentials_file):
        log.error("GOOGLE_CREDENTIALS_FILE not found: %s", credentials_file)
        return
    if not sheet_id or not sheet_name:
        log.error("GOOGLE_SHEET_ID or GOOGLE_SHEET_NAME is empty")
        return

    engine, SessionLocal = db.init_engine_and_session(database_url)
    models.Base.metadata.create_all(bind=engine)
    _ensure_provider_export_state(engine, lookback_days)

    with _single_runner_guard() as acquired:
        if not acquired:
            log.warning("Предыдущий запуск provider export ещё выполняется — пропускаем этот.")
            return
        _run_export(
            log=log,
            service=_build_sheets_client(credentials_file),
            SessionLocal=SessionLocal,
            sheet_id=sheet_id,
            sheet_name=sheet_name,
            queue_started_at=_load_queue_started_at(engine),
        )


def _run_export(
    *,
    log,
    service,
    SessionLocal,
    sheet_id: str,
    sheet_name: str,
    queue_started_at: Optional[datetime] = None,
) -> None:
    with SessionLocal() as s:
        pending = _fetch_pending_provider_leads(s, MAX_ROWS_PER_RUN, queue_started_at=queue_started_at)

    if not pending:
        # Очереди нет, но уведомление могло не поставиться в прошлом запуске.
        # _notify_groups_limit_reached() сам найдёт группы, достигшие лимита
        # сегодня без успешного уведомления, поэтому ранний return не мешает.
        with SessionLocal() as s:
            retried = _notify_groups_limit_reached(s, ())
        log.info(
            "Нет невыгруженных provider-лидов; повторная попытка уведомлений о лимите: отправлено=%d",
            retried,
        )
        return

    with SessionLocal() as s:
        # План строится ТОЛЬКО по кандидатам этой пачки.  Собственная
        # выборка всех NULL-лидов внутри плана видела бы до-деплойную
        # историю, она заняла бы квоту и заблокировала новые строки.
        # Сюда попадают и строки с project_id IS NULL: они не могут быть в группе.
        plan = limit_groups.build_export_plan(
            s, lead_ids=[int(row.id) for row in pending]
        )
        allowed_ids = set(plan.lead_ids())
        selected = [row for row in pending if int(row.id) in allowed_ids]
        held_back_by_limit = len(pending) - len(selected)
        # Группы из РАССЧИТАННЫХ КВОТ, а не из plan.group_ids().
        # group_ids() смотрит только на реально разрешённые строки, поэтому
        # группа с remaining_today == 0 в него не попадает — а её pending-лиды
        # именно из-за исчерпанной квоты и не были выгружены.  Без quotas
        # таким группам нельзя было бы повторить неудачное уведомление.
        touched_group_ids = list(plan.quotas.keys())

    log.info(
        "Очередь выгрузки: кандидатов=%d, разрешено квотой=%d, отложено по лимиту групп=%d, "
        "из них в группах=%d, вне групп=%d",
        len(pending),
        len(selected),
        held_back_by_limit,
        plan.grouped,
        plan.ungrouped,
    )

    # Сохраняем id разрешённых строк: после Google-записи помечаем именно их.
    allowed_row_ids = {int(row.id) for row in selected}

    if not selected:
        # Ничего не отправили, но группы с исчерпанной квотой всё равно надо
        # проверить: их лиды в плане отсутствуют, иначе retry не случился бы.
        with SessionLocal() as s:
            notified = _notify_groups_limit_reached(s, touched_group_ids)
        log.info(
            "Экспорт пропущен: все строки отложены дневными лимитами групп. "
            "Уведомлений о достижении лимита отправлено=%d",
            notified,
        )
        return

    existing_ids = _get_existing_ids(service, sheet_id, sheet_name)
    existing_rows_count = len(existing_ids)

    out_rows: List[List[str]] = []
    duplicate_ids: List[int] = []
    skipped_count = 0
    for r in selected:
        if str(r.vid).strip() in existing_ids:
            skipped_count += 1
            duplicate_ids.append(int(r.id))
            continue
        project_tag = _build_project_tag(r.project_name, r.subdomain)
        gck_tag = _build_gck_tag(r.project_name, r.subdomain)
        out_rows.append(
            [
                _format_dt(r.prov_created_at),
                str(r.vid),
                _extract_first_phone(r),
                "",
                project_tag,
                gck_tag,
                r.subdomain or "",
                format_provider_lead_lk_id(r.id),
            ]
        )

    sent_ids: List[int] = []
    added_rows = 0
    if out_rows:
        sheet_id_num, row_count = _get_sheet_meta(service, sheet_id, sheet_name)
        required_rows = 1 + existing_rows_count + len(out_rows)
        added_rows = _ensure_rows(service, sheet_id, sheet_id_num, required_rows, row_count)

        body = {"values": out_rows}
        try:
            _safe_google_call(
                lambda: service.spreadsheets().values().append(
                    spreadsheetId=sheet_id,
                    range=f"{sheet_name}!A:H",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body=body,
                ).execute()
            )
        except Exception:
            # Запись в Google не подтверждена — метку НЕ ставим.
            # Строки останутся в очереди и уйдут следующим запуском.
            log.exception("Ошибка при записи данных в Google Sheets")
            with SessionLocal() as s:
                _notify_groups_limit_reached(s, touched_group_ids)
            return
        sent_ids = [
            int(row.id)
            for row in selected
            if int(row.id) in allowed_row_ids and str(row.vid).strip() not in existing_ids
        ]

    # Дубликаты по vid тоже считаем выгруженными: они уже есть в таблице,
    # повторно отправлять их не нужно (защита от повторов сохраняется).
    marked = 0
    with SessionLocal() as s:
        marked += _mark_provider_exported(s, sent_ids + duplicate_ids)
        # Квота достигнута ровно сейчас -> уведомляем по СВЕЖЕМУ состоянию.
        notified = _notify_groups_limit_reached(s, touched_group_ids)

    log.info(
        "Экспорт завершен. Отправлено=%d, дубликатов (пропущено)=%d, помечено в БД=%d, "
        "отложено по лимиту групп=%d, добавлено строк в таблицу=%d, уведомлений о лимите=%d",
        len(sent_ids),
        skipped_count,
        marked,
        held_back_by_limit,
        added_rows,
        notified,
    )


def main():
    load_dotenv()
    logging_setup.setup_logging()
    logging_setup.setup_provider_export_logger()
    logging.getLogger().setLevel(logging.INFO)
    export_provider_leads()


if __name__ == "__main__":
    main()
