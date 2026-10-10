"""訊息處理協調器：LINE 訊息 → 固定指令／選擇狀態／Gemini 意圖解析 → 日曆操作 → 回覆。

- update/delete 找到多筆時把候選存 Firestore（UserState），下一則訊息選編號
- confidence < 0.5 一律先問清楚，避免誤改行程
- update 二階段解析：先定位行程，再帶原行程給 parse_update_details 精算（「延後 30 分鐘」要知道原時間）
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from linebot.v3.messaging.exceptions import ApiException

from app.models.intent import ActionType, CalendarIntent, TimeRange
from app.models.user import UserState
from app.services import auth, calendar, calendar_notify, line_messaging, nlp
from app.services.nlp import RateLimitExceeded
from app.store import firestore as store
from app.utils import i18n
from app.utils.datetime_utils import event_time, format_event_time
from app.config import settings

logger = logging.getLogger(__name__)


_NOTE_MAX_LEN = 80


def _with_assumption_note(msg: str, intent: CalendarIntent) -> str:
    """在回覆訊息末尾附加模型的推定說明（若有）。

    設計理由：
    - 模型推定不明確資訊後會在 clarification_needed 說明推定內容
      （例如：「已假設時間為今日下午 3 點」）
    - 附加在訊息末尾而非另外詢問，降低使用者操作負擔，同時保持透明度
    - 使用 💡 圖示視覺區隔推定說明與主要回覆
    """
    note = intent.clarification_needed
    if note:
        # 模型偶爾會陷入重複輸出，硬性截斷避免洗版
        if len(note) > _NOTE_MAX_LEN:
            note = note[:_NOTE_MAX_LEN] + "…"
        return msg + f"\n\n💡 {note}"
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

    # 取得 Service Account 共享憑證（所有使用者共用同一 Service Account 的日曆存取權）
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

    # 讀取對話記憶，讓模型理解多輪對話的上下文（如代名詞指涉）
    conversation_history = await store.get_conversation_history(user_id)

    # 呼叫 Gemini 解析自然語言意圖
    try:
        intent = await nlp.parse_intent(text, conversation_history, user_id=user_id)
    except RateLimitExceeded as e:
        await line_messaging.reply_text(reply_token, str(e))
        return
    except Exception:
        logger.exception("NLP parse failed")
        await line_messaging.reply_text(reply_token, i18n.PARSE_ERROR)
        return

    # confidence < 0.5 表示模型無法判斷意圖，向使用者要求澄清
    # 設計理由：低信心直接執行可能導致誤操作行程，寧可多問一次
    if intent.confidence < 0.5:
        msg = intent.clarification_needed or i18n.PARSE_ERROR
        reply_msg = i18n.CLARIFICATION_NEEDED.format(message=msg)
        await line_messaging.reply_text(reply_token, reply_msg)
        await store.append_conversation_turn(user_id, text, reply_msg)
        return

    reply_msg = await _execute_intent(user_id, reply_token, intent, credentials)
    # 無論成功或失敗都寫入對話記憶，讓下一輪對話知道此次操作的結果
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
    - 回覆失敗與日曆失敗分開攔截：兩者的處置不同（見下方 except），
      混在一起會讓 log 指向錯誤的元件
    - 回傳 str：回覆訊息文字，用於寫入對話記憶（供下一輪參考）
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
    except ApiException:
        # 各 _handle_* 內部自己會 reply，所以這裡攔到的 ApiException 代表
        # 「日曆操作成功、但回覆送不出去」（最常見：replyToken 過期或已用過）。
        # 不能再 reply 一次——同一個 token 只會再失敗一遍，還會把真正的錯因
        # 蓋成 CALENDAR_ERROR，讓日後查 log 時分不出是日曆壞了還是回覆壞了。
        logger.exception("LINE reply failed")
        return i18n.CALENDAR_ERROR
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
    1. 模型從訊息中提取的提醒設定（details.reminder_minutes）
    2. 使用者的預設提醒設定（Firestore user_prefs.default_reminder_minutes）
    3. 無提醒（Google Calendar 使用日曆預設值）
    多筆新增（intent.events）：逐筆建立，單筆失敗只標記該筆，不中斷其餘。
    設計理由：前面幾筆已寫進日曆，若整批回 CALENDAR_ERROR，使用者會以為全沒建、
    重送一次就重複建立。
    """
    details_list = intent.events or [intent.event_details]
    default_reminder = await store.get_default_reminder_minutes(user_id)

    blocks = []
    created = []  # (summary, time_str)，回覆後才推播，避免拖慢 reply
    for details in details_list:
        # 提醒設定：優先使用模型從訊息提取的值，若無則使用使用者預設設定
        reminder_minutes = details.reminder_minutes
        if reminder_minutes is None:
            reminder_minutes = default_reminder

        try:
            event = await calendar.create_event(credentials, details, line_user_id=user_id, reminder_minutes=reminder_minutes)
        except Exception:
            if len(details_list) == 1:
                raise
            logger.exception("Create event failed: %s", details.summary)
            blocks.append(i18n.EVENT_CREATE_FAILED.format(summary=details.summary or "(無標題)"))
            continue

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
        blocks.append(msg)
        created.append((event.get("summary", ""), time_str))

    reply_msg = _with_assumption_note("\n\n".join(blocks), intent)
    await line_messaging.reply_text(reply_token, reply_msg)

    # 通知其他已登記的用戶（共用日曆場景）
    for summary, time_str in created:
        await calendar_notify.notify_others("create", user_id, summary, time_str)

    return reply_msg


