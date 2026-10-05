import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import DB_PATH
from .models import ColumnMapping, StatusRule


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT UNIQUE,
                project_name TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS column_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT NOT NULL,
                sheet_name TEXT NOT NULL,
                date_column TEXT,
                phone_column TEXT,
                channel_column TEXT,
                source_column TEXT,
                status_column TEXT NOT NULL,
                comment_column TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS status_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT,
                pattern TEXT NOT NULL,
                pattern_key TEXT,
                match_type TEXT NOT NULL,
                group_name TEXT NOT NULL,
                subgroup_name TEXT,
                comment TEXT,
                priority INTEGER DEFAULT 100,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS client_status_rule_migrations (
                client_id INTEGER PRIMARY KEY,
                migrated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS client_status_rule_conflicts (
                client_id INTEGER NOT NULL,
                pattern TEXT NOT NULL,
                pattern_key TEXT NOT NULL,
                group_names_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(client_id, pattern_key)
            );
            CREATE TABLE IF NOT EXISTS source_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT,
                source_value TEXT NOT NULL,
                normalized_source TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS manual_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT,
                pattern TEXT NOT NULL,
                flag_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS file_match_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT NOT NULL,
                file_role TEXT NOT NULL,
                sheet_name TEXT NOT NULL,
                lkid_column TEXT,
                phone_column TEXT,
                date_column TEXT,
                source_column TEXT,
                status_column TEXT,
                comment_column TEXT,
                project_column TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis_exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT NOT NULL,
                export_number INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                analysis_date TEXT NOT NULL,
                source_file_name TEXT NOT NULL,
                report_file_name TEXT,
                total_count INTEGER NOT NULL DEFAULT 0,
                missed_count INTEGER NOT NULL DEFAULT 0,
                missed_rate REAL NOT NULL DEFAULT 0,
                quality_count INTEGER NOT NULL DEFAULT 0,
                quality_rate REAL NOT NULL DEFAULT 0,
                demand_count INTEGER NOT NULL DEFAULT 0,
                demand_rate REAL NOT NULL DEFAULT 0,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                settings_json TEXT NOT NULL DEFAULT '{}',
                run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_code, export_number)
            );
            CREATE TABLE IF NOT EXISTS analysis_export_breakdowns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                export_id INTEGER NOT NULL,
                breakdown_type TEXT NOT NULL,
                dimension_1 TEXT,
                dimension_2 TEXT,
                total_count INTEGER NOT NULL DEFAULT 0,
                missed_count INTEGER NOT NULL DEFAULT 0,
                missed_rate REAL NOT NULL DEFAULT 0,
                quality_count INTEGER NOT NULL DEFAULT 0,
                quality_rate REAL NOT NULL DEFAULT 0,
                demand_count INTEGER NOT NULL DEFAULT 0,
                demand_rate REAL NOT NULL DEFAULT 0,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(export_id) REFERENCES analysis_exports(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS analysis_export_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                export_id INTEGER NOT NULL,
                period_index INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                total_count INTEGER NOT NULL DEFAULT 0,
                missed_count INTEGER NOT NULL DEFAULT 0,
                missed_rate REAL NOT NULL DEFAULT 0,
                quality_count INTEGER NOT NULL DEFAULT 0,
                quality_rate REAL NOT NULL DEFAULT 0,
                demand_count INTEGER NOT NULL DEFAULT 0,
                demand_rate REAL NOT NULL DEFAULT 0,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(export_id, period_index),
                FOREIGN KEY(export_id) REFERENCES analysis_exports(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS processing_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                group_id INTEGER,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT '',
                processed_rows INTEGER NOT NULL DEFAULT 0,
                total_rows INTEGER NOT NULL DEFAULT 0,
                error_text TEXT,
                output_file_name TEXT,
                export_id INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_processing_jobs_status_id
            ON processing_jobs(status, id);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_processing_jobs_active_run
            ON processing_jobs(run_id) WHERE status IN ('queued', 'running');
            CREATE TABLE IF NOT EXISTS analytics_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                project_ids_json TEXT NOT NULL,
                spreadsheet_url TEXT,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_analytics_groups_client
            ON analytics_groups(client_id, archived, id);
            CREATE TABLE IF NOT EXISTS analytics_runs (
                id TEXT PRIMARY KEY,
                group_id INTEGER NOT NULL,
                client_id INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                project_ids_json TEXT NOT NULL,
                project_names_json TEXT NOT NULL,
                periods_json TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                lk_snapshot TEXT NOT NULL,
                client_snapshot TEXT NOT NULL,
                source_file_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared',
                created_at TEXT NOT NULL,
                FOREIGN KEY(group_id) REFERENCES analytics_groups(id)
            );
            CREATE INDEX IF NOT EXISTS idx_analytics_runs_group
            ON analytics_runs(group_id, created_at);
            """
        )
        job_columns = {row["name"] for row in conn.execute("PRAGMA table_info(processing_jobs)")}
        if "group_id" not in job_columns:
            conn.execute("ALTER TABLE processing_jobs ADD COLUMN group_id INTEGER")
        if "export_id" not in job_columns:
            conn.execute("ALTER TABLE processing_jobs ADD COLUMN export_id INTEGER")
        export_columns = {row["name"] for row in conn.execute("PRAGMA table_info(analysis_exports)")}
        if "run_id" not in export_columns:
            conn.execute("ALTER TABLE analysis_exports ADD COLUMN run_id TEXT")
        if "settings_json" not in export_columns:
            conn.execute("ALTER TABLE analysis_exports ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(status_rules)")}
        if "pattern_key" not in columns:
            conn.execute("ALTER TABLE status_rules ADD COLUMN pattern_key TEXT")
        breakdown_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(analysis_export_breakdowns)")
        }
        if "period_id" not in breakdown_columns:
            conn.execute("ALTER TABLE analysis_export_breakdowns ADD COLUMN period_id INTEGER")
        conn.execute("DROP INDEX IF EXISTS uq_status_rules_project_pattern")
        rows = conn.execute(
            """
            SELECT id, project_code, pattern
            FROM status_rules
            ORDER BY priority ASC, id ASC
            """
        ).fetchall()
        seen: set[tuple[str, str]] = set()
        for row in rows:
            pattern_key = normalize_status_pattern(row["pattern"])
            project = row["project_code"]
            key = (project, pattern_key) if project is not None else None
            if key is not None and key in seen:
                conn.execute("DELETE FROM status_rules WHERE id = ?", (row["id"],))
                continue
            if key is not None:
                seen.add(key)
            conn.execute(
                "UPDATE status_rules SET pattern_key = ? WHERE id = ?",
                (pattern_key, row["id"]),
            )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_status_rules_project_pattern
            ON status_rules(project_code, pattern_key)
            WHERE project_code IS NOT NULL
            """
        )


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_status_pattern(pattern: str) -> str:
    return pattern.strip().casefold()


def ensure_project(project: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO projects(project_code, project_name, created_at)
            VALUES (?, ?, ?)
            """,
            (project, project, now_text()),
        )


def create_processing_job(
    run_id: str, kind: str, payload: dict[str, object], group_id: int | None = None, *, deferred: bool = False
) -> dict[str, object]:
    stamp = now_text()
    with connect() as conn:
        # Serialize the run check with insertion. The partial unique index remains
        # a second line of defence for older databases and unexpected writers.
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT 1 FROM processing_jobs WHERE run_id=? AND status IN ('queued','running') LIMIT 1",
            (run_id,),
        ).fetchone()
        if active is not None:
            raise sqlite3.IntegrityError("A processing job is already active for this run")
        cursor = conn.execute(
            """
            INSERT INTO processing_jobs(
                run_id, group_id, kind, payload_json, status, phase, started_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, group_id, kind, json.dumps(payload, ensure_ascii=False),
             "running" if deferred else "queued", "Сохранение настроек" if deferred else "",
             stamp if deferred else None, stamp, stamp),
        )
        job_id = int(cursor.lastrowid)
    return get_processing_job(job_id)


def get_processing_job(job_id: int) -> dict[str, object]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM processing_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("Задание не найдено")
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def claim_next_processing_job() -> dict[str, object] | None:
    stamp = now_text()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM processing_jobs WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cursor = conn.execute(
            """
            UPDATE processing_jobs
            SET status = 'running', started_at = ?, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (stamp, stamp, row["id"]),
        )
        if cursor.rowcount != 1:
            return None
    return get_processing_job(int(row["id"]))


def has_queued_processing_jobs() -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM processing_jobs WHERE status = 'queued' LIMIT 1"
        ).fetchone()
    return row is not None


def has_active_run_job(run_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM processing_jobs WHERE run_id=? AND status IN ('queued','running') LIMIT 1",
            (run_id,),
        ).fetchone()
    return row is not None


def get_analysis_export_id(project_code: str, run_id: str) -> int | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM analysis_exports WHERE project_code=? AND run_id=? ORDER BY id DESC LIMIT 1",
            (project_code, run_id),
        ).fetchone()
    return int(row["id"]) if row is not None else None


def fail_interrupted_jobs() -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE processing_jobs SET status='failed', phase='Прервано перезапуском',
            error_text='Обработка была прервана перезапуском сервера', completed_at=?, updated_at=?
            WHERE status='running'""",
            (now_text(), now_text()),
        )


def group_key(group_id: int) -> str:
    return f"lk-group:{int(group_id)}"


def client_status_rule_key(client_id: int) -> str:
    return f"lk-client:{int(client_id)}"


def _group(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    value = dict(row)
    value["project_ids"] = json.loads(value.pop("project_ids_json"))
    value["archived"] = bool(value["archived"])
    return value


def list_groups(client_id: int) -> list[dict[str, object]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM analytics_groups WHERE client_id=? ORDER BY archived, name COLLATE NOCASE, id",
            (client_id,),
        ).fetchall()
    return [_group(row) for row in rows if row is not None]


def get_group(group_id: int) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM analytics_groups WHERE id=?", (group_id,)).fetchone()
    return _group(row)


def create_group(client_id: int, name: str, project_ids: list[int], spreadsheet_url: str | None) -> int:
    stamp = now_text()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO analytics_groups(client_id,name,project_ids_json,spreadsheet_url,created_at,updated_at)
            VALUES (?,?,?,?,?,?)""",
            (client_id, name, json.dumps(sorted(set(project_ids))), spreadsheet_url, stamp, stamp),
        )
    return int(cur.lastrowid)


def update_group(group_id: int, name: str, project_ids: list[int], spreadsheet_url: str | None) -> bool:
    with connect() as conn:
        cur = conn.execute(
            """UPDATE analytics_groups SET name=?, project_ids_json=?, spreadsheet_url=?, updated_at=?
            WHERE id=? AND archived=0""",
            (name, json.dumps(sorted(set(project_ids))), spreadsheet_url, now_text(), group_id),
        )
    return cur.rowcount > 0


def update_group_spreadsheet_url(group_id: int, spreadsheet_url: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE analytics_groups SET spreadsheet_url=?, updated_at=? WHERE id=? AND archived=0",
            (spreadsheet_url, now_text(), group_id),
        )
    return cur.rowcount > 0


def archive_group(group_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE analytics_groups SET archived=1, updated_at=? WHERE id=? AND archived=0",
            (now_text(), group_id),
        )
    return cur.rowcount > 0


def create_run(run: dict[str, object]) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO analytics_runs(
              id,group_id,client_id,group_name,project_ids_json,project_names_json,periods_json,
              settings_json,lk_snapshot,client_snapshot,source_file_name,status,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run["id"], run["group_id"], run["client_id"], run["group_name"],
                json.dumps(run["project_ids"], ensure_ascii=False),
                json.dumps(run["project_names"], ensure_ascii=False),
                json.dumps(run["periods"], ensure_ascii=False),
                json.dumps(run.get("settings", {}), ensure_ascii=False), run["lk_snapshot"],
                run["client_snapshot"], run["source_file_name"], run.get("status", "prepared"),
                now_text(),
            ),
        )


def get_run(run_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM analytics_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        return None
    value = dict(row)
    for column, key in (("project_ids_json", "project_ids"), ("project_names_json", "project_names"),
                        ("periods_json", "periods"), ("settings_json", "settings")):
        value[key] = json.loads(value.pop(column))
    return value


def update_run_settings(run_id: str, settings: dict[str, object], status: str = "prepared") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE analytics_runs SET settings_json=?, status=? WHERE id=?",
            (json.dumps(settings, ensure_ascii=False), status, run_id),
        )


def update_run_status(run_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE analytics_runs SET status=? WHERE id=?", (status, run_id))


def update_processing_job(
    job_id: int,
    *,
    status: str | None = None,
    phase: str | None = None,
    processed_rows: int | None = None,
    total_rows: int | None = None,
    error_text: str | None = None,
    output_file_name: str | None = None,
    export_id: int | None = None,
) -> None:
    fields: list[str] = ["updated_at = ?"]
    values: list[object] = [now_text()]
    for column, value in {
        "status": status,
        "phase": phase,
        "processed_rows": processed_rows,
        "total_rows": total_rows,
        "error_text": error_text,
        "output_file_name": output_file_name,
        "export_id": export_id,
    }.items():
        if value is not None:
            fields.append(f"{column} = ?")
            values.append(value)
    if status in {"completed", "failed"}:
        fields.append("completed_at = ?")
        values.append(now_text())
    values.append(job_id)
    with connect() as conn:
        conn.execute(f"UPDATE processing_jobs SET {', '.join(fields)} WHERE id = ?", values)


def list_projects() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT project_code FROM projects
            ORDER BY project_code COLLATE NOCASE ASC
            """
        ).fetchall()
    return [row["project_code"] for row in rows]


def get_column_mapping(project: str) -> ColumnMapping | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM column_mappings
            WHERE project_code = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (project,),
        ).fetchone()
    if not row:
        return None
    return ColumnMapping(
        sheet_name=row["sheet_name"],
        date_column=row["date_column"],
        phone_column=row["phone_column"],
        channel_column=row["channel_column"],
        source_column=row["source_column"],
        status_column=row["status_column"],
        comment_column=row["comment_column"],
    )


