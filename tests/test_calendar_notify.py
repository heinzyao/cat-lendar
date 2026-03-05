"""跨用戶行事曆異動通知測試"""
import os
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REFRESH_TOKEN", "test_refresh_token")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.services.calendar_notify import notify_others

_ACTOR = "Uaaaa1111"
_OTHER1 = "Ubbbb2222"
_OTHER2 = "Ucccc3333"


@pytest.mark.asyncio
async def test_notify_others_sends_to_all_except_actor():
    """CREATE 後應推播給所有非操作者"""
    with (
        patch("app.services.calendar_notify.store") as mock_store,
        patch("app.services.calendar_notify.line_messaging") as mock_line,
    ):
        mock_store.get_all_user_ids = AsyncMock(return_value=[_ACTOR, _OTHER1, _OTHER2])
        mock_line.get_display_name = AsyncMock(return_value="小明")
        mock_line.push_text = AsyncMock()

        await notify_others("create", _ACTOR, "週會", "03/20 10:00-11:00")

        assert mock_line.push_text.call_count == 2
        notified = {call.args[0] for call in mock_line.push_text.call_args_list}
        assert notified == {_OTHER1, _OTHER2}
        # 操作者不應收到
        assert _ACTOR not in notified


@pytest.mark.asyncio
async def test_notify_others_only_one_user_no_push():
    """只有一個用戶時不應推播（沒有其他人）"""
    with (
        patch("app.services.calendar_notify.store") as mock_store,
        patch("app.services.calendar_notify.line_messaging") as mock_line,
    ):
        mock_store.get_all_user_ids = AsyncMock(return_value=[_ACTOR])
        mock_line.get_display_name = AsyncMock(return_value="小明")
        mock_line.push_text = AsyncMock()

        await notify_others("create", _ACTOR, "週會", "03/20 10:00")

        mock_line.push_text.assert_not_called()


@pytest.mark.asyncio
async def test_notify_others_fallback_display_name():
    """無法取得顯示名稱時，fallback 到 user_id 後四碼"""
    with (
        patch("app.services.calendar_notify.store") as mock_store,
        patch("app.services.calendar_notify.line_messaging") as mock_line,
    ):
        mock_store.get_all_user_ids = AsyncMock(return_value=[_ACTOR, _OTHER1])
        mock_line.get_display_name = AsyncMock(return_value=None)
        mock_line.push_text = AsyncMock()

        await notify_others("delete", _ACTOR, "週會")

        mock_line.push_text.assert_awaited_once()
        msg = mock_line.push_text.call_args[0][1]
        assert "1111" in msg  # _ACTOR 末四碼


@pytest.mark.asyncio
async def test_notify_create_message_format():
    """CREATE 通知訊息應包含用戶名、行程名稱、時間"""
    with (
        patch("app.services.calendar_notify.store") as mock_store,
        patch("app.services.calendar_notify.line_messaging") as mock_line,
    ):
        mock_store.get_all_user_ids = AsyncMock(return_value=[_ACTOR, _OTHER1])
        mock_line.get_display_name = AsyncMock(return_value="Alice")
        mock_line.push_text = AsyncMock()

        await notify_others("create", _ACTOR, "客戶簡報", "03/20 14:00-15:00")

        msg = mock_line.push_text.call_args[0][1]
        assert "Alice" in msg
        assert "客戶簡報" in msg
        assert "14:00" in msg
        assert "新增" in msg


@pytest.mark.asyncio
async def test_notify_update_message_format():
    """UPDATE 通知訊息應包含「修改」"""
    with (
        patch("app.services.calendar_notify.store") as mock_store,
        patch("app.services.calendar_notify.line_messaging") as mock_line,
    ):
        mock_store.get_all_user_ids = AsyncMock(return_value=[_ACTOR, _OTHER1])
        mock_line.get_display_name = AsyncMock(return_value="Bob")
        mock_line.push_text = AsyncMock()

        await notify_others("update", _ACTOR, "週會", "03/21 10:00-11:00")

        msg = mock_line.push_text.call_args[0][1]
        assert "修改" in msg
        assert "Bob" in msg


@pytest.mark.asyncio
async def test_notify_delete_message_format():
    """DELETE 通知訊息應包含「刪除」且不含時間"""
    with (
        patch("app.services.calendar_notify.store") as mock_store,
        patch("app.services.calendar_notify.line_messaging") as mock_line,
    ):
        mock_store.get_all_user_ids = AsyncMock(return_value=[_ACTOR, _OTHER1])
        mock_line.get_display_name = AsyncMock(return_value="Carol")
        mock_line.push_text = AsyncMock()

        await notify_others("delete", _ACTOR, "週會")

        msg = mock_line.push_text.call_args[0][1]
        assert "刪除" in msg
        assert "Carol" in msg
        assert "週會" in msg


@pytest.mark.asyncio
async def test_notify_push_failure_does_not_crash():
    """某個用戶推播失敗不應影響其他用戶"""
    with (
        patch("app.services.calendar_notify.store") as mock_store,
        patch("app.services.calendar_notify.line_messaging") as mock_line,
    ):
        mock_store.get_all_user_ids = AsyncMock(return_value=[_ACTOR, _OTHER1, _OTHER2])
        mock_line.get_display_name = AsyncMock(return_value="Dave")
        mock_line.push_text = AsyncMock(
            side_effect=[Exception("push failed"), None]
        )

        # 不應 raise
        await notify_others("create", _ACTOR, "週會", "03/20 10:00")

        # 兩個 push 都應嘗試（即使第一個失敗）
        assert mock_line.push_text.call_count == 2
