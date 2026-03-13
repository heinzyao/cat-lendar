"""_handle_update / _handle_selection 整合測試（mock store / nlp / calendar）"""
import os
import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REFRESH_TOKEN", "test_refresh_token")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.handlers.message import _handle_update, _handle_selection
from app.models.intent import ActionType, CalendarIntent, EventDetails, TimeRange
from app.models.user import UserState

_USER = "U_test_user"
_REPLY_TOKEN = "reply_token_test"

_NOW = datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc)

_SAMPLE_EVENT = {
    "id": "evt001",
    "summary": "週會",
    "start": {"dateTime": "2024-03-15T10:00:00+08:00"},
    "end": {"dateTime": "2024-03-15T11:00:00+08:00"},
    "location": None,
    "description": None,
}

_UPDATED_EVENT = {
    "id": "evt001",
    "summary": "週會",
    "start": {"dateTime": "2024-03-16T10:00:00+08:00"},
    "end": {"dateTime": "2024-03-16T11:00:00+08:00"},
}

_MOCK_CREDS = MagicMock()


def _make_intent(original_message: str = "把週會移到明天") -> CalendarIntent:
    return CalendarIntent(
        action=ActionType.UPDATE,
        search_keyword="週會",
        time_range=TimeRange(
            start=_NOW - timedelta(days=1),
            end=_NOW + timedelta(days=1),
        ),
        event_details=EventDetails(summary="週會"),
        original_message=original_message,
        confidence=0.9,
    )


# ── 單一事件：parse_update_details 成功 → 用二次解析結果 ──


@pytest.mark.asyncio
async def test_handle_update_single_event_uses_parse_update_details():
    intent = _make_intent()
    refined = EventDetails(
        start_time=datetime(2024, 3, 16, 10, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 3, 16, 11, 0, tzinfo=timezone.utc),
    )

    with (
        patch("app.handlers.message.calendar") as mock_cal,
        patch("app.handlers.message.nlp") as mock_nlp,
        patch("app.handlers.message.line_messaging") as mock_msg,
        patch("app.handlers.message.calendar_notify") as mock_notify,
    ):
        mock_cal.query_events = AsyncMock(return_value=[_SAMPLE_EVENT])
        mock_nlp.parse_update_details = AsyncMock(return_value=refined)
        mock_cal.update_event = AsyncMock(return_value=_UPDATED_EVENT)
        mock_msg.reply_text = AsyncMock()
        mock_notify.notify_others = AsyncMock()

        await _handle_update(_USER, _REPLY_TOKEN, intent, _MOCK_CREDS)

        mock_nlp.parse_update_details.assert_awaited_once_with(
            intent.original_message, _SAMPLE_EVENT, user_id=_USER
        )
        mock_cal.update_event.assert_awaited_once_with(
            _MOCK_CREDS, "evt001", refined, line_user_id=_USER
        )
        mock_msg.reply_text.assert_awaited_once()


# ── 單一事件：parse_update_details 返回 None → fallback 到 intent.event_details ──


@pytest.mark.asyncio
async def test_handle_update_single_event_fallback_to_event_details():
    intent = _make_intent()

    with (
        patch("app.handlers.message.calendar") as mock_cal,
        patch("app.handlers.message.nlp") as mock_nlp,
        patch("app.handlers.message.line_messaging") as mock_msg,
        patch("app.handlers.message.calendar_notify") as mock_notify,
    ):
        mock_cal.query_events = AsyncMock(return_value=[_SAMPLE_EVENT])
        mock_nlp.parse_update_details = AsyncMock(return_value=None)
        mock_cal.update_event = AsyncMock(return_value=_UPDATED_EVENT)
        mock_msg.reply_text = AsyncMock()
        mock_notify.notify_others = AsyncMock()

        await _handle_update(_USER, _REPLY_TOKEN, intent, _MOCK_CREDS)

        mock_cal.update_event.assert_awaited_once_with(
            _MOCK_CREDS, "evt001", intent.event_details, line_user_id=_USER
        )


# ── 多事件選擇後更新：二次解析帶入 original_message 和完整事件資料 ──


@pytest.mark.asyncio
async def test_handle_selection_update_uses_parse_update_details():
    intent = _make_intent()
    refined = EventDetails(summary="改名後的週會")

    selected = {
        "id": "evt001",
        "summary": "週會",
        "start": {"dateTime": "2024-03-15T10:00:00+08:00"},
        "end": {"dateTime": "2024-03-15T11:00:00+08:00"},
        "location": None,
        "description": None,
    }

    user_state = UserState(
        line_user_id=_USER,
        action="select_event_for_update",
        candidates=[selected],
        original_intent=intent.model_dump(mode="json"),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )

    with (
        patch("app.handlers.message.store") as mock_store,
        patch("app.handlers.message.calendar") as mock_cal,
        patch("app.handlers.message.nlp") as mock_nlp,
        patch("app.handlers.message.line_messaging") as mock_msg,
        patch("app.handlers.message.calendar_notify") as mock_notify,
    ):
        mock_store.delete_user_state = AsyncMock()
        mock_nlp.parse_update_details = AsyncMock(return_value=refined)
        mock_cal.update_event = AsyncMock(return_value={**selected, "summary": "改名後的週會"})
        mock_msg.reply_text = AsyncMock()
        mock_notify.notify_others = AsyncMock()

        await _handle_selection(_USER, _REPLY_TOKEN, "1", user_state, _MOCK_CREDS)

        mock_nlp.parse_update_details.assert_awaited_once_with(
            intent.original_message, selected, user_id=_USER
        )
        mock_cal.update_event.assert_awaited_once_with(
            _MOCK_CREDS, "evt001", refined, line_user_id=_USER
        )
        mock_msg.reply_text.assert_awaited_once()


# ── assumption note 附加在回覆訊息末尾 ──


@pytest.mark.asyncio
async def test_handle_update_appends_assumption_note():
    intent = _make_intent()
    intent.clarification_needed = "已推定為今天的週會"
    refined = EventDetails(
        start_time=datetime(2024, 3, 16, 10, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 3, 16, 11, 0, tzinfo=timezone.utc),
    )

    with (
        patch("app.handlers.message.calendar") as mock_cal,
        patch("app.handlers.message.nlp") as mock_nlp,
        patch("app.handlers.message.line_messaging") as mock_msg,
        patch("app.handlers.message.calendar_notify") as mock_notify,
    ):
        mock_cal.query_events = AsyncMock(return_value=[_SAMPLE_EVENT])
        mock_nlp.parse_update_details = AsyncMock(return_value=refined)
        mock_cal.update_event = AsyncMock(return_value=_UPDATED_EVENT)
        mock_msg.reply_text = AsyncMock()
        mock_notify.notify_others = AsyncMock()

        result = await _handle_update(_USER, _REPLY_TOKEN, intent, _MOCK_CREDS)

        assert "\n\n💡 已推定為今天的週會" in result
