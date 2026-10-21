"""Gemini 輸出的時間：非本地時區保留牆上時刻改標本地（實測 lite 偶爾把 10:00 標成 +00:00）。
只校正 Gemini 回傳的物件，程式內部建立的 model 不受影響。"""

import os

os.environ.setdefault("LINE_CHANNEL_SECRET", "x")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "x")
os.environ.setdefault("GEMINI_API_KEY", "x")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.intent import CalendarIntentPayload, EventDetails, EventTarget, TimeRange
from app.services import nlp
from app.utils.datetime_utils import local_tz

TPE = timedelta(hours=8)
UTC10 = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)


def test_localize_nested_payload():
    p = CalendarIntentPayload(
        action="update",
        confidence=0.9,
        event_details=EventDetails(summary="a", start_time=UTC10, end_time=datetime(2026, 10, 24, 11, 0)),
        events=[EventDetails(summary="b", start_time=UTC10)],
        time_range=TimeRange(start=UTC10, end=UTC10 + timedelta(hours=1)),
        targets=[EventTarget(search_keyword="牙醫", time_range=TimeRange(start=UTC10, end=UTC10))],
    )
    nlp._localize_times(p)
    for dt in (p.event_details.start_time, p.event_details.end_time, p.events[0].start_time,
               p.time_range.start, p.targets[0].time_range.start):
        assert dt.utcoffset() == TPE
    assert p.event_details.start_time.hour == 10 and p.event_details.end_time.hour == 11


def test_local_time_untouched():
    st = datetime(2026, 10, 24, 10, 0, tzinfo=local_tz())
    d = EventDetails(summary="x", start_time=st)
    nlp._localize_times(d)
    assert d.start_time == st


def test_internal_models_not_rewritten():
    """不經過 nlp 的 model（例如由 Calendar API 的 UTC 時間建立）保持原樣。"""
    assert EventDetails(summary="x", start_time=UTC10).start_time == UTC10
    assert TimeRange(start=UTC10, end=UTC10).start == UTC10


async def test_parse_update_details_localizes_gemini_output():
    resp = MagicMock()
    resp.parsed = EventDetails(start_time=UTC10)
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=resp)
    ev = {"id": "e", "summary": "牙醫", "start": {"dateTime": "2026-10-22T10:00:00+08:00"},
          "end": {"dateTime": "2026-10-22T11:00:00+08:00"}}
    with patch.object(nlp, "_get_client", return_value=client):
        d = await nlp.parse_update_details("改到後天", ev)
    assert (d.start_time.hour, d.start_time.utcoffset()) == (10, TPE)


async def test_parse_intent_localizes_gemini_output():
    resp = MagicMock()
    resp.parsed = CalendarIntentPayload(
        action="create", confidence=0.9, event_details=EventDetails(summary="會議", start_time=UTC10))
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=resp)
    with patch.object(nlp, "_get_client", return_value=client):
        intent = await nlp.parse_intent("明天十點開會", None)
    assert (intent.event_details.start_time.hour, intent.event_details.start_time.utcoffset()) == (10, TPE)
