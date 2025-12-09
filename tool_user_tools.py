"""
Утилита для управления пользователями (логины/пароли) в БД проекта.

Запуск из корня проекта:

  python user_tools.py list
      - показать всех пользователей

  python user_tools.py create --login admin2 --password 1234
      - создать нового пользователя

  python user_tools.py set-password --login admin
      - сменить пароль (будет интерактивный запрос пароля)

Пароли всегда хранятся в виде bcrypt-хешей через backend.app.auth.hash_password.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import select

from backend.app import db as db_mod
from backend.app import models, auth


def init_session():
    """Инициализируем подключение к БД так же, как делает приложение."""
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    engine, SessionLocal = db_mod.init_engine_and_session(database_url)
    # На всякий случай создадим таблицы, если это новый файл БД.
    models.Base.metadata.create_all(bind=engine)
    return engine, SessionLocal


def _prompt_password() -> str | None:
    """
    Интерактивный запрос пароля с двойным вводом.

    Внимание: здесь пароль отображается в консоли (через input),
    т.к. скрипт используется локально администратором.
    """
    pwd1 = input("Введите новый пароль (видимый ввод): ")
    pwd2 = input("Повторите пароль: ")
    if not pwd1:
        print("Пароль не может быть пустым.")
        return None
    if pwd1 != pwd2:
        print("Пароли не совпадают, попробуйте ещё раз.")
        return None
    return pwd1


def cmd_list(_args) -> None:
    """Показать список пользователей."""
    _engine, SessionLocal = init_session()
    with SessionLocal() as s:
        users = s.execute(select(models.User).order_by(models.User.id)).scalars().all()
        if not users:
            print("В таблице users пока нет ни одного пользователя.")
            return
        print("Пользователи:")
        for u in users:
            created = u.created_at.isoformat(sep=" ", timespec="seconds") if u.created_at else "?"
            print(f"- id={u.id:3d}  login={u.login!r}  created_at={created}")


def cmd_create(args) -> None:
    """Создать нового пользователя."""
    login = (args.login or "").strip()
    if not login:
        print("Логин не может быть пустым.")
        return

    password = args.password or _prompt_password()
    if not password:
        # Сообщение уже выведено в _prompt_password.
        return

    _engine, SessionLocal = init_session()
    with SessionLocal() as s:
        existing = s.execute(
            select(models.User).where(models.User.login == login)
        ).scalar_one_or_none()
        if existing:
            print(f"Пользователь с логином {login!r} уже существует (id={existing.id}).")
            return
        user = models.User(
            login=login,
            password_hash=auth.hash_password(password),
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        print(f"Создан пользователь: id={user.id}, login={user.login!r}")


def cmd_set_password(args) -> None:
    """Сменить пароль существующему пользователю."""
    login = (args.login or "").strip()
    if not login:
        print("Нужно указать логин через --login.")
        return

    password = args.password or _prompt_password()
    if not password:
        return

    _engine, SessionLocal = init_session()
    with SessionLocal() as s:
        user = s.execute(
            select(models.User).where(models.User.login == login)
        ).scalar_one_or_none()
        if not user:
            print(f"Пользователь с логином {login!r} не найден.")
            return

        user.password_hash = auth.hash_password(password)
        s.commit()
        print(f"Пароль для пользователя {login!r} успешно обновлён.")

    # После успешного обновления пароля запишем его в локальный файл users.txt
    # Формат строки: ISO-время | login | пароль
    try:
        line = f"{datetime.utcnow().isoformat(timespec='seconds')}Z login={login} password={password}\n"
        with open("users.txt", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        # Не прерываем выполнение, если не удалось записать файл, просто сообщаем.
        print(f"Внимание: не удалось записать users.txt: {exc}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Инструменты управления пользователями LK.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Показать всех пользователей")

    p_create = sub.add_parser("create", help="Создать нового пользователя")
    p_create.add_argument("--login", required=True, help="Логин нового пользователя")
    p_create.add_argument(
        "--password",
        help="Пароль (если не указан, будет запрошен интерактивно)",
    )

    p_pass = sub.add_parser("set-password", help="Сменить пароль существующему пользователю")
    p_pass.add_argument("--login", required=True, help="Логин пользователя")
    p_pass.add_argument(
        "--password",
        help="Новый пароль (если не указан, будет запрошен интерактивно)",
    )

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "create":
        cmd_create(args)
    elif args.cmd == "set-password":
        cmd_set_password(args)


if __name__ == "__main__":
    main()


