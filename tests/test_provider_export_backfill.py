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


def test_reconcile_marks_only_vids_present_in_sheet(legacy_engine):
    """
    Сверка с Google Sheet — источник истины, а не период.

    В таблице лежат только ``recent-*``; ``old-*`` в неё не попадали.
    Значит помечаться должны ровно 30 recent-строк, а 30 старых обязаны
    остаться NULL и уйти в ближайшую выгрузку.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)

    with legacy_engine.begin() as conn:
        assert export_mod._reconciliation_pending(conn) is True
        export_mod.reconcile_provider_export_state(
            conn,
            existing_vids={f"recent-{index}" for index in range(30)},
            lookback_days=3,
        )

    inspector = inspect(legacy_engine)
    columns = {c["name"] for c in inspector.get_columns("provider_leads")}
    assert "provider_sheet_exported_at" in columns

    # Ровно те, кто реально в таблице.
    assert _marked_count(legacy_engine) == 30

    with legacy_engine.begin() as conn:
        marked_vids = {
            str(row[0])
            for row in conn.execute(
                text(
                    "SELECT vid FROM provider_leads "
                    "WHERE lead_source = 'provider' AND provider_sheet_exported_at IS NOT NULL"
                )
            ).fetchall()
        }
        assert all(vid.startswith("recent-") for vid in marked_vids)
        assert not any(vid.startswith("old-") for vid in marked_vids)


def test_reconcile_keeps_rows_missing_from_sheet_pending(legacy_engine):
    """Строки, которых нет в таблице, не теряются — они ждут выгрузки."""
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)

    # Пустая таблица: не выгружено НИЧЕГО.
    with legacy_engine.begin() as conn:
        export_mod.reconcile_provider_export_state(
            conn, existing_vids=set(), lookback_days=3
        )

    assert _marked_count(legacy_engine) == 0

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        queue_started_at = export_mod._load_queue_started_at(
            legacy_engine, existing_vids=set()
        )
        # Сверка выполнена, но подтвердила, что ничего не выгружено,
        # поэтому очередь НЕ ограничена по времени — вся история в записи.
        assert queue_started_at is None
        pending = export_mod._fetch_pending_provider_leads(
            s, 500, queue_started_at=queue_started_at
        )
        assert len(pending) == 60, "ни одна реально невыгруженная строка не потеряна"


def test_reconcile_leads_with_old_prov_created_at_still_export(legacy_engine):
    """
    Регрессия: поздно пришедший лид с ДАВНИМ prov_created_at.

    Раньше граница очереди считалась по prov_created_at, и такой лид
    мог выпасть из выгрузки навсегда.  Теперь граница — по imported_at,
    а после сверки её вообще нет.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)

    # В таблицу попал только один vid.
    with legacy_engine.begin() as conn:
        export_mod.reconcile_provider_export_state(
            conn, existing_vids={"recent-0"}, lookback_days=3
        )

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        # Пришёл сегодня, но провайдер проставил дату месяц назад.
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

        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=None)
        vids = [row.vid for row in pending]
        assert "late-arrival" in vids, "свежий imported_at не должен отсекаться старым prov_created_at"
        # Он встаёт в конец FIFO: старые невыгруженные уходят раньше.
        assert vids[-1] == "late-arrival"


def test_reconcile_runs_only_once(legacy_engine):
    """Вторая попытка сверки не должна трогать новые строки."""
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)
    with legacy_engine.begin() as conn:
        export_mod.reconcile_provider_export_state(
            conn, existing_vids={f"recent-{i}" for i in range(30)}, lookback_days=3
        )
        assert export_mod._reconciliation_pending(conn) is False

    with legacy_engine.begin() as conn:
        now = as_local_naive(now_msk())
        conn.execute(
            text(
                "INSERT INTO provider_leads "
                "(vid, lead_source, prov_created_at, imported_at, project_id) "
                "VALUES ('late-1', 'provider', :when, :when, 1)"
            ),
            {"when": now},
        )
        # Повторная сверка: метка уже есть, поэтому ничего не пересчитываем.
        export_mod.reconcile_provider_export_state(
            conn, existing_vids={f"recent-{i}" for i in range(30)} | {"late-1"}, lookback_days=3
        )

    assert _marked_count(legacy_engine) == 30, "после первой сверки метка не меняется"
    with legacy_engine.begin() as conn:
        state = conn.execute(
            text("SELECT COUNT(*) FROM provider_export_backfill_state")
        ).scalar_one()
    assert state == 1, "служебная метка сверки должна быть ровно одна"


def test_reconcile_never_touches_pixel(legacy_engine):
    """Pixel-контур полностью изолирован от provider-сверки."""
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)
    with legacy_engine.begin() as conn:
        export_mod.reconcile_provider_export_state(
            # Все vid, включая pixel-овские, «есть» в таблице.
            conn,
            existing_vids=(
                {f"recent-{i}" for i in range(30)}
                | {f"old-{i}" for i in range(30)}
                | {f"pixel-{i}" for i in range(10)}
            ),
            lookback_days=3,
        )

    assert _marked_count(legacy_engine) == 60
    assert _marked_count(legacy_engine, lead_source="pixel") == 0, (
        "Pixel не должен получать provider-метку даже при совпадении vid"
    )


def test_queue_boundary_used_only_when_google_unavailable(legacy_engine):
    """
    Аварийный режим: Google недоступен, сверки не было.

    Тогда единственная защита от отправки всей истории — окно по imported_at.
    Как только сверка выполнена, граница исчезает совсем.
    """
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)

    # Сверки не было и vid недоступны -> окно по времени.
    assert export_mod._load_queue_started_at(legacy_engine) is not None

    # Сверка выполнена -> границы нет.
    with legacy_engine.begin() as conn:
        export_mod.reconcile_provider_export_state(
            conn, existing_vids={f"recent-{i}" for i in range(30)}, lookback_days=3
        )
    assert export_mod._load_queue_started_at(legacy_engine) is None
    assert export_mod._load_queue_started_at(legacy_engine, existing_vids=set()) is None


def test_pixel_rows_never_enter_provider_queue(legacy_engine):
    """Pixel-контур изолирован и на уровне очереди выгрузки."""
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)
    with legacy_engine.begin() as conn:
        export_mod.reconcile_provider_export_state(
            conn, existing_vids={f"recent-{i}" for i in range(30)}, lookback_days=3
        )

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=None)
        assert all(row.lead_source == "provider" for row in pending)
        assert all("pixel" not in (row.vid or "") for row in pending)
