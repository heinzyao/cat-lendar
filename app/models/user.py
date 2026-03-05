from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UserState(BaseModel):
    """暫存使用者對話狀態，例如選擇要編輯哪筆行程"""

    line_user_id: str
    action: str  # e.g. "select_event_for_update", "select_event_for_delete"
    candidates: list[dict] = []  # 候選行程列表
    original_intent: dict = {}  # 原始意圖 JSON
    expires_at: datetime


class ConversationMessage(BaseModel):
    """對話記憶中的單一訊息"""

    role: str  # "user" | "assistant"
    content: str
    timestamp: datetime


class ConversationHistory(BaseModel):
    """使用者的近期對話記憶"""

    line_user_id: str
    messages: list[ConversationMessage] = Field(default_factory=list)
    updated_at: datetime
