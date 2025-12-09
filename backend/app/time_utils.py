from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone


def _get_tz():
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        tz_name = os.getenv("SHEETS_TZ", "Europe/Moscow")
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            return timezone(timedelta(hours=3))
    except Exception:
        return timezone(timedelta(hours=3))


def now_msk() -> datetime:
    """Возвращает текущий момент в MSK (или SHEETS_TZ) c tzinfo."""
    return datetime.now(_get_tz())

