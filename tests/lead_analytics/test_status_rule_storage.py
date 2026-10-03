import sqlite3

from backend.app.lead_analytics import db
from backend.app.lead_analytics.models import StatusRule


def test_project_status_rule_is_updated_instead_of_duplicated(tmp_path, monkeypatch):
    db_path = tmp_path / "analytics.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()

    db.add_status_rule(
        StatusRule(
            project_code="Demo",
            pattern="Новый статус",
            match_type="exact",
            group_name="Качественные",
            priority=10,
        )
    )
    db.add_status_rule(
        StatusRule(
            project_code="Demo",
            pattern="  новый СТАТУС ",
            match_type="exact",
            group_name="Недозвон",
            priority=10,
        )
    )

    rows = db.list_project_status_rules("Demo")
    assert len(rows) == 1
    assert rows[0]["pattern"] == "  новый СТАТУС "
    assert rows[0]["group_name"] == "Недозвон"


def test_project_rule_update_and_delete_are_scoped_to_project(tmp_path, monkeypatch):
    db_path = tmp_path / "analytics.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    for project in ["First", "Second"]:
        db.add_status_rule(
            StatusRule(
                project_code=project,
                pattern="Одинаковый статус",
                match_type="exact",
                group_name="Качественные",
                priority=10,
            )
        )

    first_rule = db.list_project_status_rules("First")[0]
    assert not db.update_project_status_rule_group(first_rule["id"], "Second", "Недозвон")
    assert db.update_project_status_rule_group(first_rule["id"], "First", "Недозвон")
    assert db.list_project_status_rules("First")[0]["group_name"] == "Недозвон"
    assert db.list_project_status_rules("Second")[0]["group_name"] == "Качественные"

    assert not db.delete_project_status_rule(first_rule["id"], "Second")
    assert db.delete_project_status_rule(first_rule["id"], "First")
    assert db.list_project_status_rules("First") == []
    assert len(db.list_project_status_rules("Second")) == 1


def test_init_db_deduplicates_legacy_project_rules(tmp_path):
    db_path = tmp_path / "analytics.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE status_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT,
                pattern TEXT NOT NULL,
                match_type TEXT NOT NULL,
                group_name TEXT NOT NULL,
                subgroup_name TEXT,
                comment TEXT,
                priority INTEGER DEFAULT 100,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        values = (
            "Demo",
            "Повтор",
            "exact",
            "Качественные",
            None,
            None,
            10,
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        )
        conn.execute(
            """
            INSERT INTO status_rules(
                project_code, pattern, match_type, group_name, subgroup_name,
                comment, priority, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.execute(
            """
            INSERT INTO status_rules(
                project_code, pattern, match_type, group_name, subgroup_name,
                comment, priority, created_at, updated_at
            ) VALUES (?, upper(?), ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    db.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT count(*) FROM status_rules WHERE project_code = 'Demo'"
        ).fetchone()[0]
        indexes = conn.execute("PRAGMA index_list(status_rules)").fetchall()
    assert count == 1
    assert any(index[1] == "uq_status_rules_project_pattern" for index in indexes)
