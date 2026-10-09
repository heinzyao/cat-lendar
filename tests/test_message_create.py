"""_handle_create 多筆新增測試（mock store / calendar / line）"""
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.handlers.message import _NOTE_MAX_LEN, _handle_create, _with_assumption_note
from app.models.intent import ActionType, CalendarIntent, EventDetails


def _event(summary: str) -> dict:
    return {
        "id": summary,
        "summary": summary,
        "start": {"dateTime": "2024-03-15T10:00:00+08:00"},
        "end": {"dateTime": "2024-03-15T11:00:00+08:00"},
    }


@pytest.mark.asyncio
async def test_create_multiple_events_partial_failure():
    start = datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc)
    intent = CalendarIntent(
        action=ActionType.CREATE,
        events=[
            EventDetails(summary="看牙醫", start_time=start),
            EventDetails(summary="壞掉", start_time=start),
            EventDetails(summary="聚餐", start_time=start),
        ],
        confidence=0.9,
    )

    async def fake_create(creds, details, **kw):
        if details.summary == "壞掉":
            raise RuntimeError("boom")
        return _event(details.summary)

    with (
        patch("app.handlers.message.store") as store,
        patch("app.handlers.message.calendar") as cal,
        patch("app.handlers.message.line_messaging") as line,
        patch("app.handlers.message.calendar_notify") as notify,
    ):
        store.get_default_reminder_minutes = AsyncMock(return_value=None)
        cal.create_event = AsyncMock(side_effect=fake_create)
        line.reply_text = AsyncMock()
        notify.notify_others = AsyncMock()

        msg = await _handle_create("tok", intent, MagicMock(), "U1")

    assert cal.create_event.await_count == 3
    assert "看牙醫" in msg and "聚餐" in msg and "❌『壞掉』" in msg
    line.reply_text.assert_awaited_once_with("tok", msg)
    assert [c.args[2] for c in notify.notify_others.await_args_list] == ["看牙醫", "聚餐"]


def test_assumption_note_truncates_runaway_output():
    intent = CalendarIntent(action=ActionType.CREATE, clarification_needed="推定為明天～！" * 50)
    note = _with_assumption_note("ok", intent).split("💡 ")[1]
    assert len(note) == _NOTE_MAX_LEN + 1 and note.endswith("…")
