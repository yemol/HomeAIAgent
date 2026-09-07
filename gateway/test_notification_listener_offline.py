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
