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


def test_backfill_marks_history_and_is_idempotent(legacy_engine):
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)

    inspector = inspect(legacy_engine)
    columns = {c["name"] for c in inspector.get_columns("provider_leads")}
    assert "provider_sheet_exported_at" in columns

    with legacy_engine.begin() as conn:
        # Всё внутри lookback помечено как уже выгруженное.
        recent_marked = conn.execute(
            text(
                "SELECT COUNT(*) FROM provider_leads "
                "WHERE lead_source = 'provider' AND provider_sheet_exported_at IS NOT NULL"
            )
        ).scalar_one()
        assert recent_marked == 30, "история внутри lookback считается выгруженной"

        # Пиксель не тронут.
        pixel_marked = conn.execute(
            text(
                "SELECT COUNT(*) FROM provider_leads "
                "WHERE lead_source = 'pixel' AND provider_sheet_exported_at IS NOT NULL"
            )
        ).scalar_one()
        assert pixel_marked == 0, "Pixel-контур не должен получать provider-метку"

        # Старые строки остаются NULL, но они и не попадают в новую очередь
        # из-за окна выборки экспорта.
        old_total = conn.execute(
            text(
                "SELECT COUNT(*) FROM provider_leads "
                "WHERE lead_source = 'provider' AND prov_created_at < :t"
            ),
            {"t": as_local_naive(now_msk() - timedelta(days=4))},
        ).scalar_one()
        assert old_total == 30


def test_backfill_runs_only_once(legacy_engine):
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)
    with legacy_engine.begin() as conn:
        # Вторая волна «исторических» строк уже НЕ будет помечена:
        # метка деплоя проставлена один раз.
        now = as_local_naive(now_msk()) - timedelta(days=1)
        conn.execute(
            text(
                "INSERT INTO provider_leads "
                "(vid, lead_source, prov_created_at, imported_at, project_id) "
                "VALUES ('late-1', 'provider', :when, :when, 1)"
            ),
            {"when": now},
        )

    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)

    with legacy_engine.begin() as conn:
        marked = conn.execute(
            text(
                "SELECT COUNT(*) FROM provider_leads "
                "WHERE lead_source = 'provider' AND provider_sheet_exported_at IS NOT NULL"
            )
        ).scalar_one()
        # 30 (первый backfill) + 0 (повторный запуск ничего не трогает)
        assert marked == 30
        state = conn.execute(
            text("SELECT COUNT(*) FROM provider_export_backfill_state")
        ).scalar_one()
        assert state == 1, "служебная метка деплоя должна быть ровно одна"


def test_new_rows_after_deploy_stay_pending(legacy_engine):
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)
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

        # Без границы очереди новые лиды смешиваются с до-деплойной историей.
        pending_all = export_mod._fetch_pending_provider_leads(s, 500)
        assert "brand-new" in [row.vid for row in pending_all]
        assert "old-0" in [row.vid for row in pending_all], (
            "до-деплойная история остаётся NULL по дефолту — "
            "именно граница очереди не даёт ей уйти в Google повторно"
        )

        # С границей в очередь попадает ТОЛЬКО то, что пришло после деплоя.
        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=queue_started_at)
        assert [row.vid for row in pending] == ["brand-new"], (
            "новые лиды после деплоя попадают в очередь выгрузки, "
            "а исторические строки повторно не отправляются"
        )


def test_queue_boundary_survives_repeated_runs(legacy_engine):
    """Граница очереди фиксируется один раз и не «ползит» с каждым запуском."""
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)
    first = export_mod._load_queue_started_at(legacy_engine)
    second = export_mod._load_queue_started_at(legacy_engine)
    assert first == second


def test_pixel_rows_never_enter_provider_queue(legacy_engine):
    """Pixel-контур полностью изолирован: его лиды не попадают в provider-очередь."""
    export_mod._ensure_provider_export_state(legacy_engine, lookback_days=3)
    queue_started_at = export_mod._load_queue_started_at(legacy_engine)

    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine, future=True)
    with LocalSession() as s:
        pending = export_mod._fetch_pending_provider_leads(s, 500, queue_started_at=queue_started_at)
        assert all(row.lead_source == "provider" for row in pending)
        assert all("pixel" not in (row.vid or "") for row in pending)
