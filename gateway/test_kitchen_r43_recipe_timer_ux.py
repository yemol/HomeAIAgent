#!/usr/bin/env python3
from kitchen_menu import parse_kitchen_menu
from companion_gateway import _kitchen_compact_generated_steps, _kitchen_html

SAMPLE = """---
type: dinner-menu
schema: kitchen-menu-v1
date: 2026-09-17
menu:
  - name: 测试菜
    category: main
    cook_order: 1
---
## 🍳 四、烹饪步骤
### 1. 测试菜
#### 食材
- 肉 1份
#### 调味
- 盐 少许
#### 做法
1. 热锅。
2. 下油。
3. 放葱姜。
4. 下肉翻炒。
5. 加调料。
6. 加少量水。
7. 盖盖焖 8 分钟。
   - ⏱️ 计时：8分钟
8. 开盖翻匀。
9. 大火收汁。
10. 关火装盘。
#### 关键点
- 不要烧干。
"""

def main():
    menu=parse_kitchen_menu(SAMPLE, source='<r43>')
    r=menu['recipes'][0]
    # Prep + compacted cook steps; timed stage must remain independently timed.
    assert len(r['cook_steps']) <= 7
    assert any(t and t.get('default_sec') == 480 for t in r['cook_step_timers'])
    assert len(r['steps']) == len(r['cook_steps']) + 1
    src=['热锅','下油','放葱','下肉','调味','加水','翻炒','收汁','装盘']
    out=_kitchen_compact_generated_steps(src)
    assert len(out) <= 7
    assert all(any(part in '；'.join(out) for part in [x]) for x in src)
    html=_kitchen_html().decode('utf-8')
    assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in html
    assert 'timer-touch-main' in html
    assert 'min-height:66px' in html
    print('PASS test_kitchen_r43_recipe_timer_ux')

if __name__ == '__main__':
    main()
