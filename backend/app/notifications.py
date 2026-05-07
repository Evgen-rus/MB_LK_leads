from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from . import crud


@dataclass(frozen=True)
class NotificationResult:
    delivered: bool
    channel: Optional[str]
    reason: Optional[str] = None
    notification_id: Optional[int] = None


def _env_flag(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def send_telegram_notification(
    *,
    db_sess: Session,
    bot_token: str = "",
    chat_id: str,
    text: str,
    parse_mode: Optional[str] = "HTML",
    kind: str = "system",
    metadata: Optional[Dict[str, Any]] = None,
) -> NotificationResult:
    if not _env_flag("NOTIFICATIONS_TELEGRAM_ENABLED", default=True):
        logging.getLogger("app").info(
            "Telegram notification queue suppressed: kind=%s reason=telegram_disabled chat_id=%s",
            kind,
            chat_id,
        )
        return NotificationResult(delivered=False, channel=None, reason="telegram_disabled")

    _ = bot_token  # Backend no longer sends Telegram directly; NL worker owns Bot API calls.
    chat_id_value = str(chat_id or "").strip()
    text_value = str(text or "")
    if not chat_id_value:
        return NotificationResult(delivered=False, channel="telegram_outbox", reason="missing_chat_id")
    if not text_value:
        return NotificationResult(delivered=False, channel="telegram_outbox", reason="empty_text")

    try:
        row = crud.create_telegram_notification(
            db_sess,
            kind=kind,
            chat_id=chat_id_value,
            text=text_value,
            parse_mode=parse_mode,
            metadata=metadata,
        )
    except Exception:
        logging.getLogger("app").warning(
            "Failed to queue Telegram notification: kind=%s chat_id=%s",
            kind,
            chat_id_value,
            exc_info=True,
        )
        return NotificationResult(delivered=False, channel="telegram_outbox", reason="queue_failed")

    return NotificationResult(
        delivered=True,
        channel="telegram_outbox",
        reason="queued",
        notification_id=int(row.id),
    )


def send_system_notification(
    *,
    db_sess: Session,
    bot_token: str = "",
    chat_id: str,
    text: str,
    parse_mode: Optional[str] = "HTML",
    metadata: Optional[Dict[str, Any]] = None,
) -> NotificationResult:
    return send_telegram_notification(
        db_sess=db_sess,
        bot_token=bot_token,
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        kind="system",
        metadata=metadata,
    )
