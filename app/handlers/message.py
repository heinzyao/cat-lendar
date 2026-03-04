from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.models.intent import ActionType, CalendarIntent
from app.services import auth, calendar, line_messaging, nlp
from app.store import firestore as store
from app.utils import i18n
from app.utils.datetime_utils import local_tz

logger = logging.getLogger(__name__)


async def handle_message(
    user_id: str, reply_token: str, text: str
) -> None:
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

    # 檢查是否已授權
    credentials = await auth.get_valid_credentials(user_id)
    if credentials is None:
        auth_url = await auth.create_auth_url(user_id)
        await line_messaging.reply_auth_button(reply_token, auth_url)
        return

    # 檢查是否在選擇狀態（模糊匹配多筆結果的後續選擇）
    user_state = await store.get_user_state(user_id)
    if user_state is not None:
        await _handle_selection(user_id, reply_token, text, user_state, credentials)
        return

    # Claude 解析意圖
    try:
        intent = await nlp.parse_intent(text)
    except Exception:
        logger.exception("NLP parse failed")
        await line_messaging.reply_text(reply_token, i18n.PARSE_ERROR)
        return

    # 信心不足或需要澄清
    if intent.confidence < 0.5 or intent.clarification_needed:
        msg = intent.clarification_needed or i18n.PARSE_ERROR
        await line_messaging.reply_text(
            reply_token, i18n.CLARIFICATION_NEEDED.format(message=msg)
        )
        return

    await _execute_intent(user_id, reply_token, intent, credentials)


async def _execute_intent(
    user_id: str,
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> None:
    try:
        if intent.action == ActionType.CREATE:
            await _handle_create(reply_token, intent, credentials)
        elif intent.action == ActionType.QUERY:
            await _handle_query(reply_token, intent, credentials)
        elif intent.action == ActionType.UPDATE:
            await _handle_update(user_id, reply_token, intent, credentials)
        elif intent.action == ActionType.DELETE:
            await _handle_delete(user_id, reply_token, intent, credentials)
        else:
            await line_messaging.reply_text(reply_token, i18n.PARSE_ERROR)
    except Exception:
        logger.exception("Calendar operation failed")
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_ERROR)


async def _handle_create(reply_token: str, intent: CalendarIntent, credentials) -> None:
    details = intent.event_details
    event = await calendar.create_event(credentials, details)

    start = event.get("start", {})
    end = event.get("end", {})
    from app.utils.datetime_utils import format_event_time

    time_str = format_event_time(
        start.get("dateTime", start.get("date", "")),
        end.get("dateTime", end.get("date", "")),
    )

    if details.location:
        msg = i18n.EVENT_CREATED_WITH_LOCATION.format(
            summary=event.get("summary", ""),
            time=time_str,
            location=details.location,
        )
    else:
        msg = i18n.EVENT_CREATED.format(
            summary=event.get("summary", ""), time=time_str
        )
    await line_messaging.reply_text(reply_token, msg)


async def _handle_query(reply_token: str, intent: CalendarIntent, credentials) -> None:
    events = await calendar.query_events(
        credentials,
        intent.time_range,
        keyword=intent.search_keyword,
    )

    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return

    lines = [i18n.EVENTS_LIST_HEADER]
    for idx, event in enumerate(events, 1):
        lines.append(
            i18n.EVENT_LIST_ITEM.format(
                index=idx,
                summary=event.get("summary", "(無標題)"),
                time=_get_event_time_str(event),
            )
        )
    await line_messaging.reply_text(reply_token, "".join(lines).strip())


async def _handle_update(
    user_id: str, reply_token: str, intent: CalendarIntent, credentials
) -> None:
    events = await _find_matching_events(intent, credentials)
    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return

    if len(events) == 1:
        updated = await calendar.update_event(
            credentials, events[0]["id"], intent.event_details
        )
        time_str = _get_event_time_str(updated)
        msg = i18n.EVENT_UPDATED.format(
            summary=updated.get("summary", ""), time=time_str
        )
        await line_messaging.reply_text(reply_token, msg)
    else:
        await _save_selection_state(
            user_id, "select_event_for_update", events, intent
        )
        await _reply_selection(reply_token, events)


async def _handle_delete(
    user_id: str, reply_token: str, intent: CalendarIntent, credentials
) -> None:
    events = await _find_matching_events(intent, credentials)
    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return

    if len(events) == 1:
        summary = events[0].get("summary", "(無標題)")
        await calendar.delete_event(credentials, events[0]["id"])
        await line_messaging.reply_text(
            reply_token, i18n.EVENT_DELETED.format(summary=summary)
        )
    else:
        await _save_selection_state(
            user_id, "select_event_for_delete", events, intent
        )
        await _reply_selection(reply_token, events)


async def _handle_selection(
    user_id: str, reply_token: str, text: str, user_state, credentials
) -> None:
    """處理使用者的編號選擇"""
    try:
        choice = int(text)
    except ValueError:
        # 不是數字，清除狀態，重新解析
        await store.delete_user_state(user_id)
        await handle_message(user_id, reply_token, text)
        return

    candidates = user_state.candidates
    if choice < 1 or choice > len(candidates):
        await line_messaging.reply_text(
            reply_token, f"請輸入 1~{len(candidates)} 的數字。"
        )
        return

    selected = candidates[choice - 1]
    await store.delete_user_state(user_id)

    try:
        if user_state.action == "select_event_for_update":
            intent = CalendarIntent.model_validate(user_state.original_intent)
            updated = await calendar.update_event(
                credentials, selected["id"], intent.event_details
            )
            time_str = _get_event_time_str(updated)
            msg = i18n.EVENT_UPDATED.format(
                summary=updated.get("summary", ""), time=time_str
            )
            await line_messaging.reply_text(reply_token, msg)

        elif user_state.action == "select_event_for_delete":
            summary = selected.get("summary", "(無標題)")
            await calendar.delete_event(credentials, selected["id"])
            await line_messaging.reply_text(
                reply_token, i18n.EVENT_DELETED.format(summary=summary)
            )
    except Exception:
        logger.exception("Selection action failed")
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_ERROR)


# ── Helpers ──


async def _find_matching_events(intent: CalendarIntent, credentials) -> list[dict]:
    if intent.time_range:
        return await calendar.query_events(
            credentials,
            intent.time_range,
            keyword=intent.search_keyword,
        )
    return []


async def _save_selection_state(
    user_id: str, action: str, events: list[dict], intent: CalendarIntent
) -> None:
    from app.models.user import UserState
    from app.config import settings

    state = UserState(
        line_user_id=user_id,
        action=action,
        candidates=[
            {"id": e["id"], "summary": e.get("summary", ""), "start": e.get("start", {})}
            for e in events
        ],
        original_intent=intent.model_dump(mode="json"),
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.user_state_ttl_seconds),
    )
    await store.save_user_state(state)


async def _reply_selection(reply_token: str, events: list[dict]) -> None:
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
    await line_messaging.reply_text(reply_token, "".join(lines).strip())


def _get_event_time_str(event: dict) -> str:
    from app.utils.datetime_utils import format_event_time

    start = event.get("start", {})
    end = event.get("end", {})
    return format_event_time(
        start.get("dateTime", start.get("date", "")),
        end.get("dateTime", end.get("date", "")),
    )
