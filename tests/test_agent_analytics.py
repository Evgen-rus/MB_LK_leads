"""Synthetic prepared-run tests; worker and external input readers never run."""
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models
from backend.app.agent_api.router import build_app
from backend.app.lead_analytics import db, router
from backend.app.lead_analytics.models import ColumnMapping, StatusRule


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("LK_AGENT_READ_TOKEN", "synthetic-read")
    monkeypatch.setenv("LK_AGENT_COMPUTE_TOKEN", "synthetic-compute")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    monkeypatch.setattr(router, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(router, "start_worker", lambda: None)
    db.init_db()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        session.add(models.User(id=2, login="synthetic-client", role="client", password_hash="unused"))
        session.add(models.Project(id=20, user_id=2, name="Synthetic project", tag="synthetic",
                                   collection_source="Сайты", data_source_code="B1", status="Активен",
                                   delivery_status="Активна", data_limit=10))
        session.commit()

    def get_db():
        with sessions() as session:
            yield session

    group_id = db.create_group(2, "Synthetic group", [20], "https://example.invalid/private")
    db.create_run({"id": "synthetic-run", "group_id": group_id, "client_id": 2, "group_name": "Synthetic group",
                   "project_ids": [20], "project_names": {"20": "Synthetic project"},
                   "periods": [{"period_start": "2026-09-01", "period_end": "2026-09-30"}],
                   "settings": {}, "lk_snapshot": "input/lk.xlsx", "client_snapshot": "input/client.xlsx",
                   "source_file_name": "private-file.xlsx", "status": "matched"})
    output = router.RUNS_DIR / "synthetic-run" / "output"
    output.mkdir(parents=True)
    workbook = Workbook()
    workbook.active.title = "Joined"
    workbook.active.append(["Дата", "Статус", "Телефон"])
    workbook.active.append([datetime(2026, 9, 2), "Known", "+70000000000"])
    workbook.save(output / "joined_сопоставление.xlsx")
    key = db.group_key(group_id)
    db.ensure_project(key)
    db.save_column_mapping(key, ColumnMapping(sheet_name="Joined", date_column="Дата", status_column="Статус", phone_column="Телефон"))
    db.add_status_rule(StatusRule(pattern="Known", match_type="exact", group_name="Качественные", project_code=key))
    app = FastAPI()
    app.mount("/agent/v1", build_app(get_db, {"SHEETS_TZ": "Europe/Moscow"}))
    yield TestClient(app), group_id
    engine.dispose()


def h(compute=False):
    return {"Authorization": "Bearer synthetic-compute" if compute else "Bearer synthetic-read"}


@pytest.mark.parametrize("action,params", [
    ("overview", {}), ("clients.list", {}), ("clients.find", {"q": "synthetic"}),
    ("client.show", {"client_id": 2}), ("projects.list", {"client_id": 2}),
    ("project.show", {"project_id": 20}), ("project.stats", {"project_id": 20}),
    ("leads.stats", {"client_id": 2}),
])
def test_read_http_composes_real_crud_with_envelope(prepared, action, params):
    client, _ = prepared
    if action != "project.show":
        params = {**params, "from_date": "2026-09-01", "to_date": "2026-09-30"}
    response = client.get(f"/agent/v1/{action}", params=params, headers=h())
    assert response.status_code == 200, response.text
    assert response.json()["ok"] and response.json()["action"] == action
    assert response.json()["request_id"] == response.headers["X-Request-Id"]
    assert isinstance(response.json()["data"], dict)


def test_groups_history_and_safe_result(prepared):
    client, group = prepared
    groups = client.get("/agent/v1/analytics.groups?client_id=2", headers=h()).json()
    assert groups["data"]["items"][0]["id"] == group
    assert "example.invalid" not in str(groups)
    assert client.get(f"/agent/v1/analytics.history?group_id={group}", headers=h()).json()["data"]["items"] == []
    response = client.get(f"/agent/v1/analytics.result?group_id={group}&run_id=synthetic-run", headers=h())
    assert response.json()["data"]["status"] == "matched"
    assert "private-file" not in response.text and "+70000000000" not in response.text
    assert client.get(f"/agent/v1/analytics.result?group_id={group}&export_id=99", headers=h()).json()["error"]["code"] == "RESULT_NOT_FOUND"


def test_existing_report_returns_aggregates_only(prepared):
    client, group = prepared
    with db.connect() as conn:
        cursor = conn.execute("""INSERT INTO analysis_exports(project_code,export_number,period_start,period_end,
            analysis_date,source_file_name,run_id,total_count,quality_count,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (db.group_key(group), 1, "2026-09-01", "2026-09-30", "2026-10-01", "private-file.xlsx",
             "synthetic-run", 12, 9, db.now_text(), db.now_text()))
        export_id = cursor.lastrowid
    history = client.get(f"/agent/v1/analytics.history?group_id={group}", headers=h()).json()
    assert history["data"]["total"] == 1
    response = client.get(f"/agent/v1/analytics.result?group_id={group}&export_id={export_id}", headers=h())
    assert response.json()["data"]["total_count"] == 12
    assert response.json()["data"]["quality_count"] == 9
    assert response.json()["data"]["periods"][0]["total_count"] == 12
    assert "private-file" not in response.text and "settings" not in response.text


def test_fresh_run_needs_human_preparation(prepared):
    client, group = prepared
    response = client.post("/agent/v1/analytics.run", headers=h(True), json={"group_id": group, "period_start": "2026-09-01", "period_end": "2026-09-30"})
    assert response.json()["state"] == "needs_input"
    assert response.json()["error"]["code"] == "PREPARATION_REQUIRED"
    assert not db.has_active_run_job("synthetic-run")


def test_prepared_run_uses_shared_queue_and_preserves_settings(prepared, monkeypatch):
    client, group = prepared
    before = db.list_project_status_rules(db.group_key(group))
    monkeypatch.setattr(db, "save_column_mapping", lambda *a: pytest.fail("Agent must not change mapping"))
    monkeypatch.setattr(db, "add_status_rule", lambda *a: pytest.fail("Agent must not change rules"))
    response = client.post("/agent/v1/analytics.run", headers=h(True), json={"group_id": group, "run_id": "synthetic-run"})
    assert response.status_code == 200, response.text
    job_id = response.json()["data"]["job"]["id"]
    job = db.get_processing_job(job_id)
    assert job["kind"] == "analyze" and job["status"] == "queued"
    assert job["payload"]["storage_key"] == db.group_key(group)
    assert job["payload"]["periods"] == db.get_run("synthetic-run")["periods"]
    assert db.list_project_status_rules(db.group_key(group)) == before
    repeat = client.post("/agent/v1/analytics.run", headers=h(True), json={"run_id": "synthetic-run"})
    assert repeat.json()["data"]["already_active"]
    assert repeat.json()["data"]["job"]["id"] == job_id


def test_unknown_statuses_do_not_guess_or_enqueue(prepared):
    client, group = prepared
    with db.connect() as conn:
        conn.execute("DELETE FROM status_rules")
    response = client.post("/agent/v1/analytics.run", headers=h(True), json={"run_id": "synthetic-run"})
    assert response.status_code == 409
    assert response.json()["state"] == "needs_input"
    assert response.json()["error"]["code"] == "UNKNOWN_STATUSES"
    assert response.json()["data"]["statuses"] == ["Known"]
    assert not db.has_active_run_job("synthetic-run")
    assert db.list_project_status_rules(db.group_key(group)) == []


def test_period_mismatch_and_completed_run_are_not_repeated(prepared):
    client, group = prepared
    mismatch = client.post("/agent/v1/analytics.run", headers=h(True), json={"run_id": "synthetic-run", "period_start": "2026-08-01", "period_end": "2026-08-31"})
    assert mismatch.json()["error"]["code"] == "PERIOD_MISMATCH"
    db.update_run_status("synthetic-run", "completed")
    response = client.post("/agent/v1/analytics.run", headers=h(True), json={"run_id": "synthetic-run"})
    assert response.json()["error"]["code"] == "NEW_RUN_REQUIRED"
    assert not db.has_active_run_job("synthetic-run")
