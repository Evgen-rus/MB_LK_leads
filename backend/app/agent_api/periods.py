"""Validation shared by Agent analytics prepare and run requests."""
from datetime import date
import re

from .contracts import AgentError

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_PERIODS = 64


def requested_periods(params: dict, *, required: bool = True) -> list[dict[str, str]] | None:
    """Return periods in caller order, supporting the original single-range fields."""
    raw = params.get("periods")
    start, end = params.get("period_start"), params.get("period_end")
    if raw is not None and (start is not None or end is not None):
        raise AgentError("INVALID_PERIOD", "Используйте periods или period_start/period_end", 422)
    if raw is None:
        if start is None and end is None and not required:
            return None
        raw = [{"period_start": start, "period_end": end}]
    if not isinstance(raw, list) or not raw or len(raw) > MAX_PERIODS:
        raise AgentError("INVALID_PERIOD", f"Укажите от 1 до {MAX_PERIODS} периодов", 422)

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    parsed: list[tuple[date, date]] = []
    try:
        for item in raw:
            if not isinstance(item, dict) or set(item) != {"period_start", "period_end"}:
                raise ValueError
            first_text, last_text = item["period_start"], item["period_end"]
            if not isinstance(first_text, str) or not isinstance(last_text, str):
                raise ValueError
            if not _DATE.fullmatch(first_text) or not _DATE.fullmatch(last_text):
                raise ValueError
            first, last = date.fromisoformat(first_text), date.fromisoformat(last_text)
            if first > last or (last - first).days >= 366:
                raise ValueError
            key = (first.isoformat(), last.isoformat())
            if key in seen:
                raise ValueError
            seen.add(key)
            parsed.append((first, last))
            result.append({"period_start": key[0], "period_end": key[1]})
        if (max(last for _, last in parsed) - min(first for first, _ in parsed)).days >= 366:
            raise ValueError
    except (TypeError, ValueError):
        raise AgentError("INVALID_PERIOD", "Проверьте даты, повторы и общий диапазон периодов (максимум 366 дней)", 422) from None
    return result
