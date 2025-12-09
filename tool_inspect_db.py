"""
Вспомогательный скрипт для просмотра содержимого app.db.

Что делает:
- Выводит все строки из таблицы projects (сортировка по id).
- Для leads берёт два project_id (минимальный и максимальный) и показывает
  по 5 первых и 5 последних записей по id для каждого.

Запуск из корня проекта:
    python inspect_db.py
    python inspect_db.py --db path/to/app.db
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute("PRAGMA table_info(%s)" % table)
    return [row["name"] for row in cur.fetchall()]


def _print_rows(title: str, rows: list[sqlite3.Row], col_order: list[str] | None = None) -> None:
    print(f"\n{title} (rows={len(rows)})")
    if not rows:
        print("  <пусто>")
        return
    cols = col_order or rows[0].keys()
    for r in rows:
        parts = [f"{c}={r[c]!r}" for c in cols if c in r.keys()]
        print("  " + ", ".join(parts))


def _fetch_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM projects ORDER BY id ASC")
    return cur.fetchall()


def _fetch_project_ids_from_leads(conn: sqlite3.Connection) -> list[int]:
    cur = conn.execute("SELECT DISTINCT project_id FROM leads ORDER BY project_id ASC")
    return [row["project_id"] for row in cur.fetchall() if row["project_id"] is not None]


def _fetch_leads(conn: sqlite3.Connection, project_id: int, cols: list[str]) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    cols_sql = ", ".join(cols)
    first_cur = conn.execute(
        f"SELECT {cols_sql} FROM leads WHERE project_id = ? ORDER BY id ASC LIMIT 5",
        (project_id,),
    )
    last_cur = conn.execute(
        f"SELECT {cols_sql} FROM leads WHERE project_id = ? ORDER BY id DESC LIMIT 5",
        (project_id,),
    )
    first_rows = first_cur.fetchall()
    last_rows = list(reversed(last_cur.fetchall()))  # Чтобы вывод был по возрастанию id
    return first_rows, last_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Просмотр projects и выборок leads из app.db")
    parser.add_argument("--db", dest="db_path", default="app.db", help="Путь к SQLite файлу (по умолчанию app.db)")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"Файл БД не найден: {db_path}")
        return

    conn = _connect(db_path)
    try:
        projects = _fetch_projects(conn)
        _print_rows("Projects", projects)

        lead_cols_available = _get_table_columns(conn, "leads")
        lead_cols_desired = [
            "id",
            "project_id",
            "ext_id",
            "phone",
            "source",
            "utm_campaign",
            "created_at",
            "imported_at",
            "spreadsheet_id",
            "sheet_name",
        ]
        lead_cols = [c for c in lead_cols_desired if c in lead_cols_available]
        if not lead_cols:
            print("\nLeads: нет доступных столбцов для выборки (проверьте структуру таблицы)")
            return

        project_ids = _fetch_project_ids_from_leads(conn)
        if not project_ids:
            print("\nLeads: таблица пуста")
            return

        targets: list[int] = []
        if project_ids:
            targets.append(project_ids[0])
        if len(project_ids) > 1 and project_ids[-1] != project_ids[0]:
            targets.append(project_ids[-1])

        print("\nLeads выборки (по project_id):")
        for pid in targets:
            first_rows, last_rows = _fetch_leads(conn, pid, lead_cols)
            _print_rows(f"project_id={pid} первые 5 (id ASC)", first_rows, lead_cols)
            _print_rows(f"project_id={pid} последние 5 (id DESC→ASC)", last_rows, lead_cols)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