def save_column_mapping(project: str, mapping: ColumnMapping) -> None:
    stamp = now_text()
    with connect() as conn:
        conn.execute("DELETE FROM column_mappings WHERE project_code = ?", (project,))
        conn.execute(
            """
            INSERT INTO column_mappings(
                project_code, sheet_name, date_column, phone_column, channel_column,
                source_column, status_column, comment_column, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project,
                mapping.sheet_name,
                mapping.date_column,
                mapping.phone_column,
                mapping.channel_column,
                mapping.source_column,
                mapping.status_column,
                mapping.comment_column,
                stamp,
                stamp,
            ),
        )


def get_match_mapping(project: str, file_role: str) -> ColumnMapping | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM file_match_mappings
            WHERE project_code = ? AND file_role = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (project, file_role),
        ).fetchone()
    if not row:
        return None
    return ColumnMapping(
        sheet_name=row["sheet_name"],
        lkid_column=row["lkid_column"],
        phone_column=row["phone_column"],
        date_column=row["date_column"],
        source_column=row["source_column"],
        status_column=row["status_column"],
        comment_column=row["comment_column"],
        project_column=row["project_column"],
    )


def save_match_mapping(project: str, file_role: str, mapping: ColumnMapping) -> None:
    stamp = now_text()
    with connect() as conn:
        conn.execute(
            "DELETE FROM file_match_mappings WHERE project_code = ? AND file_role = ?",
            (project, file_role),
        )
        conn.execute(
            """
            INSERT INTO file_match_mappings(
                project_code, file_role, sheet_name, lkid_column, phone_column,
                date_column, source_column, status_column, comment_column,
                project_column, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project,
                file_role,
                mapping.sheet_name,
                mapping.lkid_column,
                mapping.phone_column,
                mapping.date_column,
                mapping.source_column,
                mapping.status_column,
                mapping.comment_column,
                mapping.project_column,
                stamp,
                stamp,
            ),
        )


