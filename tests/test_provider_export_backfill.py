"""
Проверка миграции состояния выгрузки provider-лидов.

Сценарий повторяет деплой на существующей БД: таблица ``provider_leads``
уже содержит историю, а колонки ``provider_sheet_exported_at`` ещё нет.

    python -m pytest tests/test_provider_export_backfill.py -q
"""

from __future__ import annotations

import contextlib
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SHEETS_TZ", "Europe/Moscow")

from backend.app import models  # noqa: E402
from backend.app.time_utils import as_local_naive, now_msk  # noqa: E402

import tool_export_provider_leads as export_mod  # noqa: E402


def _tmp_dir() -> Path:
    """
    Каталог для временных БД тестов.

    Системный temp в этом окружении недоступен для записи pytest, поэтому
    используем локальный каталог рядом с тестами и убираем его в конце.
    """
    path = Path(__file__).resolve().parent / "_tmp_backfill"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _drop_db(db_path: Path) -> None:
    """Удаляет файлы SQLite: Windows держит handle, пока engine не закрыт."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = db_path.with_name(db_path.name + suffix)
        if candidate.exists():
            with contextlib.suppress(OSError):
                candidate.unlink()


@pytest.fixture()
def legacy_engine():
    """
    БД «до деплоя»: provider_leads со старой схемой, без
    provider_sheet_exported_at, и с накопленной историей.
    """
    db_path = _tmp_dir() / "legacy.db"
    _drop_db(db_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        # Создаём таблицу вручную — как если бы её создала старая версия кода.
        conn.execute(
            text(
                """
                CREATE TABLE provider_leads (
                    id INTEGER PRIMARY KEY,
                    vid VARCHAR NOT NULL,
                    lead_source VARCHAR DEFAULT 'provider',
                    phone VARCHAR,
                    phones_raw JSON,
                    project_name VARCHAR,
                    prov_created_at TIMESTAMP,
                    prov_chanel VARCHAR,
                    prov_source VARCHAR,
                    subdomain VARCHAR,
                    pixel_url VARCHAR,
                    imported_at TIMESTAMP NOT NULL,
                    client_sheet_exported_at TIMESTAMP,
                    project_id INTEGER
                )
                """
            )
        )
        now = as_local_naive(now_msk())
        rows = []
        # 3 дня истории внутри lookback=3 -> старая выгрузка их уже отправила.
        for index in range(30):
            when = now - timedelta(days=index % 3, minutes=index)
            rows.append(
                {
                    "vid": f"recent-{index}",
                    "lead_source": "provider",
                    "prov_created_at": when,
                    "imported_at": when,
                    "project_id": 1,
                }
            )
        # История старше lookback -> в таблицу не попадала никогда.
        for index in range(30):
            when = now - timedelta(days=30 + index, minutes=index)
            rows.append(
                {
                    "vid": f"old-{index}",
                    "lead_source": "provider",
                    "prov_created_at": when,
                    "imported_at": when,
                    "project_id": 1,
                }
            )
        # Пиксель не должен затронутым быть ни при каких условиях.
        for index in range(10):
            when = now - timedelta(hours=index)
            rows.append(
                {
                    "vid": f"pixel-{index}",
                    "lead_source": "pixel",
                    "prov_created_at": when,
                    "imported_at": when,
                    "project_id": 1,
                }
            )
        conn.execute(
            text(
                "INSERT INTO provider_leads "
                "(vid, lead_source, prov_created_at, imported_at, project_id) "
                "VALUES (:vid, :lead_source, :prov_created_at, :imported_at, :project_id)"
            ),
            rows,
        )
        # Таблица меток: её ещё нет, как и при первом деплое.
    yield engine
    engine.dispose()
    _drop_db(db_path)
    # Каталог тоже убираем, чтобы в репозитории не оставалось мусора.
    with contextlib.suppress(OSError):
        db_path.parent.rmdir()


def _marked_count(engine, *, lead_source: str = "provider") -> int:
    with engine.begin() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM provider_leads "
                    "WHERE lead_source = :src AND provider_sheet_exported_at IS NOT NULL"
                ),
                {"src": lead_source},
            ).scalar_one()
        )


def test_backfill_closes_all_history_before_deploy(legacy_engine):
    """
    Регрессия против самой опасной ошибки перехода.

    Раньше backfill сверялся с Google Sheet по ``vid``.  После ежемесячной
    чистки таблицы ``vid`` отсутствуют, сверка отвечала «не выгружено», и
    десятки тысяч уже распределённых строк возвращались в таблицу.

    Теперь сверки нет: вся история до деплоя закрывается по окну, и в очередь
    попадают только строки, пришедшие после.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)

    inspector = inspect(legacy_engine)
    columns = {c["name"] for c in inspector.get_columns("provider_leads")}
    assert "provider_sheet_exported_at" in columns

    # Вся до-деплойная история (recent + old) помечена как выгруженная.
    assert _marked_count(legacy_engine) == 60
    # Pixel не тронут ни при каких условиях.
    assert _marked_count(legacy_engine, lead_source="pixel") == 0

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        pending = export_mod._fetch_pending_provider_leads(
            s, 500, queue_started_at=export_mod._load_queue_started_at(legacy_engine)
        )
        assert pending == [], "ни одна историческая строка не должна попасть в очередь"


