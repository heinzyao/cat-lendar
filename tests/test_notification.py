"""notification.py 及 Firestore reminder 相關單元測試"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
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


async def test_check_and_send_reminders_skips_started_event():
    """事件已開始的提醒不推播，只標記 sent（避免 sync 重設後大量補推過期提醒）"""
    now = datetime.now(timezone.utc)
    reminder = {
        "id": "stale",
        "line_user_id": _USER,
        "event_summary": "昨天的會",
        "start_time": now - timedelta(days=1),
        "reminder_at": now - timedelta(days=1, minutes=15),
        "reminder_minutes": 15,
        "sent": False,
    }

    with (
        patch("app.services.notification.store") as mock_store,
        patch("app.services.notification.line_messaging") as mock_line,
    ):
        mock_store.get_due_reminders = AsyncMock(return_value=[reminder])
        mock_store.mark_reminder_sent = AsyncMock()
        mock_line.push_text = AsyncMock()

        sent = await check_and_send_reminders()

    assert sent == 0
    mock_line.push_text.assert_not_awaited()
    mock_store.mark_reminder_sent.assert_awaited_once_with("stale")


# ── update_reminders_time_by_event_id ──


def _fake_reminder_doc(data: dict):
    doc = MagicMock()
    doc.to_dict.return_value = data
    doc.reference.update = AsyncMock()
    return doc


async def test_sync_update_keeps_sent_when_start_unchanged():
    from app.store import firestore as store

    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    doc = _fake_reminder_doc({"start_time": start, "reminder_minutes": 15, "sent": True})
    db = MagicMock()
    db.collection.return_value.where.return_value.get = AsyncMock(return_value=[doc])

    with patch.object(store, "get_db", return_value=db):
        updated = await store.update_reminders_time_by_event_id("ev1", start)

    assert updated == 0
    doc.reference.update.assert_not_awaited()


async def test_sync_update_resets_sent_when_start_changed():
    from app.store import firestore as store

    old = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    new = old + timedelta(hours=2)
    doc = _fake_reminder_doc({"start_time": old, "reminder_minutes": 15, "sent": True})
    db = MagicMock()
    db.collection.return_value.where.return_value.get = AsyncMock(return_value=[doc])

    with patch.object(store, "get_db", return_value=db):
        updated = await store.update_reminders_time_by_event_id("ev1", new)

    assert updated == 1
    doc.reference.update.assert_awaited_once_with({
        "start_time": new,
        "reminder_at": new - timedelta(minutes=15),
        "sent": False,
    })


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
