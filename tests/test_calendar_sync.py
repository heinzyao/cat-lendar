"""calendar_sync.run_sync：分頁、token 過期重設、取消／改時間的處理。"""

import os

os.environ.setdefault("LINE_CHANNEL_SECRET", "x")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "x")
os.environ.setdefault("GEMINI_API_KEY", "x")
os.environ.setdefault("GOOGLE_CALENDAR_ID", "cal")

from unittest.mock import AsyncMock, MagicMock, patch

from googleapiclient.errors import HttpError

from app.services import calendar_sync


def _service(pages):
    """events().list(**params) 依序回傳 pages；記下每次的 params。"""
    service = MagicMock()
    calls = []

    def list_(**params):
        calls.append(params)
        return pages[len(calls) - 1]

    service.events.return_value.list.side_effect = list_
    return service, calls


async def _run(service, sync_token="tok"):
    store = MagicMock(
        get_sync_token=AsyncMock(return_value=sync_token),
        save_sync_token=AsyncMock(),
        delete_reminders_by_event_id=AsyncMock(return_value=1),
        update_reminders_time_by_event_id=AsyncMock(return_value=1),
    )

    async def execute(page):
        if isinstance(page, Exception):
            raise page
        return page

    with (
        patch.object(calendar_sync, "store", store),
        patch.object(calendar_sync, "_execute", execute),
        patch.object(calendar_sync, "_get_service", return_value=service),
        patch.object(calendar_sync.auth, "get_shared_credentials"),
    ):
        return await calendar_sync.run_sync(), store


async def test_pages_through_changes_and_saves_new_token():
    service, calls = _service([
        {"items": [{"id": "a", "status": "cancelled"}], "nextPageToken": "p2"},
        {"items": [{"id": "b", "start": {"dateTime": "2026-10-10T10:00:00+08:00"}}],
         "nextSyncToken": "new"},
    ])
    stats, store = await _run(service)

    assert stats == {"deleted": 1, "updated": 1, "token_reset": False}
    assert calls[0]["syncToken"] == "tok"
    assert "syncToken" not in calls[1] and calls[1]["pageToken"] == "p2"
    store.save_sync_token.assert_awaited_once_with("new")


async def test_expired_token_resets_without_processing_events():
    expired = HttpError(MagicMock(status=410), b"gone")
    service, calls = _service([expired, {"items": [{"id": "x"}], "nextSyncToken": "fresh"}])
    stats, store = await _run(service)

    assert stats["token_reset"] is True
    assert "syncToken" not in calls[1]
    store.save_sync_token.assert_awaited_once_with("fresh")
    store.update_reminders_time_by_event_id.assert_not_awaited()
