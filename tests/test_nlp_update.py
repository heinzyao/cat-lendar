"""nlp.parse_update_details 單元測試（mock Anthropic API）"""
import os
import base64
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "https://example.com/oauth/callback")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.services import nlp

_TZ = ZoneInfo("Asia/Taipei")

_SAMPLE_EVENT = {
    "id": "evt001",
    "summary": "週會",
    "start": {"dateTime": "2024-03-15T10:00:00+08:00"},
    "end": {"dateTime": "2024-03-15T11:00:00+08:00"},
}


def _make_response(text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


# ── 成功修改名稱 ──


@pytest.mark.asyncio
async def test_parse_update_details_summary_only():
    """只改名稱，回傳僅含 summary 的 EventDetails"""
    response_json = '{"summary": "月會"}'
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_response(response_json))

    with patch("app.services.nlp._get_client", return_value=mock_client):
        result = await nlp.parse_update_details("把週會改成月會", _SAMPLE_EVENT)

    assert result is not None
    assert result.summary == "月會"
    assert result.start_time is None
    assert result.end_time is None


# ── 成功移動時間（移到明天，保持持續時間）──


@pytest.mark.asyncio
async def test_parse_update_details_move_to_tomorrow():
    """移到明天，start/end 都更新"""
    response_json = (
        '{"start_time": "2024-03-16T10:00:00+08:00", "end_time": "2024-03-16T11:00:00+08:00"}'
    )
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_response(response_json))

    with patch("app.services.nlp._get_client", return_value=mock_client):
        result = await nlp.parse_update_details("移到明天", _SAMPLE_EVENT)

    assert result is not None
    assert result.start_time is not None
    assert result.end_time is not None
    assert result.start_time.date() > datetime(2024, 3, 15, tzinfo=_TZ).date()


# ── API 失敗時回傳 None ──


@pytest.mark.asyncio
async def test_parse_update_details_api_error_returns_none():
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))

    with patch("app.services.nlp._get_client", return_value=mock_client):
        result = await nlp.parse_update_details("改時間", _SAMPLE_EVENT)

    assert result is None


# ── JSON 解析失敗時回傳 None ──


@pytest.mark.asyncio
async def test_parse_update_details_invalid_json_returns_none():
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=_make_response("這不是 JSON 格式的回應")
    )

    with patch("app.services.nlp._get_client", return_value=mock_client):
        result = await nlp.parse_update_details("改時間", _SAMPLE_EVENT)

    assert result is None


# ── 模型驗證失敗時回傳 None ──


@pytest.mark.asyncio
async def test_parse_update_details_validation_error_returns_none():
    # start_time 格式錯誤，無法驗證
    response_json = '{"start_time": "not-a-datetime"}'
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_response(response_json))

    with patch("app.services.nlp._get_client", return_value=mock_client):
        result = await nlp.parse_update_details("改時間", _SAMPLE_EVENT)

    assert result is None


# ── user_message 為空時回傳 None ──


@pytest.mark.asyncio
async def test_parse_update_details_empty_message_returns_none():
    result = await nlp.parse_update_details("", _SAMPLE_EVENT)
    assert result is None
