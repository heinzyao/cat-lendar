from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.models.intent import EventDetails, TimeRange
from app.store import firestore as store
from app.utils.datetime_utils import format_event_time, to_date_str, to_rfc3339

logger = logging.getLogger(__name__)


def _to_calendar_format(event_id: str, data: dict) -> dict:
    """將 Firestore 儲存格式轉為與 Google Calendar API 相同的結構"""
    event: dict = {"id": event_id, "summary": data.get("summary", "")}

    if data.get("all_day"):
        event["start"] = {"date": to_date_str(data["start_time"])}
        end_time = data.get("end_time") or data["start_time"]
        event["end"] = {"date": to_date_str(end_time)}
    else:
        event["start"] = {"dateTime": to_rfc3339(data["start_time"])}
        event["end"] = {"dateTime": to_rfc3339(data["end_time"])}

    if data.get("location"):
        event["location"] = data["location"]
    if data.get("description"):
        event["description"] = data["description"]

    return event


async def create_event(line_user_id: str, details: EventDetails) -> dict:
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    data = {
        "summary": details.summary,
        "start_time": details.start_time,
        "end_time": details.end_time or details.start_time,
        "location": details.location,
        "description": details.description,
        "all_day": details.all_day,
        "created_at": now,
        "updated_at": now,
    }
    await store.create_local_event(line_user_id, event_id, data)
    return _to_calendar_format(event_id, data)


async def query_events(
    line_user_id: str,
    time_range: TimeRange,
    keyword: str | None = None,
) -> list[dict]:
    raw_events = await store.list_local_events(
        line_user_id, time_range=time_range, keyword=keyword
    )
    return [_to_calendar_format(e["id"], e) for e in raw_events]


async def update_event(
    line_user_id: str, event_id: str, updates: EventDetails
) -> dict:
    raw = await store.get_local_event(line_user_id, event_id)
    if raw is None:
        raise ValueError(f"Event {event_id} not found")

    patch: dict = {}
    if updates.summary is not None:
        patch["summary"] = updates.summary
    if updates.start_time is not None:
        patch["start_time"] = updates.start_time
    if updates.end_time is not None:
        patch["end_time"] = updates.end_time
    if updates.location is not None:
        patch["location"] = updates.location
    if updates.description is not None:
        patch["description"] = updates.description
    if updates.all_day:
        patch["all_day"] = updates.all_day

    await store.update_local_event(line_user_id, event_id, patch)
    raw.update(patch)
    return _to_calendar_format(event_id, raw)


async def delete_event(line_user_id: str, event_id: str) -> None:
    await store.delete_local_event(line_user_id, event_id)


def format_event_summary(event: dict) -> str:
    """格式化單一 event 供顯示（與 calendar.py 介面相同）"""
    summary = event.get("summary", "(無標題)")
    start = event.get("start", {})
    end = event.get("end", {})

    start_str = start.get("dateTime", start.get("date", ""))
    end_str = end.get("dateTime", end.get("date", ""))

    time_str = format_event_time(start_str, end_str)
    location = event.get("location", "")
    parts = [f"📌 {summary}", f"🕐 {time_str}"]
    if location:
        parts.append(f"📍 {location}")
    return "\n".join(parts)
