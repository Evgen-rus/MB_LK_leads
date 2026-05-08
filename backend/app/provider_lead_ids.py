from __future__ import annotations

from typing import Any


PROVIDER_LEAD_LK_ID_OFFSET = 30_100_000


def format_provider_lead_lk_id(raw_id: Any) -> str:
    return str(PROVIDER_LEAD_LK_ID_OFFSET + int(raw_id))
