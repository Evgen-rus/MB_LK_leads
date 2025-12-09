"""
Простой CLI для маппинга внешних project_id (из лидов/источников) на внутренние проекты из таблицы projects, с учётом source (B1/B2/B3/B4).

Возможности:
- list: показать текущие соответствия external_id [+ source] -> project_id (с именем проекта)
- set: создать/обновить соответствие
- delete: удалить соответствие по external_id [+ source]
- unmapped: показать внешние id+source из leads, для которых нет маппинга

Запуск из корня:
    python map_projects.py list
    python map_projects.py set --external 128 --source B1 --project 5
    python map_projects.py delete --external 128 --source B1
    python map_projects.py unmapped
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _recreate_table(conn: sqlite3.Connection, has_source: bool) -> None:
    """
    Пересоздаём таблицу, чтобы убрать PK на external_id и сделать normal PK+UNIQUE.
    Старая схема: external_id PK → ломает множественные source.
    Новая схема: id PK, UNIQUE(external_id, source).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_id_map_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id INTEGER NOT NULL,
            source TEXT,
            project_id INTEGER NOT NULL,
            UNIQUE (external_id, source)
        );
        """
    )
    if has_source:
        conn.execute(
            """
            INSERT OR IGNORE INTO project_id_map_new (external_id, source, project_id)
            SELECT external_id, source, project_id FROM project_id_map;
            """
        )
    else:
        conn.execute(
            """
            INSERT OR IGNORE INTO project_id_map_new (external_id, source, project_id)
            SELECT external_id, NULL AS source, project_id FROM project_id_map;
            """
        )
    conn.execute("DROP TABLE project_id_map;")
    conn.execute("ALTER TABLE project_id_map_new RENAME TO project_id_map;")
    conn.commit()


def _ensure_table(conn: sqlite3.Connection) -> None:
    """
    Создаём таблицу при необходимости и делаем схему:
      id PK, external_id NOT NULL, source TEXT NULL, project_id NOT NULL,
      UNIQUE(external_id, source)
    Если старая схема имела PK на external_id, пересоздаём.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS project_id_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id INTEGER NOT NULL,
            source TEXT,
            project_id INTEGER NOT NULL,
            UNIQUE (external_id, source)
        );
        """
    )

    cols_info = conn.execute("PRAGMA table_info(project_id_map);").fetchall()
    col_names = [row["name"] for row in cols_info]
    has_source = "source" in col_names
    pk_cols = [row["name"] for row in cols_info if row["pk"]]

    # Если PK только на external_id, нужно пересоздать
    if pk_cols == ["external_id"]:
        _recreate_table(conn, has_source=has_source)
        return

    # Если нет source, добавляем и обеспечиваем UNIQUE индекс
    if "source" not in col_names:
        conn.execute("ALTER TABLE project_id_map ADD COLUMN source TEXT;")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_project_id_map_ext_src
        ON project_id_map (external_id, source);
        """
    )
    conn.commit()


def _fmt_row(row: sqlite3.Row, cols: Iterable[str]) -> str:
    return ", ".join(f"{c}={row[c]!r}" for c in cols if c in row.keys())


