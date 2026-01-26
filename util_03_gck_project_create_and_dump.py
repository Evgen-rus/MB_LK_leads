"""
Создаёт тестовый проект (выключен), находит его в списке проектов
и сохраняет полную информацию о нём в JSON.

Запуск:
python util_03_gck_project_create_and_dump.py [out_file]
"""

import json
import os
import sys
from datetime import datetime

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return value


def mask_token(value: str) -> str:
    if not value:
        return value
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def append_log(log_file: str, text: str) -> None:
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(text)


def log_request(api_url: str, payload: dict, log_file: str) -> None:
    safe = dict(payload)
    if "token" in safe:
        safe["token"] = mask_token(str(safe["token"]))
    block = (
        "\n=== API REQUEST ===\n"
        f"URL: {api_url}\n"
        f"Payload: {json.dumps(safe, ensure_ascii=False, indent=2)}\n"
    )
    print(block, end="")
    append_log(log_file, block)


def log_response(raw_text: str, status_code: int, log_file: str) -> None:
    block = f"=== API RESPONSE (raw) ===\nStatus: {status_code}\n{raw_text}\n"
    print(block, end="")
    append_log(log_file, block)


def post_json(api_url: str, payload: dict, log_file: str, log_enabled: bool = True) -> dict:
    if log_enabled:
        log_request(api_url, payload, log_file)
    response = requests.post(api_url, json=payload, timeout=60)
    raw_text = response.text
    if log_enabled:
        log_response(raw_text, response.status_code, log_file)
    if not response.ok:
        parsed_error = None
        try:
            parsed_error = response.json()
        except Exception:
            parsed_error = None
        return {
            "_http_error": True,
            "status_code": response.status_code,
            "raw_text": raw_text,
            "json": parsed_error,
        }
    try:
        return response.json()
    except Exception:
        out = {
            "error": "non_json_response",
            "http_status": response.status_code,
            "text": raw_text[:5000],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise


def main() -> None:
    load_dotenv()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base_name = f"auto_test_{ts}"
    
    # Создание проекта с "type": "hosts"  (сайты)
    """
    create_payload = {
        "token": token,
        "command": "gck_project_create",
        "type": "hosts",
        "src": "bl",
        "name": base_name,
        "tag": base_name,
        "limit": 1,
        "content": "test502312262.ru,test402312262.ru",
        "status": 0,  # проект должен быть выключен
        "is_crm": 0,
        "workdays": "12345",
    }
    """
    # Создание проекта с "type": "calls  (звонки)
    create_payload = {
        "token": token,
        "command": "gck_project_create",
        "type": "calls",
        "src": "rt",
        "name": base_name,
        "tag": base_name,
        "limit": 1,
        "content": "79231920440,79231920441",
        "status": 0,  # проект должен быть выключен
        "is_crm": 0,
        "workdays": "12345",
    }

    log_file = f"gck_api_log_{ts}.txt"
    create_response = post_json(api_url, create_payload, log_file)
    if create_response.get("_http_error"):
        items = [d.strip() for d in create_payload.get("content", "").split(",") if d.strip()]
        duplicates = {}
        target_type = str(create_payload.get("type") or "").strip()
        target_src = str(create_payload.get("src") or "").strip()
        duplicate_check = {
            "target_type": target_type,
            "target_src": target_src,
            "min_id": 4189111,
            "projects_total": 0,
            "projects_filtered": 0,
            "projects_scanned": 0,
            "project_errors": 0,
            "projects_list_error": None,
        }
        if items and target_type in ("hosts", "calls"):
            projects_resp = post_json(
                api_url,
                {"token": token, "command": "gck_projects"},
                log_file,
                log_enabled=False,
            )
            if projects_resp.get("_http_error"):
                duplicate_check["projects_list_error"] = {
                    "status_code": projects_resp.get("status_code"),
                    "raw_text": projects_resp.get("raw_text"),
                }
                projects = []
            else:
                projects = projects_resp.get("result") or []
            duplicate_check["projects_total"] = len(projects)
            min_id = duplicate_check["min_id"]
            filtered = []
            for item in projects:
                if item.get("type") != target_type:
                    continue
                try:
                    pid = int(str(item.get("id", "")).strip())
                except Exception:
                    continue
                if pid < min_id:
                    continue
                filtered.append((pid, item))
            duplicate_check["projects_filtered"] = len(filtered)
            filtered.sort(key=lambda x: x[0], reverse=True)
            remaining = set(items)
            for pid, item in filtered:
                if not remaining:
                    break
                project_id = str(pid)
                detail = post_json(
                    api_url,
                    {"token": token, "command": "gck_project", "id": project_id},
                    log_file,
                    log_enabled=False,
                )
                duplicate_check["projects_scanned"] += 1
                if detail.get("_http_error"):
                    duplicate_check["project_errors"] += 1
                    continue
                detail_result = detail.get("result") or {}
                if target_src and detail_result.get("src") != target_src:
                    continue
                content = str(detail_result.get("content") or "")
                if not content:
                    continue
                for d in list(remaining):
                    if d in content:
                        duplicates.setdefault(d, []).append(
                            f"{detail_result.get('id')}|{detail_result.get('name')}"
                        )
                        remaining.discard(d)

        out = {
            "created_request": create_payload,
            "create_response": create_response,
            "duplicate_items": duplicates,
            "duplicate_check": duplicate_check,
        }
        out_file = sys.argv[1] if len(sys.argv) > 1 else f"gck_create_and_dump_{ts}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"Saved result to: {out_file}")
        print(f"Saved API log to: {log_file}")
        if duplicates:
            label = "домены" if target_type == "hosts" else "номера"
            print(f"Проект не создан. Эти {label} уже используются:")
            for item, projects in duplicates.items():
                print(f"  - {item} -> {', '.join(projects)}")
            print("Удалите их из других проектов или укажите другие.")
        elif duplicate_check.get("projects_list_error") or duplicate_check.get("project_errors"):
            print("Не удалось проверить дубли полностью (ошибки при чтении проектов).")
            print("Смотрите gck_create_and_dump_*.json для деталей.")
        else:
            print("API request failed. See raw response in log.")
        return
    create_result = create_response.get("result") or {}
    project_id = str(create_result.get("id")) if create_result.get("id") is not None else None

    project_details = None
    if project_id:
        project_details = post_json(api_url, {"token": token, "command": "gck_project", "id": project_id}, log_file)

    out = {
        "created_request": create_payload,
        "create_response": create_response,
        "selected_project_id": project_id,
        "project_details": project_details,
    }

    out_file = sys.argv[1] if len(sys.argv) > 1 else f"gck_create_and_dump_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Saved result to: {out_file}")
    print(f"Saved API log to: {log_file}")
    if project_id:
        print("Created project id:", project_id)
    else:
        print("Created project id not returned in response.")


if __name__ == "__main__":
    main()
