"""
Простая проверка доступности API Prostats.

Запуск:
python util_check_prostats_api.py
python util_check_prostats_api.py --timeout 20
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверить доступность API Prostats")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Таймаут запроса в секундах. По умолчанию: 10",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout должен быть больше 0")
    return args


def get_api_url() -> str:
    return os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT


def get_token() -> str:
    return os.getenv("PROSTATS_TOKEN", "").strip()


def short_text(value: str, limit: int = 500) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_url = get_api_url()
    token = get_token()

    if not token:
        raise SystemExit("ERROR: PROSTATS_TOKEN не найден в .env или окружении.")

    payload = {"token": token, "command": "gck_projects"}

    print(f"API URL: {api_url}")
    print(f"Command: {payload['command']}")
    print(f"Timeout: {args.timeout:g} sec")
    print("Sending request...")

    started_at = time.monotonic()
    try:
        response = requests.post(api_url, json=payload, timeout=args.timeout)
    except requests.exceptions.ConnectTimeout:
        elapsed = time.monotonic() - started_at
        raise SystemExit(f"ERROR: не удалось подключиться за {elapsed:.2f} sec.")
    except requests.exceptions.ReadTimeout:
        elapsed = time.monotonic() - started_at
        raise SystemExit(f"ERROR: API не ответило за {elapsed:.2f} sec.")
    except requests.exceptions.Timeout:
        elapsed = time.monotonic() - started_at
        raise SystemExit(f"ERROR: истёк таймаут запроса за {elapsed:.2f} sec.")
    except requests.exceptions.SSLError as exc:
        raise SystemExit(f"ERROR: SSL ошибка: {exc}")
    except requests.exceptions.ConnectionError as exc:
        if "Read timed out" in str(exc):
            elapsed = time.monotonic() - started_at
            raise SystemExit(f"ERROR: API не ответило за {elapsed:.2f} sec.")
        raise SystemExit(f"ERROR: ошибка соединения: {exc}")
    except requests.exceptions.RequestException as exc:
        raise SystemExit(f"ERROR: ошибка запроса: {exc}")

    elapsed = time.monotonic() - started_at
    print(f"HTTP status: {response.status_code}")
    print(f"Elapsed: {elapsed:.2f} sec")

    raw_text = response.text
    try:
        data = response.json()
    except ValueError:
        print("ERROR: API ответило, но ответ не JSON.")
        print("Response text:")
        print(short_text(raw_text))
        raise SystemExit(1)

    status = str(data.get("status") or "").lower()
    if response.ok and status == "success":
        print("OK: API доступно и вернуло успешный ответ.")
        result = data.get("result")
        if isinstance(result, list):
            print(f"Projects in response: {len(result)}")
        return

    print("WARNING: API доступно, но вернуло неуспешный ответ.")
    print_json(data)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