def add_status_rule(rule: StatusRule) -> None:
    stamp = now_text()
    pattern_key = normalize_status_pattern(rule.pattern)
    with connect() as conn:
        if rule.project_code is not None:
            existing = conn.execute(
                """
                SELECT id FROM status_rules
                WHERE project_code = ?
                  AND pattern_key = ?
                ORDER BY priority ASC, id ASC
                LIMIT 1
                """,
                (rule.project_code, pattern_key),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE status_rules
                    SET pattern = ?, pattern_key = ?, match_type = ?, group_name = ?, subgroup_name = ?,
                        comment = ?, priority = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        rule.pattern,
                        pattern_key,
                        rule.match_type,
                        rule.group_name,
                        rule.subgroup_name,
                        rule.comment,
                        rule.priority,
                        stamp,
                        existing["id"],
                    ),
                )
                _clear_client_status_rule_conflict(conn, rule, pattern_key)
                return
        conn.execute(
            """
            INSERT INTO status_rules(
                project_code, pattern, pattern_key, match_type, group_name,
                subgroup_name, comment, priority, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule.project_code,
                rule.pattern,
                pattern_key,
                rule.match_type,
                rule.group_name,
                rule.subgroup_name,
                rule.comment,
                rule.priority,
                stamp,
                stamp,
            ),
        )
        _clear_client_status_rule_conflict(conn, rule, pattern_key)


def _clear_client_status_rule_conflict(conn: sqlite3.Connection, rule: StatusRule, pattern_key: str) -> None:
    prefix = "lk-client:"
    if rule.match_type != "exact" or not rule.project_code or not rule.project_code.startswith(prefix):
        return
    try:
        client_id = int(rule.project_code[len(prefix):])
    except ValueError:
        return
    conn.execute(
        "DELETE FROM client_status_rule_conflicts WHERE client_id=? AND pattern_key=?",
        (client_id, pattern_key),
    )


def list_status_rules(project: str | None = None) -> list[sqlite3.Row]:
    with connect() as conn:
        if project:
            return list(
                conn.execute(
                    """
                    SELECT * FROM status_rules
                    WHERE project_code IS NULL OR project_code = ?
                    ORDER BY project_code DESC, priority ASC, id ASC
                    """,
                    (project,),
                )
            )
        return list(conn.execute("SELECT * FROM status_rules ORDER BY id ASC"))


def list_project_status_rules(project: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT * FROM status_rules
                WHERE project_code = ?
                ORDER BY pattern COLLATE NOCASE ASC, id ASC
                """,
                (project,),
            )
        )


