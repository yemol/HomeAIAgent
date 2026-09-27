#!/usr/bin/env python3
from pathlib import Path
import tempfile
import companion_gateway as g
from kitchen_menu import load_kitchen_menu, recipe_for

old_menu_dir = g.KITCHEN_MENU_DIR
old_extra = g.KITCHEN_TODAY_EXTRA_FILE
old_menu = g.KITCHEN_CURRENT_MENU
old_cache = dict(g.KITCHEN_PICKER_RECOMMENDATIONS)
old_progress = {k: dict(v) for k, v in g.KITCHEN_RECIPE_PROGRESS.items()}
old_prep = {k: set(v) for k, v in g.KITCHEN_PREP_CHECKED.items()}
try:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        menu_dir = root / 'Obsidian' / '晚餐推荐'
        menu_dir.mkdir(parents=True)
        g.KITCHEN_MENU_DIR = menu_dir
        g.KITCHEN_TODAY_EXTRA_FILE = root / 'legacy_extra.json'
        g.KITCHEN_CURRENT_MENU = None
        g.KITCHEN_PICKER_RECOMMENDATIONS.clear()
        date_text = g._kitchen_today()
        recipe = g.ensure_recipe_prep_first({
            'name':'糖醋小排','type_label':'主菜','estimated_text':'约50分钟',
            'ingredients':['排骨 1份'],'seasoning':['料酒 1勺','糖 3勺','醋 4勺'],
            'prep_items':['排骨洗净沥干','姜切片'],
            'steps':['姜片爆香后下排骨炒至变色','加入料汁和开水，小火炖40分钟','大火收汁'],
            'step_timers':[None, {'default_sec':2400,'max_sec':2400,'label':'40分钟','source':'explicit'}, None],
            'key_points':['收汁时注意不要烧干']
        })
        g.KITCHEN_PICKER_RECOMMENDATIONS['air-r36'] = {
            'id':'air-r36','name':'糖醋小排','source_kind':'inventory_ai','source_label':'库存推荐',
            'source':'test','recipe':recipe,'recommendation_reason':'测试'
        }
        menu = g._kitchen_add_library_recipe_to_today('air-r36')
        path = menu_dir / f'{date_text}.md'
        assert path.exists(), 'Today Menu must be persisted in Obsidian'
        assert not g.KITCHEN_TODAY_EXTRA_FILE.exists(), 'No Gateway extra JSON should be created'
        text = path.read_text(encoding='utf-8')
        assert 'name: "糖醋小排"' in text or 'name: 糖醋小排' in text
        assert '### 1. 糖醋小排' in text
        assert '#### 备菜' in text
        assert '⏱️ 计时：40分钟' in text
        reloaded = load_kitchen_menu(menu_dir, date_text)
        assert recipe_for(reloaded, '糖醋小排') is not None
        assert [x['name'] for x in reloaded['items']] == ['糖醋小排']

        # Progress/prep state for a removed dish should be cleaned as well.
        g.KITCHEN_RECIPE_PROGRESS[date_text] = {'糖醋小排': 2}
        prep_id = g._kitchen_prep_task_id(date_text, '糖醋小排', 0, '排骨洗净沥干')
        g.KITCHEN_PREP_CHECKED[date_text] = {prep_id}
        removed = g._kitchen_remove_recipe_from_today('糖醋小排')
        assert removed['items'] == []
        reloaded2 = load_kitchen_menu(menu_dir, date_text)
        assert reloaded2['items'] == [] and reloaded2['recipes'] == []
        assert '糖醋小排' not in g.KITCHEN_RECIPE_PROGRESS.get(date_text, {})
        assert prep_id not in g.KITCHEN_PREP_CHECKED.get(date_text, set())

        # Legacy R20-R35 extra JSON is migrated into Obsidian once, then removed.
        legacy_recipe = g.ensure_recipe_prep_first({
            'name':'番茄炒蛋','type_label':'家常菜','estimated_text':'约15分钟',
            'ingredients':['番茄 2个','鸡蛋 3个'],'seasoning':['盐 适量'],
            'prep_items':['番茄切块','鸡蛋打散'],'steps':['炒鸡蛋盛出','炒番茄后回锅鸡蛋'],
            'step_timers':[None,None],'key_points':['不要炒老']
        })
        g.KITCHEN_TODAY_EXTRA_FILE.write_text(__import__('json').dumps({date_text:[{'name':'番茄炒蛋','category':'other','recipe':legacy_recipe}]},ensure_ascii=False),encoding='utf-8')
        g.KITCHEN_CURRENT_MENU = None
        migrated = g._kitchen_load(date_text)
        assert recipe_for(migrated, '番茄炒蛋') is not None
        assert not g.KITCHEN_TODAY_EXTRA_FILE.exists()

        html = g._kitchen_html().decode('utf-8')
        assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in html
        assert "action('today_remove'" in html
        assert "menu.remove_recipe\": \"today_remove" in Path(g.__file__).read_text(encoding='utf-8')
        assert 'mock-menu-remove' in html and '移除' in html
finally:
    g.KITCHEN_MENU_DIR = old_menu_dir
    g.KITCHEN_TODAY_EXTRA_FILE = old_extra
    g.KITCHEN_CURRENT_MENU = old_menu
    g.KITCHEN_PICKER_RECOMMENDATIONS.clear(); g.KITCHEN_PICKER_RECOMMENDATIONS.update(old_cache)
    g.KITCHEN_RECIPE_PROGRESS.clear(); g.KITCHEN_RECIPE_PROGRESS.update(old_progress)
    g.KITCHEN_PREP_CHECKED.clear(); g.KITCHEN_PREP_CHECKED.update(old_prep)

print('KitchenTerminal R36 Obsidian-only Today Menu regression: PASS')
