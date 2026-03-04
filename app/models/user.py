from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UserToken(BaseModel):
    line_user_id: str
    encrypted_access_token: str
    encrypted_refresh_token: str
    token_expiry: datetime
    scopes: list[str] = []
    created_at: datetime
    updated_at: datetime


class OAuthState(BaseModel):
    state_token: str
    line_user_id: str
    expires_at: datetime
    code_verifier: str | None = None


class UserState(BaseModel):
    """暫存使用者對話狀態，例如選擇要編輯哪筆行程"""

    line_user_id: str
    action: str  # e.g. "select_event_for_update", "select_event_for_delete"
    candidates: list[dict] = []  # 候選行程列表
    original_intent: dict = {}  # 原始意圖 JSON
    expires_at: datetime
