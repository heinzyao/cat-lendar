"""Google OAuth 認證模組：建立用於操作 Google Calendar API 的憑證。

架構設計——Shared Calendar（共享日曆）
--------------------------------------
所有 LINE 使用者共用 App Owner 的 Google 帳號，無需每位使用者各自授權。

優點：
- 使用者體驗簡單：加 LINE Bot 為好友即可使用，不需要 Google 登入
- 適用於家庭或小型團隊共用日曆的場景

限制：
- 無法依使用者隔離日曆（所有人看到同一個日曆）
- App Owner 必須保持 Google 帳號正常，否則所有人都無法使用

token=None 的設計：
每次呼叫 Google API 時，google-auth 函式庫會自動用 refresh_token
向 token_uri 換取新的 access_token（access_token 有效期約 1 小時）
因此不需要自行快取 access_token，每次建立 Credentials 物件即可
"""

from __future__ import annotations

import asyncio
import logging

from google.oauth2.credentials import Credentials

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# 記憶體快取：OAuth 重新授權後更新，避免需要重新部署才能生效
_cached_refresh_token: str | None = None


def get_shared_credentials() -> Credentials:
    """建立 App Owner 的 Google OAuth Credentials（共享日曆架構）。

    優先使用記憶體快取（重新授權後更新），fallback 到啟動時環境變數。
    """
    token = _cached_refresh_token or settings.google_refresh_token
    return Credentials(
        token=None,
        refresh_token=token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
    )


async def update_refresh_token(new_token: str) -> None:
    """更新 refresh token：寫入記憶體快取並同步更新 Secret Manager。"""
    global _cached_refresh_token
    _cached_refresh_token = new_token
    await asyncio.to_thread(_write_to_secret_manager, new_token)


def _write_to_secret_manager(token: str) -> None:
    if not settings.gcp_project_id:
        logger.warning("gcp_project_id 未設定，略過 Secret Manager 更新")
        return
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{settings.gcp_project_id}/secrets/GOOGLE_REFRESH_TOKEN"
        client.add_secret_version(
            request={"parent": parent, "payload": {"data": token.encode("utf-8")}}
        )
        logger.info("GOOGLE_REFRESH_TOKEN 已更新至 Secret Manager")
    except Exception:
        logger.exception("寫入 Secret Manager 失敗")