def ensure_client_status_rules(client_id: int, valid_groups: set[str] | list[str]) -> None:
    """Move legacy group rules to the shared client key once, preserving disagreements for review."""
    client_id = int(client_id)
    client_key = client_status_rule_key(client_id)
    allowed_groups = set(valid_groups)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        migrated = conn.execute(
            "SELECT 1 FROM client_status_rule_migrations WHERE client_id=?", (client_id,)
        ).fetchone()
        if migrated:
            return

        groups = conn.execute(
            "SELECT id, name FROM analytics_groups WHERE client_id=? ORDER BY id", (client_id,)
        ).fetchall()
        candidates: dict[str, list[sqlite3.Row]] = {}
        for group in groups:  # Include archived groups: their saved mappings were still this client's rules.
            for row in conn.execute(
                "SELECT * FROM status_rules WHERE project_code=? ORDER BY priority ASC, id ASC",
                (group_key(int(group["id"])),),
            ):
                pattern_key = normalize_status_pattern(row["pattern"])
                candidates.setdefault(pattern_key, []).append(row)

        stamp = now_text()
        for pattern_key, records in candidates.items():
            if conn.execute(
                "SELECT 1 FROM status_rules WHERE project_code=? AND pattern_key=?",
                (client_key, pattern_key),
            ).fetchone():
                continue
            categories = {str(row["group_name"]) for row in records}
            if len(categories) == 1 and next(iter(categories)) in allowed_groups:
                row = records[0]
                conn.execute(
                    """INSERT INTO status_rules(
                        project_code, pattern, pattern_key, match_type, group_name, subgroup_name,
                        comment, priority, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (client_key, row["pattern"], pattern_key, row["match_type"], row["group_name"],
                     row["subgroup_name"], row["comment"], row["priority"], row["created_at"], stamp),
                )
            else:
                first = records[0]
                conn.execute(
                    """INSERT OR REPLACE INTO client_status_rule_conflicts(
                        client_id, pattern, pattern_key, group_names_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (client_id, first["pattern"], pattern_key,
                     json.dumps(sorted(categories), ensure_ascii=False), stamp),
                )
        conn.execute(
            "INSERT INTO client_status_rule_migrations(client_id, migrated_at) VALUES (?, ?)",
            (client_id, stamp),
        )


def list_client_status_rule_conflicts(client_id: int) -> list[dict[str, object]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT pattern, pattern_key, group_names_json
            FROM client_status_rule_conflicts WHERE client_id=? ORDER BY pattern COLLATE NOCASE""",
            (int(client_id),),
        ).fetchall()
    return [{"pattern": str(row["pattern"]), "pattern_key": str(row["pattern_key"]),
             "group_names": json.loads(row["group_names_json"])} for row in rows]


def list_global_status_rules() -> list[sqlite3.Row]:
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT * FROM status_rules
                WHERE project_code IS NULL
                ORDER BY priority ASC, id ASC
                """
            )
        )


