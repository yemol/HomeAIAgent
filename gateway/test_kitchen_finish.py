#!/usr/bin/env python3
import asyncio
import tempfile
from pathlib import Path

from kitchen_menu import parse_kitchen_menu
import companion_gateway as g

SAMPLE = '''---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-14
weekday: 周一
servings: 3
menu:
  - name: 测试河虾
    category: side
    cook_order: 1
---
## 📋 二、今晚菜单
1. **测试河虾**（小荤）

## 🍳 四、烹饪步骤
### 1. 测试河虾
**类型**：小荤
**预计用时**：约 8 分钟
#### 食材
- 河虾 300g
- 姜 4片
#### 调味
- 盐 1小勺
#### 做法
1. 水烧开。
2. 河虾下锅煮 30～40 秒。
   - ⏱️ 计时：30秒，可延长至40秒
#### 关键点
- 不要煮老。
'''

async def main_async() -> None:
    menu = parse_kitchen_menu(SAMPLE)
    g.KITCHEN_CURRENT_MENU = menu
    g.KITCHEN_CURRENT_STATE = {"screen":"menu","date":"2026-09-14","dish":"","step":0}
    g.KITCHEN_TIMERS.clear()
    with tempfile.TemporaryDirectory() as td:
        g.KITCHEN_PRIVATE_RECIPE_DIR = Path(td) / "私房菜"
        await g._kitchen_begin_finish(speak=False)
        assert g.KITCHEN_CURRENT_STATE["screen"] == "finish"
        await g._kitchen_confirm_finish(speak=False)
        assert g.KITCHEN_CURRENT_STATE["screen"] == "save_private"
        _, saved = await g._kitchen_finalize_day(["测试河虾"], speak=False)
        assert saved == ["测试河虾"]
        files = list(g.KITCHEN_PRIVATE_RECIPE_DIR.glob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "schema: kitchen-private-recipe-v1" in text
        assert "# 🍳 私房菜 · 测试河虾" in text
        assert "⏱️ 计时：30秒，可延长至40秒" in text
        assert g.KITCHEN_CURRENT_STATE["screen"] == "done"
        assert g.KITCHEN_CURRENT_MENU is None
        assert g.KITCHEN_RETURN_IDLE_AT > 0
        g.KITCHEN_RETURN_IDLE_AT = 1
        assert g._kitchen_maybe_return_idle() is True
        assert g.KITCHEN_CURRENT_STATE["screen"] == "idle"
    html = g._kitchen_html().decode("utf-8")
    assert "KitchenTerminal A3.0b" in html
    assert "结束今日烹饪" in html
    assert "保存所选并结束" in html
    assert "timer_dismiss" in html
    assert "audio_ack" in html
    print("KitchenTerminal A3.0b finish/private-recipe self-test: PASS")

if __name__ == "__main__":
    asyncio.run(main_async())
