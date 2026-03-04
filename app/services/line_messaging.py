from __future__ import annotations

import logging

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
    TemplateMessage,
    ButtonsTemplate,
    URIAction,
)

from app.config import settings
from app.utils import i18n

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


async def reply_auth_button(reply_token: str, auth_url: str) -> None:
    """傳送授權按鈕，使用 openExternalBrowser=1 強制外部瀏覽器"""
    # 在 URL 加上 openExternalBrowser=1 讓 LINE 用外部瀏覽器開啟
    separator = "&" if "?" in auth_url else "?"
    external_url = f"{auth_url}{separator}openExternalBrowser=1"

    api = _get_api()
    api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[
                TemplateMessage(
                    alt_text=i18n.AUTH_REQUIRED,
                    template=ButtonsTemplate(
                        text=i18n.AUTH_REQUIRED,
                        actions=[
                            URIAction(
                                label=i18n.AUTH_BUTTON_LABEL,
                                uri=external_url,
                            )
                        ],
                    ),
                )
            ],
        )
    )
