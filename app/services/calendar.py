from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import settings
from app.models.intent import EventDetails, TimeRange
from app.store import firestore as store
from app.utils.datetime_utils import format_event_time, to_date_str, to_rfc3339

logger = logging.getLogger(__name__)


def _get_service(credentials: Credentials):
    return build("calendar", "v3", credentials=credentials)


def _build_description(original: str | None, line_user_id: str) -> str:
    """在 description 尾端附加/更新操作者 LINE ID 標記"""
    base = re.sub(r"\s*\[LINE: [^\]]+\]", "", original or "").rstrip()
    tag = f"[LINE: {line_user_id}]"
    return f"{base}\n{tag}".strip() if base else tag


async def create_event(
    credentials: Credentials,
    details: EventDetails,
    line_user_id: str | None = None,
    reminder_minutes: int | None = None,
) -> dict:
    service = _get_service(credentials)

    body: dict = {"summary": details.summary}

    if details.all_day:
        body["start"] = {"date": to_date_str(details.start_time)}
        end = details.end_time or details.start_time
        body["end"] = {"date": to_date_str(end)}
    else:
        body["start"] = {"dateTime": to_rfc3339(details.start_time)}
        body["end"] = {"dateTime": to_rfc3339(details.end_time)}

    if details.location:
        body["location"] = details.location

    desc = details.description
    if line_user_id:
        desc = _build_description(desc, line_user_id)
    if desc:
        body["description"] = desc

    effective_minutes = reminder_minutes if reminder_minutes is not None else details.reminder_minutes
    if effective_minutes is not None:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": effective_minutes}],
        }

    event = service.events().insert(calendarId=settings.google_calendar_id, body=body).execute()

    if effective_minutes is not None and line_user_id and details.start_time:
        reminder_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        reminder_at = details.start_time - timedelta(minutes=effective_minutes)
        await store.create_reminder(reminder_id, {
            "line_user_id": line_user_id,
            "event_id": event["id"],
            "event_summary": details.summary or "",
            "start_time": details.start_time,
            "reminder_at": reminder_at,
            "reminder_minutes": effective_minutes,
            "sent": False,
            "created_at": now,
        })

    return event


async def query_events(
    credentials: Credentials,
    time_range: TimeRange,
    keyword: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    service = _get_service(credentials)

    params = {
        "calendarId": settings.google_calendar_id,
        "timeMin": to_rfc3339(time_range.start),
        "timeMax": to_rfc3339(time_range.end),
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if keyword:
        params["q"] = keyword

    result = service.events().list(**params).execute()
    return result.get("items", [])


async def update_event(
    credentials: Credentials,
    event_id: str,
    updates: EventDetails,
    line_user_id: str | None = None,
) -> dict:
    service = _get_service(credentials)

    event = service.events().get(calendarId=settings.google_calendar_id, eventId=event_id).execute()

    if updates.summary:
        event["summary"] = updates.summary
    if updates.start_time:
        if updates.all_day:
            event["start"] = {"date": to_date_str(updates.start_time)}
        else:
            event["start"] = {"dateTime": to_rfc3339(updates.start_time)}
    if updates.end_time:
        if updates.all_day:
            event["end"] = {"date": to_date_str(updates.end_time)}
        else:
            event["end"] = {"dateTime": to_rfc3339(updates.end_time)}
    if updates.location is not None:
        event["location"] = updates.location

    if line_user_id:
        event["description"] = _build_description(
            updates.description if updates.description is not None else event.get("description"),
            line_user_id,
        )
    elif updates.description is not None:
        event["description"] = updates.description

    updated = (
        service.events()
        .update(calendarId=settings.google_calendar_id, eventId=event_id, body=event)
        .execute()
    )

    if updates.start_time is not None and line_user_id:
        existing_reminder = await store.get_reminder_by_event(line_user_id, event_id)
        if existing_reminder:
            minutes = existing_reminder["reminder_minutes"]
            new_reminder_at = updates.start_time - timedelta(minutes=minutes)
            await store.update_reminder_by_event(line_user_id, event_id, {
                "start_time": updates.start_time,
                "reminder_at": new_reminder_at,
                "event_summary": updates.summary or existing_reminder["event_summary"],
                "sent": False,
            })

    return updated


async def delete_event(
    credentials: Credentials,
    event_id: str,
    line_user_id: str | None = None,
) -> None:
    service = _get_service(credentials)
    service.events().delete(calendarId=settings.google_calendar_id, eventId=event_id).execute()
    if line_user_id:
        await store.delete_reminder_by_event(line_user_id, event_id)


def format_event_summary(event: dict) -> str:
    """格式化單一 event 供顯示"""
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
