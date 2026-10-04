#!/usr/bin/env python3
"""A5.0.5 strict source-bound terminal/session/audio/timer routing audit."""

import asyncio
import inspect
import json

import companion_gateway as g


class DummyWS:
    async def send(self, _payload):
        return None


def make_companion(device_id: str):
    s = g.ClientSession(ws=DummyWS())
    g._apply_device_hello(s, {
        "type": "device.hello",
        "device_id": device_id,
        "device_role": "companion",
    })
    return s


def make_speaker(device_id: str, parent: str, priority: int = 100):
    s = g.ClientSession(ws=DummyWS())
    g._apply_device_hello(s, {
        "type": "device.hello",
        "device_id": device_id,
        "device_role": "speaker",
        "parent_device_id": parent,
        "audio_priority": priority,
        "capabilities": {"volume_control": True},
    })
    return s


async def main() -> None:
    main_agent = make_companion(g.HOMEAI_PRIMARY_DEVICE_ID)
    mini = make_companion(g.HOMEAI_MINI_DEVICE_ID)
    main_speaker = make_speaker("speaker-network-01", g.HOMEAI_PRIMARY_DEVICE_ID)
    dock = make_speaker("speaker-mini-dock-01", g.HOMEAI_MINI_DEVICE_ID)

    # 1) Three terminal voice sessions are distinct.
    assert main_agent.openclaw_user == g.OPENCLAW_USER
    assert mini.openclaw_user == g.OPENCLAW_MINI_USER
    assert g.OPENCLAW_KITCHEN_USER not in {g.OPENCLAW_USER, g.OPENCLAW_MINI_USER}
    assert g.OPENCLAW_KITCHEN_TOOL_USER not in {g.OPENCLAW_USER, g.OPENCLAW_MINI_USER, g.OPENCLAW_KITCHEN_USER}
    assert len({
        g._openclaw_voice_session_key(g.OPENCLAW_USER),
        g._openclaw_voice_session_key(g.OPENCLAW_MINI_USER),
        g._openclaw_voice_session_key(g.OPENCLAW_KITCHEN_USER),
    }) == 3

    # Core terminal mappings cannot be overridden or made to share a user via
    # the optional future-device JSON map.
    old_json = g.HOMEAI_DEVICE_OPENCLAW_USERS_JSON
    try:
        g.HOMEAI_DEVICE_OPENCLAW_USERS_JSON = json.dumps({
            g.HOMEAI_MINI_DEVICE_ID: g.OPENCLAW_USER,
            g.KITCHEN_DEVICE_ID: g.OPENCLAW_USER,
            "future-bad": g.OPENCLAW_MINI_USER,
            "future-good": "home-ai-future-good:main",
        })
        guarded = g._load_device_openclaw_users()
        assert guarded[g.HOMEAI_PRIMARY_DEVICE_ID] == g.OPENCLAW_USER
        assert guarded[g.HOMEAI_MINI_DEVICE_ID] == g.OPENCLAW_MINI_USER
        assert "future-bad" not in guarded
        assert guarded["future-good"] == "home-ai-future-good:main"
    finally:
        g.HOMEAI_DEVICE_OPENCLAW_USERS_JSON = old_json

    targets = {device_id: (session_key, user)
               for session_key, device_id, user in g._notification_listener_targets()}
    assert targets[g.HOMEAI_PRIMARY_DEVICE_ID][1] == g.OPENCLAW_USER
    assert targets[g.HOMEAI_MINI_DEVICE_ID][1] == g.OPENCLAW_MINI_USER
    assert targets[g.KITCHEN_DEVICE_ID][1] == g.OPENCLAW_KITCHEN_USER

    # 2) Prompt context cannot leak Kitchen into Main/Mini, and Mini cannot
    # inherit Main's Glass2 information context.
    context = {
        "current": {"category": "游戏", "headline": "MAIN_ONLY_HEADLINE", "item_id": "x"},
        "previous": {}, "next": {},
    }
    main_prompt = g.build_agent_prompt("这个讲讲", context, device_id=g.HOMEAI_PRIMARY_DEVICE_ID)
    mini_prompt = g.build_agent_prompt("这个讲讲", context, device_id=g.HOMEAI_MINI_DEVICE_ID)
    assert "MAIN_ONLY_HEADLINE" in main_prompt
    assert "MAIN_ONLY_HEADLINE" not in mini_prompt
    assert "KitchenTerminal 当前上下文" not in main_prompt
    assert "KitchenTerminal 当前上下文" not in mini_prompt

    # 3) Audio ownership: Mini -> Dock only. Main can select local or its own
    # NetworkSpeaker. A Kitchen-related phrase must not change this ownership.
    old_sessions = list(g.ACTIVE_SESSIONS)
    old_routes = dict(g.AUDIO_OUTPUT_ROUTES)
    try:
        g.ACTIVE_SESSIONS[:] = [main_agent, mini, main_speaker, dock]
        assert g._select_audio_sink(mini) is dock

        g.AUDIO_OUTPUT_ROUTES[g.HOMEAI_PRIMARY_DEVICE_ID] = "local"
        assert g._select_audio_sink(main_agent) is main_agent
        g.AUDIO_OUTPUT_ROUTES[g.HOMEAI_PRIMARY_DEVICE_ID] = "network"
        assert g._select_audio_sink(main_agent) is main_speaker
    finally:
        g.ACTIVE_SESSIONS[:] = old_sessions
        g.AUDIO_OUTPUT_ROUTES.clear()
        g.AUDIO_OUTPUT_ROUTES.update(old_routes)

    # 4) Companion terminals never enter or mutate Kitchen voice state, even
    # with an explicit “小K/厨房” prefix. This is the core source-domain firewall.
    old_menu = g.KITCHEN_CURRENT_MENU
    old_state = dict(g.KITCHEN_CURRENT_STATE)
    old_timers = dict(g.KITCHEN_TIMERS)
    try:
        g.KITCHEN_CURRENT_MENU = {
            "date": "2099-01-01",
            "items": [{"name": "测试菜"}],
            "recipes": [{
                "name": "测试菜",
                "steps": ["测试步骤"],
                "step_timers": [{"default_sec": 300, "max_sec": 600}],
            }],
        }
        g.KITCHEN_CURRENT_STATE.clear()
        g.KITCHEN_CURRENT_STATE.update({"screen": "recipe", "dish": "测试菜", "step": 0})
        g.KITCHEN_TIMERS.clear()

        for source in (main_agent, mini):
            for text in (
                "计时5分钟", "定时5分钟", "下一步",
                "厨房计时5分钟", "小K下一步", "小K结束今天的烹饪",
            ):
                assert await g._apply_kitchen_voice_command(source, text) is None, (source.device_id, text)
                assert not g._is_kitchen_related_utterance(text, session=source), (source.device_id, text)
        assert not g.KITCHEN_TIMERS

        # KitchenTerminal itself retains active recipe semantics.
        kitchen_session = g.KitchenSession(ws=DummyWS(), device_id="KitchenTerminal-iPadMini-test")
        recipe_timer_answer = await g._apply_kitchen_voice_command(kitchen_session, "计时5分钟")
        assert recipe_timer_answer and "测试菜" in recipe_timer_answer
        assert any(t.kind == "recipe" for t in g.KITCHEN_TIMERS.values())

        # On an idle Kitchen screen, a spoken timer is a Kitchen standalone timer.
        g.KITCHEN_TIMERS.clear()
        g.KITCHEN_CURRENT_MENU = None
        g.KITCHEN_CURRENT_STATE.clear()
        g.KITCHEN_CURRENT_STATE.update({"screen": "idle", "dish": "", "step": 0})
        standalone_answer = await g._apply_kitchen_voice_command(kitchen_session, "定时30秒")
        assert standalone_answer and "独立计时30秒" in standalone_answer
        assert len(g.KITCHEN_TIMERS) == 1
        timer = next(iter(g.KITCHEN_TIMERS.values()))
        assert timer.kind == "standalone" and timer.duration_sec == 30
    finally:
        g.KITCHEN_CURRENT_MENU = old_menu
        g.KITCHEN_CURRENT_STATE.clear()
        g.KITCHEN_CURRENT_STATE.update(old_state)
        g.KITCHEN_TIMERS.clear()
        g.KITCHEN_TIMERS.update(old_timers)

    # 5) Kitchen async reminders are a third notification domain and queue on
    # the Kitchen audio path, never a companion speaker.
    old_kitchen_sessions = list(g.KITCHEN_SESSIONS)
    original_speak = g._kitchen_speak
    captured = []

    async def fake_speak(text, *, kind="assistant", source_id=""):
        captured.append((text, kind, source_id))
        return {"event_id": "ka-test"}

    try:
        ks = g.KitchenSession(ws=DummyWS(), device_id="KitchenTerminal-iPadMini-test")
        ks.hello_received = True
        g.KITCHEN_SESSIONS[:] = [ks]
        g.KITCHEN_QA_STATE["status"] = "idle"
        g._kitchen_speak = fake_speak
        record = g.ReminderRecord(
            reminder_id="rem-kitchen-test",
            dedup_key="dedup-kitchen-test",
            text="厨房提醒测试",
            source_device_id=g.KITCHEN_DEVICE_ID,
            source_openclaw_user=g.OPENCLAW_KITCHEN_USER,
        )
        assert g._kitchen_reminder_available()
        await g._deliver_reminder_to_kitchen(record)
        assert captured == [("厨房提醒测试", "timer", "rem-kitchen-test")]
    finally:
        g._kitchen_speak = original_speak
        g.KITCHEN_SESSIONS[:] = old_kitchen_sessions
        g.KITCHEN_QA_STATE["status"] = "idle"

    # 6) Static guard: companion voice pipeline has no Kitchen command handler
    # and no Kitchen audio sink. This protects future refactors from reintroducing
    # semantic cross-routing.
    src = inspect.getsource(g.process_utterance)
    assert "_apply_kitchen_voice_command" not in src
    assert "KITCHEN_AUDIO_CACHE" not in src
    assert "kitchen-ipad" not in src
    assert "send_pcm_to_routed_sink" in src

    kitchen_chat_src = inspect.getsource(g.openclaw_kitchen_chat)
    assert "OPENCLAW_KITCHEN_USER" in kitchen_chat_src
    assert "_register_voice_reply_suppression" in kitchen_chat_src
    assert "_register_voice_turn_fence" in kitchen_chat_src

    inventory_src = inspect.getsource(g._kitchen_inventory_recommend_entries)
    assert "OPENCLAW_KITCHEN_TOOL_USER" in inventory_src
    assert '"user": OPENCLAW_KITCHEN_USER' not in inventory_src

    print("PASS: strict Main / Mini / Kitchen terminal-domain routing")


if __name__ == "__main__":
    asyncio.run(main())
