#!/usr/bin/env python3
"""Offline regression test for Notification A1 transcript reconciliation."""

import asyncio
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
    temp_root = Path(tempfile.mkdtemp(prefix="homeai-notify-test-"))
    g.NOTIFICATION_STATE_FILE = temp_root / "listener-state.json"
    g.REMINDER_QUEUE_FILE = temp_root / "queue.json"
    g.REMINDER_LOCK = asyncio.Lock()

    g.REMINDER_PENDING.clear()
    g.REMINDER_DELIVERED.clear()
    g.REMINDER_DELIVERED_KEYS.clear()
    g.NOTIFICATION_SEEN_KEYS.clear()
    g.NOTIFICATION_SEEN_SET.clear()
    g.NOTIFICATION_CURSOR_INITIALIZED = False
    g.NOTIFICATION_CURSOR_SESSION_KEY = ""
    g.NOTIFICATION_CURSOR_SESSION_ID = ""
    g.VOICE_REPLY_SUPPRESSIONS.clear()
    g.VOICE_TURN_FENCES.clear()
    g.VOICE_OPENCLAW_INFLIGHT = 0

    session_key = "agent:main:openai-user:home-ai-agent:main"

    # First startup must baseline existing history without replaying it.
    await g._reconcile_voice_session_history(
        {"sessionId": "session-a", "messages": [_message("m1", 1, "旧消息")]},
        session_key,
    )
    assert not g.REMINDER_PENDING

    # A later assistant append is an asynchronous notification and must persist.
    async_message = _message("m2", 2, "提醒你，喝水")
    await g._reconcile_voice_session_history(
        {
            "sessionId": "session-a",
            "messages": [_message("m1", 1, "旧消息"), async_message],
        },
        session_key,
    )
    assert len(g.REMINDER_PENDING) == 1
    assert g.REMINDER_PENDING[0].text == "提醒你，喝水"
    assert g._reminder_spoken_text("喝水") == "喝水"
    assert g._reminder_spoken_text("  喝水  ") == "喝水"
    assert g.REMINDER_QUEUE_FILE.exists()

    # The normal synchronous voice reply is present in the same transcript but
    # must be consumed by the suppression fingerprint instead of spoken twice.
    voice_reply = "好的，已经设置提醒。"
    g._register_voice_reply_suppression(voice_reply)
    await g._reconcile_voice_session_history(
        {
            "sessionId": "session-a",
            "messages": [
                _message("m1", 1, "旧消息"),
                async_message,
                _message("m3", 3, voice_reply),
            ],
        },
        session_key,
    )
    assert len(g.REMINDER_PENDING) == 1
    assert g.NOTIFICATION_STATE_FILE.exists()

    # One synchronous voice turn can append multiple assistant progress
    # rows before its final HTTP reply. None of those progress rows may be
    # replayed later as notifications. A truly earlier or later asynchronous
    # assistant row must still be delivered.
    pending_before_turn = _message("m4", 4, "这是语音请求前刚到的一条提醒。")
    user_turn = _user_message("u5", 5, "用户本轮语音请求")
    progress_1 = _message("m6", 6, "我先检查一下播放器状态。")
    progress_2 = _message("m7", 7, "找到了控制菜单，正在点击播放。")
    final_reply = _message("m8", 8, "音乐已经在放了，需要暂停随时喊我。")
    later_async = _message("m9", 9, "你设置的定时提醒到了。")

    final_text = "音乐已经在放了，需要暂停随时喊我。"
    g._register_voice_reply_suppression(final_text)
    g._register_voice_turn_fence(final_text)
    await g._reconcile_voice_session_history(
        {
            "sessionId": "session-a",
            "messages": [
                _message("m1", 1, "旧消息"),
                async_message,
                _message("m3", 3, voice_reply),
                pending_before_turn,
                user_turn,
                progress_1,
                progress_2,
                final_reply,
                later_async,
            ],
        },
        session_key,
    )
    assert len(g.REMINDER_PENDING) == 3
    assert g.REMINDER_PENDING[-2].text == "这是语音请求前刚到的一条提醒。"
    assert g.REMINDER_PENDING[-1].text == "你设置的定时提醒到了。"
    assert not g.VOICE_TURN_FENCES

    # Reconciliation after reconnect must not resurrect the consumed progress
    # rows from history.
    await g._reconcile_voice_session_history(
        {
            "sessionId": "session-a",
            "messages": [
                _message("m1", 1, "旧消息"),
                async_message,
                _message("m3", 3, voice_reply),
                pending_before_turn,
                user_turn,
                progress_1,
                progress_2,
                final_reply,
                later_async,
            ],
        },
        session_key,
    )
    assert len(g.REMINDER_PENDING) == 3

    # If OpenClaw has not committed the final row yet, reconciliation must
    # wait instead of prematurely queueing progress rows.
    g._register_voice_reply_suppression("第二轮最终回答")
    g._register_voice_turn_fence("第二轮最终回答")
    second_user = _user_message("u10", 10, "第二轮请求")
    second_progress = _message("m11", 11, "第二轮处理中")
    await g._reconcile_voice_session_history(
        {
            "sessionId": "session-a",
            "messages": [
                _message("m1", 1, "旧消息"),
                async_message,
                _message("m3", 3, voice_reply),
                pending_before_turn,
                user_turn,
                progress_1,
                progress_2,
                final_reply,
                later_async,
                second_user,
                second_progress,
            ],
        },
        session_key,
    )
    assert len(g.REMINDER_PENDING) == 3
    assert g.VOICE_TURN_FENCES

    second_final = _message("m12", 12, "第二轮最终回答")
    await g._reconcile_voice_session_history(
        {
            "sessionId": "session-a",
            "messages": [
                _message("m1", 1, "旧消息"),
                async_message,
                _message("m3", 3, voice_reply),
                pending_before_turn,
                user_turn,
                progress_1,
                progress_2,
                final_reply,
                later_async,
                second_user,
                second_progress,
                second_final,
            ],
        },
        session_key,
    )
    assert len(g.REMINDER_PENDING) == 3
    assert not g.VOICE_TURN_FENCES

    # Accept both top-level and nested transcript update targets.
    assert g._listener_event_targets_voice_session(
        {
            "type": "event",
            "event": "session.message",
            "payload": {"target": {"sessionKey": session_key}},
        },
        session_key,
    )

    print("HomeAIAgent Notification A1 offline regression: PASS")


if __name__ == "__main__":
    asyncio.run(_run())
