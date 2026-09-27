#!/usr/bin/env python3
from pathlib import Path
import tempfile
import companion_gateway as g
from kitchen_menu import load_kitchen_menu

html=g._kitchen_html().decode('utf-8')
src=Path(g.__file__).read_text(encoding='utf-8')
assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in html
assert 'todayRemoveModal' in html and '确认移除' in html
assert 'window.confirm' not in src[src.find('function renderTodayMenu'):src.find('function renderPicker')]
assert 'kitchen-timer-mode' in html and '倒计时' in html and '正计时' in html
for label in ('1分钟','5分钟','10分钟','15分钟','30分钟','1小时'):
    assert label in html
assert 'mock-cook-layout' in html and 'mock-side-nav prev' in html and 'mock-side-nav next' in html
assert "mock-cook-actions{display:none!important}" in html

old_dir=g.KITCHEN_MENU_DIR; old_menu=g.KITCHEN_CURRENT_MENU
try:
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); g.KITCHEN_MENU_DIR=root; g.KITCHEN_CURRENT_MENU=None
        d=g._kitchen_today()
        # Simulate a partially parsed Obsidian menu: visible item exists, recipe is absent.
        menu={'date':d,'items':[{'name':'测试菜','category':'other','cook_order':1}], 'recipes':[], 'shopping':[], 'timeline':[]}
        # Serializer cannot preserve an item without a recipe, so write a minimal valid menu then
        # inject current menu to specifically exercise removal compatibility.
        g.KITCHEN_CURRENT_MENU=menu
        out=g._kitchen_remove_recipe_from_today('测试菜')
        assert out.get('items')==[]
        assert (root/f'{d}.md').exists()
        re=load_kitchen_menu(root,d)
        assert re.get('items')==[]
finally:
    g.KITCHEN_MENU_DIR=old_dir; g.KITCHEN_CURRENT_MENU=old_menu
print('KitchenTerminal R37 UI fixes regression: PASS')
