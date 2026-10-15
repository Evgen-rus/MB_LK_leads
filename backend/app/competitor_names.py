"""Normalize competitor names keyed by Russian phone number."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


MAX_COMPETITOR_NAMES = 500
MAX_COMPETITOR_NAME_LENGTH = 120


def normalize_competitor_phone(value: Any) -> str:
    raw = str(value or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if len(digits) != 11:
        raise ValueError(f'Некорректный номер телефона "{raw}": нужно ровно 11 цифр')
    if digits[0] == "8":
        return f"7{digits[1:]}"
    if digits[0] != "7":
        raise ValueError(f'Некорректный номер телефона "{raw}": номер должен начинаться с 7 или 8')
    return digits


def normalize_competitor_names(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Соответствия конкурентов должны быть объектом телефон-название")
    if len(value) > MAX_COMPETITOR_NAMES:
        raise ValueError(f"Допускается не более {MAX_COMPETITOR_NAMES} соответствий конкурентов")

    normalized: dict[str, str] = {}
    for raw_phone, raw_name in value.items():
        if raw_name is None:
            continue
        if not isinstance(raw_name, str):
            raise ValueError("Название конкурента должно быть строкой")
        name = raw_name.strip()
        if not name:
            continue
        if len(name) > MAX_COMPETITOR_NAME_LENGTH:
            raise ValueError(f"Название конкурента не должно превышать {MAX_COMPETITOR_NAME_LENGTH} символов")
        phone = normalize_competitor_phone(raw_phone)
        previous = normalized.get(phone)
        if previous is not None and previous != name:
            raise ValueError(f"Для номера {phone} указаны разные названия конкурента")
        normalized[phone] = name
    return normalized


def find_competitor_name(mapping: Any, subdomain: Any) -> str | None:
    raw_subdomain = str(subdomain or "").split(";", 1)[0].strip()
    try:
        phone = normalize_competitor_phone(raw_subdomain)
    except ValueError:
        return None
    if not isinstance(mapping, Mapping):
        return None
    name = mapping.get(phone)
    return name.strip() if isinstance(name, str) and name.strip() else None
