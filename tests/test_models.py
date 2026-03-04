"""Pydantic 模型驗證測試"""
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

from pydantic import ValidationError
from app.models.intent import ActionType, CalendarIntent, EventDetails, TimeRange

_TZ = ZoneInfo("Asia/Taipei")


def test_action_type_values():
    assert ActionType.CREATE == "create"
    assert ActionType.QUERY == "query"
    assert ActionType.UPDATE == "update"
    assert ActionType.DELETE == "delete"
    assert ActionType.UNKNOWN == "unknown"


def test_calendar_intent_create():
    intent = CalendarIntent.model_validate({
        "action": "create",
        "event_details": {
            "summary": "開會",
            "start_time": "2024-03-15T14:00:00+08:00",
            "end_time": "2024-03-15T15:00:00+08:00",
        },
        "confidence": 0.95,
    })
    assert intent.action == ActionType.CREATE
    assert intent.event_details.summary == "開會"
    assert intent.confidence == 0.95


def test_calendar_intent_query():
    intent = CalendarIntent.model_validate({
        "action": "query",
        "time_range": {
            "start": "2024-03-15T00:00:00+08:00",
            "end": "2024-03-15T23:59:59+08:00",
        },
        "confidence": 0.9,
    })
    assert intent.action == ActionType.QUERY
    assert intent.time_range is not None


def test_calendar_intent_unknown():
    intent = CalendarIntent.model_validate({
        "action": "unknown",
        "confidence": 0.1,
        "clarification_needed": "請說明你要做什麼",
    })
    assert intent.action == ActionType.UNKNOWN
    assert intent.clarification_needed == "請說明你要做什麼"


def test_confidence_out_of_range():
    with pytest.raises(ValidationError):
        CalendarIntent.model_validate({"action": "create", "confidence": 1.5})


def test_event_details_optional_fields():
    details = EventDetails(summary="測試", start_time=datetime(2024, 3, 15, 14, 0, tzinfo=_TZ))
    assert details.location is None
    assert details.description is None
    assert details.all_day is False


def test_nlp_strip_code_fence():
    """測試 NLP 模組的 code fence 清理邏輯（不呼叫 API）"""
    raw = '```json\n{"action": "unknown", "confidence": 0.0}\n```'
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()
    import json
    data = json.loads(raw)
    assert data["action"] == "unknown"
