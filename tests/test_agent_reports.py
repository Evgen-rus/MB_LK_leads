"""Machine download is compute-only and restricted to registered reports."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.agent_api import reports
from backend.app.agent_api.router import build_app


@pytest.fixture
def download_api(tmp_path, monkeypatch):
    monkeypatch.setenv("LK_AGENT_READ_TOKEN", "read-test")
    monkeypatch.setenv("LK_AGENT_COMPUTE_TOKEN", "compute-test")
    path = tmp_path / f"{uuid.uuid4().hex}.xlsx"
    Workbook().save(path)
    monkeypatch.setattr(reports.db, "get_group", lambda gid: {"name": "Synthetic"} if gid == 2 else None)
    monkeypatch.setattr(reports.db, "group_key", lambda gid: str(gid))
    monkeypatch.setattr(reports.db, "get_run", lambda rid: None)
    monkeypatch.setattr(reports, "list_exports", lambda key: [{
        "id": 4, "export_number": 1, "report_file_name": path.name,
        "period_start": "2026-09-01", "period_end": "2026-09-30",
        "periods": [{"period_start": "2026-09-01", "period_end": "2026-09-30"}],
        "settings": {"source_sheet_name": "Client"},
    }])
    monkeypatch.setattr(reports, "analysis_report_path", lambda name: path)
    app = FastAPI()
    app.mount("/agent/v1", build_app(lambda: None, {}))
    return TestClient(app), path


def test_download_requires_compute_and_returns_registered_xlsx(download_api):
    client, path = download_api
    url = "/agent/v1/analytics.download?group_id=2&export_id=4"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer read-test"}).status_code == 403
    response = client.get(url, headers={"Authorization": "Bearer compute-test"})
    assert response.status_code == 200
    assert response.content == path.read_bytes()
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert response.headers["X-Request-Id"]


@pytest.mark.parametrize("query,code", [("group_id=3&export_id=4", "GROUP_NOT_FOUND"),
                                     ("group_id=2&export_id=99", "RESULT_NOT_FOUND"),
                                     ("group_id=2&export_id=4&path=secret", "INVALID_PARAMETERS")])
def test_download_rejects_unknown_group_report_and_user_path(download_api, query, code):
    client, _ = download_api
    response = client.get("/agent/v1/analytics.download?" + query, headers={"Authorization": "Bearer compute-test"})
    assert response.json()["error"]["code"] == code


def test_missing_file_is_json(download_api):
    client, path = download_api
    path.unlink()
    response = client.get("/agent/v1/analytics.download?group_id=2&export_id=4", headers={"Authorization": "Bearer compute-test"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPORT_UNAVAILABLE"
