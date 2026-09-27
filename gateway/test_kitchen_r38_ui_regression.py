#!/usr/bin/env python3
from pathlib import Path
import tempfile
import companion_gateway as g
from kitchen_menu import load_kitchen_menu

html = g._kitchen_html().decode('utf-8')
src = Path(g.__file__).read_text(encoding='utf-8')
assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in html
# Timer modal must have real render logic, including the helper that R37 accidentally lost.
assert 'function standaloneTimer(){' in html
assert 'function renderIdleStandaloneTimer()' in html
assert 'idleTimerHost' in html
for label in ('倒计时','正计时','1分钟','5分钟','10分钟','15分钟','30分钟','1小时'):
    assert label in html
# Delete success must close modal; duplicate delete is idempotent.
assert 'closeTodayRemoveModal();setTimeout(poll,60);' in html
assert 'Today Menu remove already absent' in src
# Side navigation is narrow but vertically longer, content stays in the middle.
assert 'height:clamp(118px,19vh,168px)' in html
assert 'grid-template-columns:48px minmax(0,1fr) 48px' in html
# Home card order and global nav order: shopping before food.
assert html.index("feature('▤','今日采购清单'") < html.index("feature('▰','食材管理'")
nav = html[html.index('<nav id="globalBottomNav"'):html.index('</nav>', html.index('<nav id="globalBottomNav"'))]
assert nav.index('data-global-nav="shopping"') < nav.index('data-global-nav="food"')

old_dir=g.KITCHEN_MENU_DIR; old_menu=g.KITCHEN_CURRENT_MENU
try:
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); g.KITCHEN_MENU_DIR=root; g.KITCHEN_CURRENT_MENU=None
        d=g._kitchen_today()
        menu={'date':d,'items':[{'name':'测试菜','category':'other','cook_order':1}], 'recipes':[], 'shopping':[], 'timeline':[]}
        g.KITCHEN_CURRENT_MENU=menu
        out=g._kitchen_remove_recipe_from_today('测试菜')
        assert out.get('items')==[]
        # Duplicate tap/retry should be a successful no-op, not an error popup.
        out2=g._kitchen_remove_recipe_from_today('测试菜')
        assert out2.get('items')==[]
        assert (root/f'{d}.md').exists()
        re=load_kitchen_menu(root,d)
        assert re.get('items')==[]
finally:
    g.KITCHEN_MENU_DIR=old_dir; g.KITCHEN_CURRENT_MENU=old_menu
print('KitchenTerminal R39 UI regression: PASS')
