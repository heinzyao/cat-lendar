"""LINE Webhook 路由：接收並驗證 LINE 平台傳入的訊息事件。

設計理由
--------
LINE 平台在使用者發送訊息時，會向此端點 POST 一個包含事件的 JSON 請求。
Bot 必須在 60 秒內回應 200 OK，否則 LINE 平台會重試（可能導致重複處理）。

安全驗證（HMAC-SHA256）：
- LINE 在請求標頭附上 X-Line-Signature
- WebhookParser.parse() 驗證簽名是否與 Channel Secret 一致
- 若簽名不符（非 LINE 平台的請求），回傳 400 Bad Request
- 設計理由：防止任意人偽造 Webhook 請求觸發 Bot 操作

事件過濾策略：
- 只處理 MessageEvent + TextMessageContent（文字訊息）
- 圖片、貼圖、位置等其他事件類型靜默忽略（LINE 規格要求仍回傳 200）
- isinstance 雙重判斷確保型別安全（linebot.v3 SDK 的設計）

錯誤隔離：
- 每個事件的處理都在獨立的 try/except 中
- 某個使用者訊息處理失敗不影響同一 Webhook 請求中的其他事件
- 回傳 {"status": "ok"} 確保 LINE 平台不會重試

_parser 模組級別 Singleton：
WebhookParser 初始化時載入 Channel Secret，複用可避免每次請求都重新建立
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
)
from linebot.v3.webhook import WebhookParser

from app.config import settings
from app.handlers.message import handle_message

logger = logging.getLogger(__name__)
router = APIRouter()

# Singleton WebhookParser：初始化時載入 Channel Secret，供簽名驗證使用
_parser = WebhookParser(settings.line_channel_secret)


@router.post("/webhook")
async def webhook(
    request: Request,
    x_line_signature: str = Header(...),  # 必填標頭，缺少則 FastAPI 自動回傳 422
):
    """接收 LINE Webhook 事件，驗證簽名後分派至訊息處理器。"""
    body = (await request.body()).decode("utf-8")

    # 驗證 HMAC-SHA256 簽名，確認請求來自 LINE 平台
    try:
        events = _parser.parse(body, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        # 只處理文字訊息事件，其他類型（圖片、貼圖等）靜默忽略
        if isinstance(event, MessageEvent) and isinstance(
            event.message, TextMessageContent
        ):
            user_id = event.source.user_id
            reply_token = event.reply_token
            text = event.message.text

            # 每個事件獨立 try/except：單一失敗不影響其他事件
            try:
                await handle_message(user_id, reply_token, text)
            except Exception:
                logger.exception("Error handling message from %s", user_id)

    # 必須回傳 200 OK，否則 LINE 平台會重試 Webhook
    return {"status": "ok"}
