import uuid

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.lead_analytics import db, export_history
from backend.app.lead_analytics.export_history import ExportMetadata, ExportPeriod, save_analysis_export
from backend.app.lead_analytics.router import build_router


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()
    app = FastAPI()
    app.include_router(build_router(lambda: {"id": 1}, lambda: None,
                                    lambda value: value or "", lambda value: value or "",
                                    lambda value: value or ""))
    return TestClient(app)


def _save_export(group_id, periods, report_name=None):
    key = db.group_key(group_id)
    totals = pd.DataFrame([{
        "Период": "2026-01-01 - 2026-01-31", "Всего идентификаций": 100,
        "Качественные": 4, "Кач. %": 0.04, "Рабочий потенциал": 8,
        "Рабочий потенциал %": 0.08, "Уже наши / купил %": 0.01,
        "Сигнал спроса": 10, "Сигнал спроса %": 0.10, "Недозвон": 20,
        "Недозвон %": 0.20, "Некач. %": 0.70, "Не подходит по гео %": 0.10,
        "Требует проверки %": 0.0,
    }])
    breakdown = pd.DataFrame([{
        "Домен": "example.com", "Канал": "organic", "Всего идентификаций": 10,
        "Качественные": 1, "Кач. %": 0.10, "Недозвон": 2, "Недозвон %": 0.20,
        "Сигнал спроса": 2, "Сигнал спроса %": 0.20,
    }])
    source_breakdown = breakdown.rename(columns={"Домен": "Полный источник"})
    results = [(period, totals, {
        "domain_channel": breakdown, "source_channel": source_breakdown, "channel": breakdown,
    }) for period in periods]
    return save_analysis_export(
        key, ExportMetadata(None, periods), results, report_file_name=report_name,
    )


def _workbook(path, period_label="01.01-31.01"):
    book = Workbook()
    statuses = book.active
    statuses.title = f"Статусы {period_label}"
    statuses.append(["Группа статуса", "Исходный статус", "Количество"])
    statuses.append(["Качественные", "Новый", 2])
    statuses.append(["Недозвон", "Нет ответа", 1])
    data = book.create_sheet(f"Данные {period_label}")
    data.append(["Канал", "Кач. %", "Всего идентификаций", "Кампания"])
    data.append(["A", 0.03, 10, "alpha"])
    data.append(["B", 0.02, 5, "alpha"])
    data.append(["C", 0.01, 15, "alpha"])
    data.append(["D", 0.04, 2, "beta"])
    data.append(["E", 0.05, 3, None])
    book.save(path)