def test_backfill_runs_only_once(legacy_engine):
    """Повторный запуск не должен пересматривать уже размеченную историю."""
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        now = as_local_naive(now_msk())
        s.add(
            models.ProviderLead(
                id=7777,
                vid="brand-new",
                lead_source="provider",
                project_name="B1_new",
                prov_created_at=now,
                imported_at=now,
                project_id=1,
            )
        )
        s.commit()

    # Второй запуск: метка деплоя уже есть, поэтому новая строка не трогается.
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)
    assert _marked_count(legacy_engine) == 60, "свежая строка не должна помечаться backfill'ом"

    with legacy_engine.begin() as conn:
        state = conn.execute(
            text("SELECT COUNT(*) FROM provider_export_backfill_state")
        ).scalar_one()
    assert state == 1, "служебная метка деплоя должна быть ровно одна"


def test_new_rows_after_deploy_still_export(legacy_engine):
    """
    Главное свойство: backfill не съедает лиды, реально ждущие выгрузки.

    Строка, пришедшая после деплоя, обязана попасть в очередь.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)
    queue_started_at = export_mod._load_queue_started_at(legacy_engine)

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        now = as_local_naive(now_msk())
        s.add(
            models.ProviderLead(
                id=9999,
                vid="brand-new",
                lead_source="provider",
                project_name="B1_new",
                prov_created_at=now,
                imported_at=now,
                project_id=1,
            )
        )
        s.commit()

        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=queue_started_at)
        assert [row.vid for row in pending] == ["brand-new"], (
            "новые лиды после деплоя попадают в очередь, "
            "а исторические строки повторно не отправляются"
        )


def test_late_arrival_with_old_prov_created_at_still_exports(legacy_engine):
    """
    Регрессия: поздно пришедший лид с ДАВНИМ prov_created_at.

    Граница очереди считается по ``imported_at``, а не по ``prov_created_at``,
    поэтому такой лид не выпадает из выгрузки.  Раньше он мог пропасть навсегда.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)
    queue_started_at = export_mod._load_queue_started_at(legacy_engine)

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        imported_at = as_local_naive(now_msk())
        prov_created_at = imported_at - timedelta(days=45)
        s.add(
            models.ProviderLead(
                id=8888,
                vid="late-arrival",
                lead_source="provider",
                project_name="B1_late",
                prov_created_at=prov_created_at,
                imported_at=imported_at,
                project_id=1,
            )
        )
        s.commit()

        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=queue_started_at)
        vids = [row.vid for row in pending]
        assert "late-arrival" in vids, "свежий imported_at не отсекается старым prov_created_at"
        assert vids[-1] == "late-arrival", "и встаёт в конец FIFO, а не в начало"


