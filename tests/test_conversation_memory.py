"""對話記憶功能測試（mock Firestore + Claude API）"""

import os
import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test_client_id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "https://example.com/oauth/callback")
os.environ.setdefault("ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.config import settings
from app.models.user import ConversationMessage


_USER = "U_test_user"


# ── Firestore: get_conversation_history ──


async def test_get_conversation_history_empty():
    """文件不存在時回傳空列表"""
    mock_doc = MagicMock()
    mock_doc.exists = False

    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_doc)

    with patch("app.store.firestore.get_db") as mock_db:
        mock_db.return_value.collection.return_value.document.return_value = (
            mock_doc_ref
        )
        from app.store.firestore import get_conversation_history

        result = await get_conversation_history(_USER)

    assert result == []


async def test_get_conversation_history_returns_messages():
    """正常取得對話記憶"""
    now = datetime.now(timezone.utc)
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "messages": [
            {"role": "user", "content": "明天有什麼行程？", "timestamp": now},
            {"role": "assistant", "content": "已查詢行程", "timestamp": now},
        ],
        "updated_at": now,
    }

    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_doc)

    with patch("app.store.firestore.get_db") as mock_db:
        mock_db.return_value.collection.return_value.document.return_value = (
            mock_doc_ref
        )
        from app.store.firestore import get_conversation_history

        result = await get_conversation_history(_USER)

    assert len(result) == 2
    assert result[0].role == "user"
    assert result[0].content == "明天有什麼行程？"
    assert result[1].role == "assistant"


async def test_get_conversation_history_expired():
    """已過期的對話記憶應被清除並回傳空列表"""
    expired_time = datetime.now(timezone.utc) - timedelta(
        seconds=settings.conversation_history_ttl_seconds + 60
    )
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "messages": [
            {"role": "user", "content": "舊訊息", "timestamp": expired_time},
        ],
        "updated_at": expired_time,
    }

    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_doc)
    mock_doc_ref.delete = AsyncMock()

    with patch("app.store.firestore.get_db") as mock_db:
        mock_db.return_value.collection.return_value.document.return_value = (
            mock_doc_ref
        )
        from app.store.firestore import get_conversation_history

        result = await get_conversation_history(_USER)

    assert result == []
    mock_doc_ref.delete.assert_awaited_once()


# ── Firestore: append_conversation_turn ──


async def test_append_conversation_turn_new_doc():
    """文件不存在時建立新的對話記憶"""
    mock_doc = MagicMock()
    mock_doc.exists = False

    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_doc)
    mock_doc_ref.set = AsyncMock()

    with patch("app.store.firestore.get_db") as mock_db:
        mock_db.return_value.collection.return_value.document.return_value = (
            mock_doc_ref
        )
        from app.store.firestore import append_conversation_turn

        await append_conversation_turn(_USER, "你好", "你好！有什麼可以幫你的嗎？")

    mock_doc_ref.set.assert_awaited_once()
    saved_data = mock_doc_ref.set.call_args[0][0]
    assert len(saved_data["messages"]) == 2
    assert saved_data["messages"][0]["role"] == "user"
    assert saved_data["messages"][0]["content"] == "你好"
    assert saved_data["messages"][1]["role"] == "assistant"
    assert saved_data["messages"][1]["content"] == "你好！有什麼可以幫你的嗎？"


async def test_append_conversation_turn_appends_to_existing():
    """既有對話記憶追加新的一輪"""
    now = datetime.now(timezone.utc)
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "messages": [
            {"role": "user", "content": "明天有什麼行程？", "timestamp": now},
            {"role": "assistant", "content": "已查詢行程", "timestamp": now},
        ],
        "updated_at": now,
    }

    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_doc)
    mock_doc_ref.set = AsyncMock()

    with patch("app.store.firestore.get_db") as mock_db:
        mock_db.return_value.collection.return_value.document.return_value = (
            mock_doc_ref
        )
        from app.store.firestore import append_conversation_turn

        await append_conversation_turn(_USER, "那後天呢？", "已查詢後天行程")

    saved_data = mock_doc_ref.set.call_args[0][0]
    assert len(saved_data["messages"]) == 4


