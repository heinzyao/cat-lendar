"""Firestore 資料庫操作層：使用者狀態、對話記憶、行程提醒的持久化儲存。

設計理由——為何選 Firestore？
- Cloud Run 是無狀態（stateless）容器，每個請求可能落在不同的實例
  若用記憶體儲存對話記憶/選擇狀態，不同實例間無法共享 → 必須用外部儲存
- Firestore 提供即時一致性、自動 TTL（expires_at 手動檢查）、原生非同步 SDK
- 與 GCP 生態系（Cloud Run、Cloud Scheduler）無縫整合，不需額外設定

資料集合設計
-----------
┌──────────────────┬────────────────────────────────────────────────┐
│ 集合名稱          │ 用途                                            │
├──────────────────┼────────────────────────────────────────────────┤
│ users            │ 已互動用戶登記（first_seen, last_seen）          │
│ user_states      │ 多筆行程選擇的中間狀態（帶 expires_at TTL）      │
│ conversation_history│ 近期對話記憶（供 Claude 多輪理解上下文）      │
│ reminders        │ 行程提醒記錄（reminder_at <= now 時推播）        │
│ user_prefs       │ 使用者偏好（預設提醒分鐘數、通知開關）           │
└──────────────────┴────────────────────────────────────────────────┘

TTL 策略（手動實作，Firestore 無內建 TTL）：
- user_states：expires_at 欄位，get 時檢查是否過期並刪除
- conversation_history：updated_at + conversation_history_ttl_seconds，get 時檢查

Singleton Client 設計：
_db 全局唯一，避免重複建立 gRPC 連線（AsyncClient 內部維護連線池）
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from google.cloud.firestore import AsyncClient

from app.config import settings
from app.models.user import ConversationMessage, UserState

logger = logging.getLogger(__name__)

# Singleton Firestore 客戶端：延遲初始化，首次呼叫 get_db() 時建立
_db: AsyncClient | None = None


def get_db() -> AsyncClient:
    """取得或建立 Firestore 非同步客戶端（Singleton 模式）。

    設計理由：
    - 延遲初始化確保模組載入時不需要 GCP 憑證（方便本機測試 mock）
    - project=None 時使用 GOOGLE_CLOUD_PROJECT 環境變數或 ADC 預設專案
    """
    global _db
    if _db is None:
        _db = AsyncClient(project=settings.gcp_project_id or None)
    return _db


# ── Users (已互動用戶登記) ──


async def register_user(line_user_id: str) -> None:
    """登記或更新用戶的 last_seen（用於跨用戶推播通知）"""
    now = datetime.now(timezone.utc)
    ref = get_db().collection("users").document(line_user_id)
    doc = await ref.get()
    if doc.exists:
        await ref.update({"last_seen": now})
    else:
        await ref.set({"first_seen": now, "last_seen": now})


async def get_all_user_ids() -> list[str]:
    """取得所有已登記的用戶 ID"""
    docs = await get_db().collection("users").get()
    return [doc.id for doc in docs]


# ── User States (對話狀態) ──


async def save_user_state(state: UserState) -> None:
    await (
        get_db()
        .collection("user_states")
        .document(state.line_user_id)
        .set({
            "action": state.action,
            "candidates": state.candidates,
            "original_intent": state.original_intent,
            "expires_at": state.expires_at,
        })
    )


async def get_user_state(line_user_id: str) -> UserState | None:
    doc_ref = get_db().collection("user_states").document(line_user_id)
    doc = await doc_ref.get()
    if not doc.exists:
        return None

    data = doc.to_dict()
    if data["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await doc_ref.delete()
        return None

    return UserState(
        line_user_id=line_user_id,
        action=data["action"],
        candidates=data["candidates"],
        original_intent=data["original_intent"],
        expires_at=data["expires_at"],
    )


async def delete_user_state(line_user_id: str) -> None:
    await get_db().collection("user_states").document(line_user_id).delete()


# ── Conversation History (對話記憶) ──


async def get_conversation_history(
    line_user_id: str,
) -> list[ConversationMessage]:
    """取得使用者近期對話記憶，若已過期則清除並回傳空列表。"""
    doc_ref = get_db().collection("conversation_history").document(line_user_id)
    doc = await doc_ref.get()
    if not doc.exists:
        return []

    data = doc.to_dict()
    updated_at = data.get("updated_at")
    if updated_at is not None:
        if updated_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc) - timedelta(seconds=settings.conversation_history_ttl_seconds):
            await doc_ref.delete()
            return []

    messages_raw = data.get("messages", [])
    return [
        ConversationMessage(
            role=m["role"],
            content=m["content"],
            timestamp=m["timestamp"],
        )
        for m in messages_raw
    ]


async def append_conversation_turn(
    line_user_id: str,
    user_message: str,
    assistant_message: str,
) -> None:
    """新增一輪對話（user + assistant），超過 max_turns 則裁剪最舊的。"""
    now = datetime.now(timezone.utc)
    doc_ref = get_db().collection("conversation_history").document(line_user_id)
    doc = await doc_ref.get()

    messages: list[dict] = []
    if doc.exists:
        data = doc.to_dict()
        updated_at = data.get("updated_at")
        if updated_at is not None:
            if updated_at.replace(tzinfo=timezone.utc) >= datetime.now(timezone.utc) - timedelta(seconds=settings.conversation_history_ttl_seconds):
                messages = data.get("messages", [])

    messages.append({"role": "user", "content": user_message, "timestamp": now})
    messages.append({"role": "assistant", "content": assistant_message, "timestamp": now})

    max_messages = settings.max_conversation_turns * 2
    if len(messages) > max_messages:
        messages = messages[-max_messages:]

    await doc_ref.set({"messages": messages, "updated_at": now})


async def clear_conversation_history(line_user_id: str) -> None:
    """清除使用者的對話記憶。"""
    await get_db().collection("conversation_history").document(line_user_id).delete()


# ── Reminders ──


async def create_reminder(reminder_id: str, data: dict) -> None:
    await get_db().collection("reminders").document(reminder_id).set(data)


async def get_reminder_by_event(line_user_id: str, event_id: str) -> dict | None:
    docs = await (
        get_db()
        .collection("reminders")
        .where("line_user_id", "==", line_user_id)
        .where("event_id", "==", event_id)
        .limit(1)
        .get()
    )
    if not docs:
        return None
    doc = docs[0]
    return {"id": doc.id, **doc.to_dict()}


async def update_reminder_by_event(
    line_user_id: str, event_id: str, updates: dict
) -> None:
    docs = await (
        get_db()
        .collection("reminders")
        .where("line_user_id", "==", line_user_id)
        .where("event_id", "==", event_id)
        .limit(1)
        .get()
    )
    for doc in docs:
        await doc.reference.update(updates)


async def delete_reminder_by_event(line_user_id: str, event_id: str) -> None:
    docs = await (
        get_db()
        .collection("reminders")
        .where("line_user_id", "==", line_user_id)
        .where("event_id", "==", event_id)
        .get()
    )
    for doc in docs:
        await doc.reference.delete()


async def get_due_reminders() -> list[dict]:
    """取得所有到期且尚未發送的提醒（reminder_at <= now AND sent == False）"""
    now = datetime.now(timezone.utc)
    docs = await (
        get_db()
        .collection("reminders")
        .where("sent", "==", False)
        .where("reminder_at", "<=", now)
        .get()
    )
    return [{"id": doc.id, **doc.to_dict()} for doc in docs]


async def mark_reminder_sent(reminder_id: str) -> None:
    await get_db().collection("reminders").document(reminder_id).update({"sent": True})


# ── User default reminder preferences ──


async def get_default_reminder_minutes(line_user_id: str) -> int | None:
    doc = await get_db().collection("user_prefs").document(line_user_id).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("default_reminder_minutes")


async def set_default_reminder_minutes(line_user_id: str, minutes: int | None) -> None:
    now = datetime.now(timezone.utc)
    await get_db().collection("user_prefs").document(line_user_id).set(
        {"default_reminder_minutes": minutes, "updated_at": now}, merge=True
    )


# ── Notification preferences ──


async def get_notify_enabled(line_user_id: str) -> bool:
    """取得用戶的異動通知開關，預設為 True（未設定也視為開啟）"""
    doc = await get_db().collection("user_prefs").document(line_user_id).get()
    if not doc.exists:
        return True
    return doc.to_dict().get("notify_on_change", True)


async def set_notify_enabled(line_user_id: str, enabled: bool) -> None:
    now = datetime.now(timezone.utc)
    await get_db().collection("user_prefs").document(line_user_id).set(
        {"notify_on_change": enabled, "updated_at": now}, merge=True
    )


# ── OAuth States (CSRF 防護用一次性 state) ──


async def save_oauth_state(state: str) -> None:
    """儲存 OAuth state token，10 分鐘後過期。"""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    await get_db().collection("oauth_states").document(state).set({"expires_at": expires_at})


async def verify_and_consume_oauth_state(state: str) -> bool:
    """驗證並消費 OAuth state（一次性使用）。回傳 True 表示有效。"""
    ref = get_db().collection("oauth_states").document(state)
    doc = await ref.get()
    if not doc.exists:
        return False
    await ref.delete()
    data = doc.to_dict()
    expires_at = data["expires_at"]
    if expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return False
    return True
