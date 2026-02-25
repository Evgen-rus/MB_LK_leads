"""
Разовая привязка старых лидов к проектам в таблице provider_leads.

Назначение:
- Проставить provider_leads.project_id для строк, где project_id ещё NULL.
- Сопоставление выполняется строго по имени:
  provider_leads.project_name == projects.name
- Если для одного имени найдено несколько проектов, строка пропускается как неоднозначная.

Важно:
- Скрипт работает только с вашей БД.
- В API поставщика никаких запросов не выполняется.

Запуск:
    python util_13_backfill_provider_leads_project_id.py --dry-run
    python util_13_backfill_provider_leads_project_id.py

Опции:
    --db-url   Явный DATABASE_URL (по умолчанию из env, иначе sqlite:///./app.db)
    --dry-run  Проверка без записи в БД
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.app import db as db_mod
from backend.app import models


DEFAULT_DB_URL = "sqlite:///./app.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill provider_leads.project_id по exact match project_name == projects.name"
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL", DEFAULT_DB_URL),
        help="DATABASE_URL для подключения (по умолчанию из env или sqlite:///./app.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Проверка без записи в БД",
    )
    return parser.parse_args()


def _load_project_name_map(db_sess) -> tuple[Dict[str, int], Dict[str, List[int]]]:
    rows = db_sess.execute(select(models.Project.id, models.Project.name)).all()
    by_name: Dict[str, List[int]] = defaultdict(list)
    for project_id, name in rows:
        if name is None:
            continue
        key = str(name)
        by_name[key].append(int(project_id))

    exact_map: Dict[str, int] = {}
    ambiguous_map: Dict[str, List[int]] = {}
    for name, ids in by_name.items():
        uniq_ids = sorted(set(ids))
        if len(uniq_ids) == 1:
            exact_map[name] = uniq_ids[0]
        else:
            ambiguous_map[name] = uniq_ids
    return exact_map, ambiguous_map


def main() -> None:
    load_dotenv()
    args = parse_args()

    _, session_local = db_mod.init_engine_and_session(args.db_url)
    db_sess = session_local()

    total_null = 0
    matched = 0
    updated = 0
    unmatched = 0
    ambiguous = 0
    errors = 0
    skipped_empty_name = 0

    unmatched_names: Set[str] = set()
    ambiguous_names_seen: Set[str] = set()

    print(f"[INFO] start dry_run={args.dry_run}")
    try:
        exact_map, ambiguous_map = _load_project_name_map(db_sess)
        print(
            f"[INFO] projects loaded: exact_names={len(exact_map)}, "
            f"ambiguous_names={len(ambiguous_map)}"
        )

        leads = db_sess.execute(
            select(models.ProviderLead).where(models.ProviderLead.project_id.is_(None))
        ).scalars().all()
        total_null = len(leads)
        print(f"[INFO] provider_leads with NULL project_id: {total_null}")

        for idx, lead in enumerate(leads, start=1):
            raw_name = lead.project_name
            name = str(raw_name) if raw_name is not None else ""
            if not name:
                skipped_empty_name += 1
                print(f"[SKIP] idx={idx} lead_id={lead.id}: empty project_name")
                continue

            if name in ambiguous_map:
                ambiguous += 1
                ambiguous_names_seen.add(name)
                print(
                    f"[SKIP] idx={idx} lead_id={lead.id}: ambiguous name={name!r}, "
                    f"project_ids={ambiguous_map[name]}"
                )
                continue

            project_id = exact_map.get(name)
            if project_id is None:
                unmatched += 1
                unmatched_names.add(name)
                print(f"[SKIP] idx={idx} lead_id={lead.id}: no project match for name={name!r}")
                continue

            matched += 1
            if args.dry_run:
                print(
                    f"[OK]   idx={idx} DRY-RUN lead_id={lead.id} "
                    f"name={name!r} -> project_id={project_id}"
                )
                continue

            try:
                lead.project_id = project_id
                db_sess.commit()
                updated += 1
                print(
                    f"[OK]   idx={idx} updated lead_id={lead.id} "
                    f"name={name!r} -> project_id={project_id}"
                )
            except SQLAlchemyError as exc:
                db_sess.rollback()
                errors += 1
                print(f"[ERR]  idx={idx} lead_id={lead.id}: DB error: {exc}")
            except Exception as exc:
                db_sess.rollback()
                errors += 1
                print(f"[ERR]  idx={idx} lead_id={lead.id}: unexpected error: {exc}")

    finally:
        db_sess.close()

    print("")
    print("[SUMMARY]")
    print(f"total_null_project_id={total_null}")
    print(f"matched={matched}")
    print(f"updated={updated}")
    print(f"unmatched={unmatched} (unique names={len(unmatched_names)})")
    print(f"ambiguous={ambiguous} (unique names={len(ambiguous_names_seen)})")
    print(f"skipped_empty_name={skipped_empty_name}")
    print(f"errors={errors}")
    print(f"dry_run={args.dry_run}")

    if unmatched_names:
        sample = sorted(unmatched_names)[:20]
        print("")
        print("[UNMATCHED_NAMES_SAMPLE]")
        for n in sample:
            print(f"- {n}")
        if len(unmatched_names) > len(sample):
            print(f"... and {len(unmatched_names) - len(sample)} more")

    if ambiguous_names_seen:
        sample_amb = sorted(ambiguous_names_seen)[:20]
        print("")
        print("[AMBIGUOUS_NAMES_SAMPLE]")
        for n in sample_amb:
            print(f"- {n}")
        if len(ambiguous_names_seen) > len(sample_amb):
            print(f"... and {len(ambiguous_names_seen) - len(sample_amb)} more")


if __name__ == "__main__":
    main()
