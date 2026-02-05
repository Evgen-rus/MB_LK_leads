"""
Вспомогательный скрипт для просмотра содержимого БД через SQLAlchemy.

Что делает:
- Выводит все строки из таблицы projects (сортировка по id).
- Для leads берёт два project_id (минимальный и максимальный) и показывает
  по 5 первых и 5 последних записей по id для каждого.

Запуск из корня проекта:
    python tool_inspect_db.py
    python tool_inspect_db.py --db-url postgresql+psycopg://user:pass@host:5432/db
"""

from __future__ import annotations

import argparse
import os
from typing import Dict, Iterable, List

from dotenv import load_dotenv
from sqlalchemy import select

from backend.app import db as db_mod
from backend.app import models


def _model_to_dict(obj) -> Dict[str, object]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _print_rows(title: str, rows: List[Dict[str, object]], col_order: List[str]) -> None:
    print(f"\n{title} (rows={len(rows)})")
    if not rows:
        print("  <пусто>")
        return
    for r in rows:
        parts = [f"{c}={r.get(c)!r}" for c in col_order if c in r]
        print("  " + ", ".join(parts))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Просмотр projects и выборок leads из БД")
    parser.add_argument(
        "--db-url",
        dest="db_url",
        default=os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        help="DATABASE_URL для подключения (переопределяет значение из .env)",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = _parse_args()
    db_url = args.db_url

    engine, SessionLocal = db_mod.init_engine_and_session(db_url)
    models.Base.metadata.create_all(bind=engine)

    project_cols = [c.name for c in models.Project.__table__.columns]
    lead_cols = [
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

    with SessionLocal() as s:
        projects = s.execute(select(models.Project).order_by(models.Project.id.asc())).scalars().all()
        project_rows = [_model_to_dict(p) for p in projects]
        _print_rows("Projects", project_rows, project_cols)

        project_ids = (
            s.execute(select(models.Lead.project_id).distinct().order_by(models.Lead.project_id.asc()))
            .scalars()
            .all()
        )
        project_ids = [pid for pid in project_ids if pid is not None]
        if not project_ids:
            print("\nLeads: таблица пуста")
            return

        targets: List[int] = [project_ids[0]]
        if len(project_ids) > 1 and project_ids[-1] != project_ids[0]:
            targets.append(project_ids[-1])

        print("\nLeads выборки (по project_id):")
        for pid in targets:
            first_rows = (
                s.execute(
                    select(models.Lead)
                    .where(models.Lead.project_id == pid)
                    .order_by(models.Lead.id.asc())
                    .limit(5)
                )
                .scalars()
                .all()
            )
            last_rows = (
                s.execute(
                    select(models.Lead)
                    .where(models.Lead.project_id == pid)
                    .order_by(models.Lead.id.desc())
                    .limit(5)
                )
                .scalars()
                .all()
            )
            first_dicts = [_model_to_dict(r) for r in first_rows]
            last_dicts = list(reversed([_model_to_dict(r) for r in last_rows]))
            _print_rows(f"project_id={pid} первые 5 (id ASC)", first_dicts, lead_cols)
            _print_rows(f"project_id={pid} последние 5 (id DESC→ASC)", last_dicts, lead_cols)


if __name__ == "__main__":
    main()

