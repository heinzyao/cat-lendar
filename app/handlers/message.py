from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.models.intent import ActionType, CalendarIntent, EventDetails, TimeRange
from app.models.user import UserState
from app.services import auth, calendar, line_messaging, nlp
from app.services import local_calendar
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

    if text in ("解除授權", "取消授權", "登出"):
        await auth.revoke_auth(user_id)
        await line_messaging.reply_text(reply_token, "已解除 Google 日曆授權。")
        return

    if text == "切換行事曆":
        await _handle_switch_calendar(user_id, reply_token)
        return

    user_state = await store.get_user_state(user_id)
    calendar_mode = await store.get_calendar_mode(user_id)

    # 新使用者：尚未選擇行事曆模式
    if calendar_mode is None:
        if user_state and user_state.action == "choose_calendar_mode":
            await _handle_calendar_mode_choice(user_id, reply_token, text, user_state)
        else:
            state = UserState(
                line_user_id=user_id,
                action="choose_calendar_mode",
                original_intent={"original_message": text},
                expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=settings.user_state_ttl_seconds),
            )
            await store.save_user_state(state)
            await line_messaging.reply_text(reply_token, i18n.CHOOSE_CALENDAR_MODE)
        return

    # 切換模式的目標選擇
    if user_state and user_state.action == "switch_calendar_choice":
        await _handle_switch_mode_choice(user_id, reply_token, text, user_state)
        return

    # 遷移確認
    if user_state and user_state.action == "confirm_migration":
        await _handle_migration_choice(user_id, reply_token, text, user_state)
        return

    # 取得 Google credentials（Google 模式）
    credentials = None
    if calendar_mode == "google":
        credentials = await auth.get_valid_credentials(user_id)
        if credentials is None:
            auth_url = await auth.create_auth_url(user_id)
            await line_messaging.reply_auth_button(reply_token, auth_url)
            return

    # 多筆事件選擇狀態
    if user_state and user_state.action in (
        "select_event_for_update",
        "select_event_for_delete",
    ):
        reply_msg = await _handle_selection(
            user_id, reply_token, text, user_state, credentials, calendar_mode
        )
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

    reply_msg = await _execute_intent(user_id, reply_token, intent, credentials, calendar_mode)
    await store.append_conversation_turn(user_id, text, reply_msg)


# ── Calendar mode selection ──


async def _handle_calendar_mode_choice(
    user_id: str, reply_token: str, text: str, user_state: UserState
) -> None:
    original_message = user_state.original_intent.get("original_message", "")
    await store.delete_user_state(user_id)

    if text == "1":
        await store.set_calendar_mode(user_id, "google")
        auth_url = await auth.create_auth_url(user_id)
        await line_messaging.reply_auth_button(
            reply_token, auth_url, i18n.CALENDAR_MODE_SET_GOOGLE
        )
    elif text == "2":
        await store.set_calendar_mode(user_id, "local")
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_MODE_SET_LOCAL)
        if original_message:
            await _auto_process_message(user_id, original_message, "local")
    else:
        state = UserState(
            line_user_id=user_id,
            action="choose_calendar_mode",
            original_intent=user_state.original_intent,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=settings.user_state_ttl_seconds),
        )
        await store.save_user_state(state)
        await line_messaging.reply_text(reply_token, i18n.CHOOSE_CALENDAR_MODE)


# ── Calendar switch ──


async def _handle_switch_calendar(user_id: str, reply_token: str) -> None:
    current_mode = await store.get_calendar_mode(user_id)
    mode_name = (
        "Google Calendar" if current_mode == "google"
        else "內建行事曆" if current_mode == "local"
        else "未設定"
    )
    state = UserState(
        line_user_id=user_id,
        action="switch_calendar_choice",
        original_intent={"current_mode": current_mode or ""},
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.user_state_ttl_seconds),
    )
    await store.save_user_state(state)
    await line_messaging.reply_text(
        reply_token, i18n.SWITCH_CALENDAR_PROMPT.format(current_mode=mode_name)
    )


