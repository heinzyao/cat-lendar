from __future__ import annotations

import asyncio
import logging

from app.services import line_messaging
from app.store import firestore as store
from app.utils import i18n

logger = logging.getLogger(__name__)


async def notify_others(
    action: str,
    actor_user_id: str,
    summary: str,
    time_str: str | None = None,
) -> None:
    """行事曆異動後，推播通知給除了操作者以外的所有已登記用戶。

    action: "create" | "update" | "delete"
    """
    try:
        all_user_ids = await store.get_all_user_ids()
        others = [uid for uid in all_user_ids if uid != actor_user_id]
        if not others:
            return

        # 過濾掉關閉通知的用戶
        notify_checks = await asyncio.gather(
            *[store.get_notify_enabled(uid) for uid in others]
        )
        others = [uid for uid, enabled in zip(others, notify_checks) if enabled]
        if not others:
            return

        display_name = await line_messaging.get_display_name(actor_user_id)
        if not display_name:
            display_name = f"用戶 ...{actor_user_id[-4:]}"

        if action == "create":
            msg = i18n.NOTIFY_EVENT_CREATED.format(
                name=display_name, summary=summary, time=time_str or ""
            )
        elif action == "update":
            msg = i18n.NOTIFY_EVENT_UPDATED.format(
                name=display_name, summary=summary, time=time_str or ""
            )
        else:  # delete
            msg = i18n.NOTIFY_EVENT_DELETED.format(
                name=display_name, summary=summary
            )

        await asyncio.gather(
            *[_try_push(uid, msg) for uid in others],
            return_exceptions=True,
        )
    except Exception:
        logger.exception("notify_others failed for actor=%s action=%s", actor_user_id, action)


async def _try_push(user_id: str, msg: str) -> None:
    try:
        await line_messaging.push_text(user_id, msg)
    except Exception:
        logger.warning("Failed to push notification to %s", user_id)
