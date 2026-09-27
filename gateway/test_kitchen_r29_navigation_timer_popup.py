#!/usr/bin/env python3
import asyncio
from kitchen_menu import parse_kitchen_menu
import companion_gateway as g

SAMPLE = """---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-16
weekday: 周三
servings: 3
menu:
  - name: 测试菜
    category: side
    cook_order: 1
---
## 📋 二、今晚菜单
1. **测试菜**（小荤）
## 🛒 三、购物清单
### 测试采购
- 鸡蛋 3个
## 🍳 四、烹饪步骤
### 1. 测试菜
**类型**：小荤
#### 食材
- 鸡蛋 3个
#### 调味
- 盐 少许
#### 做法
1. 备菜。
2. 下锅。
"""

async def check_finish():
    menu = parse_kitchen_menu(SAMPLE)
    g.KITCHEN_CURRENT_MENU = menu
    g.KITCHEN_CURRENT_STATE = {'screen':'menu','date':'2026-09-16','dish':'','step':0}
    g.KITCHEN_RETURN_IDLE_AT = 123.0
    delivered, saved = await g._kitchen_finalize_day([], speak=False)
    assert saved == []
    assert g.KITCHEN_CURRENT_STATE['screen'] == 'idle'
    assert g.KITCHEN_RETURN_IDLE_AT == 0.0
    assert g.KITCHEN_CURRENT_VIEW.get('type') == 'kitchen.show_idle'

def check_html():
    h = g._kitchen_html().decode('utf-8')
    assert 'id="globalBottomNav"' in h
    for target in ('home','today','food','shopping'):
        assert f'data-global-nav="{target}"' in h
    assert "function renderShopping(v)" in h
    assert "if(v.type==='kitchen.show_shopping'){renderShopping(v);return;}" in h
    assert "function openStandaloneTimer()" in h
    assert 'id="standaloneTimerModal"' in h
    assert "feature('◴','厨房计时'" in h and 'openStandaloneTimer' in h
    assert 'homeTimerWrap' not in h
    assert "返回今日菜谱');menu.onclick" not in h
    assert "返回首页');hb.onclick" not in h
    assert 'R37 · 小K MENU REMOVE + TIMER MODAL + SIDE NAV' in h

if __name__ == '__main__':
    asyncio.run(check_finish())
    check_html()
    print('KitchenTerminal R29 global navigation + timer popup regression: PASS')
