#!/usr/bin/env python3
import tempfile
from pathlib import Path
import companion_gateway as g


def main():
    old = g.FOOD_DB_FILE
    try:
        with tempfile.TemporaryDirectory() as td:
            g.FOOD_DB_FILE = Path(td) / 'food.sqlite3'
            g._food_add('西红市', 4, '个', '蔬菜', '菜市场')
            g._food_edit('西红市', '西红柿', 2)
            snap = g._food_snapshot()
            by = {x['name']: x for x in snap['items']}
            assert '西红市' not in by
            assert by['西红柿']['quantity'] == 2
            ev = snap['events']
            assert any(x['event_type']=='rename' and '西红市 → 西红柿' in x['note'] for x in ev)
            assert any(x['event_type']=='adjust' and '4 个 → 2 个' in x['note'] for x in ev)

            # Absolute correction to zero removes it from active inventory but preserves audit log.
            g._food_edit('西红柿', '西红柿', 0)
            snap2 = g._food_snapshot()
            assert '西红柿' not in {x['name'] for x in snap2['items']}
            assert any(x['event_type']=='adjust' and '2 个 → 0 个' in x['note'] for x in snap2['events'])

            # Status-managed items can be renamed without numeric conversion.
            g._food_set_status('料洒', '一般')
            g._food_edit('料洒', '料酒', None)
            by3 = {x['name']: x for x in g._food_snapshot()['items']}
            assert by3['料酒']['unit'] == '状态'

            # Collision protection remains intact.
            g._food_add('番茄', 1, '份', '蔬菜', '菜市场')
            try:
                g._food_edit('料酒', '番茄', None)
            except ValueError as exc:
                assert '同名' in str(exc)
            else:
                raise AssertionError('collision should fail')

        h = g._kitchen_html().decode('utf-8')
        for token in ['R50.3', 'foodEditModal', '编辑食材', "foodAction('edit'", '当前库存数量', 'private-save-page']:
            assert token in h, token
        assert "window.prompt('修改食材名称'" not in h
    finally:
        g.FOOD_DB_FILE = old
    print('PASS test_kitchen_r50_2_inventory_edit')

if __name__ == '__main__':
    main()
