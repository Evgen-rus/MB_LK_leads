"""
Регрессионные проверки интеграционного пути ``_run_export``.

Здесь нет мока Google на уровне сети: подменяются только два обращения
к таблицы (список существующих vid и append), поэтому проверяется
настоящая последовательность «очередь -> план -> Google -> метки ->
Telegram», включая места, где раньше была реальная ошибка.

    python -m pytest tests/test_provider_export_run.py -q
"""

from __future__ import annotations

import contextlib
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SHEETS_TZ", "Europe/Moscow")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import tool_export_provider_leads as export_mod  # noqa: E402
from backend.app import daily_export_limit_groups as limit_groups  # noqa: E402
from backend.app import models, notifications  # noqa: E402
from backend.app.time_utils import as_local_naive, now_msk  # noqa: E402

DAY1 = date(2026, 3, 10)


def _now() -> datetime:
    """
    Настоящее «сейчас» в бизнес-таймзоне.

    Интеграционные тесты намеренно не подменяют календарный день:
    ``get_group_quota`` без явного ``day`` считает по реальному сегодняшнему
    дню, поэтому метки выгрузки должны совпадать с текущим временем.
    """
    return as_local_naive(now_msk())


def _hours_ago(hours: float) -> datetime:
    return _now() - timedelta(hours=hours)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
    with LocalSession() as s:
        yield s
    engine.dispose()


def _at(day: date, hour: int = 10) -> datetime:
    return as_local_naive(datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=now_msk().tzinfo))


def _make_client_and_group(s, *, project_id: int = 10, daily_limit: int = 500):
    s.add(
        models.User(id=2, login="c1", password_hash="x", display_name="Клиент", role="client", created_at=now_msk())
    )
    s.add(models.ClientProfile(user_id=2, name="Клиент", created_at=now_msk(), updated_at=now_msk()))
    s.add(
        models.Project(
            id=project_id,
            user_id=2,
            name="B1_[LR223] ПрактикМ",
            tag="t",
            collection_source="Сайты",
            data_source_code="B1",
            status="Активен",
            delivery_status="Активна",
            data_limit=0,
            numbers_today=0,
            numbers_total=0,
            days_received="",
            sources_count=0,
            created_at=now_msk(),
            updated_at=now_msk(),
        )
    )
    s.commit()
    group = limit_groups.create_group(
        s, client_id=2, name="ПрактикМ", daily_limit=daily_limit, project_ids=[project_id]
    )
    return group


def _make_lead(s, lead_id: int, project_id, *, when: datetime, exported_at=None, vid=None):
    s.add(
        models.ProviderLead(
            id=lead_id,
            vid=vid or f"vid-{lead_id}",
            lead_source="provider",
            project_name="B1_[LR223] ПрактикМ",
            prov_created_at=when,
            imported_at=when,
            project_id=project_id,
            provider_sheet_exported_at=exported_at,
        )
    )
    s.commit()


class FakeSheets:
    """
    Минимальная подмена Google Sheets API.

    ``append_ok=False`` имитирует сетевую ошибку: метод бросает исключение,
    и экспорт не должен ставить метку ``provider_sheet_exported_at``.
    """

    def __init__(self, existing_vids=None, *, append_ok: bool = True):
        self.existing = set(existing_vids or ())
        self.appended: list[list[str]] = []
        self.append_ok = append_ok
        self._values = self

    # values().get -> чтение колонки B (список vid)
    # values().append -> запись строк
    class _Values:
        def __init__(self, owner):
            self._owner = owner

        def get(self, **kwargs):
            owner = self._owner
            return type(
                "R",
                (),
                {
                    "execute": lambda self: {"values": [[vid] for vid in sorted(owner.existing)]}
                },
            )()

        def append(self, **kwargs):
            owner = self._owner
            if not owner.append_ok:
                raise RuntimeError("Google API unavailable")

            def _exec():
                owner.appended.extend(kwargs["body"]["values"])
                return {}

            return type("R", (), {"execute": staticmethod(_exec)})()

    def values(self):
        return FakeSheets._Values(self)

    def spreadsheets(self):
        return self

    def batchUpdate(self, **kwargs):  # noqa: N802 - имя как в Google API
        return type("R", (), {"execute": staticmethod(lambda: {})})()

    def get(self, **kwargs):  # noqa: A003 - имя как в Google API
        return type(
            "R",
            (),
            {
                "execute": staticmethod(
                    lambda: {
                        "sheets": [
                            {
                                "properties": {
                                    "title": "Sheet1",
                                    "sheetId": 0,
                                    "gridProperties": {"rowCount": 1000},
                                }
                            }
                        ]
                    }
                )
            },
        )()


