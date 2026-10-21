"""LINE Webhook：驗簽後逐一處理文字訊息；單一事件失敗不影響其他事件，且一律回 200 避免 LINE 重送。"""

from __future__ import annotations

import asyncio
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
from app.services import line_messaging
from app.utils import i18n

logger = logging.getLogger(__name__)
router = APIRouter()

# Cloud Run 請求上限 30 秒（scripts/deploy.sh --timeout）。單次呼叫各有逾時，但一則訊息可能串好幾次
# Gemini／Calendar，加總仍會超過；整個 webhook 共用這個期限，留幾秒給逾時回覆。
# 同一個 webhook 有多個 event 時（少見），前面用完期限，後面的 event 會直接收到 TIMEOUT_ERROR。
HANDLE_DEADLINE_SEC = 25

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

    loop = asyncio.get_running_loop()
    deadline = loop.time() + HANDLE_DEADLINE_SEC

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
                async with asyncio.timeout_at(deadline):
                    await handle_message(
                        user_id, reply_token, text, one_on_one=event.source.type == "user"
                    )
            except TimeoutError:
                logger.error("handle_message exceeded %ss deadline for %s", HANDLE_DEADLINE_SEC, user_id)
                try:
                    await line_messaging.reply_text(reply_token, i18n.TIMEOUT_ERROR)
                except Exception:
                    logger.exception("Timeout reply failed for %s", user_id)
            except Exception:
                logger.exception("Error handling message from %s", user_id)

    # 必須回傳 200 OK，否則 LINE 平台會重試 Webhook
    return {"status": "ok"}
