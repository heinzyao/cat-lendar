"""外部 API 的逾時必須短於 Cloud Run 的 30 秒請求上限。

googleapiclient 預設 60 秒、google-genai 預設不設逾時；連線一卡住就撐到 Cloud Run 切斷請求（504），
使用者收不到任何回覆，/internal/sync 也會整整卡 30 秒。
"""

import os

os.environ.setdefault("LINE_CHANNEL_SECRET", "x")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "x")
os.environ.setdefault("GEMINI_API_KEY", "x")
os.environ.setdefault("GOOGLE_CALENDAR_ID", "cal")

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.oauth2.credentials import Credentials

from app.services import calendar, nlp

CLOUD_RUN_TIMEOUT_SEC = int(
    re.search(r"--timeout=(\d+)", (Path(__file__).parents[1] / "scripts/deploy.sh").read_text()).group(1)
)


def test_calendar_http_timeout_below_cloud_run_limit():
    service = calendar._get_service(Credentials(token="t"))
    # AuthorizedHttp 包著真正的 httplib2.Http
    assert service._http.http.timeout == calendar.HTTP_TIMEOUT_SEC
    assert 0 < calendar.HTTP_TIMEOUT_SEC < CLOUD_RUN_TIMEOUT_SEC


async def test_calendar_execute_no_app_level_retry():
    """不帶 num_retries：POST 重試可能重複建立行程。（httplib2 底層遇到連線中斷仍可能自己重送一次，這裡管不到。）"""
    request = MagicMock()
    request.execute.return_value = {"ok": True}
    assert await calendar._execute(request) == {"ok": True}
    request.execute.assert_called_once_with()


def test_gemini_client_timeout_below_cloud_run_limit():
    nlp._client = None
    try:
        with patch("app.services.nlp.genai.Client") as client_cls:
            nlp._get_client()
        http_options = client_cls.call_args.kwargs["http_options"]
        assert http_options.timeout == nlp.GEMINI_TIMEOUT_MS
        assert 0 < nlp.GEMINI_TIMEOUT_MS < CLOUD_RUN_TIMEOUT_SEC * 1000
    finally:
        nlp._client = None


# ── 整個請求的總時限：單次逾時加總仍可能超過 30 秒（parse_intent → 搜尋 → 二次解析 → 寫入）──

import asyncio
import base64
import hashlib
import hmac
import json

from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from app.routes import notify, webhook
from app.utils import i18n


def test_deadlines_leave_room_below_cloud_run_limit():
    assert 0 < webhook.HANDLE_DEADLINE_SEC < CLOUD_RUN_TIMEOUT_SEC
    assert 0 < notify.SYNC_DEADLINE_SEC < CLOUD_RUN_TIMEOUT_SEC


def _text_event_body(text: str = "明天有什麼行程") -> bytes:
    return json.dumps({
        "destination": "Ubot",
        "events": [{
            "type": "message", "mode": "active", "timestamp": 0,
            "webhookEventId": "e1", "deliveryContext": {"isRedelivery": False},
            "replyToken": "rt",
            "source": {"type": "user", "userId": "Uuser"},
            "message": {"type": "text", "id": "1", "quoteToken": "q", "text": text},
        }],
    }).encode()


async def _hang(*_args, **_kwargs):
    await asyncio.sleep(60)


async def test_webhook_deadline_replies_timeout_message():
    body = _text_event_body()
    sig = base64.b64encode(
        hmac.new(settings.line_channel_secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    with (
        patch.object(webhook, "HANDLE_DEADLINE_SEC", 0.05),
        patch.object(webhook, "handle_message", _hang),
        patch.object(webhook.line_messaging, "reply_text") as reply,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post("/webhook", content=body, headers={
                "Content-Type": "application/json", "x-line-signature": sig})
    assert resp.status_code == 200
    reply.assert_awaited_once_with("rt", i18n.TIMEOUT_ERROR)


async def test_sync_deadline_returns_504():
    with (
        patch.object(notify, "SYNC_DEADLINE_SEC", 0.05),
        patch.object(notify.calendar_sync, "run_sync", _hang),
        patch.object(settings, "notify_secret", "s"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post("/internal/sync", headers={"X-Internal-Secret": "s"})
    assert resp.status_code == 504
