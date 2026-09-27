#!/usr/bin/env python3
"""R50.7 regression: unified prep is strictly no-heat preparation."""
from kitchen_menu import parse_kitchen_menu, recipe_for
import companion_gateway as g

MENU = r'''---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-20
weekday: 周日
servings: 3
menu:
  - name: 青椒鸡片
    category: meat
    cook_order: 1
---
## 📋 二、今晚菜单
1. **青椒鸡片**

## 🥣 三、统一备菜
### 青椒鸡片
- 青椒洗净切丝，热锅下油翻炒至断生
- 鸡胸肉切片，腌制 10 分钟，随后下锅煎熟
- 生抽、盐和淀粉调成碗汁，倒入锅中翻炒收汁
- 烧开一锅水备用
- 西兰花洗净切小朵，焯水 30 秒，捞出沥干
- 烤箱预热至 180℃

## 🍳 四、烹饪步骤
### 1. 青椒鸡片
#### 食材
- 青椒 2个
- 鸡胸肉 1份
- 西兰花 1份
#### 调味
- 生抽 1汤匙
- 盐 适量
- 淀粉 1小勺
#### 做法
1. 烧开一锅水，西兰花焯水 30 秒后捞出沥干。
2. 热锅下油爆香蒜末，翻炒鸡片后加入青椒。
3. 加入碗汁勾芡收汁后出锅。
'''

FORBIDDEN = (
    "焯水", "焯一下", "汆烫", "汆水", "预热", "烧水", "烧开", "煮开", "烧沸",
    "热锅", "下油", "爆香", "翻炒", "煎熟", "勾芡", "收汁", "出锅",
)


def main() -> int:
    menu = parse_kitchen_menu(MENU, source='<r50.7-prep-no-heat>')
    recipe = recipe_for(menu, '青椒鸡片')
    assert recipe is not None
    items = recipe.get('prep_items') or []
    joined = '｜'.join(items)
    for word in FORBIDDEN:
        assert word not in joined, (word, items)

    assert any('青椒洗净切丝' in x for x in items), items
    assert any('鸡胸肉切片' in x for x in items), items
    assert any('腌制 10 分钟' in x for x in items), items
    assert any('碗汁' in x for x in items), items
    # Preserve the non-heating prefix from a mixed line, but drop the heat part.
    assert any('西兰花洗净切小朵' in x for x in items), items

    g.KITCHEN_PREP_CHECKED.clear()
    payload = g._kitchen_prep_payload(menu)
    tasks = [t['text'] for group in payload['groups'] for t in group['tasks']]
    task_text = '｜'.join(tasks)
    for word in FORBIDDEN:
        assert word not in task_text, (word, tasks)

    # The synthetic first recipe step is rebuilt from the sanitized prep list.
    step0 = recipe['steps'][0]
    for word in FORBIDDEN:
        assert word not in step0, (word, step0)

    # Heating actions remain in the actual cooking stages when present there.
    cook_text = '｜'.join(recipe.get('cook_steps') or [])
    assert '烧开一锅水' in cook_text and '焯水 30 秒' in cook_text, cook_text

    print('KitchenTerminal R50.7 prep no-heat boundary: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