def update_project_status_rule_group(rule_id: int, project: str, group_name: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE status_rules
            SET group_name = ?, updated_at = ?
            WHERE id = ? AND project_code = ?
            """,
            (group_name, now_text(), rule_id, project),
        )
    return cursor.rowcount > 0


def delete_project_status_rule(rule_id: int, project: str) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM status_rules WHERE id = ? AND project_code = ?",
            (rule_id, project),
        )
    return cursor.rowcount > 0


def update_status_rule(rule_id: int, **fields: str | int | None) -> None:
    allowed = {"pattern", "match_type", "group_name", "subgroup_name", "comment", "priority"}
    assignments = []
    values: list[str | int | None] = []
    for key, value in fields.items():
        if key in allowed and value is not None:
            assignments.append(f"{key} = ?")
            values.append(value)
            if key == "pattern":
                assignments.append("pattern_key = ?")
                values.append(normalize_status_pattern(str(value)))
    if not assignments:
        return
    assignments.append("updated_at = ?")
    values.append(now_text())
    values.append(rule_id)
    with connect() as conn:
        conn.execute(f"UPDATE status_rules SET {', '.join(assignments)} WHERE id = ?", values)


def delete_status_rule(rule_id: int | None = None, project: str | None = None, pattern: str | None = None) -> None:
    with connect() as conn:
        if rule_id is not None:
            conn.execute("DELETE FROM status_rules WHERE id = ?", (rule_id,))
        elif project and pattern:
            conn.execute(
                "DELETE FROM status_rules WHERE project_code = ? AND pattern = ?",
                (project, pattern),
            )
