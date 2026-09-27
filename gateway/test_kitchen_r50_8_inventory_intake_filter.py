#!/usr/bin/env python3
from pathlib import Path
import tempfile
import companion_gateway as g


def main() -> None:
    old_db = g.FOOD_DB_FILE
    with tempfile.TemporaryDirectory() as td:
        g.FOOD_DB_FILE = Path(td) / 'food.sqlite3'
        try:
            g._food_add('番茄', 2, '份', '蔬菜', '菜市场')
            g._food_add('鸡蛋', 6, '个', '蛋类', '超市')
            with g._food_db() as conn:
                conn.execute("UPDATE food_events SET created_at='2026-09-18T08:30:00+08:00' WHERE event_type='add' AND name='番茄'")
                conn.execute("UPDATE food_events SET created_at='2026-09-20T10:15:00+08:00' WHERE event_type='add' AND name='鸡蛋'")
            snap = g._food_snapshot()
            by_name = {x['name']: x for x in snap['items']}
            assert by_name['番茄']['last_added_at'].startswith('2026-09-18T08:30:00')
            assert by_name['鸡蛋']['last_added_at'].startswith('2026-09-20T10:15:00')

            html = g._kitchen_html().decode('utf-8')
            for token in ['foodInventoryIntakeDate', 'food-inventory-filter-row', 'food-inventory-sort-row', "sort==='intake'", 'it.last_added_at']:
                assert token in html, token
            assert '<option value="intake">最近入库</option>' in html
            assert 'A3.0b FIX1 R50.8 · 小K INVENTORY INTAKE DATE FILTER' in html
        finally:
            g.FOOD_DB_FILE = old_db
    print('PASS test_kitchen_r50_8_inventory_intake_filter')


if __name__ == '__main__':
    main()
