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


def as_local_naive(dt: datetime) -> datetime:
    """Приводит datetime к локальному времени без tzinfo для naive TIMESTAMP-полей."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=None)
    return dt.astimezone(_get_tz()).replace(tzinfo=None)


def now_msk_naive() -> datetime:
    """Возвращает текущий локальный момент без tzinfo для DB-полей без timezone."""
    return as_local_naive(now_msk())

