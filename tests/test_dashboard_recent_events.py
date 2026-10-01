"""Regression tests for bounded client dashboard recent activity."""

from __future__ import annotations

import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SHEETS_TZ", "Europe/Moscow")

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.app import crud, models  # noqa: E402


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
    with local_session() as db:
        yield db
    engine.dispose()


def _user(db, user_id: int, role: str = "client") -> None:
    db.add(models.User(
        id=user_id,
        login=f"user{user_id}",
        password_hash="x",
        display_name=f"Пользователь {user_id}",
        role=role,
        created_at=datetime(2026, 1, 1),
    ))


def _project(db, project_id: int, owner_id: int, *, name: str = "B1_Проект") -> None:
    db.add(models.Project(
        id=project_id,
        user_id=owner_id,
        name=name,
        tag="tag",
        client_internal_prefix="LR223",
        collection_source="Сайты",
        data_source_code="B1",
        status="Активен",
        delivery_status="Активна",
        data_limit=0,
        numbers_today=0,
        numbers_total=0,
        days_received="",
        sources_count=0,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    ))


def _seed_event_sources(db, *, client_id: int = 2, count: int = 1, base: datetime) -> None:
    """Insert count events per source; callers provide the desired time range."""
    for index in range(count):
        created_at = base + timedelta(seconds=index)
        db.add_all([
            models.AuditEvent(
                user_id=client_id,
                actor_user_id=1,
                project_id=10,
                action="update",
                created_at=created_at,
            ),
            models.ProjectOperationEvent(
                user_id=client_id,
                actor_user_id=1,
                project_id=10,
                operation="update",
                status="failed",
                project_name="B1_LR223_Проект",
                error_message="Не удалось обновить",
                created_at=created_at,
            ),
            models.ClientBalanceOperation(
                client_id=client_id,
                amount=1,
                op_type="credit",
                comment="Пополнение",
                created_by=1,
                created_at=created_at,
            ),
            models.ReportExport(
                user_id=1,
                target_client_id=client_id,
                created_at=created_at,
                from_date="2026-01-01",
                to_date="2026-01-02",
                format="csv",
            ),
        ])
    db.commit()


def _dump(items):
    return [item.model_dump() for item in items]


def test_recent_events_match_legacy_for_mixed_sources_and_timestamp_ties(session):
    _user(session, 1, "admin")
    _user(session, 2)
    _project(session, 10, 2, name="B1_LR223_Проект")
    stamp = datetime(2026, 9, 1, 12, 0, 0)

    # Seven equal-time rows exercise the old stable source order and the new
    # per-source id tie-breaker; the one-microsecond-newer audit stays first.
    session.add_all([
        models.AuditEvent(user_id=2, actor_user_id=1, project_id=10, action="update", created_at=stamp + timedelta(microseconds=2)),
        models.AuditEvent(user_id=2, actor_user_id=1, project_id=10, action="update", created_at=stamp + timedelta(microseconds=1)),
        models.ProjectOperationEvent(user_id=2, actor_user_id=1, project_id=10, operation="update", status="failed", project_name="B1_LR223_Проект", error_message="Ошибка", created_at=stamp + timedelta(microseconds=1)),
        models.ClientBalanceOperation(client_id=2, amount=5, op_type="credit", comment="Баланс", created_by=1, created_at=stamp + timedelta(microseconds=1)),
        *[
            models.ReportExport(user_id=1, target_client_id=2, created_at=stamp + timedelta(microseconds=1), from_date="2026-09-01", to_date="2026-09-02", format="csv")
            for _ in range(4)
        ],
    ])
    session.commit()

    legacy = crud.list_client_activity_events(session, client_id=2, offset=0, limit=5).items
    recent = crud.list_client_recent_activity_events(session, client_id=2, limit=5)

    assert _dump(recent) == _dump(legacy)
    assert [item.eventId.split("-")[0] for item in recent] == ["AE", "AE", "POE", "BO", "RE"]


