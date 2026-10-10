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
from app.utils.datetime_utils import event_time

logger = logging.getLogger(__name__)


async def _list_all(service, **extra) -> tuple[list[dict], str | None]:
    """分頁取完所有變動事件，回傳 (events, nextSyncToken)。"""
    params = {
        "calendarId": settings.google_calendar_id,
        "showDeleted": True,
        "singleEvents": True,
        **extra,
    }
    events: list[dict] = []
    while True:
        result = await _execute(service.events().list(**params))
        events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return events, result.get("nextSyncToken")
        # 換頁時只能帶 pageToken，不能再帶 syncToken
        params = {k: v for k, v in params.items() if k != "syncToken"}
        params["pageToken"] = page_token


async def run_sync() -> dict:
    """執行一次增量同步，回傳 {deleted, updated, token_reset}。"""
    service = _get_service(auth.get_shared_credentials())
    sync_token = await store.get_sync_token()

    try:
        all_events, new_sync_token = await _list_all(
            service, **({"syncToken": sync_token} if sync_token else {})
        )
    except HttpError as e:
        if e.status_code != 410:
            raise
        # token 過期：全量掃描只為重設 token，本次不處理事件
        logger.warning("syncToken expired, performing full sync to reset token")
        _, new_sync_token = await _list_all(service, maxResults=250)
        if new_sync_token:
            await store.save_sync_token(new_sync_token)
        return {"deleted": 0, "updated": 0, "token_reset": True}

    stats = {"deleted": 0, "updated": 0, "token_reset": False}

    for event in all_events:
        event_id = event.get("id", "")
        if event.get("status") == "cancelled":
            deleted = await store.delete_reminders_by_event_id(event_id)
            if deleted:
                logger.info("Sync: deleted %d reminder(s) for cancelled event %s", deleted, event_id)
            stats["deleted"] += deleted
        else:
            start_str = event_time(event, "start")
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
