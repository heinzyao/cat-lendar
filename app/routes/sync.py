"""Calendar 同步路由：由 Cloud Scheduler 每 5 分鐘觸發，同步 Google Calendar 變動至 Firestore。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services import calendar_sync

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/internal/sync")
async def internal_sync(request: Request):
    """由 Cloud Scheduler 觸發，執行 Calendar ↔ Firestore 增量同步。"""
    secret = request.headers.get("X-Internal-Secret", "")
    if not settings.notify_secret or secret != settings.notify_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        stats = await calendar_sync.run_sync()
    except Exception:
        logger.exception("Calendar sync failed")
        raise HTTPException(status_code=500, detail="Sync failed")

    return stats
