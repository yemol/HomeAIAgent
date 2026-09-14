#!/usr/bin/env python3
import asyncio
import companion_gateway as g

PREFIXED_SAMPLES = {
    "厨房显示今天的菜单。": "menu.today",
    "逐光，厨房加载今日菜单": "menu.today",
    "厨房推送菜单。": "menu.today",
    "厨房推送今日菜单": "menu.today",
    "厨房同步菜单": "menu.today",
    "厨房显示购物清单": "menu.shopping",
    "厨房看看烧菜顺序": "menu.timeline",
    "厨房下一步": "step.next",
    "厨房刚才那一步": "step.prev",
    "厨房回到今天的菜单": "menu.back",
    "厨房结束今天的烹饪": "day.finish",
}

async def main() -> None:
    for text, expected in PREFIXED_SAMPLES.items():
        got = g._kitchen_fast_control_intent(text)
        assert got == expected, (text, got, expected)
        _, named = g._kitchen_voice_namespace(text)
        assert named, text

    async def fake_today():
        return 1, {"items": [{"name": "A"}, {"name": "B"}]}

    original = g._kitchen_show_today_menu
    g._kitchen_show_today_menu = fake_today
    try:
        # External HomeAI sources require the 厨房 domain prefix.
        answer = await g._apply_kitchen_voice_command(None, "厨房显示今天的菜单。")
        assert answer == "已经在厨房终端打开今天的菜单，共2道。", answer
        pushed = await g._apply_kitchen_voice_command(None, "厨房推送菜单。")
        assert pushed == "已经在厨房终端打开今天的菜单，共2道。", pushed
        unprefixed = await g._apply_kitchen_voice_command(None, "显示今天的菜单。")
        assert unprefixed is None, unprefixed

        # A KitchenTerminal source may omit the prefix; this is reserved for iPad PTT.
        session = g.ClientSession(ws=None)
        session.device_id = "KitchenTerminal-iPadMini-test"
        direct = await g._apply_kitchen_voice_command(session, "显示今天的菜单。")
        assert direct == "已经在厨房终端打开今天的菜单，共2道。", direct
    finally:
        g._kitchen_show_today_menu = original

    print("KitchenTerminal A3.0b FIX1 namespace + voice fast-path self-test: PASS")

if __name__ == "__main__":
    asyncio.run(main())
