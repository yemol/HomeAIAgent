from pathlib import Path
import asyncio
import tempfile
import companion_gateway as g

h = g._kitchen_html().decode('utf-8')
assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in h
assert "'🍴 获取今日菜谱  →'" in h
assert "primary.onclick=function(){action('today');}" in h
assert "feature('🍴','选择今日菜谱'" in h
assert "sourceCard('inventory','▰','库存推荐菜'" in h
assert "sourceCard('private','♥','私房菜'" in h
assert '根据所选食材推荐菜谱' in h
assert "action('inventory_recommend'" in h
assert "['all','▤','全部菜谱'" not in h

old_menu_dir = g.KITCHEN_MENU_DIR
old_private_dir = g.KITCHEN_PRIVATE_RECIPE_DIR
old_menu = g.KITCHEN_CURRENT_MENU
old_food_snapshot = g._food_snapshot
old_broadcast = g.kitchen_broadcast
old_recommend = g._kitchen_inventory_recommend_entries
try:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        menu_dir = root / 'menus'; menu_dir.mkdir()
        private_dir = root / 'private'; private_dir.mkdir()
        g.KITCHEN_MENU_DIR = menu_dir
        g.KITCHEN_PRIVATE_RECIPE_DIR = private_dir
        g.KITCHEN_CURRENT_MENU = None
        g._food_snapshot = lambda: {
            'items': [
                {'name':'排骨','category':'肉类','quantity':2,'unit':'份'},
                {'name':'鸡蛋','category':'蛋类','quantity':8,'unit':'个'},
                {'name':'酱油','category':'佐料','quantity':0,'unit':'状态','status':'充足'},
            ]
        }
        sent = []
        async def fake_broadcast(payload):
            sent.append(payload)
            return 1
        g.kitchen_broadcast = fake_broadcast

        # Picker must open even when there is no menu file for today.
        asyncio.run(g._kitchen_show_picker())
        assert sent and sent[-1]['type'] == 'kitchen.show_picker'
        assert [x['name'] for x in sent[-1]['inventory_items']] == ['排骨','鸡蛋']
        assert sent[-1]['recommended'] == []

        async def fake_recommend(selected):
            assert selected == ['排骨']
            recipe = g.ensure_recipe_prep_first({
                'name':'糖醋小排','type_label':'主菜','estimated_text':'约50分钟',
                'ingredients':['排骨 1份'],'seasoning':['糖','醋'],
                'prep_items':['排骨洗净'],'steps':['煎至变色','加料汁炖煮收汁'],
                'step_timers':[None,None],'key_points':['最后注意收汁']
            })
            return [{'id':'air-test','name':'糖醋小排','source_kind':'inventory_ai','source_label':'库存推荐','source':'test','recipe':recipe,'recommendation_reason':'优先消耗排骨'}]
        g._kitchen_inventory_recommend_entries = fake_recommend
        sent.clear()
        asyncio.run(g._kitchen_show_inventory_recommendations(['排骨']))
        payload = sent[-1]
        assert payload['inventory_selected'] == ['排骨']
        assert payload['recommended'][0]['name'] == '糖醋小排'
        assert payload['recommended'][0]['recommendation_reason'] == '优先消耗排骨'
finally:
    g.KITCHEN_MENU_DIR = old_menu_dir
    g.KITCHEN_PRIVATE_RECIPE_DIR = old_private_dir
    g.KITCHEN_CURRENT_MENU = old_menu
    g._food_snapshot = old_food_snapshot
    g.kitchen_broadcast = old_broadcast
    g._kitchen_inventory_recommend_entries = old_recommend

print('KitchenTerminal R31 picker two-step regression: PASS')