async def test_append_conversation_turn_trims_old_messages():
    """超過 max_turns 時裁剪最舊的訊息"""
    now = datetime.now(timezone.utc)
    max_turns = settings.max_conversation_turns
    # 建立已滿的 messages（每輪 2 則）
    existing_messages = []
    for i in range(max_turns):
        existing_messages.append(
            {"role": "user", "content": f"msg{i}", "timestamp": now}
        )
        existing_messages.append(
            {"role": "assistant", "content": f"reply{i}", "timestamp": now}
        )

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "messages": existing_messages,
        "updated_at": now,
    }

    mock_doc_ref = AsyncMock()
    mock_doc_ref.get = AsyncMock(return_value=mock_doc)
    mock_doc_ref.set = AsyncMock()

    with patch("app.store.firestore.get_db") as mock_db:
        mock_db.return_value.collection.return_value.document.return_value = (
            mock_doc_ref
        )
        from app.store.firestore import append_conversation_turn

        await append_conversation_turn(_USER, "新訊息", "新回覆")

    saved_data = mock_doc_ref.set.call_args[0][0]
    # 應該被裁剪到 max_turns * 2
    assert len(saved_data["messages"]) == max_turns * 2
    # 最舊的 msg0 應被移除，最新的應該是新訊息
    assert saved_data["messages"][-1]["content"] == "新回覆"
    assert saved_data["messages"][-2]["content"] == "新訊息"
    # msg0 被移除
    contents = [m["content"] for m in saved_data["messages"]]
    assert "msg0" not in contents
    assert "reply0" not in contents


# ── Firestore: clear_conversation_history ──


async def test_clear_conversation_history():
    """清除對話記憶"""
    mock_doc_ref = AsyncMock()
    mock_doc_ref.delete = AsyncMock()

    with patch("app.store.firestore.get_db") as mock_db:
        mock_db.return_value.collection.return_value.document.return_value = (
            mock_doc_ref
        )
        from app.store.firestore import clear_conversation_history

        await clear_conversation_history(_USER)

    mock_doc_ref.delete.assert_awaited_once()


# ── NLP: parse_intent with conversation_history ──


