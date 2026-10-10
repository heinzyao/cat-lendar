"""Cloud Scheduler 觸發的內部端點：提醒推播與日曆同步。

只應被 Cloud Scheduler 呼叫，以 X-Internal-Secret 標頭驗證；
notify_secret 未設定時一律拒絕——寧可功能停用也不要無驗證的端點。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import settings
from app.services import calendar_sync, notification

logger = logging.getLogger(__name__)


def _require_internal_secret(request: Request) -> None:
    secret = request.headers.get("X-Internal-Secret", "")
    if not settings.notify_secret or secret != settings.notify_secret:
        raise HTTPException(status_code=403, detail="Forbidden")


router = APIRouter(dependencies=[Depends(_require_internal_secret)])


@router.post("/internal/notify")
async def internal_notify():
    """每分鐘掃描並推播到期提醒；回傳本次推播數量供 Scheduler 日誌記錄。"""
    return {"sent": await notification.check_and_send_reminders()}


@router.post("/internal/sync")
async def internal_sync():
    """每 5 分鐘把 Google Calendar 的變動同步到 Firestore reminders。"""
    try:
        return await calendar_sync.run_sync()
    except Exception:
        logger.exception("Calendar sync failed")
        raise HTTPException(status_code=500, detail="Sync failed")
