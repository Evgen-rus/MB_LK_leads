"""
Утилита для обслуживания БД проекта.

Запуск из корня:

  python db_tools.py list                   # показать количество записей по таблицам
  python db_tools.py clear --leads --yes    # очистить таблицу leads
  python db_tools.py clear --leads --project-id 151 --yes  # удалить лиды только проекта 151
  python db_tools.py clear --all --yes      # полностью очистить все поддерживаемые таблицы
  python db_tools.py vacuum                 # (SQLite) сжать файл базы

По умолчанию команды clear работают в dry-run; для подтверждения нужно добавить --yes.
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import delete, select, text, func

# Локальные модули проекта
from backend.app import db as db_mod
from backend.app import models


def init_session():
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    engine, SessionLocal = db_mod.init_engine_and_session(database_url)
    models.Base.metadata.create_all(bind=engine)
    return engine, SessionLocal


def get_counts(session) -> dict[str, int]:
    counts: dict[str, int] = {}
    counts["projects"] = session.execute(select(func.count()).select_from(models.Project)).scalar_one()
    counts["leads"] = session.execute(select(func.count()).select_from(models.Lead)).scalar_one()
    counts["audit_events"] = session.execute(select(func.count()).select_from(models.AuditEvent)).scalar_one()
    counts["notify_state"] = session.execute(select(func.count()).select_from(models.NotifyState)).scalar_one()
    return counts


def clear_leads(session, project_id: Optional[int]) -> int:
    stmt = delete(models.Lead)
    if project_id is not None:
        stmt = stmt.where(models.Lead.project_id == project_id)
    res = session.execute(stmt)
    session.commit()
    return res.rowcount or 0


def clear_projects(session) -> int:
    res = session.execute(delete(models.Project))
    session.commit()
    return res.rowcount or 0


def clear_audit(session) -> int:
    res = session.execute(delete(models.AuditEvent))
    session.commit()
    return res.rowcount or 0


def clear_notify_state(session) -> int:
    res = session.execute(delete(models.NotifyState))
    session.commit()
    return res.rowcount or 0


def vacuum_sqlite(engine) -> None:
    url = str(engine.url)
    if url.startswith("sqlite"):
        with engine.connect() as conn:
            conn.execute(text("VACUUM"))
            conn.commit()


def cmd_list() -> None:
    engine, SessionLocal = init_session()
    with SessionLocal() as s:
        counts = get_counts(s)
    print("Текущие размеры таблиц:")
    for k, v in counts.items():
        print(f"- {k}: {v}")


def cmd_clear(args) -> None:
    engine, SessionLocal = init_session()
    with SessionLocal() as s:
        if not args.yes:
            # dry-run информация
            counts = get_counts(s)
            print("ПРЕДПРОСМОТР (dry-run), ничего не удалено. Что будет удалено:")
            if args.all or args.leads:
                if args.project_id is not None:
                    cnt = s.execute(
                        select(func.count()).select_from(models.Lead).where(models.Lead.project_id == args.project_id)
                    ).scalar_one()
                else:
                    cnt = counts["leads"]
                print(f"- leads: {cnt}")
            if args.all or args.projects:
                print(f"- projects: {counts['projects']}")
            if args.all or args.audit:
                print(f"- audit_events: {counts['audit_events']}")
            if args.all or args.notify_state:
                print(f"- notify_state: {counts['notify_state']}")
            print("Добавьте --yes для подтверждения.")
            return

        total = 0
        if args.all or args.leads:
            total += clear_leads(s, args.project_id)
        if args.all or args.audit:
            clear_audit(s)
        if args.all or args.projects:
            clear_projects(s)
        if args.all or args.notify_state:
            clear_notify_state(s)

    if args.vacuum:
        vacuum_sqlite(engine)
    print("Готово. Удалено строк из leads:", total)


def cmd_vacuum() -> None:
    engine, _ = init_session()
    vacuum_sqlite(engine)
    print("VACUUM выполнен")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Инструменты администрирования БД")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Показать количество записей по таблицам")

    p_clear = sub.add_parser("clear", help="Очистка таблиц (по умолчанию dry-run)")
    p_clear.add_argument("--leads", action="store_true", help="Очистить leads (можно с --project-id)")
    p_clear.add_argument("--project-id", type=int, default=None, help="Ограничить очистку leads одним project_id")
    p_clear.add_argument("--projects", action="store_true", help="Очистить projects")
    p_clear.add_argument("--audit", action="store_true", help="Очистить audit_events")
    p_clear.add_argument("--notify-state", action="store_true", help="Очистить notify_state")
    p_clear.add_argument("--all", action="store_true", help="Очистить все перечисленные таблицы")
    p_clear.add_argument("--yes", action="store_true", help="Подтвердить удаление (иначе dry-run)")
    p_clear.add_argument("--vacuum", action="store_true", help="После очистки выполнить VACUUM для SQLite")

    sub.add_parser("vacuum", help="VACUUM (SQLite)")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "clear":
        if not (args.all or args.leads or args.projects or args.audit or args.notify_state):
            print("Не выбрано, что чистить. Используйте --leads/--projects/--audit/--notify-state или --all.")
            return
        cmd_clear(args)
    elif args.cmd == "vacuum":
        cmd_vacuum()


if __name__ == "__main__":
    main()