def _run(session, sheets, *, monkeypatch, queue_started_at=None):
    """Прогоняет настоящий _run_export с подменённым Google и Telegram."""
    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=session.get_bind(), future=True)
    monkeypatch.setattr(export_mod, "_get_sheet_meta", lambda *a, **k: (0, 1000))
    monkeypatch.setattr(export_mod, "_ensure_rows", lambda *a, **k: 0)
    monkeypatch.setattr(export_mod, "_safe_google_call", lambda call: call())
    export_mod._run_export(
        log=export_mod.logging.getLogger("test"),
        service=sheets,
        SessionLocal=LocalSession,
        sheet_id="sid",
        sheet_name="Sheet1",
        queue_started_at=queue_started_at,
    )


def _capture_telegram(monkeypatch) -> list[str]:
    sent: list[str] = []

    def fake_send(*, db_sess, chat_id, text, parse_mode=None, metadata=None, bot_token="", kind="system"):
        sent.append(text)
        return notifications.NotificationResult(
            delivered=True, channel="telegram_outbox", reason="queued", notification_id=len(sent)
        )

    monkeypatch.setattr(limit_groups.notifications, "send_system_notification", fake_send)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    return sent


def _exported_ids(s) -> set[int]:
    return {
        int(row[0])
        for row in s.execute(
            text("SELECT id FROM provider_leads WHERE provider_sheet_exported_at IS NOT NULL")
        ).fetchall()
    }


# -------------------------------------------------------------------
# 1. Старая NULL-история вне текущей очереди не занимает квоту
# -------------------------------------------------------------------

def test_old_history_outside_queue_does_not_consume_group_quota(session, monkeypatch):
    """
    Регрессия: build_export_plan перечитывал ВСЕ NULL-лиды проектов.

    Старая до-деплойная запись того же проекта не входит в текущую очередь,
    но раньше попадала в план и занимала место дневной квоты, из-за чего
    новая запись оставалась невыгруженной.
    """
    _make_client_and_group(session, project_id=10, daily_limit=5)
    boundary = _hours_ago(24)

    # Старая история ДО границы очереди.
    _make_lead(session, 100, 10, when=_hours_ago(24 * 40))
    # Новая pending-запись того же проекта ПОСЛЕ границы.
    _make_lead(session, 200, 10, when=_hours_ago(1))

    sheets = FakeSheets()
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=boundary)

    assert len(sheets.appended) == 1, "новая запись обязана уйти в выгрузку"
    assert sheets.appended[0][1] == "vid-200"
    assert 200 in _exported_ids(session)
    assert 100 not in _exported_ids(session), "старая история вне очереди не трогается этим запуском"


# -------------------------------------------------------------------
# 3. provider_id = NULL экспортируется как обычный ungrouped lead
# -------------------------------------------------------------------

def test_lead_without_project_id_still_exports(session, monkeypatch):
    """
    Регрессия: строки с project_id=NULL отсекались фильтром
    ``row.project_id is not None`` и никогда не выгружались.
    """
    _make_client_and_group(session, project_id=10, daily_limit=500)
    _make_lead(session, 300, None, when=_hours_ago(1))

    sheets = FakeSheets()
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=None)

    assert len(sheets.appended) == 1
    assert sheets.appended[0][1] == "vid-300"
    assert 300 in _exported_ids(session)


def test_leads_without_project_id_keep_fifo_position(session, monkeypatch):
    """Строки без project_id не теряют общий FIFO и не съедают квоту группы."""
    _make_client_and_group(session, project_id=10, daily_limit=1)
    _make_lead(session, 301, None, when=_hours_ago(3))   # раньше всех
    _make_lead(session, 302, 10, when=_hours_ago(2))     # группа, квота 1
    _make_lead(session, 303, None, when=_hours_ago(1))   # позже

    sheets = FakeSheets()
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=None)

    vids_in_sheet = [row[1] for row in sheets.appended]
    assert vids_in_sheet[0] == "vid-301", "NULL-проект идёт первым по общему FIFO"
    assert "vid-302" in vids_in_sheet, "единственная квота группы использована"
    # vid-303 не выгружен: MAX-подобного урезания тут нет, но квота не
    # относится к нему -> он обязан уйти. Проверяем отсутствие блокировки.
    assert "vid-303" in vids_in_sheet


# -------------------------------------------------------------------
# 2. Telegram по СВЕЖЕЙ квоте, а не по снапшоту до Google
# -------------------------------------------------------------------

