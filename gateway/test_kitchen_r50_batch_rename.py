#!/usr/bin/env python3
import tempfile
from pathlib import Path
import companion_gateway as g


def test_batch_normalize_merge():
    r = g._food_scan_rows_to_result({
        'items': [
            {'name':'番茄','category':'蔬菜','mode':'quantity','amount':2,'unit':'个','raw':'番茄A','confidence':.91},
            {'name':'鸡蛋','category':'蛋类','mode':'quantity','amount':10,'unit':'个','raw':'鸡蛋10枚','confidence':.98},
            {'name':'番茄','category':'蔬菜','mode':'quantity','amount':2,'unit':'个','raw':'番茄B','confidence':.87},
            {'name':'牛排','category':'肉类','mode':'quantity','amount':2,'unit':'份','raw':'西冷牛排','confidence':.86},
        ]
    }, '菜市场')
    assert len(r['items']) == 3, r
    by_name = {x['name']: x for x in r['items']}
    assert by_name['番茄']['amount'] == 4
    assert by_name['鸡蛋']['amount'] == 10
    assert by_name['牛排']['amount'] == 2


def test_rename_inventory():
    old = g.FOOD_DB_FILE
    with tempfile.TemporaryDirectory() as td:
        try:
            g.FOOD_DB_FILE = Path(td) / 'food.sqlite3'
            g._food_add('西红市', 4, '个', '蔬菜', '菜市场')
            g._food_rename('西红市', '西红柿')
            snap = g._food_snapshot()
            names = [x['name'] for x in snap['items']]
            assert '西红柿' in names and '西红市' not in names
            renames = [x for x in snap['events'] if x['event_type'] == 'rename']
            assert renames and renames[0]['note'] == '西红市 → 西红柿'
            g._food_add('番茄', 1, '份', '蔬菜', '菜市场')
            try:
                g._food_rename('西红柿', '番茄')
            except ValueError as exc:
                assert '同名' in str(exc)
            else:
                raise AssertionError('rename collision should fail')
        finally:
            g.FOOD_DB_FILE = old


def test_ui_contract():
    h = g._kitchen_html().decode('utf-8')
    for token in ['A3.0b FIX1 R50.2 · 小K INVENTORY EDIT FIX', '厨房中台', 'foodScanAddMissing', '＋ 补一项', "foodAction('edit'", "food-inventory-rename','编辑", 'foodEditModal']:
        assert token in h, token
    assert '烹饪驾驶舱' not in h


def test_batch_prompt_contract():
    s = Path(__file__).with_name('companion_gateway.py').read_text(encoding='utf-8')
    assert '这是“批量入库”识别' in s
    assert '不要识别到一个就停止' in s
    assert '最多返回 20 种不同食品/食材' in s


if __name__ == '__main__':
    test_batch_normalize_merge()
    test_rename_inventory()
    test_ui_contract()
    test_batch_prompt_contract()
    print('PASS test_kitchen_r50_batch_rename')
