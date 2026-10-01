"""Conditional aggregates must preserve the former four-query result."""

import os
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SHEETS_TZ", "Europe/Moscow")
from backend.app import crud, models, schemas

ACTIONS = ["create", "update", "delete", "blacklist_add", "blacklist_delete"]
VARIANTS = [None, [], ["unknown"], ["create", "unknown", "create"]] + [
    list(subset) for size in range(1, 6) for subset in combinations(ACTIONS, size)
]


def legacy_summary(db, actions):
    """Reference: independent grouped count for each former action category."""
    selected = [a for a in (actions or ACTIONS) if a in ACTIONS] or ACTIONS
    maps = []
    for category in (["update", "delete"], ["create"], ["blacklist_add"], ["blacklist_delete"]):
        included = [a for a in selected if a in category]
        rows = []
        if included:
            rows = db.execute(select(models.User.id, models.User.login, func.count(models.AuditEvent.id))
                              .join(models.AuditEvent, models.AuditEvent.user_id == models.User.id)
                              .where(models.User.role == "client")
                              .where(models.AuditEvent.action.in_(included))
                              .where(models.AuditEvent.admin_processed_at.is_(None))
                              .group_by(models.User.id, models.User.login)).all()
        maps.append({int(uid): (login, int(count)) for uid, login, count in rows})
    updates, creates, adds, deletes = maps
    result = []
    for uid in set(creates) | set(updates) | set(adds) | set(deletes):
        values = [mapping.get(uid, (None, 0)) for mapping in maps]
        result.append(schemas.AdminClientChangesSummaryItem(
            user=schemas.UserInfo(id=uid, login=next((login for login, _ in values if login), "")),
            pendingChanges=values[0][1], pendingCreates=values[1][1],
            pendingBlacklistAdds=values[2][1], pendingBlacklistDeletes=values[3][1],
            pendingTotal=sum(count for _, count in values),
        ).model_dump())
    return result


@pytest.mark.parametrize("actions", VARIANTS)
@pytest.mark.parametrize("client_count", [1, 100])
def test_matches_legacy_with_one_query(actions, client_count):
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, u, sql, p, x, m: statements.append(sql))
    try:
        with Session(engine) as db:
            clients = list(range(2, client_count + 2))
            roles = {1: "admin", 1001: "agent", 1002: "client", 1003: "client", 1004: "client"}
            roles.update({uid: "client" for uid in clients})
            db.add_all([models.User(id=uid, login=f"user{uid}", password_hash="x", role=role)
                        for uid, role in roles.items()])
            for uid in clients + [1, 1001]:
                for index, action in enumerate(ACTIONS):
                    db.add_all([models.AuditEvent(user_id=uid, action=action, actor_user_id=1)
                                for _ in range(index + 1)])
                    db.add(models.AuditEvent(user_id=uid, action=action, admin_processed_at=datetime(2026, 1, 1)))
                db.add(models.AuditEvent(user_id=uid, action="other"))
            # Empty client, processed-only client, and an action-specific client.
            db.add(models.AuditEvent(user_id=1003, action="create", admin_processed_at=datetime(2026, 1, 1)))
            db.add(models.AuditEvent(user_id=1004, action="blacklist_delete"))
            db.commit()
            expected = legacy_summary(db, actions)
            statements.clear()
            actual = [item.model_dump() for item in crud.admin_list_client_changes_summary(db, actions)]
            assert len(statements) == 1
            assert actual == expected
            assert not {1, 1001, 1002, 1003} & {item["user"]["id"] for item in actual}
            selected = [a for a in (actions or ACTIONS) if a in ACTIONS] or ACTIONS
            assert (1004 in {item["user"]["id"] for item in actual}) == ("blacklist_delete" in selected)
    finally:
        engine.dispose()


def test_empty_history_uses_one_query():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, u, sql, p, x, m: statements.append(sql))
    try:
        with Session(engine) as db:
            assert crud.admin_list_client_changes_summary(db) == []
            assert len(statements) == 1
    finally:
        engine.dispose()
