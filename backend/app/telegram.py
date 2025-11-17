# Файл: backend/app/telegram.py
# Назначение: простая отправка текста в Telegram Bot API (sendMessage).

import requests
from typing import Optional


def send_text(bot_token: str, chat_id: str, text: str, parse_mode: Optional[str] = "HTML") -> bool:
    if not bot_token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


