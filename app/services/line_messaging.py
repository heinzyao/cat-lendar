from __future__ import annotations

import logging

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)

from app.config import settings

logger = logging.getLogger(__name__)

_config = Configuration(access_token=settings.line_channel_access_token)


def _get_api() -> MessagingApi:
    return MessagingApi(ApiClient(_config))


async def reply_text(reply_token: str, text: str) -> None:
    api = _get_api()
    api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text)],
        )
    )


async def push_text(user_id: str, text: str) -> None:
    api = _get_api()
    api.push_message(
        PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text=text)],
        )
    )


async def get_display_name(user_id: str) -> str | None:
    """取得 LINE 用戶顯示名稱，失敗時回傳 None"""
    try:
        api = _get_api()
        profile = api.get_profile(user_id)
        return profile.display_name
    except Exception:
        logger.warning("Failed to get profile for %s", user_id)
        return None