def test_telegram_fires_when_batch_reaches_limit_exactly(session, monkeypatch):
    """
    Регрессия: снапшот квоты снимался ДО записи в Google.

    400/500 -> отправляем 100 -> в БД становится 500/500.
    Старый снапшот видел 400 и молчал; очередь пустеет, следующий cron
    выходит раньше, и уведомление не приходит вообще.
    """
    group = _make_client_and_group(session, project_id=10, daily_limit=500)
    sent = _capture_telegram(monkeypatch)

    # Уже выгружено сегодня 400.
    for index in range(400):
        _make_lead(session, 1000 + index, 10, when=_hours_ago(6), exported_at=_hours_ago(5))
    # И ровно 100 в очереди.
    for index in range(100):
        _make_lead(session, 2000 + index, 10, when=_hours_ago(1))

    sheets = FakeSheets()
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=None)

    assert len(sheets.appended) == 100
    assert len(sent) == 1, "уведомление должно уйти сразу по достижении 500/500"
    assert "Лимит группы проектов на день достигнут" in sent[0]
    assert "Выгружено сегодня: <b>500</b>" in sent[0]

    # Повторный cron того же дня молчит.
    _run(session, FakeSheets(), monkeypatch=monkeypatch, queue_started_at=None)
    assert len(sent) == 1, "дедупликация: второй запуск не дублирует"


def test_telegram_after_limit_increase(session, monkeypatch):
    """
    500 -> 700: после роста лимита группа снова упирается в потолок,
    и приходит новое уведомление.

    Сценарий сквозной, без искусственных меток: первый запуск реально
    выгружает 500 и уведомляет, затем админ поднимает лимит.
    """
    group = _make_client_and_group(session, project_id=10, daily_limit=500)
    sent = _capture_telegram(monkeypatch)

    # День 1: пришло 600, лимит 500.
    for index in range(600):
        _make_lead(session, 1000 + index, 10, when=_hours_ago(4) + timedelta(minutes=index))
    first = FakeSheets()
    _run(session, first, monkeypatch=monkeypatch, queue_started_at=None)
    assert len(first.appended) == 500
    assert len(sent) == 1, "первое уведомление — на достижении 500/500"
    assert "Выгружено сегодня: <b>500</b>" in sent[0]

    # Админ поднял лимит до 700. Новых лидов не приходило, поэтому хвост
    # из 100 pending полностью помещается в 200 новых мест.
    limit_groups.update_group(session, group_id=int(group.id), client_id=2, daily_limit=700)
    second = FakeSheets()
    _run(session, second, monkeypatch=monkeypatch, queue_started_at=None)
    assert len(second.appended) == 100, "появившиеся 200 мест хватает на весь хвост"
    assert len(sent) == 1, "600 < 700 — новый потолок не взят, повторного сообщения нет"

    # Добираем новыми лидами до 700 — вот теперь уведомление обязательно.
    for index in range(100):
        _make_lead(session, 5000 + index, 10, when=_hours_ago(1) + timedelta(minutes=index))
    third = FakeSheets()
    _run(session, third, monkeypatch=monkeypatch, queue_started_at=None)
    assert len(third.appended) == 100
    assert len(sent) == 2, "достижение нового потолка 700 — новое уведомление"
    assert "Лимит: <b>700</b>" in sent[1]
    assert "Выгружено сегодня: <b>700</b>" in sent[1]

    # Дальше повторов нет.
    _run(session, FakeSheets(), monkeypatch=monkeypatch, queue_started_at=None)
    assert len(sent) == 2


def test_no_telegram_when_limit_not_reached(session, monkeypatch):
    """Пока лимит не взят, уведомлений нет."""
    _make_client_and_group(session, project_id=10, daily_limit=500)
    sent = _capture_telegram(monkeypatch)
    for index in range(10):
        _make_lead(session, 1000 + index, 10, when=_hours_ago(1))

    _run(session, FakeSheets(), monkeypatch=monkeypatch, queue_started_at=None)
    assert sent == []


# -------------------------------------------------------------------
# 9. Ошибка Google не ставит метку
# -------------------------------------------------------------------

def test_google_failure_does_not_mark_exported(session, monkeypatch):
    _make_client_and_group(session, project_id=10, daily_limit=500)
    _capture_telegram(monkeypatch)
    for index in range(10):
        _make_lead(session, 1000 + index, 10, when=_hours_ago(1))

    sheets = FakeSheets(append_ok=False)
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=None)

    assert _exported_ids(session) == set(), "ошибка Google не должна ставить метку"

    # Следующий запуск успешно выгружает те же строки.
    ok = FakeSheets()
    _run(session, ok, monkeypatch=monkeypatch, queue_started_at=None)
    assert len(ok.appended) == 10
    assert len(_exported_ids(session)) == 10


# -------------------------------------------------------------------
# 10. Повторный cron не дублирует
# -------------------------------------------------------------------

