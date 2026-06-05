"""Google 認證模組：建立用於操作 Google Calendar API 的 Service Account 憑證。

架構設計——Service Account（伺服器對伺服器）
-------------------------------------------
使用 Service Account 取代 OAuth User Credentials：
- 憑證永不過期，無需定期重新授權
- 適合 Cloud Run → Google Calendar 的伺服器端存取
- 金鑰 JSON 儲存於 Secret Manager，啟動時透過環境變數 GOOGLE_SERVICE_ACCOUNT_JSON 注入

前置條件（一次性設定）：
  ./scripts/setup_service_account.sh
"""

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
