"""nlp.parse_update_details 單元測試（mock Gemini API）"""
import os
import base64
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.models.intent import EventDetails
from app.services import nlp

_TZ = ZoneInfo("Asia/Taipei")

_SAMPLE_EVENT = {
    "id": "evt001",
    "summary": "週會",
    "start": {"dateTime": "2024-03-15T10:00:00+08:00"},
    "end": {"dateTime": "2024-03-15T11:00:00+08:00"},
}


def _mock_client(parsed=None, text: str = "", error: Exception | None = None):
    """建立 google-genai Client mock。

    新 SDK 由 response_schema 在協議層解析，所以 mock 的是 response.parsed
    （型別化物件）而非 response.text 的 JSON 字串。
    """
    mock_response = MagicMock()
    mock_response.parsed = parsed
    mock_response.text = text
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        side_effect=error, return_value=mock_response
    )
    return client


# ── 成功修改名稱 ──


@pytest.mark.asyncio
async def test_parse_update_details_summary_only():
    """只改名稱，回傳僅含 summary 的 EventDetails"""
    details = EventDetails(summary="月會")
    with patch("app.services.nlp._get_client", return_value=_mock_client(parsed=details)):
        result = await nlp.parse_update_details("把週會改成月會", _SAMPLE_EVENT)

    assert result is not None
    assert result.summary == "月會"
    assert result.start_time is None
    assert result.end_time is None


# ── 成功移動時間（移到明天，保持持續時間）──


@pytest.mark.asyncio
async def test_parse_update_details_move_to_tomorrow():
    """移到明天，start/end 都更新"""
    details = EventDetails(
        start_time=datetime(2024, 3, 16, 10, 0, tzinfo=_TZ),
        end_time=datetime(2024, 3, 16, 11, 0, tzinfo=_TZ),
    )
    with patch("app.services.nlp._get_client", return_value=_mock_client(parsed=details)):
        result = await nlp.parse_update_details("移到明天", _SAMPLE_EVENT)

    assert result is not None
    assert result.start_time is not None
    assert result.end_time is not None
    assert result.start_time.date() > datetime(2024, 3, 15, tzinfo=_TZ).date()


# ── API 失敗時回傳 None ──


@pytest.mark.asyncio
async def test_parse_update_details_api_error_returns_none():
    client = _mock_client(error=Exception("API error"))
    with patch("app.services.nlp._get_client", return_value=client):
        result = await nlp.parse_update_details("改時間", _SAMPLE_EVENT)

    assert result is None


# ── 拿不到合法 EventDetails 時回傳 None ──


@pytest.mark.asyncio
async def test_parse_update_details_unparsable_returns_none():
    """被安全過濾擋下或回傳不符 schema 時，response.parsed 會是 None。"""
    client = _mock_client(parsed=None, text="這不是合法回應")
    with patch("app.services.nlp._get_client", return_value=client):
        result = await nlp.parse_update_details("改時間", _SAMPLE_EVENT)

    assert result is None


# ── user_message 為空時回傳 None ──


@pytest.mark.asyncio
async def test_parse_update_details_empty_message_returns_none():
    result = await nlp.parse_update_details("", _SAMPLE_EVENT)
    assert result is None