async def _handle_switch_mode_choice(
    user_id: str, reply_token: str, text: str, user_state: UserState
) -> None:
    current_mode = user_state.original_intent.get("current_mode", "")
    new_mode = "google" if text == "1" else "local" if text == "2" else None

    if new_mode is None:
        await _handle_switch_calendar(user_id, reply_token)
        return

    await store.delete_user_state(user_id)

    if new_mode == current_mode:
        mode_name = "Google Calendar" if new_mode == "google" else "內建行事曆"
        await line_messaging.reply_text(
            reply_token, f"目前已是{mode_name}模式，無需切換。"
        )
        return

    state = UserState(
        line_user_id=user_id,
        action="confirm_migration",
        original_intent={"new_mode": new_mode, "old_mode": current_mode},
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.user_state_ttl_seconds),
    )
    await store.save_user_state(state)
    await line_messaging.reply_text(reply_token, i18n.MIGRATION_PROMPT)


async def _handle_migration_choice(
    user_id: str, reply_token: str, text: str, user_state: UserState
) -> None:
    new_mode = user_state.original_intent.get("new_mode", "")
    old_mode = user_state.original_intent.get("old_mode", "")
    await store.delete_user_state(user_id)

    new_mode_name = "Google Calendar" if new_mode == "google" else "內建行事曆"

    if text == "1":  # 遷移
        if new_mode == "google":
            # Local → Google：OAuth 完成後才能遷移，先授權
            await store.set_calendar_mode(user_id, "google")
            pending = UserState(
                line_user_id=user_id,
                action="pending_local_to_google_migration",
                original_intent={},
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            await store.save_user_state(pending)
            auth_url = await auth.create_auth_url(user_id)
            await line_messaging.reply_auth_button(
                reply_token, auth_url, i18n.CALENDAR_MODE_SET_GOOGLE
            )
        else:
            # Google → Local：立即執行
            credentials = await auth.get_valid_credentials(user_id)
            count = 0
            if credentials:
                count = await _migrate_google_to_local(user_id, credentials)
            await store.set_calendar_mode(user_id, "local")
            await line_messaging.reply_text(
                reply_token, i18n.MIGRATION_SUCCESS.format(count=count)
            )

    elif text == "2":  # 不遷移
        await store.set_calendar_mode(user_id, new_mode)
        if new_mode == "google":
            auth_url = await auth.create_auth_url(user_id)
            await line_messaging.reply_auth_button(
                reply_token, auth_url, i18n.CALENDAR_MODE_SET_GOOGLE
            )
        else:
            await line_messaging.reply_text(
                reply_token, i18n.MIGRATION_SKIPPED.format(mode=new_mode_name)
            )
    else:
        state = UserState(
            line_user_id=user_id,
            action="confirm_migration",
            original_intent={"new_mode": new_mode, "old_mode": old_mode},
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=settings.user_state_ttl_seconds),
        )
        await store.save_user_state(state)
        await line_messaging.reply_text(reply_token, i18n.MIGRATION_PROMPT)


# ── Migration helpers ──


async def _migrate_google_to_local(user_id: str, credentials) -> int:
    """將未來 30 天的 Google Calendar 事件存入本地行事曆"""
    now = datetime.now(timezone.utc)
    time_range = TimeRange(start=now, end=now + timedelta(days=30))
    events = await calendar.query_events(credentials, time_range)
    for event in events:
        start_raw = event.get("start", {})
        end_raw = event.get("end", {})
        all_day = "date" in start_raw and "dateTime" not in start_raw

        if all_day:
            start_time = datetime.fromisoformat(start_raw["date"]).replace(
                tzinfo=timezone.utc
            )
            end_time = datetime.fromisoformat(end_raw["date"]).replace(
                tzinfo=timezone.utc
            )
        else:
            start_time = datetime.fromisoformat(start_raw.get("dateTime", ""))
            end_time = datetime.fromisoformat(end_raw.get("dateTime", ""))

        details = EventDetails(
            summary=event.get("summary"),
            start_time=start_time,
            end_time=end_time,
            location=event.get("location"),
            description=event.get("description"),
            all_day=all_day,
        )
        await local_calendar.create_event(user_id, details)
    return len(events)


