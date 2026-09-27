#!/usr/bin/env python3
import tempfile
from pathlib import Path
import companion_gateway as g


def main():
    html = g._kitchen_html().decode('utf-8')
    assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in html
    assert 'foodRecommendedList' in html
    assert 'foodLogDate' in html and 'foodLogName' in html and 'foodLogList' in html
    assert 'foodShowAllBtn' in html and 'foodLogBtn' in html
    assert '推荐食用顺序' in html
    assert '需要留意' not in html
    assert '最近记录' not in html
    assert "mock-recipe-consume-button','✓ 完成这道菜'" not in html
    assert "mock-cook-menu-back','首页'" in html
    assert 'position:fixed!important;top:50%!important' in html
    assert '<svg viewBox="0 0 24 24"' in html
    assert '好好吃饭' not in html

    old_db = g.FOOD_DB_FILE
    try:
        with tempfile.TemporaryDirectory() as td:
            g.FOOD_DB_FILE = Path(td) / 'food.sqlite3'
            g._food_add('牛肉', 2, '份', '肉类', '菜市场')
            g._food_add('菠菜', 1, '份', '蔬菜', '菜市场')
            g._food_add('鸡蛋', 8, '个', '蛋类', '超市')
            g._food_set_priority('菠菜', '这两天')
            g._food_set_priority('牛肉', '本周')
            snap = g._food_snapshot()
            names = [x['name'] for x in snap['recommended']]
            assert names[0] == '菠菜', names
            assert '牛肉' in names and '鸡蛋' in names
            assert len(snap['events']) >= 5
    finally:
        g.FOOD_DB_FILE = old_db
    print('PASS test_kitchen_r46_food_manager_nav')


if __name__ == '__main__':
    main()
