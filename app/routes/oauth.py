"""Google OAuth 回調路由：處理 LINE Bot 觸發的重新授權流程。

流程：
  1. LINE Owner 傳送「重新授權」→ message.py 呼叫 generate_auth_url()
  2. Bot 回傳 Google 授權連結給 Owner
  3. Owner 點擊 → Google 重定向至 GET /oauth/callback?code=...&state=...
  4. 本端點驗證 state、換取 token、更新 Secret Manager + 記憶體快取

安全設計：
- state 參數儲存於 Firestore，10 分鐘過期且一次性消費（防 CSRF）
- google_redirect_uri 未設定時回傳 503
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from google_auth_oauthlib.flow import Flow

from app.config import settings
from app.services import auth
from app.store import firestore as store

logger = logging.getLogger(__name__)
router = APIRouter()

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config, scopes=_SCOPES, redirect_uri=settings.google_redirect_uri
    )


async def generate_auth_url() -> str:
    """產生 Google OAuth 授權 URL（供 LINE 訊息處理器呼叫）。"""
    state = secrets.token_urlsafe(32)
    await store.save_oauth_state(state)
    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", state=state
    )
    return auth_url


@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    """接收 Google OAuth 授權碼，更新 refresh token。"""
    if not settings.google_redirect_uri:
        return HTMLResponse("<h1>503</h1><p>OAuth 功能未啟用。</p>", status_code=503)

    if not await store.verify_and_consume_oauth_state(state):
        return HTMLResponse(
            "<h1>❌ 無效的授權請求</h1><p>state 不符或已過期，請重新在 LINE 傳送「重新授權」。</p>",
            status_code=400,
        )

    try:
        flow = _build_flow()
        flow.fetch_token(code=code)
        credentials = flow.credentials
        if not credentials.refresh_token:
            return HTMLResponse(
                "<h1>❌ 未取得 refresh token</h1><p>請確認 Google 授權頁面已完整授權。</p>",
                status_code=400,
            )
        await auth.update_refresh_token(credentials.refresh_token)
        logger.info("Google OAuth refresh token 已成功更新")
    except Exception:
        logger.exception("OAuth callback 處理失敗")
        return HTMLResponse("<h1>❌ 授權失敗</h1><p>請稍後再試。</p>", status_code=500)

    return HTMLResponse(
        "<h1>✅ 授權成功！</h1>"
        "<p>Google Calendar 授權已更新，日曆功能立即恢復正常。</p>"
        "<p>可以關閉此視窗了。</p>"
    )
