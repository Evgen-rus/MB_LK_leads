import json
import os
from typing import Dict, List, Optional, Tuple

import requests

from .. import models, schemas

API_URL_DEFAULT = "https://prostats.info/api/index.php"
MIN_DUPLICATE_ID = 4189111


class ProstatsError(Exception):
    def __init__(self, message: str, status_code: int = 500, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _get_api_url() -> str:
    return os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT


def _get_token() -> str:
    token = os.getenv("PROSTATS_TOKEN", "").strip()
    if not token:
        raise ProstatsError("PROSTATS_TOKEN is not set", status_code=500)
    return token


def _post(payload: dict) -> Tuple[int, str, Optional[dict]]:
    response = requests.post(_get_api_url(), json=payload, timeout=60)
    raw_text = response.text
    parsed = None
    try:
        parsed = response.json()
    except Exception:
        parsed = None
    return response.status_code, raw_text, parsed


def _workdays_from_days(days: List[schemas.Day]) -> str:
    mapping = {
        "Пн": "1",
        "Вт": "2",
        "Ср": "3",
        "Чт": "4",
        "Пт": "5",
        "Сб": "6",
        "Вс": "7",
    }
    return "".join(mapping[d] for d in days if d in mapping)


def _type_from_collection(collection: schemas.CollectionSource) -> str:
    if collection in ("Сайты", "Ретросайты"):
        return "hosts"
    if collection in ("Звонки", "Ретрозвонки"):
        return "calls"
    if collection == "Пересечение":
        return "complex"
    raise ProstatsError(f"Collection source {collection} is not supported", status_code=400)


def _src_from_code(code: schemas.DataSourceCode) -> str:
    mapping = {
        "B1": "rt",
        "B2": "bl",
        "B3": "mt",
        "B4": "mg",
    }
    if code not in mapping:
        raise ProstatsError(f"Data source code {code} is not supported", status_code=400)
    return mapping[code]


def _normalize_items(items: Optional[List[str]]) -> List[str]:
    if not items:
        return []
    return [s.strip() for s in items if s and s.strip()]


def _normalize_regions(regions: Optional[List[str]]) -> List[int]:
    if not regions:
        return []
    normalized: List[int] = []
    for raw in regions:
        value = str(raw or "").strip()
        if not value:
            continue
        try:
            normalized.append(int(value))
        except ValueError as exc:
            raise ProstatsError(f"Region code must be numeric: {value}", status_code=400) from exc
    return normalized


def _build_complex_content(sites: List[str], phones: List[str], sms: Optional[str]) -> str:
    payload = {
        "hosts_content": ",".join(sites),
        "hosts_cnt": 0,
        "calls_content": ",".join(phones),
        "calls_cnt": 0,
        "sms_content": sms or "",
        "sms_regular_value": "",
        "sms_cnt": 0,
    }
    return json.dumps(payload, ensure_ascii=False)


def _strip_provider_prefix(name: str) -> str:
    raw = (name or "").strip()
    for prefix in ("B1_", "B2_", "B3_", "B4_"):
        if raw.startswith(prefix):
            return raw[len(prefix):].strip()
    return raw


def build_create_payload(item: schemas.CreateProjectItem) -> dict:
    token = _get_token()
    p_type = _type_from_collection(item.collectionSource)
    src = _src_from_code(item.dataSourceCode)

    sites = _normalize_items(item.sites)
    phones = _normalize_items(item.phones)
    sms = (item.smsSenderName or "").strip() or None

    if p_type == "complex" and src != "bl":
        raise ProstatsError("Complex projects can be created only for src=bl (B2)", status_code=400)

    if p_type == "hosts":
        content = ",".join(sites)
    elif p_type == "calls":
        content = ",".join(phones)
    else:
        content = _build_complex_content(sites, phones, sms)

    status = 1 if item.status == "Активен" else 0

    base_name = _strip_provider_prefix(item.name)
    base_tag = _strip_provider_prefix(item.tag or item.name)
    payload = {
        "token": token,
        "command": "gck_project_create",
        "type": p_type,
        "src": src,
        "name": base_name,
        "tag": base_tag or base_name,
        "limit": item.dataLimit,
        "content": content,
        "status": status,
        "is_crm": 0,
        "workdays": _workdays_from_days(item.days),
    }
    return payload


def build_update_payload(
    provider_id: str,
    project: models.Project,
    update: schemas.ProjectUpdate | schemas.AdminProjectUpdate,
) -> dict:
    token = _get_token()
    p_type = _type_from_collection(project.collection_source)
    src = _src_from_code(project.data_source_code)

    sites = _normalize_items(update.sites)
    phones = _normalize_items(update.phones)
    sms = (update.smsSenderName or "").strip() or None

    if p_type == "complex" and src != "bl":
        raise ProstatsError("Complex projects can be updated only for src=bl (B2)", status_code=400)

    if p_type == "hosts":
        content = ",".join(sites)
    elif p_type == "calls":
        content = ",".join(phones)
    else:
        content = _build_complex_content(sites, phones, sms)

    status = 1 if update.status == "Активен" else 0

    base_name = _strip_provider_prefix(update.name)
    base_tag = _strip_provider_prefix(update.tag or update.name)

    regions = _normalize_regions(update.regions)
    regions_reverse = 1 if update.regionMode == "exclude" else 0

    try:
        provider_id_value: int | str = int(str(provider_id).strip())
    except Exception:
        provider_id_value = str(provider_id).strip()

    payload = {
        "token": token,
        "command": "gck_project_update",
        "id": provider_id_value,
        "name": base_name,
        "limit": update.dataLimit,
        "content": content,
        "status": status,
        "tag": base_tag or base_name,
        "workdays": _workdays_from_days(update.days),
        "regions": regions,
        "regions_reverse": regions_reverse,
    }
    return payload


def _extract_error_message(parsed: Optional[dict], raw_text: str) -> str:
    if not parsed:
        return raw_text or "Unknown error"
    msg = parsed.get("message")
    if isinstance(msg, dict):
        return str(msg.get("message") or msg.get("status") or raw_text or "Unknown error")
    return str(msg or raw_text or "Unknown error")


def _should_check_duplicates(error_message: str, target_type: str) -> bool:
    if target_type == "hosts" and "Введите домены" in error_message:
        return True
    if target_type == "calls" and "Введите номера телефонов" in error_message:
        return True
    return False


def _find_duplicates(items: List[str], target_type: str, target_src: str) -> Dict[str, List[str]]:
    duplicates: Dict[str, List[str]] = {}
    if not items:
        return duplicates

    status, raw_text, parsed = _post({"token": _get_token(), "command": "gck_projects"})
    if status >= 400:
        return duplicates
    projects = (parsed or {}).get("result") or []

    filtered: List[int] = []
    for item in projects:
        if item.get("type") != target_type:
            continue
        try:
            pid = int(str(item.get("id", "")).strip())
        except Exception:
            continue
        if pid < MIN_DUPLICATE_ID:
            continue
        filtered.append(pid)

    filtered.sort(reverse=True)
    remaining = set(items)
    for pid in filtered:
        if not remaining:
            break
        status, raw_text, parsed = _post({"token": _get_token(), "command": "gck_project", "id": pid})
        if status >= 400:
            continue
        detail = (parsed or {}).get("result") or {}
        if target_src and detail.get("src") != target_src:
            continue
        content = str(detail.get("content") or "")
        if not content:
            continue
        for value in list(remaining):
            if value in content:
                duplicates.setdefault(value, []).append(f"{detail.get('id')}|{detail.get('name')}")
                remaining.discard(value)

    return duplicates


def _parse_content_list(content: str) -> List[str]:
    return [s.strip() for s in content.split(",") if s.strip()]


def _get_project(provider_id: str) -> Optional[dict]:
    status, raw_text, parsed = _post({"token": _get_token(), "command": "gck_project", "id": provider_id})
    if status >= 400:
        return None
    return (parsed or {}).get("result") or None


def _build_partial_warning(name: str, target_type: str, missing: List[str], duplicates: Dict[str, List[str]]) -> str:
    label = "домены" if target_type == "hosts" else "номера"
    lines: List[str] = []
    for item in missing:
        if item in duplicates and duplicates[item]:
            lines.append(f"{item} -> {', '.join(duplicates[item])}")
        else:
            lines.append(item)
    return (
        f'Проект "{name}" создан, но некоторые {label} уже используются в других проектах:\n'
        + "\n".join(lines)
        + "\nУдалите их из других проектов или укажите другие."
    )


def create_project(item: schemas.CreateProjectItem) -> dict:
    payload = build_create_payload(item)
    status_code, raw_text, parsed = _post(payload)

    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        error_message = _extract_error_message(parsed, raw_text)
        target_type = payload.get("type")
        duplicates = {}
        if _should_check_duplicates(error_message, target_type):
            items = [s.strip() for s in str(payload.get("content") or "").split(",") if s.strip()]
            duplicates = _find_duplicates(items, target_type, payload.get("src") or "")

        raise ProstatsError(
            error_message,
            status_code=422 if duplicates else status_code,
            details={"duplicates": duplicates, "raw": raw_text},
        )

    result = parsed.get("result") or {}
    provider_id = result.get("id")
    warning = None
    missing_items: List[str] = []

    target_type = payload.get("type")
    if provider_id and target_type in ("hosts", "calls"):
        expected = _parse_content_list(str(payload.get("content") or ""))
        detail = _get_project(str(provider_id))
        if detail:
            actual = _parse_content_list(str(detail.get("content") or ""))
            missing_items = sorted(set(expected) - set(actual))
            if missing_items:
                duplicates = _find_duplicates(missing_items, target_type, payload.get("src") or "")
                warning = _build_partial_warning(item.name, target_type, missing_items, duplicates)

    return {
        "provider_id": provider_id,
        "raw": parsed,
        "warning": warning,
        "missing_items": missing_items,
        "provider_content": str(detail.get("content") or "") if provider_id and detail else "",
        "target_type": target_type,
    }


def update_project(
    provider_id: str,
    project: models.Project,
    update: schemas.ProjectUpdate | schemas.AdminProjectUpdate,
) -> dict:
    payload = build_update_payload(provider_id, project, update)
    status_code, raw_text, parsed = _post(payload)

    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        error_message = _extract_error_message(parsed, raw_text)
        raise ProstatsError(error_message, status_code=status_code, details={"raw": raw_text})

    return {"raw": parsed}
