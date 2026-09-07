#!/usr/bin/env python3
"""Read-only live check for the HomeAIAgent OpenClaw session listener."""

import asyncio
import companion_gateway as g


async def main() -> None:
    key = g._openclaw_voice_session_key()
    print(f"[CHECK] voice_session={key}")
    ws = await g._openclaw_listener_connect()
    try:
        payload, _ = await g._listener_rpc(
            ws,
            "sessions.messages.subscribe",
            {"key": key, "agentId": g.OPENCLAW_VOICE_AGENT_ID},
            session_key=key,
        )
        print(f"[OK] sessions.messages.subscribe payload={payload}")
        history, _ = await g._listener_rpc(
            ws,
            "chat.history",
            {
                "sessionKey": key,
                "agentId": g.OPENCLAW_VOICE_AGENT_ID,
                "limit": 8,
                "maxChars": 12000,
            },
            session_key=key,
        )
        messages = history.get("messages") if isinstance(history, dict) else None
        if not isinstance(messages, list):
            messages = []
        assistant = [m for m in messages if g._is_user_visible_async_assistant_message(m)]
        print(
            f"[PASS] OpenClaw listener contract ready; "
            f"history_rows={len(messages)} assistant_rows={len(assistant)}"
        )
    finally:
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
