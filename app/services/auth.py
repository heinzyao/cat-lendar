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

from google.oauth2.credentials import Credentials

from app.config import settings

# Google Calendar 讀寫權限
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_shared_credentials() -> Credentials:
    """建立 App Owner 的 Google OAuth Credentials（共享日曆架構）。

    token=None：google-auth 函式庫會在第一次 API 呼叫時自動用 refresh_token
    換取有效的 access_token，無需手動管理 access_token 的快取與更新。
    """
    return Credentials(
        token=None,                                           # access_token 由 SDK 自動取得
        refresh_token=settings.google_refresh_token,          # 長效 refresh token（設定於 .env）
        token_uri="https://oauth2.googleapis.com/token",      # Google OAuth 2.0 token 端點
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
    )
