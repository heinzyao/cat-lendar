from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from google.cloud.firestore import AsyncClient, AsyncTransaction

from app.config import settings
from app.models.user import ConversationMessage, OAuthState, UserState, UserToken
from app.store.encryption import decrypt, encrypt

logger = logging.getLogger(__name__)

_db: AsyncClient | None = None


def get_db() -> AsyncClient:
    global _db
    if _db is None:
        _db = AsyncClient(project=settings.gcp_project_id or None)
    return _db


# ── User Tokens ──


async def get_user_token(line_user_id: str) -> UserToken | None:
    doc = await get_db().collection("users").document(line_user_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    return UserToken(
        line_user_id=line_user_id,
        encrypted_access_token=data["encrypted_access_token"],
        encrypted_refresh_token=data["encrypted_refresh_token"],
        token_expiry=data["token_expiry"],
        scopes=data.get("scopes", []),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


async def save_user_token(
    line_user_id: str,
    access_token: str,
    refresh_token: str,
    token_expiry: datetime,
    scopes: list[str] | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    doc_ref = get_db().collection("users").document(line_user_id)
    existing = await doc_ref.get()

    data = {
        "encrypted_access_token": encrypt(access_token),
        "encrypted_refresh_token": encrypt(refresh_token),
        "token_expiry": token_expiry,
        "scopes": scopes or [],
        "updated_at": now,
    }
    if not existing.exists:
        data["created_at"] = now

    await doc_ref.set(data, merge=True)


async def refresh_user_token_transactional(
    line_user_id: str,
    do_refresh: callable,
) -> tuple[str, str, datetime]:
    """用 Firestore transaction 做 token refresh，避免多 instance 競爭。

    do_refresh: async callable(refresh_token) -> (new_access, new_refresh, new_expiry)
    """
    db = get_db()
    doc_ref = db.collection("users").document(line_user_id)

    @db.async_transactional
    async def _txn(transaction: AsyncTransaction):
        doc = await doc_ref.get(transaction=transaction)
        if not doc.exists:
            raise ValueError(f"User {line_user_id} not found")

        data = doc.to_dict()
        # 如果 token 還沒過期，直接回傳現有的
        if data["token_expiry"] > datetime.now(timezone.utc):
            return (
                decrypt(data["encrypted_access_token"]),
                decrypt(data["encrypted_refresh_token"]),
                data["token_expiry"],
            )

        current_refresh = decrypt(data["encrypted_refresh_token"])
        new_access, new_refresh, new_expiry = await do_refresh(current_refresh)

        transaction.update(doc_ref, {
            "encrypted_access_token": encrypt(new_access),
            "encrypted_refresh_token": encrypt(new_refresh),
            "token_expiry": new_expiry,
            "updated_at": datetime.now(timezone.utc),
        })
        return new_access, new_refresh, new_expiry

    return await _txn(db.transaction())


async def delete_user_token(line_user_id: str) -> None:
    await get_db().collection("users").document(line_user_id).delete()


def get_decrypted_tokens(token: UserToken) -> tuple[str, str]:
    return decrypt(token.encrypted_access_token), decrypt(token.encrypted_refresh_token)


# ── OAuth States ──


async def save_oauth_state(state: OAuthState) -> None:
    await (
        get_db()
        .collection("oauth_states")
        .document(state.state_token)
        .set({
            "line_user_id": state.line_user_id,
            "expires_at": state.expires_at,
            "code_verifier": state.code_verifier,
        })
    )


async def get_and_delete_oauth_state(state_token: str) -> OAuthState | None:
    doc_ref = get_db().collection("oauth_states").document(state_token)
    doc = await doc_ref.get()
    if not doc.exists:
        return None

    data = doc.to_dict()
    if data["expires_at"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await doc_ref.delete()
        return None

    await doc_ref.delete()
    return OAuthState(
        state_token=state_token,
        line_user_id=data["line_user_id"],
        expires_at=data["expires_at"],
        code_verifier=data.get("code_verifier"),
    )


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


# ── User Prefs (行事曆模式選擇) ──


async def get_calendar_mode(line_user_id: str) -> str | None:
    doc = await get_db().collection("user_prefs").document(line_user_id).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("calendar_mode")


async def set_calendar_mode(line_user_id: str, mode: str) -> None:
    now = datetime.now(timezone.utc)
    await get_db().collection("user_prefs").document(line_user_id).set(
        {"calendar_mode": mode, "updated_at": now}, merge=True
    )


# ── Local Events (Firestore 內建行事曆) ──


def _local_events_col(line_user_id: str):
    return (
        get_db()
        .collection("local_events")
        .document(line_user_id)
        .collection("events")
    )


async def create_local_event(line_user_id: str, event_id: str, data: dict) -> None:
    await _local_events_col(line_user_id).document(event_id).set(data)


async def get_local_event(line_user_id: str, event_id: str) -> dict | None:
    doc = await _local_events_col(line_user_id).document(event_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


async def list_local_events(
    line_user_id: str,
    time_range=None,
    keyword: str | None = None,
) -> list[dict]:
    col = _local_events_col(line_user_id)
    if time_range:
        query = (
            col.where("start_time", ">=", time_range.start)
            .where("start_time", "<=", time_range.end)
            .order_by("start_time")
        )
    else:
        query = col.order_by("start_time")
    docs = await query.get()
    events = [{"id": doc.id, **doc.to_dict()} for doc in docs]
    if keyword:
        kw = keyword.lower()
        events = [
            e for e in events
            if kw in (e.get("summary") or "").lower()
            or kw in (e.get("description") or "").lower()
        ]
    return events


async def update_local_event(
    line_user_id: str, event_id: str, updates: dict
) -> None:
    updates["updated_at"] = datetime.now(timezone.utc)
    await _local_events_col(line_user_id).document(event_id).update(updates)


async def delete_local_event(line_user_id: str, event_id: str) -> None:
    await _local_events_col(line_user_id).document(event_id).delete()


async def delete_all_local_events(line_user_id: str) -> None:
    docs = await _local_events_col(line_user_id).get()
    for doc in docs:
        await doc.reference.delete()


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
        # 若已過期，從空白開始
        if updated_at is not None:
            if updated_at.replace(tzinfo=timezone.utc) >= datetime.now(timezone.utc) - timedelta(seconds=settings.conversation_history_ttl_seconds):
                messages = data.get("messages", [])

    # 新增本輪對話
    messages.append({"role": "user", "content": user_message, "timestamp": now})
    messages.append({"role": "assistant", "content": assistant_message, "timestamp": now})

    # 裁剪：每輪 2 則，max_turns 輪 = max_turns * 2 則
    max_messages = settings.max_conversation_turns * 2
    if len(messages) > max_messages:
        messages = messages[-max_messages:]

    await doc_ref.set({"messages": messages, "updated_at": now})


async def clear_conversation_history(line_user_id: str) -> None:
    """清除使用者的對話記憶。"""
    await get_db().collection("conversation_history").document(line_user_id).delete()
