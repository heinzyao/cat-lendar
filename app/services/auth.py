"""Service Account 憑證（共享日曆）。金鑰 JSON 由 Secret Manager 以 GOOGLE_SERVICE_ACCOUNT_JSON 注入；
初次設定：./scripts/setup_service_account.sh"""

from __future__ import annotations

import json
import logging

from google.oauth2.service_account import Credentials

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_shared_credentials() -> Credentials:
    """建立 Service Account Credentials，用於操作共享 Google Calendar。"""
    if not settings.google_service_account_json:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON 未設定，請執行 scripts/setup_service_account.sh"
        )
    info = json.loads(settings.google_service_account_json)
    return Credentials.from_service_account_info(info, scopes=SCOPES)
