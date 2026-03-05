from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.models.intent import ActionType, CalendarIntent, EventDetails, TimeRange
from app.models.user import UserState
from app.services import auth, calendar, line_messaging, nlp
from app.store import firestore as store
from app.utils import i18n
from app.config import settings

logger = logging.getLogger(__name__)


# ── Main entry point ──


async def handle_message(user_id: str, reply_token: str, text: str) -> None:
    """訊息處理協調器：主要進入點"""
    text = text.strip()

    # 特殊指令
    if text in ("說明", "help", "幫助"):
        await line_messaging.reply_text(reply_token, i18n.HELP_MESSAGE)
        return

    if text.startswith("設定預設提醒"):
        await _handle_set_default_reminder(user_id, reply_token, text)
        return

    if text in ("關閉預設提醒", "取消預設提醒"):
        await store.set_default_reminder_minutes(user_id, None)
        await line_messaging.reply_text(reply_token, i18n.DEFAULT_REMINDER_CLEARED)
        return

    # 取得共享憑證
    credentials = auth.get_shared_credentials()

    # 多筆事件選擇狀態
    user_state = await store.get_user_state(user_id)
    if user_state and user_state.action in (
        "select_event_for_update",
        "select_event_for_delete",
    ):
        reply_msg = await _handle_selection(user_id, reply_token, text, user_state, credentials)
        if reply_msg:
            await store.append_conversation_turn(user_id, text, reply_msg)
        return

    # 讀取對話記憶
    conversation_history = await store.get_conversation_history(user_id)

    # Claude 解析意圖
    try:
        intent = await nlp.parse_intent(text, conversation_history)
    except Exception:
        logger.exception("NLP parse failed")
        await line_messaging.reply_text(reply_token, i18n.PARSE_ERROR)
        return

    if intent.confidence < 0.5 or intent.clarification_needed:
        msg = intent.clarification_needed or i18n.PARSE_ERROR
        reply_msg = i18n.CLARIFICATION_NEEDED.format(message=msg)
        await line_messaging.reply_text(reply_token, reply_msg)
        await store.append_conversation_turn(user_id, text, reply_msg)
        return

    reply_msg = await _execute_intent(user_id, reply_token, intent, credentials)
    await store.append_conversation_turn(user_id, text, reply_msg)


# ── Intent execution ──


async def _execute_intent(
    user_id: str,
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> str:
    try:
        if intent.action == ActionType.CREATE:
            return await _handle_create(reply_token, intent, credentials, user_id)
        elif intent.action == ActionType.QUERY:
            return await _handle_query(reply_token, intent, credentials)
        elif intent.action == ActionType.UPDATE:
            return await _handle_update(user_id, reply_token, intent, credentials)
        elif intent.action == ActionType.DELETE:
            return await _handle_delete(user_id, reply_token, intent, credentials)
        elif intent.action == ActionType.SET_REMINDER:
            return await _handle_set_reminder(user_id, reply_token, intent, credentials)
        else:
            await line_messaging.reply_text(reply_token, i18n.PARSE_ERROR)
            return i18n.PARSE_ERROR
    except Exception:
        logger.exception("Calendar operation failed")
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_ERROR)
        return i18n.CALENDAR_ERROR


async def _handle_create(
    reply_token: str,
    intent: CalendarIntent,
    credentials,
    user_id: str,
) -> str:
    details = intent.event_details

    reminder_minutes = details.reminder_minutes
    if reminder_minutes is None:
        reminder_minutes = await store.get_default_reminder_minutes(user_id)

    event = await calendar.create_event(credentials, details, line_user_id=user_id, reminder_minutes=reminder_minutes)

    time_str = _get_event_time_str(event)
    if details.location:
        msg = i18n.EVENT_CREATED_WITH_LOCATION.format(
            summary=event.get("summary", ""), time=time_str, location=details.location
        )
    else:
        msg = i18n.EVENT_CREATED.format(summary=event.get("summary", ""), time=time_str)

    if reminder_minutes is not None:
        msg += "\n" + i18n.REMINDER_SET.format(minutes=reminder_minutes)

    await line_messaging.reply_text(reply_token, msg)
    return msg