def test_window_extends_when_old_pending_exists(legacy_engine):
    """
    Ключевое требование: лимит ограничивает поток, но НЕ обрезает историю.

    Если группа переполнялась дольше окна, в базе остаются невыгруженные
    строки старше LEADS_EXPORT_LOOKBACK_DAYS.  Окно должно расшириться до
    самой старой такой строки, иначе она выпала бы из очереди навсегда.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        # Хвост, провисевший 20 дней: он не вписывается в окно в 5 дней.
        twenty_days_ago = as_local_naive(now_msk()) - timedelta(days=20)
        s.add(
            models.ProviderLead(
                id=7001,
                vid="overdue-20-days",
                lead_source="provider",
                project_name="B1_wait",
                prov_created_at=twenty_days_ago,
                imported_at=twenty_days_ago,
                project_id=1,
            )
        )
        s.commit()

    # Граница уехала назад — до самой старой невыгруженной строки.
    queue_started_at = export_mod._load_queue_started_at(legacy_engine)
    assert queue_started_at <= twenty_days_ago, "окно должно расшириться до хвоста"

    with LocalSession() as s:
        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=queue_started_at)
        assert "overdue-20-days" in [row.vid for row in pending], (
            "хвост старше окна не должен отсекаться — он доработается"
        )


def test_window_stays_narrow_when_no_old_pending(legacy_engine):
    """
    Обратный случай: если старых невыгруженных нет, окно не растёт.

    Иначе окно постепенно расползлось бы на всю базу — и мы вернулись бы
    к повторной выгрузке истории, от которой окно и защищает.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)
    base = as_local_naive(now_msk()) - timedelta(days=5)
    queue_started_at = export_mod._load_queue_started_at(legacy_engine)
    assert queue_started_at >= base - timedelta(seconds=5), "окно осталось базовым"


def test_expanded_window_does_not_resurrect_exported_history(legacy_engine):
    """
    Расширение окна не должно ослаблять защиту от повторов.

    Backfill закрывает всю историю, поэтому даже при расширенном окне
    в очередь не попадает ничего, что уже помечено выгруженным.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        twenty_days_ago = as_local_naive(now_msk()) - timedelta(days=20)
        s.add(
            models.ProviderLead(
                id=7002,
                vid="really-overdue",
                lead_source="provider",
                project_name="B1_wait",
                prov_created_at=twenty_days_ago,
                imported_at=twenty_days_ago,
                project_id=1,
            )
        )
        s.commit()

    queue_started_at = export_mod._load_queue_started_at(legacy_engine)
    with LocalSession() as s:
        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=queue_started_at)
        vids = [row.vid for row in pending]
        # В очередь попал только реально невыгруженный хвост.
        assert vids == ["really-overdue"]
        assert not any(vid.startswith("old-") for vid in vids), (
            "история, закрытая backfill'ом, не должна возвращаться даже при расширенном окне"
        )
        assert not any(vid.startswith("recent-") for vid in vids)


def test_cleaned_sheet_cannot_resurrect_history(legacy_engine, monkeypatch):
    """
    Ключевая защита от повторной выгрузки после чистки таблицы.

    Пользователь удаляет из Google-таблицы уже распределённые строки.  Таблица
    «помнит» их меньше всех, поэтому ориентироваться на неё при переходе
    нельзя.  Здесь таблица пуста — и всё равно история не возвращается.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=5)
    queue_started_at = export_mod._load_queue_started_at(legacy_engine)

    # Таблица полностью очищена: ни одного vid не осталось.
    monkeypatch.setattr(export_mod, "_get_existing_ids", lambda *a, **k: set())

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=queue_started_at)
        assert pending == [], "очищенная таблица не должна воскресить историю"
