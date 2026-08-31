import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import requests

from .. import models, schemas

API_URL_DEFAULT = "https://prostats.info/api/index.php"
MIN_DUPLICATE_ID = 4189111
PROVIDER_TEMPORARILY_UNAVAILABLE_MESSAGE = (
    "Сервис поставщика временно недоступен. Попробуйте повторить операцию позже."
)
PROVIDER_INVALID_RESPONSE_MESSAGE = "Сервис поставщика вернул некорректный ответ."

ERROR_KIND_TRANSIENT = "transient"
ERROR_KIND_PERMANENT = "permanent"
ERROR_KIND_UNKNOWN = "unknown"
TRANSIENT_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
PERMANENT_HTTP_STATUS_CODES = frozenset({400, 401, 403, 404, 405, 409, 422})

# Provider error messages are useful for validation feedback, but they must not
# accidentally turn credentials or a response dump into an API error.  Keep
# this deliberately small: unknown/non-JSON responses use a generic message.
_SENSITIVE_MESSAGE_RE = re.compile(
    r"(?i)(token|authorization|password|secret|api[_ -]?key)\s*[:=]\s*[^,;\s]+"
)
_AUTH_ERROR_MARKERS = (
    "unauthor",
    "forbidden",
    "authentication",
    "access denied",
    "invalid token",
    "token expired",
    "неверн.*токен",
    "токен.*неверн",
    "авторизац",
    "доступ запрещ",
)
_VALIDATION_ERROR_MARKERS = (
    "invalid",
    "validation",
    "required",
    "неверн",
    "некоррект",
    "обязатель",
    "не поддерж",
    "не найден",
    "недопустим",
)
_TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "temporary",
    "temporar",
    "unavailable",
    "too many requests",
    "bad gateway",
    "gateway",
    "connection",
    "connect",
    "connection reset",
    "временно",
    "таймаут",
    "соедин",
    "шлюз",
)

REGION_CODE_BY_NAME: Dict[str, int] = {
    "Республика Адыгея": 1,
    "Республика Башкортостан": 2,
    "Республика Алтай": 4,
    "Республика Дагестан": 5,
    "Республика Ингушетия": 6,
    "Республика Кабардино-Балкарская": 7,
    "Республика Калмыкия": 8,
    "Республика Карачаево-Черкесская": 9,
    "Республика Карелия": 10,
    "Республика Коми": 11,
    "Республика Марий Эл": 12,
    "Республика Мордовия": 13,
    "Республика Саха (Якутия)": 14,
    "Республика Северная Осетия - Алания": 15,
    "Республика Татарстан": 16,
    "Республика Тыва": 17,
    "Республика Удмуртская": 18,
    "Республика Хакасия": 19,
    "Чеченская Республика": 20,
    "Чувашская Республика": 21,
    "Алтайский край": 22,
    "Краснодарский край": 23,
    "Красноярский край": 24,
    "Приморский край": 25,
    "Ставропольский край": 26,
    "Хабаровский край": 27,
    "Амурская обл.": 28,
    "Архангельская обл.": 29,
    "Астраханская обл.": 30,
    "Белгородская обл.": 31,
    "Брянская обл.": 32,
    "Владимирская обл.": 33,
    "Волгоградская обл.": 34,
    "Вологодская обл.": 35,
    "Воронежская обл.": 36,
    "Ивановская обл.": 37,
    "Иркутская обл.": 38,
    "Калининградская обл.": 39,
    "Калужская обл.": 40,
    "Камчатский край": 41,
    "Кемеровская обл.": 42,
    "Кировская обл.": 43,
    "Костромская обл.": 44,
    "Курганская обл.": 45,
    "Курская обл.": 46,
    "Липецкая обл.": 48,
    "Магаданская обл.": 49,
    "Мурманская обл.": 51,
    "Нижегородская обл.": 52,
    "Новгородская обл.": 53,
    "Новосибирская обл.": 54,
    "Омская обл.": 55,
    "Оренбургская обл.": 56,
    "Орловская обл.": 57,
    "Пензенская обл.": 58,
    "Пермский край": 59,
    "Псковская обл.": 60,
    "Ростовская обл.": 61,
    "Рязанская обл.": 62,
    "Самарская обл.": 63,
    "Саратовская обл.": 64,
    "Сахалинская обл.": 65,
    "Свердловская обл.": 66,
    "Смоленская обл.": 67,
    "Тамбовская обл.": 68,
    "Тверская обл.": 69,
    "Томская обл.": 70,
    "Тульская обл.": 71,
    "Тюменская обл.": 72,
    "Ульяновская обл.": 73,
    "Челябинская обл.": 74,
    "Ярославская обл.": 76,
    "г. Москва": 77,
    "г. Санкт-Петербург": 78,
    "Еврейская автономная обл.": 79,
    "Ханты-Мансийский АО - Югра": 86,
    "Чукотский АО": 87,
}