async def execute_local_to_google_migration(user_id: str, credentials) -> int:
    """將所有本地行事曆事件遷移到 Google Calendar（OAuth 完成後由 oauth.py 呼叫）"""
    raw_events = await store.list_local_events(user_id)
    count = 0
    for raw in raw_events:
        details = EventDetails(
            summary=raw.get("summary"),
            start_time=raw.get("start_time"),
            end_time=raw.get("end_time"),
            location=raw.get("location"),
            description=raw.get("description"),
            all_day=raw.get("all_day", False),
        )
        await calendar.create_event(credentials, details)
        count += 1
    await store.delete_all_local_events(user_id)
    return count


# ── Intent execution ──


async def _execute_intent(
    user_id: str,
    reply_token: str,
    intent: CalendarIntent,
    credentials,
    calendar_mode: str,
) -> str:
    try:
        if intent.action == ActionType.CREATE:
            return await _handle_create(reply_token, intent, credentials, user_id, calendar_mode)
        elif intent.action == ActionType.QUERY:
            return await _handle_query(reply_token, intent, credentials, user_id, calendar_mode)
        elif intent.action == ActionType.UPDATE:
            return await _handle_update(user_id, reply_token, intent, credentials, calendar_mode)
        elif intent.action == ActionType.DELETE:
            return await _handle_delete(user_id, reply_token, intent, credentials, calendar_mode)
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
    calendar_mode: str,
) -> str:
    details = intent.event_details
    if calendar_mode == "google":
        event = await calendar.create_event(credentials, details)
    else:
        event = await local_calendar.create_event(user_id, details)

    time_str = _get_event_time_str(event)
    if details.location:
        msg = i18n.EVENT_CREATED_WITH_LOCATION.format(
            summary=event.get("summary", ""), time=time_str, location=details.location
        )
    else:
        msg = i18n.EVENT_CREATED.format(summary=event.get("summary", ""), time=time_str)
    await line_messaging.reply_text(reply_token, msg)
    return msg


async def _handle_query(
    reply_token: str,
    intent: CalendarIntent,
    credentials,
    user_id: str,
    calendar_mode: str,
) -> str:
    if calendar_mode == "google":
        events = await calendar.query_events(
            credentials, intent.time_range, keyword=intent.search_keyword
        )
    else:
        events = await local_calendar.query_events(
            user_id, intent.time_range, keyword=intent.search_keyword
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
    calendar_mode: str,
) -> str:
    events = await _find_matching_events(intent, credentials, user_id, calendar_mode)
    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return i18n.NO_EVENTS_FOUND

    if len(events) == 1:
        update_details = None
        if intent.original_message:
            update_details = await nlp.parse_update_details(
                intent.original_message, events[0]
            )
        details_to_use = update_details or intent.event_details
        if calendar_mode == "google":
            updated = await calendar.update_event(credentials, events[0]["id"], details_to_use)
        else:
            updated = await local_calendar.update_event(user_id, events[0]["id"], details_to_use)
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
    calendar_mode: str,
) -> str:
    events = await _find_matching_events(intent, credentials, user_id, calendar_mode)
    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return i18n.NO_EVENTS_FOUND

    if len(events) == 1:
        summary = events[0].get("summary", "(無標題)")
        if calendar_mode == "google":
            await calendar.delete_event(credentials, events[0]["id"])
        else:
            await local_calendar.delete_event(user_id, events[0]["id"])
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
    calendar_mode: str,
) -> str | None:
    """處理使用者的編號選擇，回傳實際發送的訊息字串"""
    try:
        choice = int(text)
    except ValueError:
        await store.delete_user_state(user_id)
        await handle_message(user_id, reply_token, text)
        return None  # 遞迴呼叫會自行儲存對話記憶

    candidates = user_state.candidates
    if choice < 1 or choice > len(candidates):
        await line_messaging.reply_text(
            reply_token, f"請輸入 1~{len(candidates)} 的數字。"
        )
        return None  # 狀態保留，等待正確輸入

    selected = candidates[choice - 1]
    await store.delete_user_state(user_id)

    try:
        if user_state.action == "select_event_for_update":
            intent = CalendarIntent.model_validate(user_state.original_intent)
            update_details = None
            if intent.original_message:
                update_details = await nlp.parse_update_details(
                    intent.original_message, selected
                )
            details_to_use = update_details or intent.event_details
            if calendar_mode == "google":
                updated = await calendar.update_event(credentials, selected["id"], details_to_use)
            else:
                updated = await local_calendar.update_event(user_id, selected["id"], details_to_use)
            time_str = _get_event_time_str(updated)
            msg = i18n.EVENT_UPDATED.format(
                summary=updated.get("summary", ""), time=time_str
            )
            await line_messaging.reply_text(reply_token, msg)
            return msg

        elif user_state.action == "select_event_for_delete":
            summary = selected.get("summary", "(無標題)")
            if calendar_mode == "google":
                await calendar.delete_event(credentials, selected["id"])
            else:
                await local_calendar.delete_event(user_id, selected["id"])
            msg = i18n.EVENT_DELETED.format(summary=summary)
            await line_messaging.reply_text(reply_token, msg)
            return msg
    except Exception:
        logger.exception("Selection action failed")
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_ERROR)
        return i18n.CALENDAR_ERROR
    return None


