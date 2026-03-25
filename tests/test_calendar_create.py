import os
import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REFRESH_TOKEN", "test_refresh_token")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.models.intent import EventDetails
from app.services.calendar import create_event

_MOCK_CREDS = MagicMock()


def _build_service_mock():
    service = MagicMock()
    events = service.events.return_value
    insert = events.insert
    insert.return_value.execute.return_value = {"id": "evt001"}
    return service


@pytest.mark.asyncio
async def test_create_event_end_time_defaults_to_one_hour_later():
    start_time = datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc)
    details = EventDetails(
        summary="測試行程",
        start_time=start_time,
        end_time=None,
        all_day=False,
    )
    service = _build_service_mock()

    with (
        patch("app.services.calendar.build", return_value=service),
        patch("app.services.calendar.store") as mock_store,
    ):
        await create_event(_MOCK_CREDS, details)

    body = service.events.return_value.insert.call_args.kwargs["body"]
    assert body["start"]["dateTime"] == start_time.isoformat()
    assert body["end"]["dateTime"] == (start_time + timedelta(hours=1)).isoformat()
    mock_store.create_reminder.assert_not_called()


@pytest.mark.asyncio
async def test_create_event_with_explicit_end_time():
    start_time = datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc)
    end_time = start_time + timedelta(hours=2)
    details = EventDetails(
        summary="明確結束時間",
        start_time=start_time,
        end_time=end_time,
        all_day=False,
    )
    service = _build_service_mock()

    with (
        patch("app.services.calendar.build", return_value=service),
        patch("app.services.calendar.store") as mock_store,
    ):
        await create_event(_MOCK_CREDS, details)

    body = service.events.return_value.insert.call_args.kwargs["body"]
    assert body["start"]["dateTime"] == start_time.isoformat()
    assert body["end"]["dateTime"] == end_time.isoformat()
    mock_store.create_reminder.assert_not_called()


@pytest.mark.asyncio
async def test_create_event_all_day_no_end_time():
    start_time = datetime(2024, 3, 20, 0, 0, tzinfo=timezone.utc)
    details = EventDetails(
        summary="全天事件",
        start_time=start_time,
        end_time=None,
        all_day=True,
    )
    service = _build_service_mock()

    with (
        patch("app.services.calendar.build", return_value=service),
        patch("app.services.calendar.store") as mock_store,
    ):
        await create_event(_MOCK_CREDS, details)

    body = service.events.return_value.insert.call_args.kwargs["body"]
    assert body["start"]["date"] == "2024-03-20"
    assert body["end"]["date"] == "2024-03-20"
    mock_store.create_reminder.assert_not_called()
