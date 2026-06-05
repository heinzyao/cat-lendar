"""NLP 服務模組：使用 Gemini API 將自然語言訊息解析為結構化日曆操作意圖。

設計理由
--------
為什麼選用 Gemini 而非規則解析？
- 自然語言日程輸入極為多變：「後天下午開會」、「把三點的會議推到四點」、
  「下週四午餐約會改成週五」等，規則窮舉不切實際
- Gemini 支援 multi-turn 對話，可利用前幾輪的脈絡理解代名詞與省略
- 直接輸出 JSON，省去 NLP → structured data 的中間層

Prompt 設計策略
---------------
1. 系統 Prompt 注入當前時間與時區：讓 Gemini 能正確推算「明天」「這週」等相對時間
2. 嚴格要求只輸出 JSON：避免 Gemini 在 JSON 前後加說明文字（雖有 markdown fence 處理）
3. 推定規則：要求 Gemini 盡量推定不明確的資訊，僅在真正無法判斷時才要求澄清，
   以降低使用者操作成本
4. 二階段解析：update 操作先用 parse_intent() 定位行程，再用 parse_update_details()
   結合原始行程資料精確計算時間差異（如「延後 30 分鐘」需知道原始時間）

Singleton Client 設計：
genai.configure() 全局只需呼叫一次；GenerativeModel 依 system_instruction 動態建立
（system_prompt 含即時時間，無法預先固定）
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict

import google.generativeai as genai
from google.generativeai.types import HarmBlockThreshold, HarmCategory

from app.config import settings
from app.models.intent import CalendarIntent, EventDetails
from app.models.user import ConversationMessage
from app.utils.datetime_utils import now_local, weekday_name

logger = logging.getLogger(__name__)

# genai.configure() 全局只需呼叫一次
_genai_configured: bool = False

# 停用安全過濾：日曆指令不含有害內容，過濾會造成誤封
_SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}


def _ensure_configured() -> None:
    """確保 genai 已用 API key 初始化（只執行一次）。"""
    global _genai_configured
    if not _genai_configured:
        genai.configure(api_key=settings.gemini_api_key)
        _genai_configured = True


def _get_model(system_prompt: str) -> genai.GenerativeModel:
    """建立含有 system_instruction 的 GenerativeModel 實例。

    Gemini 的 system_instruction 在 model 層設定，而非 messages 層，
    因此每次 system_prompt 不同（含即時時間）都需建立新實例。
    response_mime_type="application/json" 在協議層強制 JSON 輸出，
    避免 Gemini 自行決定改用對話格式回應。
    """
    _ensure_configured()
    return genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=system_prompt,
        safety_settings=_SAFETY_SETTINGS,
        generation_config={"response_mime_type": "application/json"},
    )


# --- Per-user Rate Limiter ---
# 設計理由：防止惡意或異常的連續訊息消耗大量 Gemini API 費用。
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
    - 注入當前時間：Gemini 無法自行取得當前時間，必須由我們傳入才能正確處理「明天」等相對時間
    - has_history：有對話記憶時追加 history_note，提醒 Gemini 善用上下文
      （無記憶時省略，避免讓 Gemini 誤以為有記憶卻找不到）
    - 動態建構而非靜態常數：因需嵌入每次呼叫時的即時時間，無法預先建構
    """
    now = now_local()
    history_note = ""
    if has_history:
        # 當有對話記憶時，提醒 Gemini 利用先前對話理解代名詞與省略
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
    """呼叫 Gemini API 解析使用者訊息，回傳結構化的 CalendarIntent。

    Multi-turn 設計：
    - conversation_history 以 Gemini chat history 形式傳入，讓模型知道前幾輪對話內容
    - Gemini 角色名稱：user / model（Anthropic 為 user / assistant）
    - 當前訊息透過 chat.send_message_async() 或 generate_content_async() 傳入

    錯誤處理策略：
    - Gemini 有時會在 JSON 前後加入 markdown code fence（```json ... ```），
      需手動剝除，否則 json.loads() 會失敗
    - JSON 解析失敗時回傳 action=unknown + confidence=0，觸發上層的澄清詢問流程
    - 不直接 raise 例外，確保每個使用者訊息都有合理的回應
    """
    if user_id:
        _check_rate_limit(user_id)

    system_prompt = _build_system_prompt(has_history=bool(conversation_history))
    model = _get_model(system_prompt)

    # 組裝 multi-turn history：Gemini 角色為 "user" / "model"
    history: list[dict] = []
    if conversation_history:
        for msg in conversation_history:
            role = "model" if msg.role == "assistant" else "user"
            history.append({"role": role, "parts": msg.content})

    if history:
        chat = model.start_chat(history=history)
        response = await chat.send_message_async(user_message)
    else:
        response = await model.generate_content_async(user_message)

    raw = response.text.strip()
    # 去掉可能的 markdown code fence（```json ... ``` 或 ``` ... ```）
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Gemini 回傳非 JSON: %s", raw)
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
    user_message: str, original_event: dict, user_id: str = ""
) -> EventDetails | None:
    """第二階段解析（Update 操作專用）：結合原事件資訊，精確計算需更新的欄位。

    設計理由——為何需要二階段？
    第一階段 parse_intent() 只看使用者訊息，不知道原行程的時間細節，
    所以無法處理「延後 30 分鐘」（需知道原始時間才能算出新時間）。
    第二階段取得原行程後，將事件細節注入 Prompt，讓 Gemini 直接計算正確的時間值。

    輸入 original_event：Google Calendar API 回傳的 dict 格式
    輸出 EventDetails：只含需要更新的欄位（未變動的欄位為 None）
    """
    if not user_message:
        return None

    if user_id:
        _check_rate_limit(user_id)

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

    model = _get_model(system_prompt)

    try:
        response = await model.generate_content_async(user_message)
    except Exception:
        logger.warning("parse_update_details API call failed", exc_info=True)
        return None

    raw = response.text.strip()
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
