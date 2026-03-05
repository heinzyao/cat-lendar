"""notification.py 及 Firestore reminder 相關單元測試"""
import os
import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "https://example.com/oauth/callback")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.services.notification import check_and_send_reminders, format_reminder_message

_TZ = timezone(timedelta(hours=8))
_USER = "U_test_user"


# ── format_reminder_message ──


def test_format_reminder_message_basic():
    start = datetime(2024, 3, 15, 14, 0, tzinfo=_TZ)
    msg = format_reminder_message("開會", start, 15)
    assert "開會" in msg
    assert "15" in msg
    assert "14:00" in msg


def test_format_reminder_message_60_minutes():
    start = datetime(2024, 3, 15, 9, 0, tzinfo=_TZ)
    msg = format_reminder_message("客戶簡報", start, 60)
    assert "客戶簡報" in msg
    assert "60" in msg


# ── check_and_send_reminders ──


async def test_check_and_send_reminders_sends_due():
    now = datetime.now(timezone.utc)
    reminder = {
        "id": "rem1",
        "line_user_id": _USER,
        "event_summary": "午餐",
        "start_time": now + timedelta(minutes=10),
        "reminder_at": now - timedelta(minutes=1),
        "reminder_minutes": 15,
        "sent": False,
        "calendar_mode": "local",
    }

    with (
        patch("app.services.notification.store") as mock_store,
        patch("app.services.notification.line_messaging") as mock_line,
    ):
        mock_store.get_due_reminders = AsyncMock(return_value=[reminder])
        mock_store.mark_reminder_sent = AsyncMock()
        mock_line.push_text = AsyncMock()

        sent = await check_and_send_reminders()

    assert sent == 1
    assert mock_line.push_text.await_count == 1
    call_args = mock_line.push_text.call_args
    assert call_args[0][0] == _USER
    assert "午餐" in call_args[0][1]
    mock_store.mark_reminder_sent.assert_awaited_once_with("rem1")


async def test_check_and_send_reminders_no_due():
    with (
        patch("app.services.notification.store") as mock_store,
        patch("app.services.notification.line_messaging") as mock_line,
    ):
        mock_store.get_due_reminders = AsyncMock(return_value=[])
        mock_line.push_text = AsyncMock()

        sent = await check_and_send_reminders()

    assert sent == 0
    mock_line.push_text.assert_not_awaited()


async def test_check_and_send_reminders_handles_push_error():
    now = datetime.now(timezone.utc)
    reminder = {
        "id": "rem2",
        "line_user_id": _USER,
        "event_summary": "失敗測試",
        "start_time": now + timedelta(minutes=5),
        "reminder_at": now - timedelta(seconds=30),
        "reminder_minutes": 10,
        "sent": False,
        "calendar_mode": "local",
    }

    with (
        patch("app.services.notification.store") as mock_store,
        patch("app.services.notification.line_messaging") as mock_line,
    ):
        mock_store.get_due_reminders = AsyncMock(return_value=[reminder])
        mock_store.mark_reminder_sent = AsyncMock()
        mock_line.push_text = AsyncMock(side_effect=Exception("LINE API 錯誤"))

        sent = await check_and_send_reminders()

    # 發送失敗應不計入 sent，且不 raise
    assert sent == 0
    mock_store.mark_reminder_sent.assert_not_awaited()


async def test_check_and_send_reminders_multiple():
    now = datetime.now(timezone.utc)
    reminders = [
        {
            "id": f"rem{i}",
            "line_user_id": _USER,
            "event_summary": f"行程{i}",
            "start_time": now + timedelta(minutes=10),
            "reminder_at": now - timedelta(minutes=1),
            "reminder_minutes": 15,
            "sent": False,
            "calendar_mode": "local",
        }
        for i in range(3)
    ]

    with (
        patch("app.services.notification.store") as mock_store,
        patch("app.services.notification.line_messaging") as mock_line,
    ):
        mock_store.get_due_reminders = AsyncMock(return_value=reminders)
        mock_store.mark_reminder_sent = AsyncMock()
        mock_line.push_text = AsyncMock()

        sent = await check_and_send_reminders()

    assert sent == 3
    assert mock_line.push_text.await_count == 3
    assert mock_store.mark_reminder_sent.await_count == 3


# ── /internal/notify endpoint ──


async def test_internal_notify_endpoint_forbidden():
    import httpx
    from httpx import AsyncClient, ASGITransport

    os.environ["NOTIFY_SECRET"] = "test-secret-xyz"

    # 重新載入 settings（因為已設定環境變數）
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/internal/notify", headers={"X-Internal-Secret": "wrong"})
    assert resp.status_code == 403


async def test_internal_notify_endpoint_success():
    from httpx import AsyncClient, ASGITransport

    os.environ["NOTIFY_SECRET"] = "test-secret-xyz"

    from app.main import app
    from app.config import settings
    settings.notify_secret = "test-secret-xyz"

    with patch("app.routes.notify.notification.check_and_send_reminders", new=AsyncMock(return_value=2)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/internal/notify",
                headers={"X-Internal-Secret": "test-secret-xyz"},
            )
    assert resp.status_code == 200
    assert resp.json()["sent"] == 2
