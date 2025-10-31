"""
Файл: backend/app/auth.py
Назначение: простая сессия по cookie без сторонних библиотек.
- /auth/login выдает httpOnly cookie с подписью (HMAC SHA256)
- Верификация по cookie в зависимостях эндпоинтов
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_session(username: str, ttl_seconds: int = 86400) -> str:
    secret = os.getenv("AUTH_SECRET", "dev-secret-change-me").encode()
    payload = {"u": username, "exp": int(time.time()) + ttl_seconds}
    body = json.dumps(payload, separators=(",", ":")).encode()
    body_b64 = _b64url(body)
    sig = hmac.new(secret, body, hashlib.sha256).digest()
    sig_b64 = _b64url(sig)
    return f"{body_b64}.{sig_b64}"


def verify_session(token: str) -> Optional[str]:
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64url_decode(body_b64)
        secret = os.getenv("AUTH_SECRET", "dev-secret-change-me").encode()
        expected = hmac.new(secret, body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        payload = json.loads(body.decode())
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return str(payload.get("u"))
    except Exception:
        return None