def test_recent_events_match_legacy_when_one_source_has_many_equal_timestamps(session):
    _user(session, 1, "admin")
    _user(session, 2)
    _project(session, 10, 2)
    stamp = datetime(2026, 9, 1, 12)
    session.add_all([
        models.AuditEvent(user_id=2, actor_user_id=1, project_id=10, action="update", created_at=stamp)
        for _ in range(9)
    ])
    session.commit()

    legacy = crud.list_client_activity_events(session, client_id=2, offset=0, limit=5).items
    recent = crud.list_client_recent_activity_events(session, client_id=2, limit=5)

    assert _dump(recent) == _dump(legacy)
    assert [item.eventId for item in recent] == [f"AE-{event_id}" for event_id in range(1, 6)]


def test_recent_events_respect_project_owner_and_clean_internal_prefixes(session):
    _user(session, 1, "admin")
    _user(session, 2)
    _user(session, 3)
    _project(session, 10, 2, name="B1_LR223_КлиентскийПроект")
    _project(session, 20, 3, name="B1_LR223_ЧужойПроект")
    stamp = datetime(2026, 9, 1, 12)
    session.add_all([
        # Audit actor/user attribution is admin; project ownership grants the event to client 2.
        models.AuditEvent(user_id=1, actor_user_id=1, project_id=10, action="update", created_at=stamp),
        # Reverse mismatch must not expose another client's project to client 2.
        models.AuditEvent(user_id=2, actor_user_id=1, project_id=20, action="update", created_at=stamp + timedelta(seconds=1)),
        models.AuditEvent(user_id=2, actor_user_id=1, project_id=None, action="blacklist_add", created_at=stamp + timedelta(seconds=8)),
        models.AuditEvent(user_id=3, actor_user_id=1, project_id=None, action="blacklist_add", created_at=stamp + timedelta(seconds=9)),
        models.ProjectOperationEvent(user_id=2, actor_user_id=1, project_id=10, operation="update", status="failed", project_name="B1_LR223_КлиентскийПроект", error_message="Ошибка", created_at=stamp + timedelta(seconds=2)),
        models.ProjectOperationEvent(user_id=3, actor_user_id=1, project_id=20, operation="update", status="failed", project_name="ЧужойПроект", error_message="Ошибка", created_at=stamp + timedelta(seconds=3)),
        models.ClientBalanceOperation(client_id=2, amount=1, op_type="credit", comment="Баланс", created_by=1, created_at=stamp + timedelta(seconds=4)),
        models.ClientBalanceOperation(client_id=3, amount=1, op_type="credit", comment="Чужой баланс", created_by=1, created_at=stamp + timedelta(seconds=5)),
        models.ReportExport(user_id=1, target_client_id=2, created_at=stamp + timedelta(seconds=6), from_date="2026-09-01", to_date="2026-09-02", format="csv"),
        models.ReportExport(user_id=1, target_client_id=3, created_at=stamp + timedelta(seconds=7), from_date="2026-09-01", to_date="2026-09-02", format="csv"),
    ])
    session.commit()

    recent = crud.list_client_recent_activity_events(session, client_id=2)
    by_id = {item.eventId: item for item in recent}

    assert set(by_id) == {"AE-1", "AE-3", "POE-1", "BO-1", "RE-1"}
    assert by_id["AE-1"].projectName == "B1_КлиентскийПроект"
    assert by_id["POE-1"].projectName == "B1_КлиентскийПроект"
    assert "LR223" not in by_id["AE-1"].description
    assert "LR223" not in by_id["POE-1"].description


