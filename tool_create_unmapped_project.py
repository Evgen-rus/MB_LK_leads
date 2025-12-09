"""
Создаёт служебный проект для фолбэка (Unmapped/Inbox) в таблице projects.

Запуск из корня:
  python create_unmapped_project.py
  python create_unmapped_project.py --name "Unmapped Inbox" --tag UNMAPPED
  python create_unmapped_project.py --db-url sqlite:///./app.db

Логика:
- Ищет проект по tag (по умолчанию UNMAPPED). Если есть — не дублирует, выводит id.
- Если нет — создаёт с минимально валидными полями.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from backend.app import db as db_mod
from backend.app import models


def get_or_create_unmapped(db_url: str, name: str, tag: str, user_id: int | None) -> models.Project:
    engine, SessionLocal = db_mod.init_engine_and_session(db_url)
    models.Base.metadata.create_all(bind=engine)

    with SessionLocal() as s:
        existing = s.query(models.Project).filter(models.Project.tag == tag).first()
        if existing:
            if user_id is not None and existing.user_id != user_id:
                existing.user_id = user_id
                s.commit()
                s.refresh(existing)
            print(f"Проект уже существует: id={existing.id}, name={existing.name}, tag={existing.tag}")
            return existing

        now = datetime.now(timezone.utc)
        proj = models.Project(
            user_id=user_id,
            name=name,
            tag=tag,
            collection_source="Звонки",
            data_source_code="UNMAPPED",
            region_mode=None,
            regions=None,
            sites=None,
            phones=None,
            sms_sender_name=None,
            status="Активен",
            delivery_status="На модерации",
            data_limit=0,
            numbers_today=0,
            numbers_total=0,
            days_received="",
            sources_count=0,
            created_at=now,
            updated_at=now,
        )
        s.add(proj)
        s.commit()
        s.refresh(proj)
        print(f"Создан проект: id={proj.id}, name={proj.name}, tag={proj.tag}")
        return proj


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать служебный проект для фолбэка несопоставленных лидов")
    parser.add_argument("--db-url", default=os.getenv("DATABASE_URL", "sqlite:///./app.db"), help="DATABASE_URL, по умолчанию sqlite:///./app.db")
    parser.add_argument("--name", default="Unmapped Inbox", help="Имя проекта")
    parser.add_argument("--tag", default="UNMAPPED", help="Тег для поиска/создания (используется как уникальный ключ)")
    parser.add_argument("--user-id", type=int, default=None, help="Привязать проект к user_id (например, 1 для админа)")
    args = parser.parse_args()

    get_or_create_unmapped(db_url=args.db_url, name=args.name, tag=args.tag, user_id=args.user_id)


if __name__ == "__main__":
    main()