async def _handle_query(
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> str:
    events = await calendar.query_events(
        credentials, intent.time_range, keyword=intent.search_keyword
    )

    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return i18n.NO_EVENTS_FOUND

    lines = [i18n.EVENTS_LIST_HEADER]
    for idx, event in enumerate(events, 1):
        lines.append(
            i18n.EVENT_LIST_ITEM.format(
                index=idx,
                summary=event.get("summary", "(無標題)"),
                time=_get_event_time_str(event),
            )
        )
    msg = "".join(lines).strip()
    await line_messaging.reply_text(reply_token, msg)
    return msg


async def _handle_update(
    user_id: str,
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> str:
    events = await _find_matching_events(intent, credentials)
    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return i18n.NO_EVENTS_FOUND

    if len(events) == 1:
        update_details = None
        if intent.original_message:
            update_details = await nlp.parse_update_details(intent.original_message, events[0])
        details_to_use = update_details or intent.event_details
        updated = await calendar.update_event(credentials, events[0]["id"], details_to_use, line_user_id=user_id)
        time_str = _get_event_time_str(updated)
        msg = i18n.EVENT_UPDATED.format(summary=updated.get("summary", ""), time=time_str)
        await line_messaging.reply_text(reply_token, msg)
        return msg
    else:
        await _save_selection_state(user_id, "select_event_for_update", events, intent)
        return await _reply_selection(reply_token, events)


async def _handle_delete(
    user_id: str,
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> str:
    events = await _find_matching_events(intent, credentials)
    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return i18n.NO_EVENTS_FOUND

    if len(events) == 1:
        summary = events[0].get("summary", "(無標題)")
        await calendar.delete_event(credentials, events[0]["id"], line_user_id=user_id)
        msg = i18n.EVENT_DELETED.format(summary=summary)
        await line_messaging.reply_text(reply_token, msg)
        return msg
    else:
        await _save_selection_state(user_id, "select_event_for_delete", events, intent)
        return await _reply_selection(reply_token, events)


async def _handle_selection(
    user_id: str,
    reply_token: str,
    text: str,
    user_state: UserState,
    credentials,
) -> str | None:
    """處理使用者的編號選擇，回傳實際發送的訊息字串"""
    try:
        choice = int(text)
    except ValueError:
        await store.delete_user_state(user_id)
        await handle_message(user_id, reply_token, text)
        return None

    candidates = user_state.candidates
    if choice < 1 or choice > len(candidates):
        await line_messaging.reply_text(
            reply_token, f"請輸入 1~{len(candidates)} 的數字。"
        )
        return None

    selected = candidates[choice - 1]
    await store.delete_user_state(user_id)

    try:
        if user_state.action == "select_event_for_update":
            intent = CalendarIntent.model_validate(user_state.original_intent)
            update_details = None
            if intent.original_message:
                update_details = await nlp.parse_update_details(intent.original_message, selected)
            details_to_use = update_details or intent.event_details
            updated = await calendar.update_event(credentials, selected["id"], details_to_use, line_user_id=user_id)
            time_str = _get_event_time_str(updated)
            msg = i18n.EVENT_UPDATED.format(summary=updated.get("summary", ""), time=time_str)
            await line_messaging.reply_text(reply_token, msg)
            return msg

        elif user_state.action == "select_event_for_delete":
            summary = selected.get("summary", "(無標題)")
            await calendar.delete_event(credentials, selected["id"], line_user_id=user_id)
            msg = i18n.EVENT_DELETED.format(summary=summary)
            await line_messaging.reply_text(reply_token, msg)
            return msg
    except Exception:
        logger.exception("Selection action failed")
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_ERROR)
        return i18n.CALENDAR_ERROR
    return None


# ── Helpers ──


async def _find_matching_events(intent: CalendarIntent, credentials) -> list[dict]:
    if not intent.time_range:
        return []
    return await calendar.query_events(
        credentials, intent.time_range, keyword=intent.search_keyword
    )


async def _save_selection_state(
    user_id: str, action: str, events: list[dict], intent: CalendarIntent
) -> None:
    state = UserState(
        line_user_id=user_id,
        action=action,
        candidates=[
            {
                "id": e["id"],
                "summary": e.get("summary", ""),
                "start": e.get("start", {}),
                "end": e.get("end", {}),
                "location": e.get("location"),
                "description": e.get("description"),
            }
            for e in events
        ],
        original_intent=intent.model_dump(mode="json"),
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.user_state_ttl_seconds),
    )
    await store.save_user_state(state)


async def _reply_selection(reply_token: str, events: list[dict]) -> str:
    lines = [i18n.MULTIPLE_EVENTS_FOUND]
    for idx, event in enumerate(events, 1):
        lines.append(
            i18n.EVENT_LIST_ITEM.format(
                index=idx,
                summary=event.get("summary", "(無標題)"),
                time=_get_event_time_str(event),
            )
        )
    lines.append(i18n.SELECT_PROMPT)
    msg = "".join(lines).strip()
    await line_messaging.reply_text(reply_token, msg)
    return msg


def _get_event_time_str(event: dict) -> str:
    from app.utils.datetime_utils import format_event_time

    start = event.get("start", {})
    end = event.get("end", {})
    return format_event_time(
        start.get("dateTime", start.get("date", "")),
        end.get("dateTime", end.get("date", "")),
    )


async def _handle_set_default_reminder(user_id: str, reply_token: str, text: str) -> None:
    """處理「設定預設提醒 N 分鐘前」指令"""
    match = re.search(r"(\d+)", text)
    if not match:
        await line_messaging.reply_text(
            reply_token,
            "請指定提醒分鐘數，例如：設定預設提醒 30 分鐘前"
        )
        return
    minutes = int(match.group(1))
    if "小時" in text:
        minutes *= 60
    await store.set_default_reminder_minutes(user_id, minutes)
    await line_messaging.reply_text(reply_token, i18n.DEFAULT_REMINDER_SET.format(minutes=minutes))


async def _handle_set_reminder(
    user_id: str,
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> str:
    """處理對已有行程設定提醒的 set_reminder action"""
    reminder_minutes = intent.event_details.reminder_minutes if intent.event_details else None
    if reminder_minutes is None:
        await line_messaging.reply_text(reply_token, "請指定提醒分鐘數，例如：提前 15 分鐘提醒")
        return "請指定提醒分鐘數"

    events = await _find_matching_events(intent, credentials)
    if not events:
        await line_messaging.reply_text(reply_token, i18n.REMINDER_EVENT_NOT_FOUND)
        return i18n.REMINDER_EVENT_NOT_FOUND

    if len(events) > 1:
        await _save_selection_state(user_id, "select_event_for_update", events, intent)
        return await _reply_selection(reply_token, events)

    event = events[0]
    event_id = event["id"]

    start_raw = event.get("start", {})
    start_str = start_raw.get("dateTime", start_raw.get("date", ""))
    try:
        start_time = datetime.fromisoformat(start_str)
    except (ValueError, TypeError):
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_ERROR)
        return i18n.CALENDAR_ERROR

    now = datetime.now(timezone.utc)
    reminder_at = start_time.astimezone(timezone.utc) - timedelta(minutes=reminder_minutes)

    existing = await store.get_reminder_by_event(user_id, event_id)
    if existing:
        await store.update_reminder_by_event(user_id, event_id, {
            "reminder_minutes": reminder_minutes,
            "reminder_at": reminder_at,
            "event_summary": event.get("summary", ""),
            "sent": False,
        })
        msg = i18n.REMINDER_UPDATED.format(minutes=reminder_minutes)
    else:
        reminder_id = str(uuid.uuid4())
        await store.create_reminder(reminder_id, {
            "line_user_id": user_id,
            "event_id": event_id,
            "event_summary": event.get("summary", ""),
            "start_time": start_time,
            "reminder_at": reminder_at,
            "reminder_minutes": reminder_minutes,
            "sent": False,
            "created_at": now,
        })
        msg = i18n.REMINDER_SET.format(minutes=reminder_minutes)

    await line_messaging.reply_text(reply_token, msg)
    return msg
