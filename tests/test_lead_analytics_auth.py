"""Exercise the real LK admin dependency without importing main/startup."""
import ast
from pathlib import Path
import re
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import auth, crud, models
from backend.app.lead_analytics import db
from backend.app.lead_analytics.router import build_client_router, build_router


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_SECRET", "synthetic-analytics-auth-test-secret")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        session.add_all([
            models.User(id=1, login="synthetic-root", password_hash="unused", role="admin"),
            models.User(id=2, login="synthetic-client", password_hash="unused", role="client"),
            models.User(id=3, login="synthetic-agent", password_hash="unused", role="agent"),
        ])
        session.commit()

    def get_db():
        with sessions() as session:
            yield session

    source = Path(__file__).resolve().parents[1] / "backend/app/main.py"
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    dependency = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "require_admin")
    namespace = dict(Request=Request, Session=Session, Depends=Depends, get_db=get_db,
                     HTTPException=HTTPException, auth=auth, models=models, crud=crud, Optional=Optional)
    exec(compile(ast.Module(body=[dependency], type_ignores=[]), str(source), "exec"), namespace)
    auth_dependency = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "require_auth")
    exec(compile(ast.Module(body=[auth_dependency], type_ignores=[]), str(source), "exec"), namespace)
    router = build_router(namespace["require_admin"], get_db, lambda v: v or "", lambda v: v or "", lambda v: v or "")
    app = FastAPI()
    app.include_router(router)
    app.include_router(build_client_router(namespace["require_auth"]))
    with TestClient(app) as test_client:
        yield test_client, router
    engine.dispose()


@pytest.mark.parametrize("user_id", [None, 2, 3])
def test_all_analytics_endpoints_require_actual_admin(client, user_id):
    test_client, router = client
    # Forged role/is_admin claims never grant another user admin access.
    headers = {} if user_id is None else {"Authorization": f"Bearer {auth.create_access_token(user_id, is_admin=True, role='admin')}"}
    for route in router.routes:
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods:
            json_body = {"published": False} if path.endswith("/publication") else None
            response = test_client.request(method, path, headers=headers, json=json_body)
            assert response.status_code == (401 if user_id is None else 403), (method, path, response.text)


def test_existing_admin_token_can_list_clients(client):
    test_client, _ = client
    response = test_client.get("/admin/analytics/clients", headers={"Authorization": f"Bearer {auth.create_access_token(1, is_admin=True, role='admin')}"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [2]


def test_client_analytics_requires_authenticated_client_role(client):
    test_client, _ = client
    assert test_client.get("/analytics/exports").status_code == 401
    for user_id in (1, 3):
        token = auth.create_access_token(user_id, is_admin=True, role="client")
        assert test_client.get("/analytics/exports", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    token = auth.create_access_token(2, is_admin=False, role="admin")
    response = test_client.get("/analytics/exports", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == []
