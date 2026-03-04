"""local_calendar.py 單元測試（mock Firestore）"""
import os
import base64
from datetime import datetime, timedelta, timezone
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

from app.models.intent import EventDetails, TimeRange
from app.services import local_calendar

_TZ = ZoneInfo("Asia/Taipei")
_USER = "U_test_user"


def _dt(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=_TZ)


# ── create_event ──


async def test_create_event_stores_and_returns_calendar_format():
    details = EventDetails(
        summary="開會",
        start_time=_dt(2024, 3, 15, 14, 0),
        end_time=_dt(2024, 3, 15, 15, 0),
    )

    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.create_local_event = AsyncMock()
        event = await local_calendar.create_event(_USER, details)

    assert event["summary"] == "開會"
    assert "dateTime" in event["start"]
    assert "dateTime" in event["end"]
    assert "id" in event
    mock_store.create_local_event.assert_awaited_once()


async def test_create_event_all_day():
    details = EventDetails(
        summary="假日",
        start_time=_dt(2024, 3, 15),
        all_day=True,
    )

    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.create_local_event = AsyncMock()
        event = await local_calendar.create_event(_USER, details)

    assert event["summary"] == "假日"
    assert "date" in event["start"]
    assert "dateTime" not in event["start"]


async def test_create_event_with_location():
    details = EventDetails(
        summary="晚餐",
        start_time=_dt(2024, 3, 15, 19, 0),
        end_time=_dt(2024, 3, 15, 21, 0),
        location="鼎泰豐",
    )

    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.create_local_event = AsyncMock()
        event = await local_calendar.create_event(_USER, details)

    assert event.get("location") == "鼎泰豐"


# ── query_events ──


async def test_query_events_filters_by_time_range():
    raw = [
        {
            "id": "evt1",
            "summary": "會議",
            "start_time": _dt(2024, 3, 15, 10, 0),
            "end_time": _dt(2024, 3, 15, 11, 0),
            "all_day": False,
            "location": None,
            "description": None,
        }
    ]
    time_range = TimeRange(start=_dt(2024, 3, 15), end=_dt(2024, 3, 16))

    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.list_local_events = AsyncMock(return_value=raw)
        events = await local_calendar.query_events(_USER, time_range)

    assert len(events) == 1
    assert events[0]["summary"] == "會議"
    mock_store.list_local_events.assert_awaited_once_with(
        _USER, time_range=time_range, keyword=None
    )


async def test_query_events_with_keyword():
    time_range = TimeRange(start=_dt(2024, 3, 15), end=_dt(2024, 3, 16))

    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.list_local_events = AsyncMock(return_value=[])
        await local_calendar.query_events(_USER, time_range, keyword="開會")

    mock_store.list_local_events.assert_awaited_once_with(
        _USER, time_range=time_range, keyword="開會"
    )


async def test_query_events_returns_calendar_format():
    raw = [
        {
            "id": "evt2",
            "summary": "牙醫",
            "start_time": _dt(2024, 3, 15, 9, 0),
            "end_time": _dt(2024, 3, 15, 10, 0),
            "all_day": False,
            "location": "診所",
            "description": None,
        }
    ]
    time_range = TimeRange(start=_dt(2024, 3, 15), end=_dt(2024, 3, 16))

    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.list_local_events = AsyncMock(return_value=raw)
        events = await local_calendar.query_events(_USER, time_range)

    assert events[0]["location"] == "診所"
    assert "dateTime" in events[0]["start"]


# ── update_event ──


async def test_update_event_patches_summary():
    original = {
        "id": "evt3",
        "summary": "舊會議",
        "start_time": _dt(2024, 3, 15, 10, 0),
        "end_time": _dt(2024, 3, 15, 11, 0),
        "all_day": False,
        "location": None,
        "description": None,
    }
    updates = EventDetails(summary="新會議")

    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.get_local_event = AsyncMock(return_value=original)
        mock_store.update_local_event = AsyncMock()
        event = await local_calendar.update_event(_USER, "evt3", updates)

    assert event["summary"] == "新會議"
    mock_store.update_local_event.assert_awaited_once()


async def test_update_event_not_found_raises():
    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.get_local_event = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await local_calendar.update_event(_USER, "missing", EventDetails())


# ── delete_event ──


async def test_delete_event_calls_store():
    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.delete_local_event = AsyncMock()
        await local_calendar.delete_event(_USER, "evt4")

    mock_store.delete_local_event.assert_awaited_once_with(_USER, "evt4")


# ── format_event_summary ──


def test_format_event_summary_includes_time_and_location():
    event = {
        "summary": "開會",
        "start": {"dateTime": "2024-03-15T14:00:00+08:00"},
        "end": {"dateTime": "2024-03-15T15:00:00+08:00"},
        "location": "Room 101",
    }
    result = local_calendar.format_event_summary(event)
    assert "開會" in result
    assert "Room 101" in result
    assert "🕐" in result
    assert "📍" in result


def test_format_event_summary_all_day():
    event = {
        "summary": "假日",
        "start": {"date": "2024-03-15"},
        "end": {"date": "2024-03-15"},
    }
    result = local_calendar.format_event_summary(event)
    assert "假日" in result
    assert "03/15" in result


# ── _to_calendar_format ──


async def test_to_calendar_format_no_end_time_uses_start():
    """create_event 時 end_time=None → 使用 start_time 補齊"""
    details = EventDetails(
        summary="備忘",
        start_time=_dt(2024, 3, 15, 8, 0),
        end_time=None,
    )

    with patch("app.services.local_calendar.store") as mock_store:
        mock_store.create_local_event = AsyncMock()
        event = await local_calendar.create_event(_USER, details)

    # end 應存在且不為空
    assert event["end"].get("dateTime") or event["end"].get("date")
