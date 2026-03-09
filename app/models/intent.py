"""意圖模型模組：定義 Claude NLP 解析結果的資料結構。

設計理由
--------
使用 Pydantic BaseModel 作為資料容器，原因：
1. 自動驗證 Claude 回傳的 JSON，欄位型別不符時拋出清楚的錯誤
2. model_validate() 支援從 dict 直接建構，與 json.loads() 搭配無縫
3. 所有欄位預設為 None，容忍 Claude 省略非必要欄位
4. model_dump(mode="json") 可將 datetime 序列化為字串，存入 Firestore 時使用

意圖流程
--------
使用者訊息 → Claude API → JSON 字串 → json.loads() → CalendarIntent.model_validate()
→ handlers/message.py 依 action 分派至對應處理函式
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """使用者意圖的操作類型。

    繼承 str 的設計理由：
    - 讓 Enum 值可直接當 str 使用（如字串比對、JSON 序列化），無需額外轉換
    - Claude 輸出 "create"、"query" 等小寫字串，與 Enum 值直接對應
    """
    CREATE = "create"          # 新增行程
    QUERY = "query"            # 查詢行程
    UPDATE = "update"          # 修改行程
    DELETE = "delete"          # 刪除行程
    SET_REMINDER = "set_reminder"  # 對已有行程設定提醒
    UNKNOWN = "unknown"        # 無法識別的意圖（confidence 通常 < 0.5）


class EventDetails(BaseModel):
    """行程的詳細資訊，用於新增/修改操作。

    所有欄位皆為選填，原因：
    - 修改操作只需填寫要更新的欄位，未填的欄位保持原值
    - Claude 會根據使用者訊息推斷，不確定的欄位直接省略（回傳 null）
    """
    summary: str | None = None           # 行程名稱（標題）
    start_time: datetime | None = None   # 開始時間（ISO8601，含時區）
    end_time: datetime | None = None     # 結束時間；create 時若未指定，預設開始後 1 小時
    location: str | None = None          # 地點（可選）
    description: str | None = None       # 備註說明（可選）
    all_day: bool = False                # 是否為全天事件；True 時使用 date 格式而非 dateTime
    reminder_minutes: int | None = None  # 提前幾分鐘提醒；None 表示使用使用者預設設定


class TimeRange(BaseModel):
    """時間範圍，用於查詢/修改/刪除操作指定搜尋區間。

    設計理由：
    - 從 EventDetails 獨立出來，語意更清晰（這是搜尋條件，不是行程本身的時間）
    - 查詢「今天的行程」→ start=今天 00:00, end=今天 23:59
    - update/delete 若未指定時間範圍，Claude 預設搜尋前後各一週
    """
    start: datetime  # 查詢起始時間（含）
    end: datetime    # 查詢結束時間（含）


class CalendarIntent(BaseModel):
    """Claude NLP 解析的完整意圖物件，作為 handlers/message.py 的決策輸入。

    欄位選填策略：
    - create：需要 event_details（含 summary + start_time）
    - query：需要 time_range
    - update/delete：需要 time_range 或 search_keyword，event_details 放更新值
    - set_reminder：需要 time_range 或 search_keyword，event_details.reminder_minutes 必填

    confidence 的使用方式：
    - >= 0.5：直接執行操作
    - < 0.5：向使用者要求澄清，不執行操作（避免誤操作）
    - Claude 在推定不明確資訊後會設 0.7+，並在 clarification_needed 說明推定內容
    """
    action: ActionType                      # 操作類型，決定後續分派邏輯
    event_details: EventDetails | None = None  # 行程細節（create/update 用）
    time_range: TimeRange | None = None        # 搜尋時間範圍（query/update/delete 用）
    search_keyword: str | None = Field(
        default=None, description="用於查詢/修改/刪除時的關鍵字"
    )                                          # 關鍵字搜尋（補充 time_range 或單獨使用）
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)  # Claude 解析信心分數（0~1）
    clarification_needed: str | None = Field(
        default=None, description="對使用者的補充說明（推定內容、建議確認事項等）"
    )                                          # 推定說明或需確認事項，顯示在回覆訊息末尾
    original_message: str | None = None        # 保留原始訊息，供 update 二次精細解析用
