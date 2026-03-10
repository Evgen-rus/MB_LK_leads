#!/usr/bin/env python3
"""
Простой скрипт: при получении сообщения бот отвечает ID пользователя и ID чата (группы).
Запуск: python scripts/telegram_echo_ids.py
Остановка: Ctrl+C
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# Подключаем .env из корня проекта
root = Path(__file__).resolve().parent.parent
env_path = root / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("Ошибка: TELEGRAM_BOT_TOKEN не найден в .env")
    sys.exit(1)

import requests

BASE = f"https://api.telegram.org/bot{TOKEN}"


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def send_reply(chat_id: int, text: str) -> bool:
    """Отправляет ответ в чат."""
    try:
        r = requests.post(
            f"{BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        ok = r.status_code == 200
        if not ok:
            print(f"[{_ts()}] Ошибка отправки: {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:
        print(f"[{_ts()}] Ошибка отправки: {e}")
        return False


def main():
    offset = 0

    # Проверка бота и webhook при старте
    try:
        r = requests.get(f"{BASE}/getMe", timeout=10)
        me = r.json()
        if me.get("ok"):
            print(f"[{_ts()}] Бот: @{me['result']['username']} — доступен")
        else:
            print(f"[{_ts()}] getMe ошибка: {me}")
    except Exception as e:
        print(f"[{_ts()}] Не удалось проверить бота: {e}")
        sys.exit(1)

    try:
        r = requests.get(f"{BASE}/getWebhookInfo", timeout=10)
        wh = r.json()
        if wh.get("ok") and wh.get("result", {}).get("url"):
            print(f"[{_ts()}] ⚠️ Webhook установлен: {wh['result']['url']}")
            print(f"[{_ts()}] Обновления идут на webhook, getUpdates будет пустым!")
            print(f"[{_ts()}] Чтобы скрипт работал: deleteWebhook")
        else:
            print(f"[{_ts()}] Webhook не установлен — polling доступен")
    except Exception as e:
        print(f"[{_ts()}] Не удалось проверить webhook: {e}")

    print(f"[{_ts()}] Ожидаю сообщения (offset={offset}). Отправь боту сообщение.")
    print("Остановка: Ctrl+C\n")

    poll_count = 0

    while True:
        try:
            poll_count += 1
            r = requests.get(
                f"{BASE}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            data = r.json()
        except Exception as e:
            print(f"[{_ts()}] Ошибка getUpdates: {e}")
            continue

        if not data.get("ok"):
            print(f"[{_ts()}] API ошибка: {data}")
            continue

        results = data.get("result", [])
        if results:
            print(f"[{_ts()}] Получено сообщений: {len(results)}")
        else:
            # Раз в 10 опросов напоминаем, что ждём (каждые ~5 мин при timeout=30)
            if poll_count % 10 == 1 and poll_count > 1:
                print(f"[{_ts()}] Жду... (опрос #{poll_count}, offset={offset})")

        for upd in results:
            offset = upd["update_id"] + 1
            msg = upd.get("message")
            if not msg:
                continue

            user_id = msg.get("from", {}).get("id")
            chat_id = msg.get("chat", {}).get("id")

            if user_id is None or chat_id is None:
                continue

            # Формируем ответ
            text = f"user_id: {user_id}\nchat_id: {chat_id}"
            if chat_id < 0:
                text += "\n(это группа/супергруппа)"
            else:
                text += "\n(личный чат)"

            if send_reply(chat_id, text):
                print(f"[{_ts()}] Ответ отправлен: user_id={user_id}, chat_id={chat_id}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОстановлено.")
