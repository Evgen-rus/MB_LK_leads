from __future__ import annotations

import re

from rapidfuzz import fuzz

from . import db
from .models import StatusRule


DEMAND_GROUPS = {"Качественные", "Рабочий потенциал", "Уже наши / уже купил"}
ALL_GROUPS = [
    "Качественные",
    "Рабочий потенциал",
    "Уже наши / уже купил",
    "Конкурент",
    "Недозвон",
    "Некачественные",
    "Не подходит по гео",
    "Еще не звонили",
    "Не учитывать",
    "Требует проверки",
]


def load_db_rules(project: str) -> list[StatusRule]:
    db.init_db()
    rows = db.list_project_status_rules(project)
    return [
        StatusRule(
            pattern=row["pattern"],
            match_type=row["match_type"],
            group_name=row["group_name"],
            subgroup_name=row["subgroup_name"],
            comment=row["comment"],
            priority=row["priority"],
            project_code=row["project_code"],
        )
        for row in rows
    ]


def sorted_rules(project: str) -> list[StatusRule]:
    return sorted(load_db_rules(project), key=lambda rule: rule.priority)


def _match(rule: StatusRule, text: str, status_text: str) -> bool:
    value = text.casefold()
    status_value = status_text.casefold()
    pattern = rule.pattern.casefold()
    if rule.match_type == "exact":
        return status_value.strip() == pattern.strip()
    if rule.match_type == "contains":
        return pattern in value
    if rule.match_type == "regex":
        return re.search(rule.pattern, text, flags=re.IGNORECASE) is not None
    if rule.match_type == "fuzzy":
        return fuzz.partial_ratio(pattern, value) >= 88
    return False


def is_missing_status(value: object) -> bool:
    if value is None:
        return True
    try:
        if value != value:
            return True
    except TypeError:
        pass
    text = str(value).strip().casefold()
    return text in {"", "nan", "none", "<na>", "nat"}


def _clean_text(value: object) -> str:
    if is_missing_status(value):
        return ""
    return str(value).strip()


def classify(
    status: object,
    comment: object = None,
    project: str = "",
    rules: list[StatusRule] | None = None,
) -> tuple[str, str]:
    status_text = _clean_text(status)
    text = status_text
    if not text.strip():
        return "Не учитывать", "пустой статус"
    active_rules = rules if rules is not None else sorted_rules(project)
    for rule in active_rules:
        if _match(rule, text, status_text):
            return rule.group_name, rule.pattern
    return "Требует проверки", "нет правила"


def unknown_statuses(statuses: list[object], project: str) -> list[str]:
    result = []
    rules = sorted_rules(project)
    for value in sorted({_clean_text(status) for status in statuses if _clean_text(status)}):
        if not any(_match(rule, value, value) for rule in rules):
            result.append(value)
    return result