async def test_parse_intent_passes_history_as_messages():
    """parse_intent 應將 conversation_history 轉為 multi-turn messages"""
    now = datetime.now(timezone.utc)
    history = [
        ConversationMessage(role="user", content="明天有什麼行程？", timestamp=now),
        ConversationMessage(
            role="assistant", content="明天有開會 10:00-11:00", timestamp=now
        ),
    ]

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text='{"action": "update", "search_keyword": "開會", "event_details": {"start_time": "2024-03-16T14:00:00+08:00"}, "confidence": 0.9}'
        )
    ]

    with patch("app.services.nlp._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        from app.services.nlp import parse_intent

        intent = await parse_intent("把它改到下午兩點", history)

    # 驗證 messages 包含 history + 新訊息
    call_kwargs = mock_client.messages.create.call_args[1]
    messages = call_kwargs["messages"]
    assert len(messages) == 3  # 2 history + 1 new
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "明天有什麼行程？"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "明天有開會 10:00-11:00"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "把它改到下午兩點"


async def test_parse_intent_without_history():
    """不傳 history 時只有單一 user message"""
    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text='{"action": "query", "time_range": {"start": "2024-03-15T00:00:00+08:00", "end": "2024-03-15T23:59:59+08:00"}, "confidence": 0.9}'
        )
    ]

    with patch("app.services.nlp._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        from app.services.nlp import parse_intent

        intent = await parse_intent("今天有什麼行程？")

    call_kwargs = mock_client.messages.create.call_args[1]
    messages = call_kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


async def test_parse_intent_system_prompt_includes_history_note():
    """有 history 時 system prompt 應包含上下文提示"""
    now = datetime.now(timezone.utc)
    history = [
        ConversationMessage(role="user", content="test", timestamp=now),
    ]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"action": "unknown", "confidence": 0.5}')]

    with patch("app.services.nlp._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client

        from app.services.nlp import parse_intent

        await parse_intent("改到明天", history)

    call_kwargs = mock_client.messages.create.call_args[1]
    system_prompt = call_kwargs["system"]
    assert (
        "對話歷史" in system_prompt
        or "對話上下文" in system_prompt
        or "代名詞" in system_prompt
    )


# ── message handler integration ──


async def test_handle_message_loads_and_saves_conversation():
    """handle_message 應讀取對話記憶、傳給 NLP、並儲存新一輪"""
    with (
        patch("app.handlers.message.store") as mock_store,
        patch("app.handlers.message.nlp") as mock_nlp,
        patch("app.handlers.message.line_messaging") as mock_line,
        patch("app.handlers.message.auth") as mock_auth,
        patch("app.handlers.message.calendar") as mock_calendar,
    ):
        # Setup mocks
        mock_store.get_user_state = AsyncMock(return_value=None)
        mock_store.get_calendar_mode = AsyncMock(return_value="local")
        mock_store.get_conversation_history = AsyncMock(return_value=[])
        mock_store.append_conversation_turn = AsyncMock()

        mock_nlp.parse_intent = AsyncMock(
            return_value=MagicMock(
                action="query",
                confidence=0.9,
                clarification_needed=None,
                event_details=None,
                time_range=MagicMock(
                    start=datetime.now(timezone.utc), end=datetime.now(timezone.utc)
                ),
                search_keyword=None,
                original_message="今天有什麼行程？",
            )
        )

        mock_line.reply_text = AsyncMock()
        mock_store.list_local_events = AsyncMock(return_value=[])

        from app.handlers.message import handle_message

        await handle_message(_USER, "reply_token_123", "今天有什麼行程？")

    # 驗證讀取對話記憶
    mock_store.get_conversation_history.assert_awaited_once_with(_USER)

    # 驗證 NLP 接收到 conversation_history
    mock_nlp.parse_intent.assert_awaited_once()
    call_args = mock_nlp.parse_intent.call_args
    assert call_args[0][0] == "今天有什麼行程？"  # user_message
    assert call_args[0][1] == []  # conversation_history (empty)

    # 驗證儲存對話記憶
    mock_store.append_conversation_turn.assert_awaited_once()
    save_args = mock_store.append_conversation_turn.call_args[0]
    assert save_args[0] == _USER  # user_id
    assert save_args[1] == "今天有什麼行程？"  # user_message


async def test_handle_message_saves_conversation_on_clarification():
    """信心不足時也應儲存對話記憶"""
    with (
        patch("app.handlers.message.store") as mock_store,
        patch("app.handlers.message.nlp") as mock_nlp,
        patch("app.handlers.message.line_messaging") as mock_line,
    ):
        mock_store.get_user_state = AsyncMock(return_value=None)
        mock_store.get_calendar_mode = AsyncMock(return_value="local")
        mock_store.get_conversation_history = AsyncMock(return_value=[])
        mock_store.append_conversation_turn = AsyncMock()

        mock_nlp.parse_intent = AsyncMock(
            return_value=MagicMock(
                action="unknown",
                confidence=0.3,
                clarification_needed="請說明你要做什麼",
            )
        )
        mock_line.reply_text = AsyncMock()

        from app.handlers.message import handle_message

        await handle_message(_USER, "reply_token_456", "嗯")

    # 低信心時也要儲存對話記憶
    mock_store.append_conversation_turn.assert_awaited_once()


# ── _summarize_intent_result ──


def test_summarize_intent_result_create():
    """建立行程的摘要"""
    from app.handlers.message import _summarize_intent_result
    from app.models.intent import ActionType, CalendarIntent, EventDetails

    intent = CalendarIntent(
        action=ActionType.CREATE,
        event_details=EventDetails(summary="開會"),
        confidence=0.9,
    )
    result = _summarize_intent_result(intent)
    assert "開會" in result
    assert "建立" in result


def test_summarize_intent_result_query():
    """查詢行程的摘要"""
    from app.handlers.message import _summarize_intent_result
    from app.models.intent import ActionType, CalendarIntent

    intent = CalendarIntent(action=ActionType.QUERY, confidence=0.9)
    result = _summarize_intent_result(intent)
    assert "查詢" in result


def test_summarize_intent_result_delete():
    """刪除行程的摘要"""
    from app.handlers.message import _summarize_intent_result
    from app.models.intent import ActionType, CalendarIntent

    intent = CalendarIntent(
        action=ActionType.DELETE,
        search_keyword="開會",
        confidence=0.9,
    )
    result = _summarize_intent_result(intent)
    assert "刪除" in result
    assert "開會" in result