def test_result_api_returns_saved_period_metrics_breakdowns_and_colors(api, tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(export_history, "ANALYSIS_REPORTS_DIR", report_dir)
    group_id = db.create_group(2, "Saved", [20], None)
    periods = [ExportPeriod("2026-01-01", "2026-01-31"), ExportPeriod("2026-02-01", "2026-02-28")]
    export_id = _save_export(group_id, periods)

    response = api.get(f"/admin/analytics/groups/{group_id}/exports/{export_id}/result")

    assert response.status_code == 200, response.text
    result = response.json()
    assert [(period["period_start"], period["period_end"]) for period in result["periods"]] == [
        ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"),
    ]
    first = result["periods"][0]
    assert first["metrics"]["Всего идентификаций"] == 100
    assert first["metrics"]["Рабочий потенциал"] == 8
    assert first["metrics"]["_fills"]["Кач. %"] == "C6EFCE"
    period_id = first["id"]
    assert result["breakdowns"][period_id]["domain_channel"][0]["Домен"] == "example.com"
    assert result["breakdowns"][period_id]["source_channel"][0]["Полный источник"] == "example.com"
    assert result["breakdowns"][period_id]["channel"][0]["Канал"] == "organic"
    assert result["breakdowns"][period_id]["channel"][0]["_fills"]["Кач. %"] == "C6EFCE"


def test_result_api_is_group_scoped(api, tmp_path, monkeypatch):
    monkeypatch.setattr(export_history, "ANALYSIS_REPORTS_DIR", tmp_path / "reports")
    first_group = db.create_group(2, "First", [20], None)
    second_group = db.create_group(2, "Second", [20], None)
    export_id = _save_export(first_group, [ExportPeriod("2026-01-01", "2026-01-31")])

    assert api.get(f"/admin/analytics/groups/{second_group}/exports/{export_id}/result").status_code == 404
    assert api.get(
        f"/admin/analytics/groups/{second_group}/exports/{export_id}/result/rows?table=data"
    ).status_code == 404


def test_rows_api_filters_sorts_then_paginates_and_handles_missing_archive(api, tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(export_history, "ANALYSIS_REPORTS_DIR", report_dir)
    group_id = db.create_group(2, "Rows", [20], None)
    filename = f"{uuid.uuid4().hex}.xlsx"
    _workbook(report_dir / filename)
    export_id = _save_export(group_id, [ExportPeriod("2026-01-01", "2026-01-31")], filename)

    response = api.get(
        f"/admin/analytics/groups/{group_id}/exports/{export_id}/result/rows",
        params={"table": "data", "page": 2, "page_size": 1, "query": "alpha",
                "filters": '{"Кач. %":{"min":2,"max":3}}', "sort": "Всего идентификаций", "direction": "desc"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert response.json()["values"]["Кампания"] == ["", "alpha", "beta"]
    assert response.json()["rows"] == [{"Канал": "B", "Кач. %": 0.02,
                                         "Всего идентификаций": 5, "Кампания": "alpha"}]
    statuses = api.get(
        f"/admin/analytics/groups/{group_id}/exports/{export_id}/result/rows",
        params={"table": "statuses"},
    ).json()
    assert statuses["rows"][0]["Группа статуса"] == "Качественные"
    empty = api.get(
        f"/admin/analytics/groups/{group_id}/exports/{export_id}/result/rows",
        params={"table": "data", "filters": '{"Кампания":{"selected":[""]}}'},
    ).json()
    assert empty["total"] == 1
    assert empty["rows"][0]["Канал"] == "E"

    missing_filename = f"{uuid.uuid4().hex}.xlsx"
    missing_id = _save_export(group_id, [ExportPeriod("2026-01-01", "2026-01-31")], missing_filename)
    missing = api.get(
        f"/admin/analytics/groups/{group_id}/exports/{missing_id}/result/rows?table=data"
    )
    assert missing.status_code == 200
    assert missing.json() == {"columns": [], "rows": [], "values": {}, "total": 0,
                              "page": 1, "page_size": 100, "available": False}


def test_rows_api_reports_missing_saved_period_sheet(api, tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(export_history, "ANALYSIS_REPORTS_DIR", report_dir)
    group_id = db.create_group(2, "Missing period", [20], None)
    filename = f"{uuid.uuid4().hex}.xlsx"
    _workbook(report_dir / filename, "02.01-31.01")
    export_id = _save_export(group_id, [ExportPeriod("2026-01-01", "2026-01-31")], filename)

    response = api.get(
        f"/admin/analytics/groups/{group_id}/exports/{export_id}/result/rows?table=data"
    )

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["rows"] == []


def test_rows_api_validates_percent_bounds_and_sort_columns(api, tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(export_history, "ANALYSIS_REPORTS_DIR", report_dir)
    group_id = db.create_group(2, "Validation", [20], None)
    filename = f"{uuid.uuid4().hex}.xlsx"
    _workbook(report_dir / filename)
    export_id = _save_export(group_id, [ExportPeriod("2026-01-01", "2026-01-31")], filename)

    base = f"/admin/analytics/groups/{group_id}/exports/{export_id}/result/rows"
    assert api.get(base, params={"table": "data", "filters": '{"Кач. %":{"min":101}}'}).status_code == 400
    assert api.get(base, params={"table": "data", "sort": "unknown"}).status_code == 400
    assert api.get(base, params={"table": "data", "page_size": 501}).status_code == 422
