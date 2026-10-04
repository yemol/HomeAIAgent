#!/usr/bin/env python3
"""A5.0.5 targeted reminder ownership regression."""

import asyncio
import companion_gateway as g


class DummyWS:
    async def send(self, _payload):
        return None


def make_session(device_id: str, role: str = "companion", parent: str = "", priority: int = 0):
    s = g.ClientSession(ws=DummyWS())
    g._apply_device_hello(s, {
        "type": "device.hello",
        "device_id": device_id,
        "device_role": role,
        "parent_device_id": parent,
        "audio_priority": priority,
    })
    return s


async def main() -> None:
    main_session = make_session(g.HOMEAI_PRIMARY_DEVICE_ID)
    mini_session = make_session(g.HOMEAI_MINI_DEVICE_ID)
    dock = make_session(
        "speaker-mini-dock-01", role="speaker",
        parent=g.HOMEAI_MINI_DEVICE_ID, priority=100,
    )
    old = list(g.ACTIVE_SESSIONS)
    try:
        g.ACTIVE_SESSIONS[:] = [main_session, mini_session, dock]
        picked = next(
            s for s in reversed(g.ACTIVE_SESSIONS)
            if g._reminder_session_available(s, g.HOMEAI_MINI_DEVICE_ID)
        )
        assert picked is mini_session
        assert g._select_audio_sink(picked) is dock

        legacy = g.ReminderRecord(reminder_id="legacy", dedup_key="legacy", text="旧提醒")
        target = legacy.source_device_id or g.HOMEAI_PRIMARY_DEVICE_ID
        assert target == g.HOMEAI_PRIMARY_DEVICE_ID
        assert g._reminder_session_available(main_session, target)
        assert not g._reminder_session_available(mini_session, target)
    finally:
        g.ACTIVE_SESSIONS[:] = old

    # All three asynchronous OpenClaw sessions have unique routing ownership.
    targets = g._notification_listener_targets()
    by_user = {user: device for _session, device, user in targets}
    assert by_user[g.OPENCLAW_USER] == g.HOMEAI_PRIMARY_DEVICE_ID
    assert by_user[g.OPENCLAW_MINI_USER] == g.HOMEAI_MINI_DEVICE_ID
    assert by_user[g.OPENCLAW_KITCHEN_USER] == g.KITCHEN_DEVICE_ID

    # Companion timers/reminders can never be stolen by Kitchen state, including
    # explicit Kitchen words. Source terminal owns the turn and its async task.
    old_menu = g.KITCHEN_CURRENT_MENU
    old_state = dict(g.KITCHEN_CURRENT_STATE)
    old_timers = dict(g.KITCHEN_TIMERS)
    try:
        g.KITCHEN_CURRENT_MENU = {
            "date": "2099-01-01",
            "items": [{"name": "测试菜"}],
            "recipes": [{"name": "测试菜", "steps": ["测试步骤"], "step_timers": [{"default_sec": 300, "max_sec": 600}]}],
        }
        g.KITCHEN_CURRENT_STATE.clear()
        g.KITCHEN_CURRENT_STATE.update({"screen": "recipe", "dish": "测试菜", "step": 0})
        g.KITCHEN_TIMERS.clear()
        for source in (main_session, mini_session):
            for text in (
                "设置一个5分钟的闹钟。",
                "设置一个30分钟的定时器。",
                "5分钟后提醒我起来走走。",
                "计时5分钟",
                "厨房计时5分钟",
                "小K计时5分钟",
            ):
                assert await g._apply_kitchen_voice_command(source, text) is None, (source.device_id, text)
                assert not g._is_kitchen_related_utterance(text, session=source), (source.device_id, text)
        assert not g.KITCHEN_TIMERS
    finally:
        g.KITCHEN_CURRENT_MENU = old_menu
        g.KITCHEN_CURRENT_STATE.clear()
        g.KITCHEN_CURRENT_STATE.update(old_state)
        g.KITCHEN_TIMERS.clear()
        g.KITCHEN_TIMERS.update(old_timers)

    print("PASS: reminder ownership stays Main / Mini / Kitchen separated")


if __name__ == "__main__":
    asyncio.run(main())