async def _handle_query(
    reply_token: str,
    intent: CalendarIntent,
    credentials,
) -> str:
    time_range = intent.time_range
    if time_range is None:
        now = datetime.now(timezone.utc)
        time_range = TimeRange(start=now, end=now + timedelta(days=7))

    events = await calendar.query_events(
        credentials, time_range, keyword=intent.search_keyword
    )

    if not events:
        reply_msg = _with_assumption_note(i18n.NO_EVENTS_FOUND, intent)
        await line_messaging.reply_text(reply_token, reply_msg)
        return reply_msg

    msg = (i18n.EVENTS_LIST_HEADER + _format_event_list(events)).strip()
    reply_msg = _with_assumption_note(msg, intent)
    await line_messaging.reply_text(reply_token, reply_msg)
    return reply_msg


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
        reply_msg = _with_assumption_note(i18n.NO_EVENTS_FOUND, intent)
        await line_messaging.reply_text(reply_token, reply_msg)
        return reply_msg

    if len(events) == 1:
        return await _apply_update(user_id, reply_token, events[0], intent, credentials)
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
        reply_msg = _with_assumption_note(i18n.NO_EVENTS_FOUND, intent)
        await line_messaging.reply_text(reply_token, reply_msg)
        return reply_msg

    if len(events) == 1:
        return await _apply_delete(user_id, reply_token, events[0], intent, credentials)
    await _save_selection_state(user_id, "select_event_for_delete", events, intent)
    return await _reply_selection(reply_token, events)


async def _apply_update(
    user_id: str, reply_token: str, event: dict, intent: CalendarIntent, credentials
) -> str:
    """修改單一行程：二次解析精算更新值（失敗時退回第一階段結果）→ 更新 → 回覆 → 通知。"""
    update_details = None
    if intent.original_message:
        update_details = await nlp.parse_update_details(intent.original_message, event, user_id=user_id)
    details_to_use = update_details or intent.event_details
    updated = await calendar.update_event(credentials, event["id"], details_to_use, line_user_id=user_id)
    time_str = _get_event_time_str(updated)
    msg = i18n.EVENT_UPDATED.format(summary=updated.get("summary", ""), time=time_str)
    reply_msg = _with_assumption_note(msg, intent)
    await line_messaging.reply_text(reply_token, reply_msg)
    await calendar_notify.notify_others("update", user_id, updated.get("summary", ""), time_str)
    return reply_msg


async def _apply_delete(
    user_id: str, reply_token: str, event: dict, intent: CalendarIntent, credentials
) -> str:
    summary = event.get("summary", "(無標題)")
    await calendar.delete_event(credentials, event["id"], line_user_id=user_id)
    reply_msg = _with_assumption_note(i18n.EVENT_DELETED.format(summary=summary), intent)
    await line_messaging.reply_text(reply_token, reply_msg)
    await calendar_notify.notify_others("delete", user_id, summary)
    return reply_msg


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

    apply = _apply_update if user_state.action == "select_event_for_update" else _apply_delete
    try:
        intent = CalendarIntent.model_validate(user_state.original_intent)
        return await apply(user_id, reply_token, selected, intent, credentials)
    except Exception:
        logger.exception("Selection action failed")
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_ERROR)
        return i18n.CALENDAR_ERROR


# ── Helpers ──


async def _find_matching_events(intent: CalendarIntent, credentials) -> list[dict]:
    time_range = intent.time_range
    if time_range is None and intent.search_keyword:
        now = datetime.now(timezone.utc)
        time_range = TimeRange(start=now - timedelta(days=7), end=now + timedelta(days=7))
    if time_range is None:
        return []
    return await calendar.query_events(
        credentials, time_range, keyword=intent.search_keyword
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
    msg = (i18n.MULTIPLE_EVENTS_FOUND + _format_event_list(events) + i18n.SELECT_PROMPT).strip()
    await line_messaging.reply_text(reply_token, msg)
    return msg


def _format_event_list(events: list[dict]) -> str:
    return "".join(
        i18n.EVENT_LIST_ITEM.format(
            index=idx, summary=e.get("summary", "(無標題)"), time=_get_event_time_str(e)
        )
        for idx, e in enumerate(events, 1)
    )


def _get_event_time_str(event: dict) -> str:
    return format_event_time(event_time(event, "start"), event_time(event, "end"))


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
        reply_msg = _with_assumption_note(i18n.REMINDER_EVENT_NOT_FOUND, intent)
        await line_messaging.reply_text(reply_token, reply_msg)
        return reply_msg

    if len(events) > 1:
        await _save_selection_state(user_id, "select_event_for_update", events, intent)
        return await _reply_selection(reply_token, events)

    event = events[0]
    event_id = event["id"]

    try:
        start_time = datetime.fromisoformat(event_time(event, "start"))
    except (ValueError, TypeError):
        await line_messaging.reply_text(reply_token, i18n.CALENDAR_ERROR)
        return i18n.CALENDAR_ERROR

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
        await store.create_reminder(
            user_id, event_id, event.get("summary", ""), start_time, reminder_minutes
        )
        msg = i18n.REMINDER_SET.format(minutes=reminder_minutes)

    reply_msg = _with_assumption_note(msg, intent)
    await line_messaging.reply_text(reply_token, reply_msg)
    return reply_msg
