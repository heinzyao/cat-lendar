from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import settings
from app.models.user import OAuthState
from app.store import firestore as store

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


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
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = settings.google_redirect_uri
    return flow


async def create_auth_url(line_user_id: str) -> str:
    """產生 Google OAuth 授權 URL（含 CSRF state）"""
    state_token = secrets.token_urlsafe(32)

    oauth_state = OAuthState(
        state_token=state_token,
        line_user_id=line_user_id,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.oauth_state_ttl_seconds),
    )
    await store.save_oauth_state(oauth_state)

    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state_token,
    )
    return auth_url


async def handle_oauth_callback(
    code: str, state: str
) -> tuple[str | None, str | None]:
    """處理 OAuth callback，回傳 (line_user_id, error_message)"""
    oauth_state = await store.get_and_delete_oauth_state(state)
    if oauth_state is None:
        return None, "state_expired"

    try:
        flow = _build_flow()
        flow.fetch_token(code=code)
        credentials: Credentials = flow.credentials

        await store.save_user_token(
            line_user_id=oauth_state.line_user_id,
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            token_expiry=credentials.expiry.replace(tzinfo=timezone.utc),
            scopes=list(credentials.scopes) if credentials.scopes else SCOPES,
        )

        return oauth_state.line_user_id, None
    except Exception:
        logger.exception("OAuth callback failed")
        return oauth_state.line_user_id, "exchange_failed"


async def get_valid_credentials(line_user_id: str) -> Credentials | None:
    """取得有效的 Google credentials，必要時自動 refresh"""
    user_token = await store.get_user_token(line_user_id)
    if user_token is None:
        return None

    access_token, refresh_token = store.get_decrypted_tokens(user_token)

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=user_token.scopes,
    )
    creds.expiry = user_token.token_expiry.replace(tzinfo=None)

    if creds.valid:
        return creds

    if not creds.refresh_token:
        await store.delete_user_token(line_user_id)
        return None

    try:

        async def _do_refresh(rt: str):
            c = Credentials(
                token=None,
                refresh_token=rt,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
            )
            c.refresh(Request())
            expiry = c.expiry.replace(tzinfo=timezone.utc) if c.expiry else (
                datetime.now(timezone.utc) + timedelta(hours=1)
            )
            return c.token, c.refresh_token or rt, expiry

        new_access, _, _ = await store.refresh_user_token_transactional(
            line_user_id, _do_refresh
        )
        creds.token = new_access
        return creds
    except Exception:
        logger.exception("Token refresh failed for %s", line_user_id)
        await store.delete_user_token(line_user_id)
        return None


async def revoke_auth(line_user_id: str) -> None:
    await store.delete_user_token(line_user_id)