def test_recent_events_empty_history_and_dashboard_uses_fast_path(session, monkeypatch):
    _user(session, 2)
    assert crud.list_client_recent_activity_events(session, client_id=2) == []
    assert crud.list_client_activity_events(session, client_id=2, offset=0, limit=5).items == []

    def legacy_must_not_run(*args, **kwargs):
        raise AssertionError("dashboard must use the bounded recent-events helper")

    monkeypatch.setattr(crud, "list_client_activity_events", legacy_must_not_run)
    start = datetime(2026, 9, 1)
    result = crud.client_dashboard(
        session,
        client_id=2,
        start_local=start,
        end_local=start + timedelta(days=1),
        today_start=start,
        today_end=start + timedelta(days=1),
        last7_start=start - timedelta(days=6),
        last30_start=start - timedelta(days=29),
        chart_start=start,
        chart_end=start + timedelta(days=1),
    )
    assert result.recentEvents == []


def test_public_activity_history_keeps_filters_total_and_offset(session):
    _user(session, 1, "admin")
    _user(session, 2)
    _project(session, 10, 2)
    base = datetime(2026, 1, 1)
    _seed_event_sources(session, count=3, base=base)

    page = crud.list_client_activity_events(
        session,
        client_id=2,
        offset=1,
        limit=1,
        start_local=base + timedelta(seconds=1),
        end_local=base + timedelta(seconds=2),
        entities=["balance"],
        q="Пополнение",
    )

    assert page.total == 2
    assert [item.eventId for item in page.items] == ["BO-2"]


@pytest.mark.parametrize(("events_per_source", "history_rows"), [(10, 40), (1000, 4000)])
def test_recent_events_load_at_most_five_rows_per_source(
    session, events_per_source, history_rows
):
    _user(session, 1, "admin")
    _user(session, 2)
    _project(session, 10, 2)
    _seed_event_sources(session, count=events_per_source, base=datetime(2026, 1, 1))

    # Save a legacy baseline and median timings without a flaky speed threshold.
    def measure(callable_):
        samples = []
        for _ in range(3):
            session.expunge_all()
            started = time.perf_counter()
            callable_()
            samples.append(time.perf_counter() - started)
        return statistics.median(samples)

    legacy_seconds = measure(lambda: crud.list_client_activity_events(session, 2, 0, 5))
    recent_seconds = measure(lambda: crud.list_client_recent_activity_events(session, 2, 5))

    session.expunge_all()
    legacy = crud.list_client_activity_events(session, 2, 0, 5).items
    session.expunge_all()
    loaded = Counter()
    source_models = (
        models.AuditEvent,
        models.ProjectOperationEvent,
        models.ClientBalanceOperation,
        models.ReportExport,
        models.Project,
    )

    def on_load(instance, _context):
        loaded[type(instance)] += 1

    statements = []

    def on_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.casefold())

    engine = session.get_bind()
    for model in source_models:
        event.listen(model, "load", on_load)
    event.listen(engine, "before_cursor_execute", on_statement)
    try:
        recent = crud.list_client_recent_activity_events(session, client_id=2, limit=5)
    finally:
        event.remove(engine, "before_cursor_execute", on_statement)
        for model in source_models:
            event.remove(model, "load", on_load)

    assert len(recent) == 5
    assert _dump(recent) == _dump(legacy)
    for model in source_models[:-1]:
        assert loaded[model] <= 5
    assert loaded[models.Project] <= 5
    assert sum(loaded[model] for model in source_models[:-1]) <= 20

    source_tables = (
        "from audit_events",
        "from project_operation_events",
        "from client_balance_operations",
        "from report_exports",
    )
    for source in source_tables:
        sql = [statement for statement in statements if source in statement]
        assert len(sql) == 1 and " limit " in sql[0], (source, sql)

    print(
        "{} history rows: legacy median={:.3f}ms; recent median={:.3f}ms; "
        "ORM events loaded={}; per-source SQL LIMIT=5".format(
            history_rows,
            legacy_seconds * 1000,
            recent_seconds * 1000,
            sum(loaded[model] for model in source_models[:-1]),
        )
    )

