"""Google Calendar 增量同步服務：用 syncToken 偵測外部變動，同步更新 Firestore reminders。

流程
----
1. 從 Firestore 取出上次的 syncToken
2. 呼叫 Calendar API events().list(syncToken=...) 取得變動事件
   - 若 token 過期（410）→ 全量掃描重設 token，本次不處理事件
3. 對每個變動事件：
   - status=cancelled → 刪除 Firestore 中對應的 reminders
   - 時間變動        → 更新 reminders 的 reminder_at
4. 儲存新的 syncToken 供下次使用
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from googleapiclient.errors import HttpError

from app.services import auth
from app.services.calendar import _execute, _get_service
from app.config import settings
from app.store import firestore as store

logger = logging.getLogger(__name__)


async def _full_sync_for_token() -> str | None:
    """全量掃描，僅為取得初始 syncToken，不處理事件內容。"""
    credentials = auth.get_shared_credentials()
    service = _get_service(credentials)
    params: dict = {
        "calendarId": settings.google_calendar_id,
        "showDeleted": True,
        "singleEvents": True,
        "maxResults": 250,
    }
    sync_token = None
    while True:
        result = await _execute(service.events().list(**params))
        sync_token = result.get("nextSyncToken")
        page_token = result.get("nextPageToken")
        if not page_token:
            break
        params = {
            "calendarId": settings.google_calendar_id,
            "showDeleted": True,
            "singleEvents": True,
            "pageToken": page_token,
        }
    return sync_token


async def run_sync() -> dict:
    """執行一次增量同步，回傳 {deleted, updated, token_reset}。"""
    credentials = auth.get_shared_credentials()
    service = _get_service(credentials)
    sync_token = await store.get_sync_token()

    params: dict = {
        "calendarId": settings.google_calendar_id,
        "showDeleted": True,
        "singleEvents": True,
    }
    if sync_token:
        params["syncToken"] = sync_token

    all_events: list[dict] = []
    new_sync_token: str | None = None

    while True:
        try:
            result = await _execute(service.events().list(**params))
        except HttpError as e:
            if e.status_code == 410:
                logger.warning("syncToken expired, performing full sync to reset token")
                new_sync_token = await _full_sync_for_token()
                if new_sync_token:
                    await store.save_sync_token(new_sync_token)
                return {"deleted": 0, "updated": 0, "token_reset": True}
            raise

        all_events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if page_token:
            params = {
                "calendarId": settings.google_calendar_id,
                "showDeleted": True,
                "singleEvents": True,
                "pageToken": page_token,
            }
        else:
            new_sync_token = result.get("nextSyncToken")
            break

    stats = {"deleted": 0, "updated": 0, "token_reset": False}

    for event in all_events:
        event_id = event.get("id", "")
        if event.get("status") == "cancelled":
            deleted = await store.delete_reminders_by_event_id(event_id)
            if deleted:
                logger.info("Sync: deleted %d reminder(s) for cancelled event %s", deleted, event_id)
            stats["deleted"] += deleted
        else:
            start = event.get("start", {})
            start_str = start.get("dateTime", start.get("date", ""))
            if start_str:
                try:
                    new_start = datetime.fromisoformat(start_str)
                    if new_start.tzinfo is None:
                        new_start = new_start.replace(tzinfo=timezone.utc)
                    updated = await store.update_reminders_time_by_event_id(event_id, new_start)
                    if updated:
                        logger.info("Sync: updated %d reminder(s) for event %s", updated, event_id)
                    stats["updated"] += updated
                except (ValueError, TypeError):
                    pass

    if new_sync_token:
        await store.save_sync_token(new_sync_token)

    return stats
