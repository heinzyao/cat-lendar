from __future__ import annotations

import logging
from datetime import datetime

from app.store import firestore as store
from app.services import line_messaging
from app.utils.datetime_utils import local_tz

logger = logging.getLogger(__name__)


def format_reminder_message(
    event_summary: str, start_time: datetime, reminder_minutes: int
) -> str:
    tz = local_tz()
    local_start = start_time.astimezone(tz)
    time_str = local_start.strftime("%m/%d %H:%M")
    return (
        f"⏰ 提醒：{event_summary} 將在 {reminder_minutes} 分鐘後開始\n"
        f"🕐 {time_str}"
    )


async def check_and_send_reminders() -> int:
    """查詢所有到期提醒並發送 LINE push，回傳發送數量"""
    due = await store.get_due_reminders()
    sent_count = 0

    for reminder in due:
        reminder_id = reminder["id"]
        try:
            msg = format_reminder_message(
                event_summary=reminder["event_summary"],
                start_time=reminder["start_time"],
                reminder_minutes=reminder["reminder_minutes"],
            )
            await line_messaging.push_text(reminder["line_user_id"], msg)
            await store.mark_reminder_sent(reminder_id)
            sent_count += 1
        except Exception:
            logger.exception("Failed to send reminder %s", reminder_id)

    return sent_count
