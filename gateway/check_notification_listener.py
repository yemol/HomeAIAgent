#!/usr/bin/env python3
"""Read-only live check for all configured HomeAIAgent reminder sessions."""

import asyncio
import companion_gateway as g


async def main() -> None:
    targets = g._notification_listener_targets()
    if not targets:
        raise RuntimeError("no configured notification listener targets")
    session_keys = tuple(t[0] for t in targets)
    print(f"[CHECK] notification_sessions={len(targets)}")
    for key, device_id, user in targets:
        print(f"[CHECK] device={device_id} user={user} session={key}")

    ws = await g._openclaw_listener_connect()
    try:
        for key, device_id, _user in targets:
            payload, _ = await g._listener_rpc(
                ws,
                "sessions.messages.subscribe",
                {"key": key, "agentId": g.OPENCLAW_VOICE_AGENT_ID},
                session_keys=session_keys,
            )
            print(f"[OK] subscribe device={device_id} payload={payload}")

        for key, device_id, _user in targets:
            history, _ = await g._listener_rpc(
                ws,
                "chat.history",
                {
                    "sessionKey": key,
                    "agentId": g.OPENCLAW_VOICE_AGENT_ID,
                    "limit": 8,
                    "maxChars": 12000,
                },
                session_keys=session_keys,
            )
            messages = history.get("messages") if isinstance(history, dict) else None
            if not isinstance(messages, list):
                messages = []
            assistant = [m for m in messages if g._is_user_visible_async_assistant_message(m)]
            print(
                f"[PASS] device={device_id} listener contract ready; "
                f"history_rows={len(messages)} assistant_rows={len(assistant)}"
            )
    finally:
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