def cmd_list(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        """
        SELECT m.external_id,
               m.source,
               m.project_id,
               p.name AS project_name,
               p.data_source_code AS project_code
        FROM project_id_map m
        LEFT JOIN projects p ON p.id = m.project_id
        ORDER BY m.external_id ASC, m.source ASC;
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("Маппинг пуст.")
        return
    for r in rows:
        print(_fmt_row(r, ["external_id", "source", "project_id", "project_name", "project_code"]))


def cmd_set(conn: sqlite3.Connection, external_id: int, source: str | None, project_id: int) -> None:
    conn.execute(
        """
        INSERT INTO project_id_map (external_id, source, project_id)
        VALUES (?, ?, ?)
        ON CONFLICT(external_id, source) DO UPDATE SET project_id=excluded.project_id;
        """,
        (external_id, source, project_id),
    )
    conn.commit()
    print(f"Сохранено: external_id={external_id}, source={source!r} -> project_id={project_id}")


def cmd_delete(conn: sqlite3.Connection, external_id: int, source: str | None) -> None:
    if source is None:
        cur = conn.execute("DELETE FROM project_id_map WHERE external_id = ?;", (external_id,))
        conn.commit()
        if cur.rowcount:
            print(f"Удалено все записи для external_id={external_id}")
        else:
            print(f"Ничего не найдено для external_id={external_id}")
    else:
        cur = conn.execute(
            "DELETE FROM project_id_map WHERE external_id = ? AND source = ?;",
            (external_id, source),
        )
        conn.commit()
        if cur.rowcount:
            print(f"Удалено: external_id={external_id}, source={source}")
        else:
            print(f"Ничего не найдено для external_id={external_id}, source={source}")


def cmd_unmapped(conn: sqlite3.Connection) -> None:
    # Покажем уникальные пары (project_id, source) из leads, которых нет в маппинге.
    cur = conn.execute(
        """
        SELECT l.project_id AS external_id,
               l.source AS source,
               COUNT(*) AS leads_count
        FROM leads l
        LEFT JOIN project_id_map m
          ON m.external_id = l.project_id
         AND (m.source = l.source OR (m.source IS NULL AND l.source IS NULL))
        WHERE m.external_id IS NULL
        GROUP BY l.project_id, l.source
        ORDER BY external_id, source;
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("Все project_id+source из leads имеют маппинг.")
        return
    for r in rows:
        print(_fmt_row(r, ["external_id", "source", "leads_count"]))


def _count_updates_for_mapping(conn: sqlite3.Connection, external_id: int, source: str | None, target_pid: int) -> int:
    sql = """
        SELECT COUNT(*) AS cnt
        FROM leads
        WHERE project_id = :ext
          AND (:src IS NULL AND source IS NULL OR source = :src)
          AND project_id <> :target
    """
    row = conn.execute(sql, {"ext": external_id, "src": source, "target": target_pid}).fetchone()
    return int(row["cnt"] if row else 0)


def _apply_updates_for_mapping(conn: sqlite3.Connection, external_id: int, source: str | None, target_pid: int) -> int:
    sql = """
        UPDATE leads
           SET project_id = :target
         WHERE project_id = :ext
           AND (:src IS NULL AND source IS NULL OR source = :src)
           AND project_id <> :target
    """
    cur = conn.execute(sql, {"ext": external_id, "src": source, "target": target_pid})
    return cur.rowcount or 0


def cmd_apply(conn: sqlite3.Connection, dry_run: bool) -> None:
    rows = conn.execute("SELECT external_id, source, project_id FROM project_id_map").fetchall()
    if not rows:
        print("Маппинг пуст, нечего применять.")
        return

    total = 0
    for r in rows:
        external_id = int(r["external_id"])
        source = r["source"] if r["source"] is not None else None
        target_pid = int(r["project_id"])
        if dry_run:
            cnt = _count_updates_for_mapping(conn, external_id, source, target_pid)
        else:
            cnt = _apply_updates_for_mapping(conn, external_id, source, target_pid)
        total += cnt
        print(f"{'Would update' if dry_run else 'Updated'}: external_id={external_id}, source={source!r} -> project_id={target_pid}, rows={cnt}")

    if not dry_run:
        conn.commit()
    print(f"Total rows {'to update' if dry_run else 'updated'}: {total}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Маппинг external project_id -> internal project_id (projects.id)")
    parser.add_argument("--db", dest="db_path", default="app.db", help="Путь к SQLite файлу (по умолчанию app.db)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Показать текущие соответствия")

    p_set = sub.add_parser("set", help="Создать/обновить соответствие external_id [+ source] -> project_id")
    p_set.add_argument("--external", type=int, required=True, help="Внешний project_id (из импорта/Sheets)")
    p_set.add_argument("--source", type=str, required=False, help="Источник (B1/B2/B3/B4). Если не задан, маппинг без учёта source.")
    p_set.add_argument("--project", type=int, required=True, help="Внутренний project_id (из таблицы projects)")

    p_del = sub.add_parser("delete", help="Удалить соответствие по external_id [+ source]")
    p_del.add_argument("--external", type=int, required=True, help="Внешний project_id для удаления")
    p_del.add_argument("--source", type=str, required=False, help="Источник (B1/B2/B3/B4). Если не задан, удаляются все соответствия с этим external_id.")

    sub.add_parser("unmapped", help="Показать project_id из leads без маппинга")

    p_apply = sub.add_parser("apply", help="Применить маппинг к таблице leads (обновить project_id)")
    p_apply.add_argument("--dry-run", action="store_true", help="Только показать сколько строк будет обновлено, без изменений")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"Файл БД не найден: {db_path}")
        return

    conn = _connect(db_path)
    try:
        _ensure_table(conn)

        if args.cmd == "list":
            cmd_list(conn)
        elif args.cmd == "set":
            src = args.source.strip() if args.source else None
            cmd_set(conn, external_id=args.external, source=src, project_id=args.project)
        elif args.cmd == "delete":
            src = args.source.strip() if args.source else None
            cmd_delete(conn, external_id=args.external, source=src)
        elif args.cmd == "unmapped":
            cmd_unmapped(conn)
        elif args.cmd == "apply":
            cmd_apply(conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

