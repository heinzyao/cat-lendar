"""一次修改／取消多個行程、複選、本地關鍵字比對"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_32bytes_padding_here!")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

from app.handlers.message import _handle_delete, _handle_selection, _handle_update, _parse_choices
from app.models.intent import ActionType, CalendarIntent, EventDetails, EventTarget
from app.models.user import UserState
from app.services.calendar import matches_keyword

_USER = "U_test"
_CREDS = MagicMock()


def _ev(i, summary):
    return {
        "id": f"e{i}", "summary": summary,
        "start": {"dateTime": "2024-03-15T10:00:00+08:00"},
        "end": {"dateTime": "2024-03-15T11:00:00+08:00"},
    }


def _patches():
    return (
        patch("app.handlers.message.calendar"),
        patch("app.handlers.message.nlp"),
        patch("app.handlers.message.line_messaging"),
        patch("app.handlers.message.calendar_notify"),
        patch("app.handlers.message.store"),
    )


async def test_delete_multiple_targets_and_reports_missing():
    intent = CalendarIntent(
        action=ActionType.DELETE, confidence=0.9,
        targets=[EventTarget(search_keyword="牙醫"), EventTarget(search_keyword="聚餐"),
                 EventTarget(search_keyword="不存在")],
    )
    found = {"牙醫": [_ev(1, "看牙醫")], "聚餐": [_ev(2, "聚餐")], "不存在": []}
    p = _patches()
    with p[0] as cal, p[1], p[2] as msg, p[3] as notify, p[4]:
        cal.query_events = AsyncMock(side_effect=lambda c, tr, keyword: found[keyword])
        cal.delete_event = AsyncMock()
        msg.reply_text = AsyncMock()
        notify.notify_others = AsyncMock()

        reply = await _handle_delete(_USER, "tok", intent, _CREDS)

    assert cal.delete_event.await_count == 2
    assert "看牙醫" in reply and "聚餐" in reply and "不存在" in reply
    assert notify.notify_others.await_count == 2


async def test_update_match_all_refines_each_event_and_isolates_failure():
    events = [_ev(1, "會議A"), _ev(2, "會議B")]
    intent = CalendarIntent(
        action=ActionType.UPDATE, confidence=0.9,
        targets=[EventTarget(search_keyword="會議", instruction="都延後一小時", match_all=True)],
    )
    p = _patches()
    with p[0] as cal, p[1] as nlp, p[2] as msg, p[3] as notify, p[4]:
        cal.query_events = AsyncMock(return_value=events)
        nlp.parse_update_details = AsyncMock(return_value=EventDetails(summary="x"))
        cal.update_event = AsyncMock(side_effect=[events[0], RuntimeError("boom")])
        msg.reply_text = AsyncMock()
        notify.notify_others = AsyncMock()

        reply = await _handle_update(_USER, "tok", intent, _CREDS)

    assert nlp.parse_update_details.await_count == 2
    assert "會議A" in reply and "❌『會議B』" in reply
    assert notify.notify_others.await_count == 1


async def test_ambiguous_target_saves_selection_then_multi_select_uses_target_instruction():
    events = [_ev(1, "週會"), _ev(2, "週會"), _ev(3, "週會")]
    intent = CalendarIntent(
        action=ActionType.UPDATE, confidence=0.9, original_message="週會改到三點、牙醫取消",
        targets=[EventTarget(search_keyword="週會", instruction="週會改到三點")],
    )
    p = _patches()
    with p[0] as cal, p[1], p[2] as msg, p[3], p[4] as store:
        cal.query_events = AsyncMock(return_value=events)
        msg.reply_text = AsyncMock()
        store.save_user_state = AsyncMock()
        reply = await _handle_update(_USER, "tok", intent, _CREDS)
        state: UserState = store.save_user_state.await_args.args[0]

    assert "1 3" in reply  # 提示可複選
    assert state.candidates[0]["target"]["instruction"] == "週會改到三點"

    p = _patches()
    with p[0] as cal, p[1] as nlp, p[2] as msg, p[3] as notify, p[4] as store:
        store.delete_user_state = AsyncMock()
        nlp.parse_update_details = AsyncMock(return_value=EventDetails(summary="週會"))
        cal.update_event = AsyncMock(side_effect=lambda c, eid, d, line_user_id: _ev(eid, "週會"))
        msg.reply_text = AsyncMock()
        notify.notify_others = AsyncMock()
        await _handle_selection(_USER, "tok", "1、3", state, _CREDS)

    assert [c.args[1] for c in cal.update_event.await_args_list] == ["e1", "e3"]
    assert all(c.args[0] == "週會改到三點" for c in nlp.parse_update_details.await_args_list)


def test_parse_choices():
    assert _parse_choices("2", 3) == [1]
    assert _parse_choices("1, 3", 3) == [0, 2]
    assert _parse_choices("全部", 3) == [0, 1, 2]
    assert _parse_choices("改到明天", 3) is None


def test_matches_keyword_substring_and_ignores_line_tag():
    e = {"summary": "看牙醫", "location": "台北 Dental", "description": "記得帶健保卡\n[LINE: U123]"}
    assert matches_keyword(e, "牙醫")
    assert matches_keyword(e, "dental 牙醫")       # 多詞全中、不分大小寫
    assert not matches_keyword(e, "牙醫 聚餐")
    assert not matches_keyword(e, "U123")          # 操作者標記不算


async def test_match_all_does_not_fallback_to_shared_details_when_refine_fails():
    """「都延後一小時」二次解析失敗時，不能把同一組第一階段值套到每一筆。"""
    events = [_ev(1, "會議A"), _ev(2, "會議B")]
    intent = CalendarIntent(
        action=ActionType.UPDATE, confidence=0.9,
        targets=[EventTarget(search_keyword="會議", instruction="都延後一小時", match_all=True,
                             event_details=EventDetails(summary="會議"))],
    )
    p = _patches()
    with p[0] as cal, p[1] as nlp, p[2] as msg, p[3] as notify, p[4]:
        cal.query_events = AsyncMock(return_value=events)
        nlp.parse_update_details = AsyncMock(return_value=None)
        cal.update_event = AsyncMock()
        msg.reply_text = AsyncMock()
        notify.notify_others = AsyncMock()
        reply = await _handle_update(_USER, "tok", intent, _CREDS)

    cal.update_event.assert_not_awaited()
    assert "❌『會議A』" in reply and "❌『會議B』" in reply


async def test_overlapping_targets_delete_once():
    intent = CalendarIntent(
        action=ActionType.DELETE, confidence=0.9,
        targets=[EventTarget(search_keyword="會議"), EventTarget(search_keyword="週會")],
    )
    p = _patches()
    with p[0] as cal, p[1], p[2] as msg, p[3] as notify, p[4]:
        cal.query_events = AsyncMock(return_value=[_ev(1, "週會")])
        cal.delete_event = AsyncMock()
        msg.reply_text = AsyncMock()
        notify.notify_others = AsyncMock()
        await _handle_delete(_USER, "tok", intent, _CREDS)

    cal.delete_event.assert_awaited_once()


async def test_set_reminder_selection_sets_reminder_not_update():
    """設提醒找到多筆 → 選編號後要真的建提醒，不能走修改流程（舊 bug）。"""
    from app.handlers.message import _handle_set_reminder

    events = [_ev(1, "開會"), _ev(2, "開會")]
    intent = CalendarIntent(
        action=ActionType.SET_REMINDER, confidence=0.9, search_keyword="開會",
        event_details=EventDetails(reminder_minutes=30),
    )
    p = _patches()
    with p[0] as cal, p[1], p[2] as msg, p[3], p[4] as store:
        cal.query_events = AsyncMock(return_value=events)
        msg.reply_text = AsyncMock()
        store.save_user_state = AsyncMock()
        await _handle_set_reminder(_USER, "tok", intent, _CREDS)
        state: UserState = store.save_user_state.await_args.args[0]

    assert state.action == "select_event_for_reminder"

    p = _patches()
    with p[0] as cal, p[1], p[2] as msg, p[3], p[4] as store:
        store.delete_user_state = AsyncMock()
        store.get_reminder_by_event = AsyncMock(return_value=None)
        store.create_reminder = AsyncMock()
        cal.update_event = AsyncMock()
        msg.reply_text = AsyncMock()
        reply = await _handle_selection(_USER, "tok", "1 2", state, _CREDS)

    cal.update_event.assert_not_awaited()
    assert [c.args[1] for c in store.create_reminder.await_args_list] == ["e1", "e2"]
    assert all(c.args[4] == 30 for c in store.create_reminder.await_args_list)
    assert reply.count("📌 開會") == 2
