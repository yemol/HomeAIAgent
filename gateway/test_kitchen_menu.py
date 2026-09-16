#!/usr/bin/env python3
from pathlib import Path
import sys

from kitchen_menu import parse_kitchen_menu, match_recipe

SAMPLE = """---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-14
weekday: 周一
servings: 3
estimated_minutes: 60
menu:
  - name: 糖醋小排
    category: main
    cook_order: 1
  - name: 白菜肉丸粉丝汤
    category: soup
    cook_order: 2
---
## 🛒 一、超市购物单
### 肉类
- 排骨 500g
- 肉丸 250g

## 📋 二、今晚菜单
1. **糖醋小排**（主菜）
2. **白菜肉丸粉丝汤**（汤）

## 🔪 三、提前备菜
- 排骨洗净。

## 🍳 四、烹饪步骤
### 1. 糖醋小排
**类型**：主菜
**预计用时**：约 50 分钟
#### 食材
- 排骨 500g
#### 调味
- 料酒 1勺
#### 做法
1. 大火炒至变色。
2. 加水后小火炖 40 分钟。
   - ⏱️ 计时：40分钟
#### 关键点
- 收汁不要烧干。

### 2. 白菜肉丸粉丝汤
**类型**：汤
**预计用时**：约 15 分钟
#### 食材
- 白菜 300g
- 肉丸 250g
#### 备菜
- 白菜洗净切段。
- 粉丝提前泡软。
#### 做法
1. 水开后下肉丸。
2. 加白菜和粉丝煮熟。
#### 关键点
- 粉丝最后放。

## ⏱️ 五、省事操作时间线
1. 先炖小排。
2. 小排炖煮时准备汤。
"""


NO_PREP_SAMPLE = """---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-15
weekday: 周二
servings: 3
menu:
  - name: 番茄炒蛋
    category: vegetable
    cook_order: 1
---
## 🛒 一、超市购物单
- 番茄 2个
- 鸡蛋 3个

## 📋 二、今晚菜单
1. **番茄炒蛋**

## 🍳 四、烹饪步骤
### 1. 番茄炒蛋
#### 食材
- 番茄 2个
- 鸡蛋 3个
#### 调味
- 盐 适量
#### 做法
1. 番茄洗净切块，鸡蛋打散。
2. 炒鸡蛋后盛出，再炒番茄并回锅。

## ⏱️ 五、省事操作时间线
1. 先做番茄炒蛋。
"""

def main() -> int:
    if len(sys.argv) > 2:
        print("usage: python test_kitchen_menu.py [/path/to/menu.md]")
        return 2
    if len(sys.argv) == 2:
        path = Path(sys.argv[1])
        text = path.read_text(encoding="utf-8")
        source = str(path)
    else:
        text = SAMPLE
        source = "<built-in-sample>"
    menu = parse_kitchen_menu(text, source=source)
    assert menu["schema"] == "kitchen-menu-v1"
    assert len(menu["items"]) >= 2
    assert len(menu["recipes"]) >= 2
    ribs = match_recipe(menu, "小排")
    assert ribs and "糖醋小排" in ribs["name"]
    assert len(ribs["steps"]) == 3
    assert ribs["step_kinds"] == ["prep", "cook", "cook"]
    assert ribs["step_timers"][0] is None
    assert "【食材】排骨 500g" in ribs["steps"][0]
    assert "【调味】料酒 1勺" in ribs["steps"][0]
    assert "排骨洗净" in ribs["steps"][0]
    soup = match_recipe(menu, "白菜肉丸")
    assert soup and "白菜洗净切段" in soup["steps"][0]
    assert "粉丝提前泡软" in soup["steps"][0]
    assert menu["shopping"]
    assert menu["timeline"]

    # Permanent project invariant: even a legacy/newly generated menu which
    # completely omits the prep section must still expose prep as step 0.
    no_prep = parse_kitchen_menu(NO_PREP_SAMPLE, source="<no-prep-sample>")
    tomato = match_recipe(no_prep, "番茄炒蛋")
    assert tomato is not None
    assert tomato["step_kinds"][0] == "prep"
    assert tomato["step_timers"][0] is None
    assert "备菜" in tomato["steps"][0]
    assert "番茄 2个" in tomato["steps"][0]
    assert "盐 适量" in tomato["steps"][0]
    assert "番茄洗净切块" in tomato["steps"][0]
    assert no_prep["prep_policy"] == "required-v1"

    # A stale recipe object must be normalized again by recipe_for/match_recipe.
    stale = {
        "recipes": [{
            "name": "测试菜",
            "ingredients": ["豆腐 1盒"],
            "seasoning": ["盐 少许"],
            "prep_items": [],
            "steps": ["下锅煮熟。"],
            "step_timers": [None],
            "step_kinds": ["cook"],
        }]
    }
    repaired = match_recipe(stale, "测试菜")
    assert repaired is not None
    assert repaired["step_kinds"][0] == "prep"
    assert len(repaired["steps"]) == 2

    print("Kitchen menu parser regression: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
