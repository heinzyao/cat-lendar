"""時區感知的時間工具（本地時區 settings.timezone）與 Google Calendar 時間格式轉換。"""

from __future__ import annotations

from datetime import datetime

from zoneinfo import ZoneInfo

from app.config import settings

# 模組級別的時區物件：避免每次操作都重新讀取時區資料庫
_tz = ZoneInfo(settings.timezone)


def local_tz() -> ZoneInfo:
    """取得設定的本地時區（供其他模組使用）。"""
    return _tz


def now_local() -> datetime:
    """取得本地時區的當前時間（含時區資訊）。"""
    return datetime.now(_tz)


def today_start() -> datetime:
    """今日 00:00:00（本地時區）。"""
    return now_local().replace(hour=0, minute=0, second=0, microsecond=0)


def to_rfc3339(dt: datetime) -> str:
    """將 datetime 轉換為 Google Calendar API 要求的 RFC3339 格式。

    RFC3339 = ISO8601 + 明確的時區資訊（如 +08:00 或 Z）
    設計理由：Google Calendar API 拒絕無時區的 naive datetime
    若傳入 naive datetime（tzinfo=None），自動套用本地時區
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz)  # naive datetime → 補上本地時區
    return dt.isoformat()


def to_date_str(dt: datetime) -> str:
    """將 datetime 轉換為 YYYY-MM-DD 格式（全天事件的日期欄位）。

    Google Calendar 全天事件使用 date 而非 dateTime 格式：
    {"start": {"date": "2026-03-08"}} 而非 {"start": {"dateTime": "2026-03-08T..."}}
    """
    return dt.strftime("%Y-%m-%d")


def event_time(event: dict, key: str) -> str:
    """Google Calendar event 的 start/end 字串（定時事件取 dateTime，全天事件取 date）。"""
    when = event.get(key, {})
    return when.get("dateTime", when.get("date", ""))


def format_event_time(start: str, end: str) -> str:
    """格式化 Google Calendar 行程時間，供 LINE 訊息顯示使用。

    處理兩種 Google Calendar 時間格式：
    - dateTime（定時事件）：ISO8601 字串，如 "2026-03-08T14:00:00+08:00"
    - date（全天事件）：純日期字串，如 "2026-03-08"

    顯示策略：
    - 同天行程：2026/03/08(Sun) 14:00–15:00（省略結束日期）
    - 跨天行程：2026/03/08(Sun) 22:00 – 2026/03/09(Mon) 02:00
    - 全天/解析失敗：2026-03-08 – 2026-03-09
    """
    # Google Calendar API 回傳 dateTime 或 date 格式，需要兩種處理
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        s_local = s.astimezone(_tz)  # 轉換為本地時區顯示
        e_local = e.astimezone(_tz)

        if s_local.date() == e_local.date():
            # 同一天：省略結束日期，格式更簡潔
            return f"{s_local:%Y/%m/%d(%a) %H:%M}–{e_local:%H:%M}"
        # 跨天：兩端都顯示完整日期
        return f"{s_local:%Y/%m/%d(%a) %H:%M} – {e_local:%Y/%m/%d(%a) %H:%M}"
    except (ValueError, TypeError):
        # 全天事件或格式不符：直接顯示原始字串
        return f"{start} – {end}" if start != end else start


def weekday_name(dt: datetime) -> str:
    """取得中文星期名稱（一～日），用於 system prompt 與使用者回覆。

    例：dt.weekday() == 0 → 「一」（週一）
    """
    names = ["一", "二", "三", "四", "五", "六", "日"]
    return names[dt.weekday()]
