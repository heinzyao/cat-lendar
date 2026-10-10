"""Google Calendar CRUD（共享日曆）。

- 每個行程的 description 附加操作者 [LINE: {user_id}]，供跨用戶通知追蹤
- 有提醒時同時設 Google Calendar popup 與 Firestore reminder：popup 不會推 LINE
"""

from __future__ import annotations

import logging
import re
import asyncio
from datetime import timedelta
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import settings
from app.models.intent import EventDetails, TimeRange
from app.store import firestore as store
from app.utils.datetime_utils import to_date_str, to_rfc3339

logger = logging.getLogger(__name__)


def _get_service(credentials: Credentials):
    """建立 Google Calendar API 服務物件。

    設計理由：
    - 每次呼叫建立新的 service 而非 Singleton，因為 credentials 可能會更新
    - googleapiclient.discovery.build 會快取 discovery document，實際開銷不大
    """
    return build("calendar", "v3", credentials=credentials)


async def _execute(request):
    """Run the blocking googleapiclient request off the event loop."""
    return await asyncio.to_thread(request.execute)


def _build_description(original: str | None, line_user_id: str) -> str:
    """在行程 description 尾端附加（或更新）操作者的 LINE ID 標記。

    格式：原始描述 + 換行 + [LINE: U1234567890abcdef]

    設計理由：
    - 追蹤「是誰最後操作了這個行程」，用於跨用戶通知（「A 修改了行程，通知 B」）
    - 使用 regex 移除舊標記：確保重複修改時不會累積多個 [LINE: ...] 標記
    - 以 strip() 處理多餘空白，避免 description 中出現孤立的換行
    """
    # 移除現有的 [LINE: ...] 標記（避免重複累積）
    base = re.sub(r"\s*\[LINE: [^\]]+\]", "", original or "").rstrip()
    tag = f"[LINE: {line_user_id}]"
    return f"{base}\n{tag}".strip() if base else tag


async def create_event(
    credentials: Credentials,
    details: EventDetails,
    line_user_id: str | None = None,
    reminder_minutes: int | None = None,
) -> dict[str, Any]:
    """在 Google Calendar 建立新行程，並在 Firestore 建立對應的提醒記錄。

    全天事件 vs 定時事件：
    - all_day=True → 使用 {"date": "YYYY-MM-DD"} 格式（Google Calendar 規格）
    - all_day=False → 使用 {"dateTime": "ISO8601"} 格式，包含時區資訊

    提醒設定（effective_minutes）：
    - 優先使用傳入的 reminder_minutes（由 handler 合併預設值後傳入）
    - fallback 至 details.reminder_minutes（模型從訊息提取）
    - 若兩者皆 None，使用 Google Calendar 的日曆預設提醒

    為何同時寫 Firestore reminder？
    - Google Calendar popup 提醒只在裝置上顯示，不會觸發 LINE 通知
    - Firestore reminder 由 Cloud Scheduler 定期掃描，到期時推播 LINE 訊息
    """
    service = _get_service(credentials)

    body: dict[str, Any] = {"summary": details.summary}

    if details.start_time is None:
        raise ValueError("create_event requires start_time")

    # 全天事件使用 date 格式，定時事件使用 dateTime 格式
    if details.all_day:
        body["start"] = {"date": to_date_str(details.start_time)}
        end = details.end_time or details.start_time  # 全天事件若無結束時間，默認同一天
        body["end"] = {"date": to_date_str(end)}
    else:
        body["start"] = {"dateTime": to_rfc3339(details.start_time)}
        end_time = details.end_time
        if end_time is None:
            end_time = details.start_time + timedelta(hours=1)
        body["end"] = {"dateTime": to_rfc3339(end_time)}

    if details.location:
        body["location"] = details.location

    desc = details.description
    if line_user_id:
        # 附加操作者 LINE ID 標記，供跨用戶通知追蹤使用
        desc = _build_description(desc, line_user_id)
    if desc:
        body["description"] = desc

    # effective_minutes：合併兩個來源的提醒設定
    effective_minutes = (
        reminder_minutes if reminder_minutes is not None else details.reminder_minutes
    )
    if effective_minutes is not None:
        # 覆蓋 Google Calendar 預設提醒，使用 popup 方式
        body["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": effective_minutes}],
        }

    event = await _execute(
        service.events().insert(calendarId=settings.google_calendar_id, body=body)
    )

    # 若有提醒設定，同步寫入 Firestore 供 Bot 定時推播 LINE 通知
    if effective_minutes is not None and line_user_id and details.start_time:
        await store.create_reminder(
            line_user_id, event["id"], details.summary or "", details.start_time, effective_minutes
        )

    return event


async def query_events(
    credentials: Credentials,
    time_range: TimeRange,
    keyword: str | None = None,
    max_results: int = 20,
) -> list[dict[str, Any]]:
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

    result = await _execute(service.events().list(**params))
    return result.get("items", [])


async def update_event(
    credentials: Credentials,
    event_id: str,
    updates: EventDetails,
    line_user_id: str | None = None,
) -> dict[str, Any]:
    service = _get_service(credentials)

    event = await _execute(
        service.events().get(calendarId=settings.google_calendar_id, eventId=event_id)
    )

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
            updates.description
            if updates.description is not None
            else event.get("description"),
            line_user_id,
        )
    elif updates.description is not None:
        event["description"] = updates.description

    updated = await _execute(
        service.events().update(
            calendarId=settings.google_calendar_id, eventId=event_id, body=event
        )
    )

    if updates.start_time is not None and line_user_id:
        existing_reminder = await store.get_reminder_by_event(line_user_id, event_id)
        if existing_reminder:
            minutes = existing_reminder["reminder_minutes"]
            new_reminder_at = updates.start_time - timedelta(minutes=minutes)
            await store.update_reminder_by_event(
                line_user_id,
                event_id,
                {
                    "start_time": updates.start_time,
                    "reminder_at": new_reminder_at,
                    "event_summary": updates.summary
                    or existing_reminder["event_summary"],
                    "sent": False,
                },
            )

    return updated


async def delete_event(
    credentials: Credentials,
    event_id: str,
    line_user_id: str | None = None,
) -> None:
    service = _get_service(credentials)
    await _execute(
        service.events().delete(calendarId=settings.google_calendar_id, eventId=event_id)
    )
    if line_user_id:
        await store.delete_reminder_by_event(line_user_id, event_id)
