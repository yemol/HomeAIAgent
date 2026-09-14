#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import companion_gateway as cg


class DummyWS:
    async def send(self, _payload):
        return None


def make_session(device_id, role="companion", parent="", priority=0):
    s = cg.ClientSession(ws=DummyWS())
    cg._apply_device_hello(s, {
        "type": "device.hello",
        "device_id": device_id,
        "device_role": role,
        "parent_device_id": parent,
        "audio_priority": priority,
    })
    return s


def main():
    # Main remains primary conversation.
    main_session = make_session(cg.HOMEAI_PRIMARY_DEVICE_ID)
    assert main_session.openclaw_user == cg.OPENCLAW_USER

    # Mini is isolated.
    mini = make_session(cg.HOMEAI_MINI_DEVICE_ID)
    assert mini.openclaw_user == cg.OPENCLAW_MINI_USER
    assert mini.openclaw_user != main_session.openclaw_user

    # Primary legacy device keeps Glass2 services; Mini does not inherit them
    # merely because it has a physical screen.
    assert cg._supports_display_policy(main_session)
    assert cg._supports_info_feed(main_session)
    assert not cg._supports_display_policy(mini)
    assert not cg._supports_info_feed(mini)

    # Future unknown companion is isolated by default.
    other = make_session("future-companion-01")
    assert other.openclaw_user != cg.OPENCLAW_USER
    assert other.openclaw_user != mini.openclaw_user

    opt_in = cg.ClientSession(ws=DummyWS())
    cg._apply_device_hello(opt_in, {
        "type": "device.hello",
        "device_id": "future-display-01",
        "capabilities": {
            "display": "240x320",
            "display_policy": True,
            "info_feed": True,
        },
    })
    assert cg._supports_display_policy(opt_in)
    assert cg._supports_info_feed(opt_in)

    # Speakers own no LLM conversation.
    mini_dock = make_session(
        "speaker-mini-dock-01",
        role="speaker",
        parent=cg.HOMEAI_MINI_DEVICE_ID,
        priority=100,
    )
    kitchen_speaker = make_session(
        "speaker-kitchen-01",
        role="speaker",
        parent="future-companion-01",
        priority=50,
    )
    assert mini_dock.openclaw_user == ""
    assert kitchen_speaker.openclaw_user == ""

    # Multiple speakers route only to their own parent.
    old = list(cg.ACTIVE_SESSIONS)
    try:
        cg.ACTIVE_SESSIONS[:] = [
            main_session, mini, other, mini_dock, kitchen_speaker
        ]
        assert cg._select_audio_sink(mini) is mini_dock
        assert cg._select_audio_sink(other) is kitchen_speaker
        assert cg._select_audio_sink(main_session) is main_session

        # Higher priority wins when two speakers target one parent.
        mini_dock_low = make_session(
            "speaker-mini-dock-low",
            role="speaker",
            parent=cg.HOMEAI_MINI_DEVICE_ID,
            priority=10,
        )
        cg.ACTIVE_SESSIONS.append(mini_dock_low)
        assert cg._select_audio_sink(mini) is mini_dock
    finally:
        cg.ACTIVE_SESSIONS[:] = old

    # Only primary companion can consume main notification queue.
    assert cg._reminder_session_available(main_session)
    assert not cg._reminder_session_available(mini)
    assert not cg._reminder_session_available(mini_dock)

    print("PASS: multi-device conversation and speaker routing")


if __name__ == "__main__":
    main()
