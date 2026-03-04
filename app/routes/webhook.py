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

_parser = WebhookParser(settings.line_channel_secret)


@router.post("/webhook")
async def webhook(
    request: Request,
    x_line_signature: str = Header(...),
):
    body = (await request.body()).decode("utf-8")

    try:
        events = _parser.parse(body, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(
            event.message, TextMessageContent
        ):
            user_id = event.source.user_id
            reply_token = event.reply_token
            text = event.message.text

            try:
                await handle_message(user_id, reply_token, text)
            except Exception:
                logger.exception("Error handling message from %s", user_id)

    return {"status": "ok"}
