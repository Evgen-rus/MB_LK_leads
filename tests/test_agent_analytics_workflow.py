"""Synthetic end-to-end tests for the agent analytics repeat workflow."""
from datetime import datetime
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import models
from backend.app.agent_api.router import build_app
from backend.app.agent_api import analytics_workflow
from backend.app.lead_analytics import db, pipeline as analysis_pipeline, router
from backend.app.lead_analytics.models import ColumnMapping, StatusRule
from backend.app.lead_analytics.status_classifier import ALL_GROUPS


def _client_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "Client"
    ws.append(["Дата", "Телефон", "Статус"])
    ws.append([datetime(2026, 9, 3), "+7 900 000-00-01", "Новый статус"])
    stream = BytesIO()
    wb.save(stream)
    return stream.getvalue()


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("LK_AGENT_READ_TOKEN", "synthetic-read")
    monkeypatch.setenv("LK_AGENT_COMPUTE_TOKEN", "synthetic-compute")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    monkeypatch.setattr(router, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(router, "start_worker", lambda: None)
    monkeypatch.setattr("backend.app.lead_analytics.export_history.ANALYSIS_REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(analysis_pipeline, "ANALYSIS_REPORTS_DIR", tmp_path / "reports")
    db.init_db()

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        session.add(models.User(id=2, login="synthetic-client", role="client", password_hash="unused"))
        session.add_all([
            models.Project(id=20, user_id=2, name="B1_[LR223] Current project", tag="x",
                           collection_source="Сайты", data_source_code="B1", status="Активен",
                           delivery_status="Активна", data_limit=10),
            models.Project(id=21, user_id=2, name="B1_[LR223] Deleted candidate", tag="x",
                           collection_source="Сайты", data_source_code="B1", status="Удалён",
                           delivery_status="Неактивна", data_limit=10),
            models.ProviderLead(id=1, vid="synthetic-vid", lead_source="provider", phone="+7 900 000-00-01",
                                project_name="B1_[LR223] Current project", prov_created_at=datetime(2026, 9, 3),
                                prov_chanel="B1", prov_source="Search", subdomain="example.test",
                                imported_at=datetime(2026, 9, 3, 10), project_id=20),
        ])
        session.commit()

    def get_db():
        with sessions() as session:
            yield session

    group_id = db.create_group(2, "Synthetic group", [20],
                               "https://docs.google.com/spreadsheets/d/synthetic-sheet/edit")
    key = db.group_key(group_id)
    db.ensure_project(key)
    db.save_match_mapping(key, "lk", ColumnMapping(
        sheet_name="leads", date_column="Дата", phone_column="Телефон", source_column="Источники",
        lkid_column="lk id",
    ))
    db.save_match_mapping(key, "client", ColumnMapping(
        sheet_name="Client", date_column="Дата", phone_column="Телефон", status_column="Статус",
    ))
    db.save_column_mapping(key, ColumnMapping(
        sheet_name="Сопоставленные", date_column="Дата клиента", phone_column="Телефон",
        status_column="Статус клиента",
    ))
    db.add_status_rule(StatusRule(pattern="Known", match_type="exact", group_name="Качественные", project_code=key))
    monkeypatch.setattr(router, "read_spreadsheet_url",
                        lambda url: (_client_xlsx(), "synthetic.xlsx", "Client"))
    display = (
        lambda value: {"B1": "A", "B2": "B", "B3": "C", "B4": "D"}.get(value, value or ""),
        lambda value: value or "",
        lambda value: value or "",
    )
    app = FastAPI()
    app.mount("/agent/v1", build_app(get_db, {"SHEETS_TZ": "Europe/Moscow"}, analytics_display=display))
    yield TestClient(app), group_id
    engine.dispose()


def _headers(compute=False):
    return {"Authorization": "Bearer synthetic-compute" if compute else "Bearer synthetic-read"}


def _run_synthetic_worker():
    assert router.JOB_WORKER_LOCK.acquire(blocking=False)
    router._worker()


def test_plan_requires_explicit_candidate_confirmation_and_includes_deleted(configured):
    client, group_id = configured
    plan = client.get(f"/agent/v1/analytics.plan?group_id={group_id}", headers=_headers()).json()["data"]
    assert plan["settings_ready"]
    assert [item["project_id"] for item in plan["new_projects"]] == [21]
    assert plan["new_projects"][0]["status"] == "Удалён"
    response = client.post("/agent/v1/analytics.prepare", headers=_headers(True), json={
        "group_id": group_id, "period_start": "2026-09-01", "period_end": "2026-09-30",
    })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_CONFIRMATION_REQUIRED"
    assert response.json()["state"] == "needs_input"
    assert db.get_group(group_id)["project_ids"] == [20]


def test_prepare_match_confirm_run_and_result_use_real_queue(configured):
    client, group_id = configured
    response = client.post("/agent/v1/analytics.prepare", headers=_headers(True), json={
        "group_id": group_id, "period_start": "2026-09-01", "period_end": "2026-09-30",
        "confirmed_project_ids": [21],
    })
    assert response.status_code == 200, response.text
    prepared = response.json()["data"]
    assert prepared["added_project_ids"] == [21]
    assert prepared["excluded_candidate_project_ids"] == []
    assert prepared["periods"] == [{"period_start": "2026-09-01", "period_end": "2026-09-30"}]
    assert prepared["job"]["kind"] == "match" and prepared["job"]["status"] == "queued"
    assert db.get_group(group_id)["project_ids"] == [20, 21]

    _run_synthetic_worker()
    assert db.get_run(prepared["run_id"])["status"] == "matched"
    with db.connect() as conn:
        job = conn.execute("SELECT id FROM processing_jobs WHERE run_id=?", (prepared["run_id"],)).fetchone()
    assert db.get_processing_job(int(job["id"]))["status"] == "completed"

    unknown = client.post("/agent/v1/analytics.run", headers=_headers(True), json={"run_id": prepared["run_id"]})
    assert unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "UNKNOWN_STATUSES"
    assert unknown.json()["data"]["statuses"] == ["Новый статус"]
    assert unknown.json()["data"]["allowed_categories"] == ALL_GROUPS
    assert not db.has_active_run_job(prepared["run_id"])

    confirmed = client.post("/agent/v1/analytics.confirm-statuses", headers=_headers(True), json={
        "group_id": group_id, "run_id": prepared["run_id"],
        "status_rules": {"Новый статус": "Качественные"},
    })
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["analysis_queued"] is False
    assert not db.has_active_run_job(prepared["run_id"])

    queued = client.post("/agent/v1/analytics.run", headers=_headers(True), json={"run_id": prepared["run_id"]})
    assert queued.status_code == 200, queued.text
    assert queued.json()["data"]["job"]["kind"] == "analyze"
    _run_synthetic_worker()
    result = client.get(f"/agent/v1/analytics.result?group_id={group_id}&run_id={prepared['run_id']}",
                        headers=_headers()).json()
    assert result["ok"]
    assert result["data"]["status"] == "completed"
    assert result["data"]["periods"] == [{"period_start": "2026-09-01", "period_end": "2026-09-30"}]
    assert result["data"]["result"]["total_count"] == 1
    assert "synthetic-vid" not in str(result)


def test_status_confirmation_rejects_noncurrent_or_invalid_categories(configured):
    client, group_id = configured
    response = client.post("/agent/v1/analytics.prepare", headers=_headers(True), json={
        "group_id": group_id, "period_start": "2026-09-01", "period_end": "2026-09-30",
        "confirmed_project_ids": [],
    })
    run_id = response.json()["data"]["run_id"]
    _run_synthetic_worker()
    wrong_status = client.post("/agent/v1/analytics.confirm-statuses", headers=_headers(True), json={
        "group_id": group_id, "run_id": run_id, "status_rules": {"Guessed": "Качественные"},
    })
    assert wrong_status.status_code == 409
    assert wrong_status.json()["error"]["code"] == "UNKNOWN_STATUSES"
    invalid_category = client.post("/agent/v1/analytics.confirm-statuses", headers=_headers(True), json={
        "group_id": group_id, "run_id": run_id, "status_rules": {"Новый статус": "Invented"},
    })
    assert invalid_category.status_code == 422
    assert invalid_category.json()["error"]["code"] == "INVALID_STATUS_CATEGORY"


def test_prepare_lock_blocks_duplicate_and_releases_after_needs_input(configured, monkeypatch):
    client, group_id = configured
    google_reads = []
    monkeypatch.setattr(router, "read_spreadsheet_url", lambda url: google_reads.append(url))
    assert analytics_workflow._PREPARE_LOCK.acquire(blocking=False)
    try:
        busy = client.post("/agent/v1/analytics.prepare", headers=_headers(True), json={
            "group_id": group_id, "period_start": "2026-09-01", "period_end": "2026-09-30",
            "confirmed_project_ids": [],
        })
    finally:
        analytics_workflow._PREPARE_LOCK.release()
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "RUN_BUSY"
    assert google_reads == []

    needs_confirmation = client.post("/agent/v1/analytics.prepare", headers=_headers(True), json={
        "group_id": group_id, "period_start": "2026-09-01", "period_end": "2026-09-30",
    })
    assert needs_confirmation.status_code == 409
    assert needs_confirmation.json()["error"]["code"] == "PROJECT_CONFIRMATION_REQUIRED"
    assert not analytics_workflow._PREPARE_LOCK.locked()
    assert google_reads == []
