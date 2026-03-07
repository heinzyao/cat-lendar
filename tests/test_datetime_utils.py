"""日期時間工具測試"""
import os
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "https://example.com/oauth/callback")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.utils.datetime_utils import (
    to_rfc3339,
    to_date_str,
    format_event_time,
    weekday_name,
)

_TZ = ZoneInfo("Asia/Taipei")


def test_to_rfc3339_with_tz():
    dt = datetime(2024, 3, 15, 14, 30, tzinfo=_TZ)
    result = to_rfc3339(dt)
    assert "2024-03-15" in result
    assert "14:30:00" in result


def test_to_rfc3339_naive_adds_tz():
    dt = datetime(2024, 3, 15, 14, 30)  # naive
    result = to_rfc3339(dt)
    assert "2024-03-15" in result
    assert "+08:00" in result


def test_to_date_str():
    dt = datetime(2024, 3, 15, 9, 0, tzinfo=_TZ)
    assert to_date_str(dt) == "2024-03-15"


def test_format_event_time_same_day():
    result = format_event_time(
        "2024-03-15T14:00:00+08:00",
        "2024-03-15T16:00:00+08:00",
    )
    assert "2024/03/15" in result
    assert "14:00" in result
    assert "16:00" in result


def test_format_event_time_cross_day():
    result = format_event_time(
        "2024-03-15T22:00:00+08:00",
        "2024-03-16T02:00:00+08:00",
    )
    assert "2024/03/15" in result
    assert "2024/03/16" in result


def test_format_event_time_all_day():
    # Python 3.11+ fromisoformat 能解析純日期，會格式化為 00:00 的 datetime
    result = format_event_time("2024-03-15", "2024-03-16")
    # 驗證包含日期資訊（全天事件直接回傳原始字串）
    assert "2024/03/15" in result


@pytest.mark.parametrize("weekday,expected", [
    (0, "一"),
    (1, "二"),
    (2, "三"),
    (3, "四"),
    (4, "五"),
    (5, "六"),
    (6, "日"),
])
def test_weekday_name(weekday, expected):
    # weekday() 0=Monday
    dt = datetime(2024, 3, 11 + weekday, tzinfo=_TZ)  # 2024-03-11 是週一
    assert weekday_name(dt) == expected
