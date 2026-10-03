"""Exercise the real LK admin dependency without importing main/startup."""
import ast
from pathlib import Path
import re

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import auth, models
from backend.app.lead_analytics.router import build_router


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "synthetic-analytics-auth-test-secret")
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
                     HTTPException=HTTPException, auth=auth, models=models)
    exec(compile(ast.Module(body=[dependency], type_ignores=[]), str(source), "exec"), namespace)
    router = build_router(namespace["require_admin"], get_db, lambda v: v or "", lambda v: v or "", lambda v: v or "")
    app = FastAPI()
    app.include_router(router)
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
            response = test_client.request(method, path, headers=headers)
            assert response.status_code == (401 if user_id is None else 403), (method, path, response.text)


def test_existing_admin_token_can_list_clients(client):
    test_client, _ = client
    response = test_client.get("/admin/analytics/clients", headers={"Authorization": f"Bearer {auth.create_access_token(1, is_admin=True, role='admin')}"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [2]
