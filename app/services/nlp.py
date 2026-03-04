from __future__ import annotations

import json
import logging

import anthropic

from app.config import settings
from app.models.intent import CalendarIntent
from app.utils.datetime_utils import now_local, weekday_name

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def _build_system_prompt() -> str:
    now = now_local()
    return f"""你是一個 Google 日曆助手，負責解析使用者的自然語言指令並轉換為結構化操作。

目前時間：{now:%Y-%m-%d %H:%M} 星期{weekday_name(now)}
時區：{settings.timezone}

請將使用者的訊息解析為以下 JSON 格式，不要輸出其他文字：
{{
  "action": "create" | "query" | "update" | "delete" | "unknown",
  "event_details": {{
    "summary": "行程名稱",
    "start_time": "ISO8601 datetime",
    "end_time": "ISO8601 datetime",
    "location": "地點（可選）",
    "description": "描述（可選）",
    "all_day": false
  }},
  "time_range": {{
    "start": "ISO8601 datetime",
    "end": "ISO8601 datetime"
  }},
  "search_keyword": "搜尋關鍵字（修改/刪除時用）",
  "confidence": 0.0-1.0,
  "clarification_needed": "需要使用者補充的資訊（可選）"
}}

規則：
1. create: event_details 必填 summary 和 start_time。若未指定 end_time，預設 1 小時後。
2. query: time_range 必填。「今天」=今天 00:00~23:59，「這週」=本週一~週日，「明天」=明天整天。
3. update: search_keyword 或 time_range 用來找到要修改的行程，event_details 放新的值。
4. delete: search_keyword 或 time_range 用來找到要刪除的行程。
5. 若資訊不足以執行操作，設 confidence < 0.5 並在 clarification_needed 說明。
6. 只輸出 JSON，不要有其他文字。欄位為 null 時可省略。"""


async def parse_intent(user_message: str) -> CalendarIntent:
    client = _get_client()

    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=1024,
        system=_build_system_prompt(),
        messages=[{"role": "user", "content": user_message}],
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

    return CalendarIntent.model_validate(data)
