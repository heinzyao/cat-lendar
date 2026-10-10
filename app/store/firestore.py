"""Firestore 存取層：用戶登記、選擇狀態、對話記憶、提醒、偏好、同步 token。

Cloud Run 多實例之間無法共享記憶體，所以狀態都放這裡；TTL 由讀取時檢查。
"""

from __future__ import annotations

import logging
import uuid
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


def _expired(data: dict) -> bool:
    """對話記憶是否超過 conversation_history_ttl_seconds 沒更新。"""
    updated_at = data.get("updated_at")
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.conversation_history_ttl_seconds)
    return updated_at is not None and updated_at.replace(tzinfo=timezone.utc) < cutoff


async def get_conversation_history(
    line_user_id: str,
) -> list[ConversationMessage]:
    """取得使用者近期對話記憶，若已過期則清除並回傳空列表。"""
    doc_ref = get_db().collection("conversation_history").document(line_user_id)
    doc = await doc_ref.get()
    if not doc.exists:
        return []

    data = doc.to_dict()
    if _expired(data):
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
    if doc.exists and (data := doc.to_dict()).get("updated_at") is not None and not _expired(data):
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


async def create_reminder(
    line_user_id: str,
    event_id: str,
    event_summary: str,
    start_time: datetime,
    reminder_minutes: int,
) -> None:
    """建立提醒：reminder_at 到達時由 /internal/notify 推播，推完標 sent=True。"""
    now = datetime.now(timezone.utc)
    await get_db().collection("reminders").document(str(uuid.uuid4())).set({
        "line_user_id": line_user_id,
        "event_id": event_id,
        "event_summary": event_summary,
        "start_time": start_time,
        "reminder_at": start_time - timedelta(minutes=reminder_minutes),
        "reminder_minutes": reminder_minutes,
        "sent": False,
        "created_at": now,
    })


def _user_event_reminders(line_user_id: str, event_id: str):
    return (
        get_db()
        .collection("reminders")
        .where("line_user_id", "==", line_user_id)
        .where("event_id", "==", event_id)
    )


async def get_reminder_by_event(line_user_id: str, event_id: str) -> dict | None:
    docs = await _user_event_reminders(line_user_id, event_id).limit(1).get()
    if not docs:
        return None
    return {"id": docs[0].id, **docs[0].to_dict()}


async def update_reminder_by_event(
    line_user_id: str, event_id: str, updates: dict
) -> None:
    for doc in await _user_event_reminders(line_user_id, event_id).limit(1).get():
        await doc.reference.update(updates)


async def delete_reminder_by_event(line_user_id: str, event_id: str) -> None:
    for doc in await _user_event_reminders(line_user_id, event_id).get():
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


# ── Calendar Sync Token ──


async def get_sync_token() -> str | None:
    doc = await get_db().collection("system").document("calendar_sync").get()
    return doc.to_dict().get("sync_token") if doc.exists else None


async def save_sync_token(token: str) -> None:
    await get_db().collection("system").document("calendar_sync").set(
        {"sync_token": token, "synced_at": datetime.now(timezone.utc)}, merge=True
    )


async def delete_reminders_by_event_id(event_id: str) -> int:
    """刪除某 event_id 的所有 reminder，回傳刪除數量。"""
    docs = await get_db().collection("reminders").where("event_id", "==", event_id).get()
    for doc in docs:
        await doc.reference.delete()
    return len(docs)


async def update_reminders_time_by_event_id(event_id: str, new_start: datetime) -> int:
    """更新某 event_id 所有 reminder 的時間，回傳更新數量。"""
    docs = await get_db().collection("reminders").where("event_id", "==", event_id).get()
    count = 0
    for doc in docs:
        data = doc.to_dict()
        # 任何欄位異動（描述、顏色…）都會進 sync，時間沒變就不能重設 sent，否則已推過的提醒會重推
        if data.get("start_time") == new_start:
            continue
        minutes = data.get("reminder_minutes", 0)
        new_reminder_at = new_start - timedelta(minutes=minutes)
        await doc.reference.update({
            "start_time": new_start,
            "reminder_at": new_reminder_at,
            "sent": False,
        })
        count += 1
    return count
