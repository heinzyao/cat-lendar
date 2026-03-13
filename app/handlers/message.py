"""訊息處理協調器：LINE 訊息 → 意圖解析 → 日曆操作 → 回覆使用者。

架構說明
--------
此模組是整個 Bot 的核心業務邏輯層，負責協調各服務：

  LINE 訊息
       ↓
  handle_message()          ← 主入口：前置處理 + 意圖分派
       ├─ 特殊指令（說明/提醒設定/通知開關）  ← 不走 NLP，直接處理
       ├─ 選擇狀態（等待使用者選第幾筆）       ← 中斷狀態機
       └─ Claude NLP 解析 → _execute_intent() ← 一般自然語言輸入
              ├─ CREATE  → _handle_create()
              ├─ QUERY   → _handle_query()
              ├─ UPDATE  → _handle_update()
              ├─ DELETE  → _handle_delete()
              └─ SET_REMINDER → _handle_set_reminder()

設計決策
--------
1. fire-and-forget 登記用戶：
   asyncio.create_task(store.register_user()) 不 await，
   確保不因 Firestore 延遲拖慢主要回覆路徑

2. 對話記憶（conversation_history）：
   每次呼叫 nlp.parse_intent() 前先讀取記憶，
   讓 Claude 理解「改成明天」等依賴前文的指令

3. 多筆事件的選擇狀態機：
   當 update/delete 找到多筆符合的行程時，先將候選存入 Firestore（UserState），
   Bot 詢問使用者選擇編號 → 下一輪訊息進入 _handle_selection() 處理
   這是有狀態對話（stateful conversation）的設計，比重新呼叫 NLP 更可靠

4. confidence 門檻 0.5：
   低於此值表示 Claude 無法判斷意圖，改為詢問澄清，避免誤操作行程

5. 二階段 update 解析：
   update 操作先以 parse_intent() 定位行程（第一階段），
   找到行程後再以 parse_update_details() 結合原始行程資料精算更新值（第二階段）
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.models.intent import ActionType, CalendarIntent, EventDetails, TimeRange
from app.models.user import UserState
from app.services import auth, calendar, calendar_notify, line_messaging, nlp
from app.services.nlp import RateLimitExceeded
from app.store import firestore as store
from app.utils import i18n
from app.config import settings

logger = logging.getLogger(__name__)


def _with_assumption_note(msg: str, intent: CalendarIntent) -> str:
    """在回覆訊息末尾附加 Claude 的推定說明（若有）。

    設計理由：
    - Claude 推定不明確資訊後會在 clarification_needed 說明推定內容
      （例如：「已假設時間為今日下午 3 點」）
    - 附加在訊息末尾而非另外詢問，降低使用者操作負擔，同時保持透明度
    - 使用 💡 圖示視覺區隔推定說明與主要回覆
    """
    if intent.clarification_needed:
        return msg + f"\n\n💡 {intent.clarification_needed}"
    return msg

# ── Main entry point ──


async def handle_message(user_id: str, reply_token: str, text: str) -> None:
    """LINE 訊息處理協調器：主要進入點。

    處理順序（優先級由高至低）：
    1. 特殊指令（說明/設定提醒/通知開關）：字串完整比對，無需 AI 處理
    2. 選擇狀態：使用者正在選擇多筆行程之一，直接處理數字輸入
    3. NLP 意圖解析：一般自然語言日程操作請求
    """
    text = text.strip()

    # 背景登記用戶（fire-and-forget）
    # 設計理由：register_user 僅更新 last_seen，非核心路徑，不需等待其完成
    # 若此任務失敗也不影響主要功能，僅有跨用戶推播通知的資料可能遺漏
    asyncio.create_task(store.register_user(user_id))

    # ── 特殊指令（繞過 NLP，直接處理）──
    # 設計理由：這些是固定的操作指令，不需要 AI 解析，也不需要消耗 API token
    if text in ("說明", "help", "幫助"):
        await line_messaging.reply_text(reply_token, i18n.HELP_MESSAGE)
        return

    if text.startswith("設定預設提醒"):
        # 格式：「設定預設提醒 30 分鐘前」或「設定預設提醒 1 小時前」
        await _handle_set_default_reminder(user_id, reply_token, text)
        return

    if text in ("關閉預設提醒", "取消預設提醒"):
        await store.set_default_reminder_minutes(user_id, None)
        await line_messaging.reply_text(reply_token, i18n.DEFAULT_REMINDER_CLEARED)
        return

    if text in ("關閉通知", "取消通知"):
        # 關閉「其他人修改行程時通知我」功能
        await store.set_notify_enabled(user_id, False)
        await line_messaging.reply_text(reply_token, i18n.NOTIFY_DISABLED)
        return

    if text in ("開啟通知", "恢復通知"):
        await store.set_notify_enabled(user_id, True)
        await line_messaging.reply_text(reply_token, i18n.NOTIFY_ENABLED)
        return

    # 取得 App Owner 的 Google OAuth 共享憑證（所有使用者共用同一 Google 帳號的日曆）
    credentials = auth.get_shared_credentials()

    # ── 多筆事件選擇狀態機 ──
    # 當 update/delete 找到多筆符合的行程時，Bot 會要求使用者選擇編號
    # 此時使用者的下一則訊息（通常是數字）會進入此分支處理
    user_state = await store.get_user_state(user_id)
    if user_state and user_state.action in (
        "select_event_for_update",
        "select_event_for_delete",
    ):
        reply_msg = await _handle_selection(user_id, reply_token, text, user_state, credentials)
        if reply_msg:
            await store.append_conversation_turn(user_id, text, reply_msg)
        return

    # ── NLP 解析（一般日程操作）──

    # 讀取對話記憶，讓 Claude 理解多輪對話的上下文（如代名詞指涉）
    conversation_history = await store.get_conversation_history(user_id)

    # 呼叫 Claude API 解析自然語言意圖
    try:
        intent = await nlp.parse_intent(text, conversation_history, user_id=user_id)
    except RateLimitExceeded as e:
        await line_messaging.reply_text(reply_token, str(e))
        return
    except Exception:
        logger.exception("NLP parse failed")
        await line_messaging.reply_text(reply_token, i18n.PARSE_ERROR)
        return

    # confidence < 0.5 表示 Claude 無法判斷意圖，向使用者要求澄清
    # 設計理由：低信心直接執行可能導致誤操作行程，寧可多問一次
    if intent.confidence < 0.5:
        msg = intent.clarification_needed or i18n.PARSE_ERROR
        reply_msg = i18n.CLARIFICATION_NEEDED.format(message=msg)
        await line_messaging.reply_text(reply_token, reply_msg)
        await store.append_conversation_turn(user_id, text, reply_msg)
        return

    reply_msg = await _execute_intent(user_id, reply_token, intent, credentials)
    # 無論成功或失敗都寫入對話記憶，讓下一輪 Claude 知道此次操作的結果
    await store.append_conversation_turn(user_id, text, reply_msg)


# ── Intent execution ──


async def _execute_intent(
    user_id: str,
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> str:
    """依據 CalendarIntent 分派至對應的操作處理函式。

    設計理由：
    - 集中 try/except 在此層：所有日曆操作異常都在這裡攔截，
      下層函式可放心 raise 而不擔心未處理的例外導致 LINE 回覆超時
    - 回傳 str：回覆訊息文字，用於寫入對話記憶（供下一輪 Claude 參考）
    """
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
            # action == UNKNOWN，理論上不應進入此分支（confidence < 0.5 已過濾）
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
    """處理建立行程意圖。

    提醒優先級：
    1. Claude 從訊息中提取的提醒設定（details.reminder_minutes）
    2. 使用者的預設提醒設定（Firestore user_prefs.default_reminder_minutes）
    3. 無提醒（Google Calendar 使用日曆預設值）
    """
    details = intent.event_details

    # 提醒設定：優先使用 Claude 從訊息提取的值，若無則使用使用者預設設定
    reminder_minutes = details.reminder_minutes
    if reminder_minutes is None:
        reminder_minutes = await store.get_default_reminder_minutes(user_id)

    event = await calendar.create_event(credentials, details, line_user_id=user_id, reminder_minutes=reminder_minutes)

    time_str = _get_event_time_str(event)
    # 有地點時顯示更豐富的確認訊息
    if details.location:
        msg = i18n.EVENT_CREATED_WITH_LOCATION.format(
            summary=event.get("summary", ""), time=time_str, location=details.location
        )
    else:
        msg = i18n.EVENT_CREATED.format(summary=event.get("summary", ""), time=time_str)

    if reminder_minutes is not None:
        msg += "\n" + i18n.REMINDER_SET.format(minutes=reminder_minutes)

    await line_messaging.reply_text(reply_token, msg)

    # 通知其他已登記的用戶（共用日曆場景）
    await calendar_notify.notify_others("create", user_id, event.get("summary", ""), time_str)

    return _with_assumption_note(msg, intent)


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
    return _with_assumption_note(msg, intent)


async def _handle_update(
    user_id: str,
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> str:
    """處理修改行程意圖。

    二階段解析策略：
    1. 用 time_range/search_keyword 找到符合的行程（第一階段已由 parse_intent 完成）
    2. 找到唯一行程時，用 parse_update_details() 結合原始行程資料精算更新值
       （例如「延後 30 分鐘」需知道原始時間才能計算新時間）
    3. 找到多筆時，進入選擇狀態機，等待使用者選擇編號

    details_to_use 的降級策略：
    - 優先使用 parse_update_details() 的精算結果（更準確）
    - 若二次解析失敗，fallback 至 parse_intent() 的初步結果（至少有欄位值）
    """
    events = await _find_matching_events(intent, credentials)
    if not events:
        await line_messaging.reply_text(reply_token, i18n.NO_EVENTS_FOUND)
        return i18n.NO_EVENTS_FOUND

    if len(events) == 1:
        # 二次解析：將原始行程資料傳給 Claude，讓它精確計算需更新的欄位
        update_details = None
        if intent.original_message:
            update_details = await nlp.parse_update_details(intent.original_message, events[0], user_id=user_id)
        # 降級 fallback：二次解析失敗時使用第一階段的結果
        details_to_use = update_details or intent.event_details
        updated = await calendar.update_event(credentials, events[0]["id"], details_to_use, line_user_id=user_id)
        time_str = _get_event_time_str(updated)
        msg = i18n.EVENT_UPDATED.format(summary=updated.get("summary", ""), time=time_str)
        await line_messaging.reply_text(reply_token, msg)

        # 通知其他用戶此行程已被修改
        await calendar_notify.notify_others("update", user_id, updated.get("summary", ""), time_str)

        return _with_assumption_note(msg, intent)
    else:
        # 多筆符合：進入選擇狀態機，要求使用者指定要修改哪一筆
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

        # 通知其他用戶
        await calendar_notify.notify_others("delete", user_id, summary)

        return _with_assumption_note(msg, intent)
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
    """處理使用者在多筆行程中的選擇（編號輸入）。

    狀態機轉換：
    - 收到有效數字 → 執行對應操作 → 清除狀態
    - 收到非數字 → 清除狀態 → 重新以一般訊息處理（使用者可能想換個操作）
    - 收到超出範圍的數字 → 提示有效範圍，保留狀態等待重新輸入

    設計理由：
    - 狀態存入 Firestore 而非記憶體：確保 Cloud Run 多個實例間狀態一致
    - expires_at 防止狀態永久殘留（預設 TTL 設定於 config）
    - 收到非數字時清除狀態並重新處理，讓使用者可以放棄選擇直接下新指令
    """
    try:
        choice = int(text)
    except ValueError:
        # 非數字：放棄選擇，清除狀態，以一般訊息重新處理
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
                update_details = await nlp.parse_update_details(intent.original_message, selected, user_id=user_id)
            details_to_use = update_details or intent.event_details
            updated = await calendar.update_event(credentials, selected["id"], details_to_use, line_user_id=user_id)
            time_str = _get_event_time_str(updated)
            msg = i18n.EVENT_UPDATED.format(summary=updated.get("summary", ""), time=time_str)
            await line_messaging.reply_text(reply_token, msg)

            # 通知其他用戶
            await calendar_notify.notify_others("update", user_id, updated.get("summary", ""), time_str)

            return msg

        elif user_state.action == "select_event_for_delete":
            summary = selected.get("summary", "(無標題)")
            await calendar.delete_event(credentials, selected["id"], line_user_id=user_id)
            msg = i18n.EVENT_DELETED.format(summary=summary)
            await line_messaging.reply_text(reply_token, msg)

            # 通知其他用戶
            await calendar_notify.notify_others("delete", user_id, summary)

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
    return _with_assumption_note(msg, intent)
