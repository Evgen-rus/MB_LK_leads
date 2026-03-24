"""
Скрипт запрашивает список номеров проекта у Prostats по id и дате.

На первом этапе это только диагностическая утилита:
- спрашивает project id и дату в консоли;
- обходит все страницы ответа gck_phones;
- сохраняет raw и JSON-ответы по страницам;
- сохраняет общий структурированный JSON со всеми страницами.
"""

import json
import os
import time
from datetime import datetime
from typing import Any, List, Tuple

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"
REQUEST_TIMEOUT_SEC = 60
PAGE_SIZE_HINT = 1000
REQUEST_DELAY_SEC = 0.3


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return value


def prompt_project_id() -> str:
    value = input("Введите id проекта Prostats: ").strip()
    if not value:
        raise SystemExit("Project id is empty. Provide a valid id.")
    return value


def prompt_date() -> str:
    value = input("Введите дату в формате YYYY-MM-DD: ").strip()
    if not value:
        raise SystemExit("Date is empty. Provide a valid date.")

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit("Invalid date format. Use YYYY-MM-DD.") from exc
    return value


def fetch_gck_phones_page(
    api_url: str,
    token: str,
    project_id: str,
    date_str: str,
    page: int,
) -> requests.Response:
    payload = {
        "token": token,
        "command": "gck_phones",
        "id": project_id,
        "date": date_str,
        "page": page,
    }
    response = requests.post(api_url, json=payload, timeout=REQUEST_TIMEOUT_SEC)
    response.raise_for_status()
    return response


def extract_page_items(data: Any) -> Tuple[List[Any], str]:
    if not isinstance(data, dict):
        return [], "top_level_not_dict"

    result = data.get("result")
    if isinstance(result, list):
        return result, "result"

    if isinstance(result, dict):
        for key in ("items", "data", "phones", "rows", "list"):
            value = result.get(key)
            if isinstance(value, list):
                return value, f"result.{key}"

        # Фолбэк: если API вернёт один объект вместо списка, сохраним и его.
        return [result], "result_object_fallback"

    return [], "result_missing_or_unsupported"


def save_text_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as file_obj:
        file_obj.write(content)


def save_json_file(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)


def main() -> None:
    load_dotenv()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")
    project_id = prompt_project_id()
    date_str = prompt_date()

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = f"gck_phones_dump_{project_id}_{date_str}_{ts}"
    os.makedirs(out_dir, exist_ok=True)

    print(f"Project id: {project_id}")
    print(f"Date: {date_str}")
    print(f"Output dir: {out_dir}")

    all_items: List[Any] = []
    pages_meta: List[dict[str, Any]] = []
    page = 1

    while True:
        print(f"Requesting page {page}...")
        response = fetch_gck_phones_page(api_url, token, project_id, date_str, page)
        raw_text = response.text

        raw_out_file = os.path.join(
            out_dir,
            f"gck_phones_project_{project_id}_date_{date_str}_page_{page}_raw.txt",
        )
        save_text_file(raw_out_file, raw_text)

        try:
            data = response.json()
        except Exception:
            error_out_file = os.path.join(
                out_dir,
                f"gck_phones_project_{project_id}_date_{date_str}_page_{page}_non_json_error.json",
            )
            save_json_file(
                error_out_file,
                {
                    "project_id": project_id,
                    "date": date_str,
                    "page": page,
                    "http_status": response.status_code,
                    "error": "non_json_response",
                    "raw_file": os.path.basename(raw_out_file),
                    "text_preview": raw_text[:5000],
                },
            )
            print(f"Page {page}: non-JSON response, raw saved to {raw_out_file}")
            raise

        json_out_file = os.path.join(
            out_dir,
            f"gck_phones_project_{project_id}_date_{date_str}_page_{page}.json",
        )
        save_json_file(json_out_file, data)

        page_items, item_source = extract_page_items(data)
        page_items_count = len(page_items)
        all_items.extend(page_items)

        pages_meta.append(
            {
                "page": page,
                "http_status": response.status_code,
                "items_count": page_items_count,
                "items_source": item_source,
                "raw_file": os.path.basename(raw_out_file),
                "json_file": os.path.basename(json_out_file),
            }
        )

        print(
            "Page processed:",
            page,
            "| items:",
            page_items_count,
            "| source:",
            item_source,
        )

        if page_items_count == 0:
            break

        if page_items_count < PAGE_SIZE_HINT:
            break

        page += 1
        time.sleep(REQUEST_DELAY_SEC)

    merged_out_file = os.path.join(
        out_dir,
        f"gck_phones_project_{project_id}_date_{date_str}_all.json",
    )
    save_json_file(
        merged_out_file,
        {
            "fetched_at": ts,
            "project_id": project_id,
            "date": date_str,
            "api_url": api_url,
            "page_size_hint": PAGE_SIZE_HINT,
            "pages_total": len(pages_meta),
            "total_items": len(all_items),
            "pages": pages_meta,
            "items": all_items,
        },
    )

    print("Done.")
    print(f"Pages fetched: {len(pages_meta)}")
    print(f"Total items: {len(all_items)}")
    print(f"Merged JSON: {merged_out_file}")


if __name__ == "__main__":
    main()
