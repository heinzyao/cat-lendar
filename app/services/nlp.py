"""NLP 服務模組：使用 Claude API 將自然語言訊息解析為結構化日曆操作意圖。

設計理由
--------
為什麼選用 Claude 而非規則解析？
- 自然語言日程輸入極為多變：「後天下午開會」、「把三點的會議推到四點」、
  「下週四午餐約會改成週五」等，規則窮舉不切實際
- Claude 支援 multi-turn 對話，可利用前幾輪的脈絡理解代名詞與省略
- 直接輸出 JSON，省去 NLP → structured data 的中間層

Prompt 設計策略
---------------
1. 系統 Prompt 注入當前時間與時區：讓 Claude 能正確推算「明天」「這週」等相對時間
2. 嚴格要求只輸出 JSON：避免 Claude 在 JSON 前後加說明文字（雖有 markdown fence 處理）
3. 推定規則：要求 Claude 盡量推定不明確的資訊，僅在真正無法判斷時才要求澄清，
   以降低使用者操作成本
4. 二階段解析：update 操作先用 parse_intent() 定位行程，再用 parse_update_details()
   結合原始行程資料精確計算時間差異（如「延後 30 分鐘」需知道原始時間）

Singleton Client 設計：
_client 全局唯一，避免每次請求都建立新的 HTTP 連線（AsyncAnthropic 內部維護連線池）
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict

import anthropic

from app.config import settings
from app.models.intent import CalendarIntent, EventDetails
from app.models.user import ConversationMessage
from app.utils.datetime_utils import now_local, weekday_name

logger = logging.getLogger(__name__)

# Singleton 模式：延遲初始化，首次呼叫時才建立客戶端
# 設計理由：避免在模組載入時就連線（減少啟動時間，也避免啟動時憑證未就緒的問題）
_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    """取得或建立 Anthropic 非同步客戶端（Singleton 模式）。"""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


# --- Per-user Rate Limiter ---
# 設計理由：防止惡意或異常的連續訊息消耗大量 Claude API 費用。
# 使用 sliding window 演算法，每位使用者每分鐘最多 10 次呼叫。
# Cloud Run 為 stateless 但單一 instance 可處理多個 concurrent request，
# 此 in-memory limiter 足以防止單一 instance 上的濫用。

_RATE_LIMIT_MAX_CALLS = 10  # 每個 window 最大呼叫次數
_RATE_LIMIT_WINDOW_SECONDS = 60  # 滑動視窗長度

_user_call_timestamps: dict[str, list[float]] = defaultdict(list)


class RateLimitExceeded(Exception):
    """使用者超過 API 呼叫頻率限制。"""
    pass


def _check_rate_limit(user_id: str) -> None:
    """檢查使用者是否超過頻率限制，超過則拋出 RateLimitExceeded。"""
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS

    # 清除過期的 timestamp
    timestamps = _user_call_timestamps[user_id]
    _user_call_timestamps[user_id] = [t for t in timestamps if t > window_start]
    timestamps = _user_call_timestamps[user_id]

    if len(timestamps) >= _RATE_LIMIT_MAX_CALLS:
        logger.warning(
            "Rate limit exceeded for user %s: %d calls in %ds",
            user_id[-4:], len(timestamps), _RATE_LIMIT_WINDOW_SECONDS,
        )
        raise RateLimitExceeded(
            f"已超過使用頻率限制（每分鐘最多 {_RATE_LIMIT_MAX_CALLS} 次），請稍後再試。"
        )

    timestamps.append(now)


def _build_system_prompt(has_history: bool = False) -> str:
    """建構系統 Prompt。

    設計理由：
    - 注入當前時間：Claude 無法自行取得當前時間，必須由我們傳入才能正確處理「明天」等相對時間
    - has_history：有對話記憶時追加 history_note，提醒 Claude 善用上下文
      （無記憶時省略，避免讓 Claude 誤以為有記憶卻找不到）
    - 動態建構而非靜態常數：因需嵌入每次呼叫時的即時時間，無法預先建構
    """
    now = now_local()
    history_note = ""
    if has_history:
        # 當有對話記憶時，提醒 Claude 利用先前對話理解代名詞與省略
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
6. 盡量推定不明確的資訊，避免頻繁詢問使用者：
   - 未指定日期 → 根據時段推定今天或明天（若已過該時段則為明天）
   - 只提時段 → 上午 09:00、下午 14:00、晚上 19:00
   - update/delete 無時間範圍 → 搜尋前後各一週
   - 對話上下文可推斷時直接引用
   推定後在 clarification_needed 簡述推定內容，confidence 設 0.7 以上。
   僅在完全無法判斷意圖時才設 confidence < 0.5。
7. 只輸出 JSON，不要有其他文字。欄位為 null 時可省略。
8. reminder_minutes 範例：「提前 15 分鐘提醒」→ 15，「提前 1 小時提醒」→ 60，「半小時前提醒」→ 30。{history_note}"""


