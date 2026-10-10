"""使用者多步驟選擇狀態與對話記憶的資料模型（存 Firestore）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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

    role 是本專案的內部格式（沿用 user/assistant 慣例）：
    - "user"：LINE 使用者發出的訊息
    - "assistant"：Bot 回覆的內容（實際發送給 LINE 的文字）

    Gemini 的角色名稱是 user/model，兩者的轉換在 services/nlp.py 組裝
    multi-turn history 時進行——存進 Firestore 的一律是這裡的內部格式。

    timestamp 用於 TTL 判斷（雖然實際 TTL 由 Firestore 的 updated_at 欄位控制）
    """
    role: str        # "user" | "assistant"（內部格式，送出前轉為 Gemini 的 user/model）
    content: str     # 訊息內容
    timestamp: datetime  # 訊息時間（UTC）
