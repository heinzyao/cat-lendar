from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from app.config import settings

_tz = ZoneInfo(settings.timezone)


def local_tz() -> ZoneInfo:
    return _tz


def now_local() -> datetime:
    return datetime.now(_tz)


def today_start() -> datetime:
    return now_local().replace(hour=0, minute=0, second=0, microsecond=0)


def today_end() -> datetime:
    return today_start() + timedelta(days=1)


def to_rfc3339(dt: datetime) -> str:
    """轉成 Google Calendar API 要求的 RFC3339 格式"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz)
    return dt.isoformat()


def to_date_str(dt: datetime) -> str:
    """轉成 YYYY-MM-DD（全天事件用）"""
    return dt.strftime("%Y-%m-%d")


def format_event_time(start: str, end: str) -> str:
    """格式化 Google Calendar event 的時間供使用者閱讀"""
    # Google Calendar API 回傳 dateTime 或 date
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        s_local = s.astimezone(_tz)
        e_local = e.astimezone(_tz)

        if s_local.date() == e_local.date():
            return f"{s_local:%m/%d(%a) %H:%M}–{e_local:%H:%M}"
        return f"{s_local:%m/%d(%a) %H:%M} – {e_local:%m/%d(%a) %H:%M}"
    except (ValueError, TypeError):
        # 全天事件: date 格式
        return f"{start} – {end}" if start != end else start


def weekday_name(dt: datetime) -> str:
    names = ["一", "二", "三", "四", "五", "六", "日"]
    return names[dt.weekday()]
