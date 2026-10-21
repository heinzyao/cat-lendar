"""「輸入中」動畫：要走 Gemini 的訊息先顯示，固定指令不顯示；動畫失敗不能擋主流程。"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.handlers import message
from app.services import line_messaging


def _patches(order: list[str]):
    store = MagicMock()
    store.register_user = AsyncMock()
    store.get_user_state = AsyncMock(return_value=None)
    store.get_conversation_history = AsyncMock(return_value=[])

    async def loading(uid):
        order.append(f"loading:{uid}")

    async def parse(*a, **kw):
        order.append("parse")
        raise RuntimeError("stop here")

    line = MagicMock()
    line.show_loading = AsyncMock(side_effect=loading)
    line.reply_text = AsyncMock()
    return store, line, parse


async def test_loading_shown_before_nlp():
    order: list[str] = []
    store, line, parse = _patches(order)
    with (
        patch.object(message, "store", store),
        patch.object(message, "line_messaging", line),
        patch.object(message.auth, "get_shared_credentials", MagicMock()),
        patch.object(message.nlp, "parse_intent", parse),
    ):
        await message.handle_message("U1", "tok", "明天下午三點開會")
    assert order == ["loading:U1", "parse"]


async def test_no_loading_for_fixed_commands():
    order: list[str] = []
    store, line, _ = _patches(order)
    with (
        patch.object(message, "store", store),
        patch.object(message, "line_messaging", line),
    ):
        await message.handle_message("U1", "tok", "說明")
    line.show_loading.assert_not_awaited()


async def test_show_loading_swallows_api_errors():
    api = MagicMock()
    api.show_loading_animation.side_effect = RuntimeError("LINE 400")
    with patch.object(line_messaging, "MessagingApi", return_value=api):
        await line_messaging.show_loading("U1")  # 不可拋出
    req = api.show_loading_animation.call_args.args[0]
    assert req.chat_id == "U1"
    assert 5 <= req.loading_seconds <= 60 and req.loading_seconds % 5 == 0


async def test_no_loading_in_group_chat():
    order: list[str] = []
    store, line, parse = _patches(order)
    with (
        patch.object(message, "store", store),
        patch.object(message, "line_messaging", line),
        patch.object(message.auth, "get_shared_credentials", MagicMock()),
        patch.object(message.nlp, "parse_intent", parse),
    ):
        await message.handle_message("U1", "tok", "明天下午三點開會", one_on_one=False)
    assert order == ["parse"]


async def test_show_loading_sets_http_timeout():
    api = MagicMock()
    with patch.object(line_messaging, "MessagingApi", return_value=api):
        await line_messaging.show_loading("U1")
    assert api.show_loading_animation.call_args.kwargs["_request_timeout"] == 2
