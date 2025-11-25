"""
Файл: backend/app/auth.py
Назначение: утилиты для аутентификации пользователей (bcrypt + JWT).
- Хеширование паролей через bcrypt
- Генерация и валидация JWT (HS256), payload: {"user_id": <int>, "exp": <ts>}
"""

from __future__ import annotations

import os
import time
from datetime import timedelta
from typing import Optional

import bcrypt
import jwt


def hash_password(plain: str) -> str:
    """Вычисляет bcrypt-хеш пароля (utf-8)."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    """Проверяет пароль против bcrypt-хеша."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: int, is_admin: bool = False, expires_delta: Optional[timedelta] = None) -> str:
    """Создаёт JWT с полями user_id, is_admin и временем жизни (по умолчанию 24 часа)."""
    secret = os.getenv("AUTH_SECRET", "dev-secret-change-me")
    ttl = int(expires_delta.total_seconds()) if expires_delta else 24 * 60 * 60
    payload = {
        "user_id": int(user_id),
        "is_admin": is_admin,
        "exp": int(time.time()) + ttl,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str) -> Optional[int]:
    """Возвращает user_id из JWT или None, если токен невалиден/просрочен."""
    try:
        secret = os.getenv("AUTH_SECRET", "dev-secret-change-me")
        data = jwt.decode(token, secret, algorithms=["HS256"])
        uid = int(data.get("user_id"))
        return uid
    except Exception:
        return None


def decode_token_payload(token: str) -> Optional[dict]:
    """Возвращает полный payload из JWT или None, если токен невалиден/просрочен."""
    try:
        secret = os.getenv("AUTH_SECRET", "dev-secret-change-me")
        return jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        return None
