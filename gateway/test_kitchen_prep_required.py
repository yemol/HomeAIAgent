#!/usr/bin/env python3
"""Permanent regression guard for the KitchenTerminal prep-first rule."""
from kitchen_menu import parse_kitchen_menu, recipe_for

MENU = """---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-15
weekday: 周二
servings: 3
menu:
  - name: 清炒卷心菜
    category: vegetable
    cook_order: 1
---
## 🛒 一、超市购物单
- 卷心菜 半颗

## 📋 二、今晚菜单
1. **清炒卷心菜**

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
"""


def main() -> int:
    menu = parse_kitchen_menu(MENU, source="<prep-required>")
    recipe = recipe_for(menu, "清炒卷心菜")
    assert recipe is not None
    assert recipe.get("prep_required") is True
    assert recipe["step_kinds"][0] == "prep"
    assert recipe["step_timers"][0] is None
    assert recipe["steps"][0].startswith("开火前先完成这道菜的备菜")
    assert "卷心菜 半颗" in recipe["steps"][0]
    assert "盐 适量" in recipe["steps"][0]
    assert "卷心菜洗净切丝" in recipe["steps"][0]
    assert "蒜切末" in recipe["steps"][0]
    assert recipe["steps"][1].startswith("卷心菜洗净切丝")
    print("KitchenTerminal permanent prep-first rule: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
