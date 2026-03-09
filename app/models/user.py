"""使用者狀態與對話記憶的資料模型。

設計理由——為何使用 Pydantic 而非一般 dataclass？
- Pydantic 的 model_validate() 支援從 Firestore dict 直接建構物件（自動型別轉換）
- model_dump(mode="json") 可將 datetime 序列化為字串（存入 Firestore 時必需）
- 型別宣告即文件，清楚說明各欄位的用途與型別

UserState 的生命週期：
  1. update/delete 找到多筆行程時，handlers/message.py 呼叫 store.save_user_state()
  2. 使用者輸入選擇編號時，handlers/message.py 讀取 UserState 執行操作
  3. 操作完成後（或 expires_at 到期）清除 UserState

ConversationHistory 的使用方式：
  Firestore 只儲存最近 max_conversation_turns 輪（messages 列表），
  實際上 get_conversation_history() 回傳 list[ConversationMessage] 供 NLP 使用，
  ConversationHistory 模型主要用於文件說明，不直接在程式中使用
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UserState(BaseModel):
    """暫存使用者的多步驟對話狀態（如選擇要編輯哪筆行程）。

    此模型存入 Firestore，以 line_user_id 為文件 ID，帶有 expires_at TTL。
    只在 update/delete 找到多筆符合行程時建立，操作完成後立即刪除。
    """
    line_user_id: str           # LINE 用戶 ID，同時也是 Firestore 文件 ID
    action: str                 # "select_event_for_update" | "select_event_for_delete"
    candidates: list[dict] = [] # 符合條件的行程候選列表（含 id, summary, start, end）
    original_intent: dict = {}  # 原始 CalendarIntent.model_dump()，選擇後重新執行用
    expires_at: datetime        # 狀態過期時間（設定於 config.user_state_ttl_seconds）


class ConversationMessage(BaseModel):
    """對話記憶中的單一訊息（一個 user 訊息或一個 assistant 回覆）。

    role 遵循 Claude API 規格：
    - "user"：LINE 使用者發出的訊息
    - "assistant"：Bot 回覆的內容（實際發送給 LINE 的文字）

    timestamp 用於 TTL 判斷（雖然實際 TTL 由 Firestore 的 updated_at 欄位控制）
    """
    role: str        # "user" | "assistant"（Claude API 要求的角色名稱）
    content: str     # 訊息內容
    timestamp: datetime  # 訊息時間（UTC）


class ConversationHistory(BaseModel):
    """使用者的近期對話記憶（文件說明用，實際操作透過 list[ConversationMessage]）。"""
    line_user_id: str
    messages: list[ConversationMessage] = Field(default_factory=list)
    updated_at: datetime  # 最後更新時間，用於 TTL 計算
