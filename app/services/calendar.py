from __future__ import annotations

import logging
from datetime import datetime

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.models.intent import EventDetails, TimeRange
from app.utils.datetime_utils import format_event_time, to_date_str, to_rfc3339

logger = logging.getLogger(__name__)


def _get_service(credentials: Credentials):
    return build("calendar", "v3", credentials=credentials)


async def create_event(
    credentials: Credentials, details: EventDetails
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
    if details.description:
        body["description"] = details.description

    event = service.events().insert(calendarId="primary", body=body).execute()
    return event


async def query_events(
    credentials: Credentials,
    time_range: TimeRange,
    keyword: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    service = _get_service(credentials)

    params = {
        "calendarId": "primary",
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
) -> dict:
    service = _get_service(credentials)

    # 先取得現有 event
    event = service.events().get(calendarId="primary", eventId=event_id).execute()

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
    if updates.description is not None:
        event["description"] = updates.description

    updated = (
        service.events()
        .update(calendarId="primary", eventId=event_id, body=event)
        .execute()
    )
    return updated


async def delete_event(credentials: Credentials, event_id: str) -> None:
    service = _get_service(credentials)
    service.events().delete(calendarId="primary", eventId=event_id).execute()


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
