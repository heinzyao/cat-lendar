from __future__ import annotations

import json
import logging

import anthropic

from app.config import settings
from app.models.intent import CalendarIntent, EventDetails
from app.models.user import ConversationMessage
from app.utils.datetime_utils import now_local, weekday_name

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def _build_system_prompt(has_history: bool = False) -> str:
    now = now_local()
    history_note = ""
    if has_history:
        history_note = "\n\n注意：對話歷史已提供在先前的 messages 中。請參考對話上下文來理解代名詞（如「它」「那個」）、省略（如「改到明天」指的是前面提到的行程）、以及後續補充資訊（如追加地點、修改時間）。"
    return f"""你是一個 Google 日曆助手，負責解析使用者的自然語言指令並轉換為結構化操作。

目前時間：{now:%Y-%m-%d %H:%M} 星期{weekday_name(now)}
時區：{settings.timezone}

請將使用者的訊息解析為以下 JSON 格式，不要輸出其他文字：
{{
  "action": "create" | "query" | "update" | "delete" | "set_reminder" | "unknown",
  "event_details": {{
    "summary": "行程名稱",
    "start_time": "ISO8601 datetime",
    "end_time": "ISO8601 datetime",
    "location": "地點（可選）",
    "description": "描述（可選）",
    "all_day": false,
    "reminder_minutes": 15
  }},
  "time_range": {{
    "start": "ISO8601 datetime",
    "end": "ISO8601 datetime"
  }},
  "search_keyword": "搜尋關鍵字（修改/刪除/設定提醒時用）",
  "confidence": 0.0-1.0,
  "clarification_needed": "需要使用者補充的資訊（可選）"
}}

規則：
1. create: event_details 必填 summary 和 start_time。若未指定 end_time，預設 1 小時後。若有提及提前提醒，設定 reminder_minutes。
2. query: time_range 必填。「今天」=今天 00:00~23:59，「這週」=本週一~週日，「明天」=明天整天。
3. update: search_keyword 或 time_range 用來找到要修改的行程，event_details 放新的值。
4. delete: search_keyword 或 time_range 用來找到要刪除的行程。
5. set_reminder: 對已有行程設定提醒。用 search_keyword 或 time_range 找到行程，event_details.reminder_minutes 放提前分鐘數。
6. 若資訊不足以執行操作，設 confidence < 0.5 並在 clarification_needed 說明。
7. 只輸出 JSON，不要有其他文字。欄位為 null 時可省略。
8. reminder_minutes 範例：「提前 15 分鐘提醒」→ 15，「提前 1 小時提醒」→ 60，「半小時前提醒」→ 30。{history_note}"""


async def parse_intent(
    user_message: str,
    conversation_history: list[ConversationMessage] | None = None,
) -> CalendarIntent:
    client = _get_client()

    # 組裝 multi-turn messages
    messages: list[dict[str, str]] = []
    if conversation_history:
        for msg in conversation_history:
            messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": user_message})

    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=1024,
        system=_build_system_prompt(has_history=bool(conversation_history)),
        messages=messages,
    )

    raw = response.content[0].text.strip()
    # 去掉可能的 markdown code fence
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Claude 回傳非 JSON: %s", raw)
        return CalendarIntent(
            action="unknown",
            confidence=0.0,
            clarification_needed="無法解析指令",
        )

    intent = CalendarIntent.model_validate(data)
    return intent.model_copy(update={"original_message": user_message})


def _format_event_for_prompt(event: dict) -> str:
    """將 Calendar 格式的 event 轉成 prompt 可讀文字"""
    from datetime import datetime

    from app.utils.datetime_utils import local_tz

    tz = local_tz()
    summary = event.get("summary", "(無標題)")

    start_raw = event.get("start", {})
    end_raw = event.get("end", {})
    start_str = start_raw.get("dateTime", start_raw.get("date", ""))
    end_str = end_raw.get("dateTime", end_raw.get("date", ""))

    try:
        start_dt = datetime.fromisoformat(start_str).astimezone(tz)
        end_dt = datetime.fromisoformat(end_str).astimezone(tz)
        duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
        start_fmt = start_dt.strftime("%Y-%m-%d %H:%M")
        end_fmt = end_dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        start_fmt = start_str
        end_fmt = end_str
        duration_minutes = 0

    lines = [
        f"名稱：{summary}",
        f"開始：{start_fmt}",
        f"結束：{end_fmt}",
        f"持續：{duration_minutes} 分鐘",
    ]
    if event.get("location"):
        lines.append(f"地點：{event['location']}")
    if event.get("description"):
        lines.append(f"描述：{event['description']}")
    return "\n".join(lines)


async def parse_update_details(
    user_message: str, original_event: dict
) -> EventDetails | None:
    """第二階段解析：結合原事件資訊，精確計算需更新的欄位"""
    if not user_message:
        return None

    client = _get_client()
    now = now_local()
    event_info = _format_event_for_prompt(original_event)

    system_prompt = f"""你是一個日曆助手，負責解析使用者想如何修改一個已知的行程。

目前時間：{now:%Y-%m-%d %H:%M} 星期{weekday_name(now)}
時區：{settings.timezone}

原始行程：
{event_info}

請根據使用者的指令，只輸出需要更新的欄位（JSON 格式），不變的欄位省略：
{{
  "summary": "新名稱（可選）",
  "start_time": "ISO8601 datetime（可選）",
  "end_time": "ISO8601 datetime（可選）",
  "location": "地點（可選）",
  "description": "描述（可選）",
  "all_day": false
}}

修改規則：
1. 「改到明天/後天/週五」→ 保持原持續時間，只移動日期，時間不變
2. 「改到下午 N 點 / N:00」→ 開始改為 N:00，結束 = 開始 + 原持續時間
3. 「延後/提前 N 小時/分鐘」→ 開始和結束各平移相同時間
4. 「改成 N 小時/分鐘」→ 結束 = 原開始 + N 小時/分鐘（開始不變）
5. 若只改名稱/地點/描述，時間欄位省略
6. 只輸出 JSON，不要有其他文字。欄位為 null 時可省略。"""

    try:
        response = await client.messages.create(
            model=settings.claude_model,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception:
        logger.warning("parse_update_details API call failed", exc_info=True)
        return None

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("parse_update_details 回傳非 JSON: %s", raw)
        return None

    try:
        return EventDetails.model_validate(data)
    except Exception:
        logger.warning("parse_update_details EventDetails 驗證失敗: %s", data)
        return None
