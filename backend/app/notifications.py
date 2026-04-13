from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from . import telegram


@dataclass(frozen=True)
class NotificationResult:
    delivered: bool
    channel: Optional[str]
    reason: Optional[str] = None


def _env_flag(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def send_system_notification(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: Optional[str] = "HTML",
) -> NotificationResult:
    if not _env_flag("NOTIFICATIONS_TELEGRAM_ENABLED", default=True):
        logging.getLogger("app").info(
            "Telegram system notification suppressed: reason=telegram_disabled chat_id=%s",
            chat_id,
        )
        return NotificationResult(delivered=False, channel=None, reason="telegram_disabled")

    if not telegram.send_text(bot_token, chat_id, text, parse_mode=parse_mode):
        return NotificationResult(delivered=False, channel="telegram", reason="send_failed")

    return NotificationResult(delivered=True, channel="telegram", reason=None)