class ProstatsError(Exception):
    """A provider failure with retry and reconciliation metadata.

    ``kind`` is intentionally a string rather than an enum so existing code
    can keep serialising/recording ``ProstatsError`` without changes.  The
    ``ambiguous`` flag means a write may have reached the provider although
    its response did not reach us; callers must reconcile before retrying.
    ``details`` is metadata only and must never contain a response body or
    request payload.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        details: Optional[dict] = None,
        *,
        kind: Optional[str] = None,
        ambiguous: bool = False,
    ):
        safe_message = _sanitize_error_message(message)
        super().__init__(safe_message)
        self.message = safe_message
        self.status_code = int(status_code or 500)
        self.kind = kind or classify_prostats_error(self.status_code, safe_message)
        self.category = self.kind
        self.ambiguous = bool(ambiguous)
        # Defensive copy: callers should not be able to add an unsafe payload
        # to an exception that may later be returned by an API handler.
        self.details = _safe_error_details(details)

    @property
    def is_transient(self) -> bool:
        return self.kind == ERROR_KIND_TRANSIENT

    @property
    def is_permanent(self) -> bool:
        return self.kind == ERROR_KIND_PERMANENT

    @property
    def is_ambiguous(self) -> bool:
        return self.ambiguous

    @property
    def retryable(self) -> bool:
        return self.is_transient

    @property
    def requires_reconciliation(self) -> bool:
        return self.ambiguous


def _sanitize_error_message(message: object) -> str:
    text = str(message or "").strip()
    if not text:
        return "Неизвестная ошибка сервиса поставщика."
    text = _SENSITIVE_MESSAGE_RE.sub(r"\1=[REDACTED]", text)
    # Keep provider validation messages useful, but cap what can cross the
    # application boundary.  A raw response body is never used as a message.
    return text[:500]


def _safe_error_details(details: Optional[dict]) -> dict:
    """Return only scalar diagnostic metadata, never raw provider content."""
    if not isinstance(details, dict):
        return {}
    safe: dict = {}
    allowed_keys = {
        "provider_status_code",
        "response_format",
        "source",
        "exception_type",
        "ambiguous",
        "category",
    }
    for key in allowed_keys:
        if key not in details:
            continue
        value = details.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe


def _contains_error_marker(text: str, markers: Tuple[str, ...]) -> bool:
    normalized = str(text or "").strip().lower()
    return any(re.search(marker, normalized) for marker in markers)


def classify_prostats_error(
    status_code: Optional[int],
    message: str = "",
    *,
    parsed: Optional[dict] = None,
) -> str:
    """Classify a failure for a durable worker.

    Transport failures and 429/5xx responses are retryable.  Auth and
    validation failures are permanent.  A JSON provider-level error usually
    arrives with HTTP 200, so its message is inspected before falling back to
    a permanent business error.
    """
    try:
        code = int(status_code or 0)
    except (TypeError, ValueError):
        code = 0

    if code in TRANSIENT_HTTP_STATUS_CODES:
        return ERROR_KIND_TRANSIENT
    if code in PERMANENT_HTTP_STATUS_CODES:
        return ERROR_KIND_PERMANENT

    provider_code: Optional[int] = None
    if isinstance(parsed, dict):
        candidates = [parsed.get("code"), parsed.get("error_code"), parsed.get("status_code")]
        provider_message = parsed.get("message")
        if isinstance(provider_message, dict):
            candidates.extend(
                [
                    provider_message.get("code"),
                    provider_message.get("status_code"),
                    provider_message.get("http_code"),
                ]
            )
        for candidate in candidates:
            try:
                provider_code = int(candidate)
                break
            except (TypeError, ValueError):
                continue
    if provider_code in TRANSIENT_HTTP_STATUS_CODES:
        return ERROR_KIND_TRANSIENT
    if provider_code in PERMANENT_HTTP_STATUS_CODES:
        return ERROR_KIND_PERMANENT

    if _contains_error_marker(message, _AUTH_ERROR_MARKERS):
        return ERROR_KIND_PERMANENT
    if _contains_error_marker(message, _VALIDATION_ERROR_MARKERS):
        return ERROR_KIND_PERMANENT
    if _contains_error_marker(message, _TRANSIENT_ERROR_MARKERS):
        return ERROR_KIND_TRANSIENT

    # A non-JSON response to an otherwise successful HTTP request is a
    # protocol/service failure and is safe to retry.  Provider JSON errors
    # without a recognised code are business failures, not endless retries.
    if code == 0:
        return ERROR_KIND_TRANSIENT
    if code < 400 and parsed is None:
        return ERROR_KIND_TRANSIENT
    if code >= 500:
        return ERROR_KIND_TRANSIENT
    if 400 <= code < 500:
        return ERROR_KIND_PERMANENT
    if parsed is not None:
        return ERROR_KIND_PERMANENT
    return ERROR_KIND_UNKNOWN


def _get_api_url() -> str:
    return os.getenv("PROSTATS_API_URL", API_URL_DEFAULT).strip() or API_URL_DEFAULT


def _get_token() -> str:
    token = os.getenv("PROSTATS_TOKEN", "").strip()
    if not token:
        raise ProstatsError(
            "PROSTATS_TOKEN is not set",
            status_code=500,
            kind=ERROR_KIND_PERMANENT,
            details={"source": "configuration", "category": ERROR_KIND_PERMANENT},
        )
    return token


def _post(payload: dict) -> Tuple[int, str, Optional[dict]]:
    try:
        response = requests.post(_get_api_url(), json=payload, timeout=60)
    except requests.exceptions.Timeout as exc:
        logging.getLogger("app.prostats").warning(
            "Prostats request timed out: command=%s",
            payload.get("command"),
            exc_info=True,
        )
        raise ProstatsError(
            PROVIDER_TEMPORARILY_UNAVAILABLE_MESSAGE,
            status_code=504,
            details={
                "source": "transport",
                "exception_type": type(exc).__name__,
                "ambiguous": True,
                "category": ERROR_KIND_TRANSIENT,
            },
            kind=ERROR_KIND_TRANSIENT,
            ambiguous=True,
        ) from exc
    except (
        requests.exceptions.InvalidURL,
        requests.exceptions.InvalidSchema,
        requests.exceptions.MissingSchema,
    ) as exc:
        logging.getLogger("app.prostats").error(
            "Invalid Prostats API URL configuration: command=%s exception_type=%s",
            payload.get("command"),
            type(exc).__name__,
        )
        raise ProstatsError(
            "Некорректно настроен адрес сервиса поставщика.",
            status_code=500,
            details={
                "source": "configuration",
                "exception_type": type(exc).__name__,
                "ambiguous": False,
                "category": ERROR_KIND_PERMANENT,
            },
            kind=ERROR_KIND_PERMANENT,
            ambiguous=False,
        ) from exc
    except requests.exceptions.RequestException as exc:
        logging.getLogger("app.prostats").warning(
            "Prostats request failed: command=%s",
            payload.get("command"),
            exc_info=True,
        )
        raise ProstatsError(
            PROVIDER_TEMPORARILY_UNAVAILABLE_MESSAGE,
            status_code=502,
            details={
                "source": "transport",
                "exception_type": type(exc).__name__,
                "ambiguous": True,
                "category": ERROR_KIND_TRANSIENT,
            },
            kind=ERROR_KIND_TRANSIENT,
            ambiguous=True,
        ) from exc

    raw_text = response.text
    parsed = None
    try:
        parsed = response.json()
    except Exception:
        parsed = None
    if response.status_code >= 500 or parsed is None:
        logging.getLogger("app.prostats").warning(
            "Unexpected Prostats response: command=%s status=%s content_type=%s body_present=%s body_length=%s",
            payload.get("command"),
            response.status_code,
            response.headers.get("Content-Type"),
            bool(raw_text),
            len(raw_text),
        )
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
    if collection == "СМС":
        return "sms"
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


def _region_code_from_value(value: str) -> Optional[int]:
    if value.isdigit():
        return int(value)
    return REGION_CODE_BY_NAME.get(value)


def _normalize_regions(regions: Optional[List[str]]) -> List[int]:
    if not regions:
        return []
    normalized: List[int] = []
    for raw in regions:
        value = str(raw or "").strip()
        if not value:
            continue
        code = _region_code_from_value(value)
        if code is None:
            raise ProstatsError(f"Unknown region: {value}", status_code=400)
        normalized.append(code)
    return normalized


def _normalize_regions_optional(regions: Optional[List[str]]) -> Optional[List[int]]:
    if not regions:
        return None
    return _normalize_regions(regions)


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


def _build_empty_content(target_type: str) -> str:
    if target_type == "complex":
        return _build_complex_content([], [], None)
    return ""


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
    elif p_type == "sms":
        if not sms:
            raise ProstatsError("SMS sender name is required for SMS projects", status_code=400)
        content = sms
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
    if item.regions:
        regions = _normalize_regions(item.regions)
        payload["regions"] = regions
        payload["regions_reverse"] = 1 if item.regionMode == "exclude" else 0
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
    elif p_type == "sms":
        if not sms:
            raise ProstatsError("SMS sender name is required for SMS projects", status_code=400)
        content = sms
    else:
        content = _build_complex_content(sites, phones, sms)

    status = 1 if update.status == "Активен" else 0

    base_name = (update.name or "").strip()
    base_tag = _strip_provider_prefix(update.tag or update.name)

    regions_reverse = 1 if update.regionMode == "exclude" else 0
    raw_regions = update.regions if update.regions is not None else None
    regions = _normalize_regions_optional(raw_regions)

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
    }
    if raw_regions is not None:
        payload["regions"] = regions or []
        payload["regions_reverse"] = regions_reverse
    return payload


def _days_from_days_received(days_received: Optional[str]) -> List[schemas.Day]:
    mapping: Dict[str, schemas.Day] = {
        "Пн": "Пн",
        "Вт": "Вт",
        "Ср": "Ср",
        "Чт": "Чт",
        "Пт": "Пт",
        "Сб": "Сб",
        "Вс": "Вс",
    }
    parts = [str(x).strip().rstrip(".") for x in str(days_received or "").split() if str(x).strip()]
    out: List[schemas.Day] = []
    for part in parts:
        day = mapping.get(part)
        if day:
            out.append(day)
    if out:
        return out
    return ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def build_status_only_payload(provider_id: str, project: models.Project, status: schemas.ProjectMutableStatus) -> dict:
    # Передаём в Prostats полный update-payload, но все поля берём из текущего проекта,
    # меняем только status.
    update_like = type(
        "StatusOnlyUpdate",
        (),
        {
            "name": project.name,
            "tag": project.tag,
            "status": status,
            "dataLimit": int(project.data_limit or 0),
            "regionMode": project.region_mode or "include",
            "regions": list(project.regions or []),
            "sites": list(project.sites or []),
            "phones": list(project.phones or []),
            "smsSenderName": project.sms_sender_name,
            "days": _days_from_days_received(project.days_received),
        },
    )()
    return build_update_payload(provider_id, project, update_like)  # type: ignore[arg-type]


def _looks_like_html(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return any(marker in normalized for marker in ("<!doctype html", "<html", "<head", "<body", "<title", "<h1"))


def _extract_error_message(parsed: Optional[dict], raw_text: str, *, status_code: int) -> str:
    if status_code in TRANSIENT_HTTP_STATUS_CODES or (
        _looks_like_html(raw_text) and status_code not in PERMANENT_HTTP_STATUS_CODES
    ):
        return PROVIDER_TEMPORARILY_UNAVAILABLE_MESSAGE
    if not parsed:
        return PROVIDER_INVALID_RESPONSE_MESSAGE
    msg = parsed.get("message")
    if isinstance(msg, dict):
        message = str(msg.get("message") or msg.get("status") or "")
    else:
        message = str(msg or "")
    if _looks_like_html(message):
        return PROVIDER_TEMPORARILY_UNAVAILABLE_MESSAGE
    return _sanitize_error_message(message) or PROVIDER_INVALID_RESPONSE_MESSAGE


def _error_status_code(status_code: int) -> int:
    return status_code if status_code >= 400 else 502


def _response_error(
    status_code: int,
    raw_text: str,
    parsed: Optional[dict],
    *,
    ambiguous: bool = False,
) -> ProstatsError:
    """Build a safe error from a provider response without retaining its body."""
    message = _extract_error_message(parsed, raw_text, status_code=status_code)
    kind = classify_prostats_error(status_code, message, parsed=parsed)
    effective_status_code = _error_status_code(status_code)
    return ProstatsError(
        message,
        status_code=effective_status_code,
        details={
            "provider_status_code": status_code,
            "response_format": "json" if parsed is not None else "unknown",
            "ambiguous": ambiguous,
            "category": kind,
        },
        kind=kind,
        ambiguous=ambiguous,
    )


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


def get_project(provider_id: str) -> Optional[dict]:
    return _get_project(provider_id)


def _status_code_from_target(target: object) -> int:
    """Normalise the only mutable provider statuses supported by this flow."""
    if isinstance(target, bool):
        raise ProstatsError(
            "Недопустимый статус проекта",
            status_code=400,
            kind=ERROR_KIND_PERMANENT,
        )
    if isinstance(target, int):
        if target in (0, 1):
            return target
    else:
        normalized = str(target or "").strip()
        if normalized in ("Активен", "1"):
            return 1
        if normalized in ("На паузе", "0"):
            return 0
    raise ProstatsError(
        "Недопустимый статус проекта",
        status_code=400,
        kind=ERROR_KIND_PERMANENT,
    )


def _status_code_from_provider(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return normalized if normalized in (0, 1) else None


def reconcile_project_status(provider_id: str, desired_status: object) -> dict:
    """Read provider state and report whether a desired status is applied.

    The helper intentionally returns only identifiers and status values, not
    the provider project payload.  A worker should call it after an ambiguous
    write (for example a timeout) before deciding whether to retry the write.

    Return contract::

        {
            "provider_id": str,
            "current_status": 0 | 1,
            "desired_status": 0 | 1,
            "already_applied": bool,
        }

    Transport/provider read failures raise :class:`ProstatsError`; no raw
    response body is retained in the exception.
    """
    normalized_provider_id = str(provider_id or "").strip()
    if not normalized_provider_id:
        raise ProstatsError(
            "Идентификатор проекта отсутствует",
            status_code=400,
            kind=ERROR_KIND_PERMANENT,
        )
    desired_code = _status_code_from_target(desired_status)
    status_code, raw_text, parsed = _post(
        {"token": _get_token(), "command": "gck_project", "id": normalized_provider_id}
    )
    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise _response_error(status_code, raw_text, parsed)

    result = parsed.get("result")
    # У поставщика пустой успешный result означает выключенное состояние.
    # Для resume это приведёт к update(status=1), для pause — к подтверждению.
    if result in (None, "", [], {}):
        current_code = 0
    elif isinstance(result, dict):
        current_code = _status_code_from_provider(result.get("status"))
    else:
        current_code = None
    if current_code is None:
        raise ProstatsError(
            PROVIDER_INVALID_RESPONSE_MESSAGE,
            status_code=502,
            kind=ERROR_KIND_TRANSIENT,
            details={
                "provider_status_code": status_code,
                "response_format": "json",
                "category": ERROR_KIND_TRANSIENT,
            },
        )
    return {
        "provider_id": normalized_provider_id,
        "current_status": current_code,
        "desired_status": desired_code,
        "already_applied": current_code == desired_code,
    }


# Explicit alias for callers that prefer a noun-style name.  Keep one
# implementation so both names have the same safe response contract.
get_project_status_reconciliation = reconcile_project_status


def _build_partial_warning(
    name: str,
    target_type: str,
    missing: List[str],
    duplicates: Dict[str, List[str]],
    action: str,
) -> str:
    label = "домены" if target_type == "hosts" else "номера"
    lines: List[str] = []
    for item in missing:
        if item in duplicates and duplicates[item]:
            lines.append(f"{item} -> {', '.join(duplicates[item])}")
        else:
            lines.append(item)
    return (
        f'Проект "{name}" {action}, но некоторые {label} уже используются в других проектах:\n'
        + "\n".join(lines)
        + "\nУдалите их из других проектов или укажите другие."
    )


def create_project(item: schemas.CreateProjectItem) -> dict:
    payload = build_create_payload(item)
    status_code, raw_text, parsed = _post(payload)

    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise _response_error(status_code, raw_text, parsed)

    result = parsed.get("result") or {}
    provider_id = result.get("id")
    missing_items: List[str] = []
    detail: Optional[dict] = None

    target_type = payload.get("type")
    if provider_id and target_type in ("hosts", "calls"):
        expected = _parse_content_list(str(payload.get("content") or ""))
        detail = _get_project(str(provider_id))
        if detail:
            actual = _parse_content_list(str(detail.get("content") or ""))
            missing_items = sorted(set(expected) - set(actual))

    return {
        "provider_id": provider_id,
        "raw": parsed,
        "missing_items": missing_items,
        "provider_content": str(detail.get("content") or "") if provider_id and detail else "",
        "target_type": target_type,
    }


def update_project(
    provider_id: str,
    project: models.Project,
    update: schemas.ProjectUpdate | schemas.AdminProjectUpdate,
) -> dict:
    target_type = _type_from_collection(project.collection_source)
    target_src = _src_from_code(project.data_source_code)
    payload = build_update_payload(provider_id, project, update)
    status_code, raw_text, parsed = _post(payload)

    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise _response_error(status_code, raw_text, parsed)

    missing_items: List[str] = []
    provider_content = ""

    if target_type in ("hosts", "calls"):
        expected = _parse_content_list(str(payload.get("content") or ""))
        detail = _get_project(str(provider_id))
        if detail:
            provider_content = str(detail.get("content") or "")
            actual = _parse_content_list(provider_content)
            missing_items = sorted(set(expected) - set(actual))

    return {
        "raw": parsed,
        "missing_items": missing_items,
        "provider_content": provider_content,
        "target_type": target_type,
    }


def delete_project(provider_id: str, project: models.Project) -> dict:
    token = _get_token()
    try:
        provider_id_value: int | str = int(str(provider_id).strip())
    except Exception:
        provider_id_value = str(provider_id).strip()

    payload = {
        "token": token,
        "command": "gck_project_delete",
        "id": provider_id_value,
    }
    status_code, raw_text, parsed = _post(payload)
    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise _response_error(status_code, raw_text, parsed)
    return {"raw": parsed}


def update_project_status(provider_id: str, project: models.Project, status: schemas.ProjectMutableStatus) -> dict:
    payload = build_status_only_payload(provider_id, project, status)
    status_code, raw_text, parsed = _post(payload)
    if status_code >= 400 or not parsed or parsed.get("status") != "success":
        raise _response_error(status_code, raw_text, parsed)
    return {"raw": parsed}