def test_second_cron_does_not_duplicate(session, monkeypatch):
    _make_client_and_group(session, project_id=10, daily_limit=500)
    _capture_telegram(monkeypatch)
    for index in range(20):
        _make_lead(session, 1000 + index, 10, when=_hours_ago(1))

    first = FakeSheets()
    _run(session, first, monkeypatch=monkeypatch, queue_started_at=None)
    assert len(first.appended) == 20

    second = FakeSheets()
    _run(session, second, monkeypatch=monkeypatch, queue_started_at=None)
    assert second.appended == [], "повторный cron не отправляет уже выгруженное"


def test_duplicate_vid_in_sheet_is_marked_not_resent(session, monkeypatch):
    """Защита от повторов по vid: строка в таблице помечается, но не дублируется."""
    _make_client_and_group(session, project_id=10, daily_limit=500)
    _capture_telegram(monkeypatch)
    _make_lead(session, 400, 10, when=_hours_ago(1))

    sheets = FakeSheets(existing_vids={"vid-400"})
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=None)

    assert sheets.appended == [], "vid уже в таблице — повторно не отправляем"
    assert 400 in _exported_ids(session), "но строка считается выгруженной"


# -------------------------------------------------------------------
# Полный сценарий 600 -> 500 + 100 pending
# -------------------------------------------------------------------

def test_day1_600_leads_limit_500_end_to_end(session, monkeypatch):
    """600 лидов при лимите 500 -> ровно 500 уходят в таблицу, 100 ждут."""
    group = _make_client_and_group(session, project_id=10, daily_limit=500)
    _capture_telegram(monkeypatch)
    for index in range(600):
        _make_lead(session, 1000 + index, 10, when=_hours_ago(1) - timedelta(minutes=index))

    sheets = FakeSheets()
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=None)

    assert len(sheets.appended) == 500
    quota = limit_groups.get_group_quota(session, int(group.id))
    assert quota.exported_today == 500
    assert quota.pending_total == 100
    assert quota.limit_reached is True


def test_day2_old_pending_goes_before_new(session, monkeypatch):
    """
    День 2: 100 старых pending + 300 новых при лимите 500 -> 400 в выгрузку.

    Состояние «день 1» задано напрямую: 500 строк помечены меткой ВЧЕРА,
    100 остались NULL.  Сегодняшняя квота снова полная (500), поэтому
    сначала обязаны уйти самые старые невыгруженные.
    """
    _make_client_and_group(session, project_id=10, daily_limit=500)
    _capture_telegram(monkeypatch)
    yesterday = _now() - timedelta(days=1)

    # День 1: 500 выгружено (метка вчера), 100 остались pending.
    for index in range(500):
        _make_lead(session, 1000 + index, 10, when=yesterday, exported_at=yesterday)
    for index in range(100):
        _make_lead(session, 2000 + index, 10, when=yesterday)
    # День 2: пришло ещё 300. Номер id растёт вместе с временем,
    # поэтому FIFO (imported_at ASC, id ASC) отдаёт их по порядку 5000, 5001...
    for index in range(300):
        _make_lead(session, 5000 + index, 10, when=_now() + timedelta(minutes=index))

    sheets = FakeSheets()
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=None)

    assert len(sheets.appended) == 400
    vids = [row[1] for row in sheets.appended]
    assert set(vids[:100]) == {f"vid-{2000 + i}" for i in range(100)}, (
        "первыми уходят вчерашние pending, а не сегодняшние"
    )
    assert vids[100] == "vid-5000"

    quota = limit_groups.get_group_quota(session, 1)
    assert quota.exported_today == 400
    assert quota.pending_total == 0


def test_day2_rollover_to_day3_when_overflow(session, monkeypatch):
    """200 старых + 600 новых при лимите 500 -> 500 в выгрузку, 300 ждут завтра."""
    group = _make_client_and_group(session, project_id=10, daily_limit=500)
    _capture_telegram(monkeypatch)
    yesterday = _now() - timedelta(days=1)

    for index in range(500):
        _make_lead(session, 1000 + index, 10, when=yesterday, exported_at=yesterday)
    for index in range(200):
        _make_lead(session, 2000 + index, 10, when=yesterday)
    for index in range(600):
        _make_lead(session, 5000 + index, 10, when=_now() - timedelta(minutes=index))

    sheets = FakeSheets()
    _run(session, sheets, monkeypatch=monkeypatch, queue_started_at=None)

    assert len(sheets.appended) == 500, "строго по дневному лимиту, без превышения"
    quota = limit_groups.get_group_quota(session, int(group.id))
    assert quota.exported_today == 500
    assert quota.pending_total == 300, "остаток переносится на следующий день"
