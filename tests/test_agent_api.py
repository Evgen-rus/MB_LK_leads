"""Operational boundary checks without main/startup or external services."""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.agent_api.router import build_app


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("LK_AGENT_READ_TOKEN", "synthetic-read-token")
    monkeypatch.setenv("LK_AGENT_COMPUTE_TOKEN", "synthetic-compute-token")
    app = FastAPI()
    app.mount("/agent/v1", build_app(lambda: None, {"SHEETS_TZ": "Europe/Moscow"}))
    return TestClient(app)


def headers(compute=False):
    return {"Authorization": "Bearer synthetic-compute-token" if compute else "Bearer synthetic-read-token"}


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Bearer synthetic-read-tokeñ"])
def test_missing_or_wrong_auth_is_json(api, authorization):
    response = api.get("/agent/v1/capabilities", headers={} if authorization is None else {"Authorization": authorization.encode("utf-8")})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert response.json()["request_id"] == response.headers["X-Request-Id"]


def test_fail_closed_and_conflicting_scopes(api, monkeypatch):
    monkeypatch.delenv("LK_AGENT_READ_TOKEN")
    monkeypatch.delenv("LK_AGENT_COMPUTE_TOKEN")
    assert api.get("/agent/v1/capabilities", headers=headers()).json()["error"]["code"] == "AGENT_DISABLED"
    monkeypatch.setenv("LK_AGENT_READ_TOKEN", "same")
    monkeypatch.setenv("LK_AGENT_COMPUTE_TOKEN", "same")
    assert api.get("/agent/v1/capabilities", headers=headers()).status_code == 503


def test_scope_discovery_and_envelope(api):
    body = api.get("/agent/v1/capabilities", headers=headers()).json()
    assert body["ok"] and body["action"] == "capabilities" and body["request_id"]
    assert body["data"]["version"] == "1"
    compute = next(item for item in body["data"]["capabilities"] if item["name"] == "analytics.run")
    assert compute["scope"] == "compute" and not compute["available"]
    assert "period_start" in compute["parameters"]
    assert api.post("/agent/v1/analytics.run", headers=headers(), json={"group_id": 1}).status_code == 403


@pytest.mark.parametrize("method,path", [("post", "projects.create"), ("delete", "project.show"),
                                        ("put", "analytics.rules"), ("get", "analytics.run"),
                                        ("post", "overview"), ("get", "leads.list")])
def test_no_mutations(api, method, path):
    response = getattr(api, method)(f"/agent/v1/{path}", headers=headers(True))
    assert response.status_code in {404, 405}
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] in {"CAPABILITY_NOT_FOUND", "METHOD_NOT_ALLOWED"}


@pytest.mark.parametrize("query", ["client_id=bad", "limit=201", "offset=-1", "token=secret", "q=" + "x" * 201])
def test_validation_errors_do_not_echo_inputs(api, query):
    response = api.get("/agent/v1/clients.list?" + query, headers=headers())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PARAMETERS"
    assert "secret" not in response.text


def test_exception_redaction_and_audit(api, monkeypatch, caplog):
    from backend.app.agent_api import service

    def failed(*args):
        raise RuntimeError("synthetic-read-token synthetic personal data")

    monkeypatch.setattr(service, "execute_read", failed)
    with caplog.at_level(logging.INFO, logger="app.agent"):
        response = api.get("/agent/v1/clients.find?q=synthetic-read-token", headers=headers())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "synthetic-read-token" not in response.text + caplog.text
    assert "personal data" not in response.text + caplog.text
    assert "action=clients.find scope=read" in caplog.text
    assert "duration_ms=" in caplog.text and "rid=" in caplog.text and "outcome=failure" in caplog.text


def test_compute_validation_does_not_echo_payload(api):
    response = api.post("/agent/v1/analytics.run", headers=headers(True), json={"token": "synthetic-compute-token"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PARAMETERS"
    assert "synthetic-compute-token" not in response.text


def test_request_id_matches_outer_backend_access_log(monkeypatch):
    monkeypatch.setenv("LK_AGENT_READ_TOKEN", "synthetic-read-token")
    monkeypatch.delenv("LK_AGENT_COMPUTE_TOKEN", raising=False)
    app = FastAPI()

    @app.middleware("http")
    async def outer(request, call_next):
        request.state.request_id = "server-assigned-id"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    app.mount("/agent/v1", build_app(lambda: None, {}))
    response = TestClient(app).get("/agent/v1/capabilities", headers=headers())
    assert response.json()["request_id"] == response.headers["X-Request-Id"] == "server-assigned-id"


@pytest.mark.parametrize("action,payload", [
    ("analytics.prepare", {"group_id": 1, "period_start": "2026-09-28", "period_end": "2026-10-02"}),
    ("analytics.confirm-statuses", {"group_id": 1, "run_id": "run", "status_rules": {"New": "Качественные"}}),
])
def test_new_compute_operations_require_compute(api, action, payload):
    response = api.post("/agent/v1/" + action, headers=headers(), json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE_REQUIRED"
    response = api.post("/agent/v1/" + action, headers=headers(True), json={**payload, "token": "synthetic-compute-token"})
    assert response.status_code == 422
    assert "synthetic-compute-token" not in response.text


def test_download_discovery_declares_binary_get_compute(api):
    operations = api.get("/agent/v1/capabilities", headers=headers()).json()["data"]["capabilities"]
    operation = next(item for item in operations if item["name"] == "analytics.download")
    assert operation["scope"] == "compute" and operation["method"] == "GET"
    assert not operation["available"]
