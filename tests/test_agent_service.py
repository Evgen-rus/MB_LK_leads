"""Focused tests for safe, read-only Agent Interface service responses."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SHEETS_TZ", "Europe/Moscow")

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.app import crud, models  # noqa: E402
from backend.app.agent_api.contracts import AgentError  # noqa: E402
from backend.app.agent_api.service import execute_read  # noqa: E402


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture()
def seeded(session):
    session.add_all([
        models.User(id=1, login="admin", password_hash="x", display_name="Admin", role="admin"),
        models.User(id=2, login="rio-login", password_hash="x", display_name="Рио-Люкс", role="client"),
    ])
    session.add(models.ClientProfile(
        user_id=2, name="Рио-Люкс", inn="PROFILE_INN_SECRET", phone="PROFILE_PHONE_SECRET",
        contact="PROFILE_CONTACT_SECRET", table_url="PROFILE_URL_SECRET", pixel_table_url="PIXEL_URL_SECRET",
    ))
    session.add(models.Project(
        id=10, user_id=2, name="B1_Проект Рио", tag="PROJECT_TAG_SECRET",
        collection_source="Сайты", data_source_code="B1", status="Активен",
        delivery_status="Активна", data_limit=50, numbers_today=2, numbers_total=2,
        days_received="Пн. Вт.", sources_count=2, is_top=True,
        sites=["PROJECT_SITE_SECRET"], phones=["PROJECT_PHONE_SECRET"],
        sms_sender_name="PROJECT_SENDER_SECRET",
        created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
    ))
    session.add_all([
        models.ProviderLead(
            vid="lead-1", phone="LEAD_PHONE_SECRET", phones_raw=["LEAD_PHONE_SECRET"],
            project_name="B1_Проект Рио", prov_created_at=datetime(2026, 9, 3, 9),
            prov_chanel="B1", prov_source="source", subdomain="subdomain",
            imported_at=datetime(2026, 9, 3, 10), project_id=10,
        ),
        models.ProviderLead(
            vid="lead-2", phone="ANOTHER_PHONE_SECRET", project_name="B1_Проект Рио",
            prov_created_at=datetime(2026, 9, 4, 9), prov_chanel="B1", prov_source="source",
            imported_at=datetime(2026, 9, 4, 10), project_id=10,
        ),
        models.ClientBalanceOperation(
            client_id=2, amount=100, op_type="credit", comment="BALANCE_COMMENT_SECRET",
            created_by=1, created_at=datetime(2026, 9, 1),
        ),
        models.ProjectOperationEvent(
            user_id=2, actor_user_id=1, project_id=10, operation="update", status="failed",
            project_name="B1_Проект Рио", error_message="PROVIDER_ERROR_SECRET",
            created_at=datetime(2026, 9, 4, 11),
        ),
    ])
    session.commit()
    return session


SETTINGS = {"SHEETS_TZ": "Europe/Moscow"}
PERIOD = {"from_date": "2026-09-01", "to_date": "2026-09-30"}


def _raw_dashboard(db, client_id=None):
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()

    def day(value):
        return datetime.combine(value, time.min)

    today_start = day(today)
    return crud.admin_dashboard(
        db,
        start_local=datetime(2026, 9, 1), end_local=datetime(2026, 10, 1),
        today_start=today_start, today_end=day(today + timedelta(days=1)),
        yesterday_start=day(today - timedelta(days=1)), yesterday_end=today_start,
        last7_start=day(today - timedelta(days=6)), last30_start=day(today - timedelta(days=29)),
        chart_start=day(today - timedelta(days=29)), chart_end=day(today + timedelta(days=1)),
        client_id=client_id,
    )


def test_overview_matches_admin_aggregates_and_drops_error_text(seeded):
    expected = _raw_dashboard(seeded)
    actual = execute_read("overview", seeded, PERIOD, SETTINGS)

    assert actual["summary"]["leads_period"] == expected.summary.leadsPeriod == 2
    assert actual["summary"]["projects"] == expected.summary.projects == 1
    assert actual["summary"]["total_remaining"] == expected.summary.totalRemaining == 98
    assert actual["attention"]["operation_errors"] == {"total": expected.attention.operationErrors.total}
    assert "PROVIDER_ERROR_SECRET" not in json.dumps(actual)


def test_client_and_project_reads_use_curated_json_and_existing_stats(seeded):
    found = execute_read("clients.find", seeded, {"q": "рИО", **PERIOD}, SETTINGS)
    shown = execute_read("client.show", seeded, {"client_id": 2, **PERIOD}, SETTINGS)
    projects = execute_read("projects.list", seeded, {"client_id": 2, **PERIOD}, SETTINGS)
    project = execute_read("project.show", seeded, {"project_id": 10}, SETTINGS)
    project_stats = execute_read("project.stats", seeded, {"project_id": 10, **PERIOD}, SETTINGS)
    client_stats = execute_read("leads.stats", seeded, {"client_id": 2, **PERIOD}, SETTINGS)
    recent_expected = _raw_dashboard(seeded, client_id=2).summary.leads30Days

    assert found["total"] == 1 and found["items"][0]["client_id"] == 2
    assert shown["projects"] == 1 and shown["leads_30d"] == recent_expected and shown["remaining"] == 98
    assert projects["total"] == 1 and projects["items"][0]["project_id"] == 10
    assert project["client_id"] == 2 and project["name"] == "B1_Проект Рио"
    assert project_stats["total"] == client_stats["total"] == 2
    assert project_stats["daily"] == client_stats["daily"]

    output = json.dumps([found, shown, projects, project, project_stats, client_stats], ensure_ascii=False)
    for secret in (
        "PROFILE_INN_SECRET", "PROFILE_PHONE_SECRET", "PROFILE_CONTACT_SECRET", "PROFILE_URL_SECRET",
        "PIXEL_URL_SECRET", "PROJECT_TAG_SECRET", "PROJECT_SITE_SECRET", "PROJECT_PHONE_SECRET",
        "PROJECT_SENDER_SECRET", "LEAD_PHONE_SECRET", "ANOTHER_PHONE_SECRET", "BALANCE_COMMENT_SECRET",
    ):
        assert secret not in output


def test_reads_do_not_write_and_missing_entities_have_stable_errors(seeded):
    writes = []
    engine = seeded.get_bind()

    def collect(_conn, _cursor, statement, _parameters, _context, _many):
        sql = statement.lstrip().casefold()
        if sql.startswith(("insert ", "update ", "delete ")):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", collect)
    try:
        execute_read("clients.list", seeded, PERIOD, SETTINGS)
        execute_read("projects.list", seeded, {"client_id": 2, **PERIOD}, SETTINGS)
        execute_read("project.stats", seeded, {"project_id": 10, **PERIOD}, SETTINGS)
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert writes == []

    for action, params, code in (
        ("projects.list", {"client_id": 99, **PERIOD}, "CLIENT_NOT_FOUND"),
        ("project.show", {"project_id": 99}, "PROJECT_NOT_FOUND"),
    ):
        with pytest.raises(AgentError) as raised:
            execute_read(action, seeded, params, SETTINGS)
        assert raised.value.code == code
        assert raised.value.status == 404


@pytest.mark.parametrize(
    "params",
    [
        {"from_date": "2026-09-02", "to_date": "2026-09-01"},
        {"from_date": "2025-01-01", "to_date": "2026-01-02"},
        {"from_date": "09/01/2026", "to_date": "2026-09-30"},
        {"from_date": "9999-12-31", "to_date": "9999-12-31"},
    ],
)
def test_date_range_validation_is_inclusive_and_bounded(seeded, params):
    with pytest.raises(AgentError) as raised:
        execute_read("leads.stats", seeded, {"client_id": 2, **params}, SETTINGS)
    assert raised.value.code == "INVALID_DATE_RANGE"
    assert raised.value.status == 422


def test_lists_are_paginated_and_find_does_not_guess(seeded):
    first = execute_read("clients.list", seeded, {**PERIOD, "limit": 1, "offset": 0}, SETTINGS)
    no_match = execute_read("clients.find", seeded, {"q": "Рио-люкс плюс", **PERIOD}, SETTINGS)
    assert first["total"] == 1 and len(first["items"]) == 1
    assert no_match["total"] == 0 and no_match["items"] == []
