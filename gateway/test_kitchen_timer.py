#!/usr/bin/env python3
from pathlib import Path

from kitchen_menu import parse_kitchen_menu, match_recipe
import companion_gateway as g


def main() -> int:
    sample = Path(__file__).resolve().parent / "_kitchen_timer_sample.md"
    if sample.exists():
        text = sample.read_text(encoding="utf-8")
    else:
        text = '''---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-13
menu:
  - name: 测试菜
    category: main
    cook_order: 1
---
## 🍳 四、烹饪步骤
### 1. 测试菜
#### 做法
1. 盖盖煮 15～20 分钟。
   - ⏱️ 计时：15分钟，可延长至20分钟
2. 翻炒 30～40 秒。
#### 关键点
- 测试
'''
    menu = parse_kitchen_menu(text)
    recipe = match_recipe(menu, "测试菜")
    assert recipe is not None
    assert recipe["step_timers"][0] is None
    assert recipe["step_kinds"][0] == "prep"
    assert recipe["step_timers"][1]["default_sec"] == 900
    assert recipe["step_timers"][1]["max_sec"] == 1200
    assert recipe["step_timers"][1]["source"] == "explicit"
    assert recipe["step_timers"][2]["default_sec"] == 30
    assert recipe["step_timers"][2]["max_sec"] == 40

    g.KITCHEN_CURRENT_MENU = menu
    g.KITCHEN_CURRENT_STATE = {
        "screen": "recipe",
        "date": "2026-09-13",
        "dish": "测试菜",
        "step": 1,
    }
    g.KITCHEN_TIMERS.clear()
    timer = g._kitchen_timer_start()
    assert timer.duration_sec == 900
    g._kitchen_timer_adjust(timer, 60)
    assert g._kitchen_timer_remaining(timer) >= 958
    g._kitchen_timer_pause(timer)
    assert timer.status == "paused"
    g._kitchen_timer_resume(timer)
    assert timer.status == "running"
    public = g._kitchen_timer_public(timer)
    assert public["dish"] == "测试菜"
    assert public["step"] == 1

    html = g._kitchen_html().decode("utf-8")
    assert "KitchenTerminal A3.0b" in html
    assert "homeai-kitchen/1.9" in html
    assert "timer_start" in html
    assert "备菜 · " in html

    g.KITCHEN_TIMERS.clear()
    g.save_kitchen_timers()
    print("KitchenTerminal A3.0b timer self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