# ── Auto-process (local mode 選定後自動處理原始訊息) ──


async def _auto_process_message(user_id: str, text: str, calendar_mode: str) -> None:
    """NLP 解析後用 push_text 傳送結果（reply_token 已使用完畢時）"""
    try:
        intent = await nlp.parse_intent(text)
    except Exception:
        logger.exception("Auto-process NLP failed for %s", user_id)
        return

    if intent.confidence < 0.5 or intent.clarification_needed:
        return  # 信心不足，略過

    try:
        msg = await _compute_intent_message(user_id, intent, None, calendar_mode)
        if msg:
            await line_messaging.push_text(user_id, msg)
    except Exception:
        logger.exception("Auto-process execute failed for %s", user_id)


async def _compute_intent_message(
    user_id: str, intent: CalendarIntent, credentials, calendar_mode: str
) -> str | None:
    """執行意圖並回傳格式化訊息字串"""
    if intent.action == ActionType.CREATE:
        if calendar_mode == "google":
            event = await calendar.create_event(credentials, intent.event_details)
        else:
            event = await local_calendar.create_event(user_id, intent.event_details)
        time_str = _get_event_time_str(event)
        details = intent.event_details
        if details and details.location:
            return i18n.EVENT_CREATED_WITH_LOCATION.format(
                summary=event.get("summary", ""),
                time=time_str,
                location=details.location,
            )
        return i18n.EVENT_CREATED.format(
            summary=event.get("summary", ""), time=time_str
        )

    if intent.action == ActionType.QUERY:
        if calendar_mode == "google":
            events = await calendar.query_events(
                credentials, intent.time_range, keyword=intent.search_keyword
            )
        else:
            events = await local_calendar.query_events(
                user_id, intent.time_range, keyword=intent.search_keyword
            )
        if not events:
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
        return "".join(lines).strip()

    return None  # UPDATE/DELETE 需互動，略過


# ── Helpers ──


async def _find_matching_events(
    intent: CalendarIntent, credentials, user_id: str, calendar_mode: str
) -> list[dict]:
    if not intent.time_range:
        return []
    if calendar_mode == "google":
        return await calendar.query_events(
            credentials, intent.time_range, keyword=intent.search_keyword
        )
    return await local_calendar.query_events(
        user_id, intent.time_range, keyword=intent.search_keyword
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


