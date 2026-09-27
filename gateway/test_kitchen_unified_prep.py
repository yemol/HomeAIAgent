#!/usr/bin/env python3
"""Regression guard for R21 unified multi-dish prep checklist."""
import tempfile
from pathlib import Path

from kitchen_menu import parse_kitchen_menu
import companion_gateway as g

MENU = '''---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-16
weekday: 周三
servings: 3
menu:
  - name: 清炒卷心菜
    category: vegetable
    cook_order: 1
  - name: 番茄炒蛋
    category: side
    cook_order: 2
---
## 📋 二、今晚菜单
1. **清炒卷心菜**
2. **番茄炒蛋**

## 🍳 四、烹饪步骤
### 1. 清炒卷心菜
#### 食材
- 卷心菜 半颗
- 蒜 2瓣
#### 调味
- 盐 适量
#### 做法
1. 卷心菜洗净切丝，蒜切末。
2. 热锅下油，爆香蒜末后下卷心菜翻炒。
3. 加盐调味后出锅。

### 2. 番茄炒蛋
#### 食材
- 番茄 2个
- 鸡蛋 3个
#### 调味
- 盐 适量
#### 做法
1. 番茄洗净切块，鸡蛋打散。
2. 先炒鸡蛋盛出。
3. 炒番茄后回锅鸡蛋调味。
'''


def main() -> int:
    menu = parse_kitchen_menu(MENU, source='<unified-prep>')
    assert len(menu['recipes']) == 2
    g.KITCHEN_RECIPE_PROGRESS.clear()
    g.KITCHEN_PREP_CHECKED.clear()
    with tempfile.TemporaryDirectory() as td:
        g.KITCHEN_PROGRESS_STATE_FILE = Path(td) / 'progress.json'
        g.KITCHEN_PREP_STATE_FILE = Path(td) / 'prep.json'
        payload = g._kitchen_prep_payload(menu)
        assert payload['type'] == 'kitchen.show_prep'
        assert len(payload['groups']) == 2
        assert payload['total'] >= 2
        assert payload['completed'] == 0
        ids = [t['id'] for group in payload['groups'] for t in group['tasks']]
        for task_id in ids:
            payload = g._kitchen_set_prep_task(menu, task_id, True)
        assert payload['all_done'] is True
        assert payload['completed'] == payload['total']
        for recipe in menu['recipes']:
            step, ok = g._kitchen_progress_get('2026-09-16', recipe['name'], len(recipe['steps']))
            assert ok and step == 1, (recipe['name'], step, ok)
        assert g.KITCHEN_PREP_STATE_FILE.exists()
        g.KITCHEN_PREP_CHECKED.clear()
        g.load_kitchen_prep_state()
        assert len(g.KITCHEN_PREP_CHECKED['2026-09-16']) == len(ids)
    print('KitchenTerminal R21 unified prep checklist: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
