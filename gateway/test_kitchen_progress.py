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
#### 做法
1. 第一步。
2. 第二步。
3. 第三步。
4. 第四步。
'''

async def main_async():
    menu = parse_kitchen_menu(SAMPLE)
    g.KITCHEN_CURRENT_MENU = menu
    g.KITCHEN_RECIPE_PROGRESS.clear()
    with tempfile.TemporaryDirectory() as td:
        g.KITCHEN_PROGRESS_STATE_FILE = Path(td) / 'progress.json'
        recipe = menu['recipes'][0]
        assert recipe['step_kinds'][0] == 'prep'
        assert len(recipe['steps']) == 5
        await g._kitchen_open_recipe(recipe, 3)
        assert g.KITCHEN_CURRENT_STATE['step'] == 3
        assert g.KITCHEN_PROGRESS_STATE_FILE.exists()
        await g._kitchen_show_menu(menu)
        payload = g.KITCHEN_CURRENT_VIEW
        assert payload['items'][0]['has_progress'] is True
        assert payload['items'][0]['progress_step'] == 3
        await g._kitchen_open_recipe(recipe, None)
        assert g.KITCHEN_CURRENT_STATE['step'] == 3
        g.KITCHEN_RECIPE_PROGRESS.clear()
        g.load_kitchen_progress()
        step, ok = g._kitchen_progress_get('2026-09-14','测试河虾',5)
        assert ok and step == 3
    print('KitchenTerminal A3.0b progress resume self-test: PASS')

if __name__ == '__main__':
    asyncio.run(main_async())
