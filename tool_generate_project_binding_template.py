"""
Генератор JSON-шаблона для ручной привязки проектов к клиентам.

Что делает:
- Читает уникальные `project_name` из `provider_leads`.
- Пропускает NULL/пустые project_name.
- Пропускает project_name, которые уже существуют в `projects.name` (точное сравнение, "как есть").
- Записывает общий JSON со списком элементов для ручного заполнения:
  - user_id (id клиента) — заполняется вручную
  - project_name — заполняется автоматически
  - id — заполняется вручную
  - provider_project_id — заполняется вручную

Запуск из корня проекта:
    python tool_generate_project_binding_template.py
    python tool_generate_project_binding_template.py --out docs/project_binding_template.json
    python tool_generate_project_binding_template.py --db-url postgresql+psycopg://user:pass@host:5432/db
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Set

from dotenv import load_dotenv
from sqlalchemy import select

from backend.app import db as db_mod
from backend.app import models


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Сгенерировать JSON-шаблон для ручной привязки проектов к клиентам",
    )
    parser.add_argument(
        "--db-url",
        dest="db_url",
        default=os.getenv("DATABASE_URL", "sqlite:///./app.db"),
        help="DATABASE_URL для подключения (переопределяет значение из .env)",
    )
    parser.add_argument(
        "--out",
        dest="output_path",
        default="project_client_binding_template.json",
        help="Путь выходного JSON-файла",
    )
    return parser.parse_args()


def _load_existing_project_names(session) -> Set[str]:
    rows = session.execute(
        select(models.Project.name).where(models.Project.name.is_not(None)).distinct()
    ).scalars()
    return {name for name in rows if isinstance(name, str)}


def _load_unique_provider_project_names(session) -> List[str]:
    rows = session.execute(
        select(models.ProviderLead.project_name)
        .where(models.ProviderLead.project_name.is_not(None))
        .distinct()
        .order_by(models.ProviderLead.project_name.asc())
    ).scalars()

    result: List[str] = []
    for name in rows:
        if not isinstance(name, str):
            continue
        if not name.strip():
            continue
        result.append(name)
    return result


def main() -> None:
    load_dotenv()
    args = _parse_args()

    engine, SessionLocal = db_mod.init_engine_and_session(args.db_url)

    with SessionLocal() as session:
        existing_names = _load_existing_project_names(session)
        provider_unique_names = _load_unique_provider_project_names(session)

    items = []
    skipped_existing = 0
    for project_name in provider_unique_names:
        if project_name in existing_names:
            skipped_existing += 1
            continue
        items.append(
            {
                "user_id": None,
                "project_name": project_name,
                "id": None,
                "provider_project_id": None,
            }
        )

    payload = {
        "meta": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_table": "provider_leads",
            "source_column": "project_name",
            "rules": {
                "skip_null_or_empty_project_name": True,
                "skip_existing_project_names_in_projects": True,
                "comparison_mode": "exact_as_is",
            },
            "stats": {
                "provider_unique_project_names": len(provider_unique_names),
                "skipped_existing_in_projects": skipped_existing,
                "items_exported": len(items),
            },
        },
        "items": items,
    }

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Готово: {output_path}")
    print(
        "Экспортировано: "
        f"{len(items)}; "
        f"пропущено (уже есть в projects): {skipped_existing}; "
        f"уникальных в provider_leads: {len(provider_unique_names)}"
    )


if __name__ == "__main__":
    main()

