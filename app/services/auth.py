from __future__ import annotations

from google.oauth2.credentials import Credentials

from app.config import settings

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_shared_credentials() -> Credentials:
    """建立使用 app owner refresh token 的 Google Credentials（同步，無 Firestore）"""
    return Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
    )