async def parse_intent(
    user_message: str,
    conversation_history: list[ConversationMessage] | None = None,
    user_id: str = "",
) -> CalendarIntent:
    """呼叫 Claude API 解析使用者訊息，回傳結構化的 CalendarIntent。

    Multi-turn 設計：
    - conversation_history 會以 messages 形式傳入，讓 Claude 知道前幾輪的對話內容
    - 當前訊息永遠以 role=user 附加在最後
    - system prompt 根據是否有記憶來調整提示策略

    錯誤處理策略：
    - Claude 有時會在 JSON 前後加入 markdown code fence（```json ... ```），
      需手動剝除，否則 json.loads() 會失敗
    - JSON 解析失敗時回傳 action=unknown + confidence=0，觸發上層的澄清詢問流程
    - 不直接 raise 例外，確保每個使用者訊息都有合理的回應

    original_message 保存策略：
    - 透過 model_copy() 附加，而非在 Prompt 中要求 Claude 回傳
    - 供 update 操作的 parse_update_details() 二次解析使用

    Rate limiting：
    - 每位使用者每分鐘最多 10 次呼叫，超過時拋出 RateLimitExceeded
    """
    if user_id:
        _check_rate_limit(user_id)

    client = _get_client()

    # 組裝 multi-turn messages：先放歷史對話，再加上本輪使用者訊息
    messages: list[dict[str, str]] = []
    if conversation_history:
        for msg in conversation_history:
            messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": user_message})

    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=1024,  # 日曆 JSON 通常 < 300 tokens，1024 已足夠且避免截斷
        system=_build_system_prompt(has_history=bool(conversation_history)),
        messages=messages,
    )

    raw = response.content[0].text.strip()
    # 去掉可能的 markdown code fence（```json ... ``` 或 ``` ... ```）
    # 設計理由：即使 Prompt 要求只輸出 JSON，Claude 有時仍會加入 code fence
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Claude 回傳非 JSON: %s", raw)
        # 回傳 unknown 意圖，讓上層顯示「無法解析」訊息，而非讓系統拋出 500 錯誤
        return CalendarIntent(
            action="unknown",
            confidence=0.0,
            clarification_needed="無法解析指令",
        )

    intent = CalendarIntent.model_validate(data)
    # 保存原始訊息供 update 操作的二次精細解析（parse_update_details）使用
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
    user_message: str, original_event: dict, user_id: str = ""
) -> EventDetails | None:
    """第二階段解析（Update 操作專用）：結合原事件資訊，精確計算需更新的欄位。

    設計理由——為何需要二階段？
    第一階段 parse_intent() 只看使用者訊息，不知道原行程的時間細節，
    所以無法處理「延後 30 分鐘」（需知道原始時間才能算出新時間）。
    第二階段取得原行程後，將事件細節注入 Prompt，讓 Claude 直接計算正確的時間值。

    例如：
      使用者說「把三點的會議延後一小時」
      原行程：start=15:00, end=16:00
      第二階段結果：start=16:00, end=17:00（保持持續時間不變）

    輸入 original_event：Google Calendar API 回傳的 dict 格式
    輸出 EventDetails：只含需要更新的欄位（未變動的欄位為 None）
    """
    if not user_message:
        return None

    if user_id:
        _check_rate_limit(user_id)

    client = _get_client()
    now = now_local()
    event_info = _format_event_for_prompt(original_event)

    # 與第一階段不同：system prompt 包含原始行程資訊，讓 Claude 知道基準時間
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
