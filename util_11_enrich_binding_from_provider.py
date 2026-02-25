"""
Назначение:
- Обогатить файл project_client_binding_template_filled_sorted.json данными из API поставщика (gck_project),
  чтобы подготовить записи к созданию проектов в ЛК.

Что заполняет для каждого item (если есть provider_project_id и запрос успешен):
- name
- tag
- collectionSource
- dataSourceCode
- dataLimit
- status
- regionMode (всегда null, заполняется вручную позже)
- regions
- sites / phones / smsSenderName
- days

Особенности:
- days берется из provider.workdays, а если данных нет/они некорректны -> ["Вт","Ср","Чт","Пт","Сб"].
- Для сети/API используются ретраи.
- Между запросами к API есть задержка (по умолчанию 0.5 сек).
- В конце печатается сводка и список проектов с непустыми regions.

Запуск:
    python util_11_enrich_binding_from_provider.py ^
      --template project_client_binding_template_filled_sorted.json ^
      --out project_client_binding_template_enriched.json

Параметры:
    --template      входной JSON (обязательный)
    --out           выходной JSON (обязательный)
    --retries       число попыток на запрос (по умолчанию 3)
    --sleep-seconds пауза между проектами (по умолчанию 0.5)
    --retry-delay   базовая задержка между ретраями, сек (по умолчанию 1.0)
    --timeout       timeout HTTP, сек (по умолчанию 60)
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


API_URL_DEFAULT = "https://prostats.info/api/index.php"
DEFAULT_DAYS = ["Вт", "Ср", "Чт", "Пт", "Сб"]

DAY_MAP = {
    "1": "Пн",
    "2": "Вт",
    "3": "Ср",
    "4": "Чт",
    "5": "Пт",
    "6": "Сб",
    "7": "Вс",
}

TYPE_TO_COLLECTION = {
    "hosts": "Сайты",
    "calls": "Звонки",
    "sms": "СМС",
    "complex": "Пересечение",
}

SRC_TO_CODE = {
    "rt": "B1",
    "bl": "B2",
    "mt": "B3",
    "mg": "B4",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Обогатить binding JSON данными gck_project")
    parser.add_argument("--template", required=True, help="Входной JSON-шаблон")
    parser.add_argument("--out", required=True, help="Выходной JSON")
    parser.add_argument("--retries", type=int, default=3, help="Число попыток запроса")
    parser.add_argument("--sleep-seconds", type=float, default=0.5, help="Пауза между проектами")
    parser.add_argument("--retry-delay", type=float, default=1.0, help="Базовая задержка между ретраями")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout (сек)")
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var {name}. Add it to .env or environment.")
    return value


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit("Некорректный JSON: ожидается объект")
    return data


def save_json(path: str, data: Dict[str, Any]) -> None:
    # Формат для чтения человеком:
    # - поля объекта остаются "в столбик" (по одному ключу на строку),
    # - значения (включая массивы) пишутся в одну строку.
    if isinstance(data.get("items"), list):
        with open(path, "w", encoding="utf-8") as f:
            f.write("{\n")
            f.write('  "items": [\n')
            items = data["items"]
            for idx, item in enumerate(items):
                if isinstance(item, dict):
                    f.write("    {\n")
                    keys = list(item.keys())
                    for k_idx, key in enumerate(keys):
                        value_str = json.dumps(
                            item[key],
                            ensure_ascii=False,
                            separators=(", ", ": "),
                        )
                        comma = "," if k_idx < len(keys) - 1 else ""
                        f.write(f'      "{key}": {value_str}{comma}\n')
                    item_comma = "," if idx < len(items) - 1 else ""
                    f.write(f"    }}{item_comma}\n")
                else:
                    line = json.dumps(item, ensure_ascii=False, separators=(", ", ": "))
                    item_comma = "," if idx < len(items) - 1 else ""
                    f.write(f"    {line}{item_comma}\n")
            f.write("  ]\n")
            f.write("}\n")
        return

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def to_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def split_csv(value: Any) -> List[str]:
    if value is None:
        return []
    raw = str(value)
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_regions(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                return [str(v).strip() for v in arr if str(v).strip()]
        except Exception:
            pass
    raw = raw.replace(";", ",")
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_days(workdays: Any) -> List[str]:
    if workdays is None:
        return DEFAULT_DAYS.copy()
    raw = str(workdays).strip()
    if not raw:
        return DEFAULT_DAYS.copy()
    out: List[str] = []
    for ch in raw:
        day = DAY_MAP.get(ch)
        if day and day not in out:
            out.append(day)
    return out if out else DEFAULT_DAYS.copy()


def parse_complex_content(content: Any) -> Tuple[List[str], List[str], Optional[str]]:
    raw = str(content or "").strip()
    if not raw:
        return [], [], None

    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            sites = split_csv(payload.get("hosts_content"))
            phones = split_csv(payload.get("calls_content"))
            sms = str(payload.get("sms_content") or "").strip() or None
            return sites, phones, sms
    except Exception:
        pass

    # fallback: если content не JSON, трактуем как телефонные значения
    return [], split_csv(raw), None


def map_provider_result(result: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []

    provider_name = str(result.get("name") or "").strip()
    provider_tag = str(result.get("tag") or "").strip() or provider_name

    provider_type = str(result.get("type") or "").strip().lower()
    if provider_type not in TYPE_TO_COLLECTION:
        raise ValueError(f"Unknown provider type: {provider_type!r}")
    collection_source = TYPE_TO_COLLECTION[provider_type]

    provider_src = str(result.get("src") or "").strip().lower()
    data_source_code = SRC_TO_CODE.get(provider_src, "UNMAPPED")
    if data_source_code == "UNMAPPED":
        warnings.append(f"unknown src={provider_src!r}, set dataSourceCode=UNMAPPED")

    status_raw = to_int(result.get("status"))
    if status_raw == 1:
        status = "Активен"
    elif status_raw == 0:
        status = "На паузе"
    else:
        status = "На паузе"
        warnings.append(f"unknown status={result.get('status')!r}, fallback='На паузе'")

    data_limit = to_int(result.get("limit"))
    if data_limit is None:
        data_limit = 0
        warnings.append("limit is missing/invalid, fallback=0")

    regions = parse_regions(result.get("regions"))
    days = parse_days(result.get("workdays"))

    content = result.get("content")
    sites: List[str] = []
    phones: List[str] = []
    sms_sender_name: Optional[str] = None

    if provider_type == "hosts":
        sites = split_csv(content)
    elif provider_type == "calls":
        phones = split_csv(content)
    elif provider_type == "sms":
        sms_sender_name = str(content or "").strip() or None
    else:
        sites, phones, sms_sender_name = parse_complex_content(content)

    mapped = {
        "name": provider_name,
        "tag": provider_tag,
        "collectionSource": collection_source,
        "dataSourceCode": data_source_code,
        "dataLimit": data_limit,
        "status": status,
        "regionMode": None,
        "regions": regions,
        "sites": sites or None,
        "phones": phones or None,
        "smsSenderName": sms_sender_name,
        "days": days,
    }
    return mapped, warnings


def post_gck_project(
    api_url: str,
    token: str,
    provider_project_id: str,
    retries: int,
    retry_delay: float,
    timeout: int,
) -> Dict[str, Any]:
    payload = {"token": token, "command": "gck_project", "id": provider_project_id}

    last_error: Optional[str] = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(api_url, json=payload, timeout=timeout)
            raw = response.text
            parsed: Optional[Dict[str, Any]]
            try:
                parsed = response.json()
            except Exception:
                parsed = None

            http_ok = response.status_code < 400
            api_ok = bool(parsed and parsed.get("status") == "success")
            if http_ok and api_ok:
                result = parsed.get("result")
                if isinstance(result, dict):
                    return result
                last_error = "success response without object result"
            else:
                msg = None
                if isinstance(parsed, dict):
                    msg = parsed.get("message")
                if msg is None:
                    msg = raw[:500]
                last_error = f"http={response.status_code}, api_status={(parsed or {}).get('status') if parsed else None}, msg={msg}"
        except requests.RequestException as exc:
            last_error = f"request_error: {exc}"

        if attempt < retries:
            sleep_for = retry_delay * attempt
            print(
                f"  [RETRY] provider_project_id={provider_project_id} "
                f"attempt={attempt}/{retries}, wait={sleep_for:.1f}s, reason={last_error}"
            )
            time.sleep(sleep_for)

    raise RuntimeError(last_error or "unknown error")


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_url = os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT
    token = require_env("PROSTATS_TOKEN")

    template = load_json(args.template)
    items = template.get("items")
    if not isinstance(items, list):
        raise SystemExit("Некорректный формат template: поле 'items' должно быть массивом")

    total = len(items)
    ok_count = 0
    skip_count = 0
    err_count = 0

    skipped: List[str] = []
    errors: List[str] = []
    region_non_empty: List[str] = []

    print(f"[INFO] Start enrich: items={total}, retries={args.retries}, sleep={args.sleep_seconds}s")

    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            skip_count += 1
            reason = f"index={idx}: item is not object"
            skipped.append(reason)
            print(f"[SKIP] {reason}")
            continue

        provider_project_id = str(item.get("provider_project_id") or "").strip()
        project_name = str(item.get("project_name") or "").strip() or f"<index:{idx}>"

        if not provider_project_id:
            skip_count += 1
            reason = f"index={idx} project={project_name}: empty provider_project_id"
            skipped.append(reason)
            print(f"[SKIP] {reason}")
            continue

        try:
            result = post_gck_project(
                api_url=api_url,
                token=token,
                provider_project_id=provider_project_id,
                retries=max(1, args.retries),
                retry_delay=max(0.0, args.retry_delay),
                timeout=max(1, args.timeout),
            )
            mapped, warnings = map_provider_result(result)
            item.update(mapped)

            if item.get("regions"):
                region_non_empty.append(
                    f"provider_project_id={provider_project_id} name={item.get('name')} regions={item.get('regions')}"
                )

            ok_count += 1
            if warnings:
                print(
                    f"[OK]  {idx}/{total} provider_project_id={provider_project_id} "
                    f"name={item.get('name')} warnings={' | '.join(warnings)}"
                )
            else:
                print(
                    f"[OK]  {idx}/{total} provider_project_id={provider_project_id} "
                    f"name={item.get('name')}"
                )
        except Exception as exc:
            err_count += 1
            reason = (
                f"index={idx} provider_project_id={provider_project_id} "
                f"project={project_name}: {exc}"
            )
            errors.append(reason)
            print(f"[ERR] {reason}")

        if idx < total:
            time.sleep(max(0.0, args.sleep_seconds))

    save_json(args.out, template)

    print("\n[SUMMARY]")
    print(f"total={total}")
    print(f"ok={ok_count}")
    print(f"skip={skip_count}")
    print(f"err={err_count}")
    print(f"out={args.out}")

    if skipped:
        print("\n[SKIPPED]")
        for line in skipped:
            print(f"- {line}")

    if errors:
        print("\n[ERRORS]")
        for line in errors:
            print(f"- {line}")

    print("\n[REGIONS_NON_EMPTY]")
    if not region_non_empty:
        print("none")
    else:
        for line in region_non_empty:
            print(f"- {line}")


if __name__ == "__main__":
    main()
