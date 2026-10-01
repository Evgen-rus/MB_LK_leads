"""Owner contract and query-count guardrail for the client summary."""

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SHEETS_TZ", "Europe/Moscow")
from backend.app import crud, models


def user(user_id, role="client", owner=None):
    return models.User(id=user_id, login=f"user{user_id}", password_hash="x",
                       display_name=f"Display {user_id}", role=role, owner_agent_id=owner)


def summary(db):
    return crud.admin_clients_summary(db, datetime(2026, 9, 30), datetime(2026, 9, 30, 23, 59, 59))


@pytest.mark.parametrize("admin_state", ["profile", "no_profile", "missing"])
def test_owner_contract(admin_state):
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            if admin_state != "missing":
                db.add(user(1, "admin"))
            if admin_state == "profile":
                db.add(models.ClientProfile(user_id=1, name="Admin profile"))
            db.add_all([user(2), user(3, owner=10), user(4, owner=99), user(10, "agent")])
            db.add(models.ClientProfile(user_id=10, name="Agent profile"))
            db.commit()
            expected_admin = crud._get_user_info(db, 1)
            expected_agent = crud._user_info_from_user(db.get(models.User, 10))
            items = {item.user.id: item for item in summary(db).items}
            assert items[2].ownerType == "admin"
            assert items[2].ownerUser == expected_admin
            if expected_admin:
                assert expected_admin.name == ("Admin profile" if admin_state == "profile" else "Display 1")
            assert items[3].ownerType == "agent"
            assert items[3].ownerUser == expected_agent
            assert items[3].ownerUser.name == "Display 10"
            assert items[4].ownerType == "agent"
            assert items[4].ownerUser is None
    finally:
        engine.dispose()


def test_admin_owner_queries_do_not_grow_with_clients():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, u, sql, p, x, m: statements.append(sql))
    try:
        with Session(engine) as db:
            db.add_all([user(1, "admin"), user(2)])
            db.add(models.ClientProfile(user_id=1, name="Admin profile"))
            db.commit()
        counts = []
        for client_count in (1, 100):
            if client_count == 100:
                with Session(engine) as db:
                    db.add_all([user(i) for i in range(3, 102)])
                    db.commit()
            with Session(engine) as db:
                admin = db.get(models.User, 1)  # Retain the same auth context as the endpoint.
                statements.clear()
                result = summary(db)
                counts.append(len(statements))
                assert len(result.items) == client_count
                assert all(item.ownerUser.id == admin.id for item in result.items)
                assert sum("FROM client_profiles" in sql for sql in statements) == 2
        assert counts[0] == counts[1]
    finally:
        engine.dispose()
