#!/usr/bin/env python3
"""Offline regression for A5.0.2 multi-device Notification/Reminder routing."""

import asyncio
import json
import tempfile
from pathlib import Path

import companion_gateway as g


def _message(message_id: str, seq: int, text: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "__openclaw": {"id": message_id, "seq": seq},
    }


def _user_message(message_id: str, seq: int, text: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "text", "text": text}],
        "__openclaw": {"id": message_id, "seq": seq},
    }


async def _run() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="homeai-notify-multi-test-"))
    g.NOTIFICATION_STATE_FILE = temp_root / "listener-state.json"
    g.REMINDER_QUEUE_FILE = temp_root / "queue.json"
    g.REMINDER_LOCK = asyncio.Lock()

    g.REMINDER_PENDING.clear()
    g.REMINDER_DELIVERED.clear()
    g.REMINDER_DELIVERED_KEYS.clear()
    g.NOTIFICATION_SESSION_STATES.clear()

    primary_key = g._openclaw_voice_session_key(g.OPENCLAW_USER)
    mini_key = g._openclaw_voice_session_key(g.OPENCLAW_MINI_USER)
    kitchen_key = g._openclaw_voice_session_key(g.OPENCLAW_KITCHEN_USER)
    assert len({primary_key, mini_key, kitchen_key}) == 3

    # First startup baselines both sessions independently without replaying history.
    await g._reconcile_voice_session_history(
        {"sessionId": "primary-a", "messages": [_message("p1", 1, "主机旧消息")]},
        primary_key,
        source_device_id=g.HOMEAI_PRIMARY_DEVICE_ID,
        source_openclaw_user=g.OPENCLAW_USER,
    )
    await g._reconcile_voice_session_history(
        {"sessionId": "mini-a", "messages": [_message("m1", 1, "Mini旧消息")]},
        mini_key,
        source_device_id=g.HOMEAI_MINI_DEVICE_ID,
        source_openclaw_user=g.OPENCLAW_MINI_USER,
    )
    await g._reconcile_voice_session_history(
        {"sessionId": "kitchen-a", "messages": [_message("k1", 1, "小K旧消息")]},
        kitchen_key,
        source_device_id=g.KITCHEN_DEVICE_ID,
        source_openclaw_user=g.OPENCLAW_KITCHEN_USER,
    )
    assert not g.REMINDER_PENDING

    # Mini async append must carry Mini as the durable delivery target.
    mini_async = _message("m2", 2, "你设置的五分钟闹钟到了。")
    await g._reconcile_voice_session_history(
        {"sessionId": "mini-a", "messages": [_message("m1", 1, "Mini旧消息"), mini_async]},
        mini_key,
        source_device_id=g.HOMEAI_MINI_DEVICE_ID,
        source_openclaw_user=g.OPENCLAW_MINI_USER,
    )
    assert len(g.REMINDER_PENDING) == 1
    record = g.REMINDER_PENDING[0]
    assert record.source_device_id == g.HOMEAI_MINI_DEVICE_ID
    assert record.source_openclaw_user == g.OPENCLAW_MINI_USER
    assert record.source_session_key == mini_key
    assert g.REMINDER_QUEUE_FILE.exists()

    # Suppression/fence state is isolated per session. Registering a primary
    # synchronous reply must not consume the same text when it appears in Mini.
    shared_text = "好的，已经设置提醒。"
    g._register_voice_reply_suppression(shared_text, primary_key)
    await g._reconcile_voice_session_history(
        {
            "sessionId": "mini-a",
            "messages": [
                _message("m1", 1, "Mini旧消息"),
                mini_async,
                _message("m3", 3, shared_text),
            ],
        },
        mini_key,
        source_device_id=g.HOMEAI_MINI_DEVICE_ID,
        source_openclaw_user=g.OPENCLAW_MINI_USER,
    )
    assert len(g.REMINDER_PENDING) == 2
    assert g.REMINDER_PENDING[-1].source_device_id == g.HOMEAI_MINI_DEVICE_ID

    # Kitchen async append is a third isolated route owned by the iPad terminal.
    kitchen_async = _message("k2", 2, "小K提醒：时间到了。")
    await g._reconcile_voice_session_history(
        {"sessionId": "kitchen-a", "messages": [_message("k1", 1, "小K旧消息"), kitchen_async]},
        kitchen_key,
        source_device_id=g.KITCHEN_DEVICE_ID,
        source_openclaw_user=g.OPENCLAW_KITCHEN_USER,
    )
    assert g.REMINDER_PENDING[-1].source_device_id == g.KITCHEN_DEVICE_ID
    assert g.REMINDER_PENDING[-1].source_openclaw_user == g.OPENCLAW_KITCHEN_USER
    assert g.REMINDER_PENDING[-1].source_session_key == kitchen_key

    # Primary synchronous progress rows remain fenced and never become reminders.
    primary_state = g._notification_state(primary_key)
    primary_state.voice_inflight = 0
    pending_before = _message("p2", 2, "语音请求前的一条主机提醒。")
    user_turn = _user_message("pu3", 3, "主机语音请求")
    progress = _message("p4", 4, "处理中")
    final = _message("p5", 5, "最终同步回答")
    later = _message("p6", 6, "主机真正的异步提醒。")
    g._register_voice_reply_suppression("最终同步回答", primary_key)
    g._register_voice_turn_fence("最终同步回答", primary_key)
    await g._reconcile_voice_session_history(
        {
            "sessionId": "primary-a",
            "messages": [
                _message("p1", 1, "主机旧消息"),
                pending_before,
                user_turn,
                progress,
                final,
                later,
            ],
        },
        primary_key,
        source_device_id=g.HOMEAI_PRIMARY_DEVICE_ID,
        source_openclaw_user=g.OPENCLAW_USER,
    )
    assert [x.text for x in g.REMINDER_PENDING[-2:]] == [
        "语音请求前的一条主机提醒。",
        "主机真正的异步提醒。",
    ]
    assert all(x.text != "处理中" for x in g.REMINDER_PENDING)
    assert not g._notification_state(primary_key).turn_fences

    # Persisted cursor file must contain independent Main + Mini + Kitchen entries.
    saved = json.loads(g.NOTIFICATION_STATE_FILE.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert primary_key in saved["sessions"]
    assert mini_key in saved["sessions"]
    assert kitchen_key in saved["sessions"]
    assert saved["sessions"][mini_key]["source_device_id"] == g.HOMEAI_MINI_DEVICE_ID
    assert saved["sessions"][kitchen_key]["source_device_id"] == g.KITCHEN_DEVICE_ID

    # Re-load verifies durable per-session cursor restoration.
    g.NOTIFICATION_SESSION_STATES.clear()
    pstate = g.load_notification_listener_state(
        primary_key,
        source_device_id=g.HOMEAI_PRIMARY_DEVICE_ID,
        openclaw_user=g.OPENCLAW_USER,
    )
    mstate = g.load_notification_listener_state(
        mini_key,
        source_device_id=g.HOMEAI_MINI_DEVICE_ID,
        openclaw_user=g.OPENCLAW_MINI_USER,
    )
    kstate = g.load_notification_listener_state(
        kitchen_key,
        source_device_id=g.KITCHEN_DEVICE_ID,
        openclaw_user=g.OPENCLAW_KITCHEN_USER,
    )
    assert pstate.initialized and mstate.initialized and kstate.initialized
    assert pstate.seen_message_set != mstate.seen_message_set
    assert kstate.seen_message_set != mstate.seen_message_set

    # Accept nested transcript update targets.
    assert g._listener_event_targets_voice_session(
        {
            "type": "event",
            "event": "session.message",
            "payload": {"target": {"sessionKey": mini_key}},
        },
        mini_key,
    )

    print("PASS: A5.0.5 three-terminal notification listener + origin routing")


if __name__ == "__main__":
    asyncio.run(_run())
