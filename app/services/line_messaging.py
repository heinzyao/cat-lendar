from __future__ import annotations

import asyncio
import logging

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
)

from app.config import settings

logger = logging.getLogger(__name__)

_config = Configuration(access_token=settings.line_channel_access_token)

# LINE 規定 5～60 秒、5 的倍數；回覆送出時動畫會自動消失。對齊 webhook 的 25 秒總時限。
LOADING_SECONDS = 25


def _get_api() -> MessagingApi:
    return MessagingApi(ApiClient(_config))


async def show_loading(user_id: str) -> None:
    """一對一聊天顯示「輸入中」動畫，讓使用者知道 bot 在處理（Gemini 解析要 4～13 秒）。

    只能用在一對一聊天：chat_id 給的是 user_id，群組訊息呼叫會讓動畫出現在該使用者的私訊裡
    （呼叫端負責判斷）。純粹是體驗加分：API 出錯或超過 2 秒都只記 log，不擋主流程。
    """
    request = ShowLoadingAnimationRequest(chat_id=user_id, loading_seconds=LOADING_SECONDS)

    def _send() -> None:
        # SDK 預設沒有 HTTP 逾時；to_thread 的執行緒取消不掉，要靠請求本身的逾時結束
        with ApiClient(_config) as client:
            MessagingApi(client).show_loading_animation(request, _request_timeout=2)

    try:
        await asyncio.wait_for(asyncio.to_thread(_send), timeout=2.5)
    except Exception:
        logger.warning("show_loading failed for %s", user_id, exc_info=True)


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
