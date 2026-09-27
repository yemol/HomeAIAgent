from pathlib import Path
import tempfile
import companion_gateway as g

h = g._kitchen_html().decode('utf-8')
assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in h
assert "feature('🍴','选择今日菜谱'" in h
assert "feature('▰','食材管理'" in h
assert "feature('▤','今日采购清单'" in h
assert "sourceCard('inventory','▰','库存推荐菜'" in h
assert "sourceCard('private','♥','私房菜'" in h
assert "['all','▤','全部菜谱'" not in h
assert "active=String(v.active_source||'inventory')" in h
assert "hc.appendChild(el('h2','','今日菜谱'))" in h
assert '今天已选 ' not in h
assert '.mock-greeting{font-size:38px' in h
assert '.mock-feature-card strong{display:block;font-size:18px' in h

# Verify private recipe source reads the configured Obsidian directory recursively.
old_dir = g.KITCHEN_PRIVATE_RECIPE_DIR
old_parser = g._kitchen_parse_private_recipe
try:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sub = root / '家常菜'
        sub.mkdir()
        target = sub / '测试私房菜.md'
        target.write_text('# placeholder', encoding='utf-8')
        seen = []
        def fake_parser(path):
            seen.append(Path(path))
            if Path(path) == target:
                return {'name':'测试私房菜','type_label':'私房菜','ingredients':[],'seasoning':[],'prep_items':['准备'],'steps':['备菜','下锅炒熟'],'step_timers':[None,None],'key_points':[]}
            return None
        g.KITCHEN_PRIVATE_RECIPE_DIR = root
        g._kitchen_parse_private_recipe = fake_parser
        items = g._kitchen_recipe_library()
        assert target in seen
        assert any(x.get('name') == '测试私房菜' and x.get('source_kind') == 'private' for x in items)
finally:
    g.KITCHEN_PRIVATE_RECIPE_DIR = old_dir
    g._kitchen_parse_private_recipe = old_parser

print('KitchenTerminal R31 home + picker regression: PASS')
