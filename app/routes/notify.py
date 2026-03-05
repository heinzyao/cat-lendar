from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services import notification

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/internal/notify")
async def internal_notify(request: Request):
    secret = request.headers.get("X-Internal-Secret", "")
    if not settings.notify_secret or secret != settings.notify_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    sent = await notification.check_and_send_reminders()
    return {"sent": sent}
