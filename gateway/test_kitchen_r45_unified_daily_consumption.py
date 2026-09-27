#!/usr/bin/env python3
import asyncio
import tempfile
from pathlib import Path

import companion_gateway as g


async def _fake_broadcast(payload):
    _fake_broadcast.last = payload
    return 1


async def _fake_speak(*args, **kwargs):
    return None


def main() -> None:
    html = g._kitchen_html().decode('utf-8')
    assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in html
    # Per-dish completion no longer interrupts with inventory consumption.
    assert '完成这道菜 · 登记消耗' not in html
    assert "mock-recipe-consume-button','✓ 完成这道菜'" not in html
    # Daily finish flow exposes unified inventory consumption.
    assert '今日食材结算' in html
    assert '确认食材消耗并继续' in html
    assert "day_consume_commit" in html
    assert "day_consume_skip" in html

    old_db = g.FOOD_DB_FILE
    old_menu = g.KITCHEN_CURRENT_MENU
    old_state = dict(g.KITCHEN_CURRENT_STATE)
    old_broadcast = g.kitchen_broadcast
    old_speak = g._kitchen_speak
    try:
        with tempfile.TemporaryDirectory() as td:
            g.FOOD_DB_FILE = Path(td) / 'food.sqlite3'
            g._food_add('排骨', 3, '份', '肉类', '菜市场')
            g._food_add('鸡蛋', 10, '个', '蛋类', '超市')
            g._food_add('高丽菜', 2, '份', '蔬菜', '菜市场')
            recipes = [
                {
                    'name': '糖醋排骨',
                    'ingredients': ['排骨 1份', '鸡蛋 2个'],
                    'steps': ['备菜', '烧熟'],
                    'step_kinds': ['prep', 'cook'],
                },
                {
                    'name': '高丽菜炒蛋',
                    'ingredients': ['高丽菜 半份', '鸡蛋 3个'],
                    'steps': ['备菜', '炒熟'],
                    'step_kinds': ['prep', 'cook'],
                },
            ]
            menu = {
                'date': '2026-09-17',
                'items': [{'name': '糖醋排骨'}, {'name': '高丽菜炒蛋'}],
                'recipes': recipes,
            }
            g.KITCHEN_CURRENT_MENU = menu
            g.KITCHEN_CURRENT_STATE = {'screen': 'finish', 'date': '2026-09-17', 'dish': '', 'step': 0}
            payload = g._kitchen_day_consumption_payload(menu)
            assert payload['scope'] == 'day'
            rows = {x['name']: x for x in payload['items']}
            assert rows['排骨']['suggested'] == 1
            assert rows['鸡蛋']['suggested'] == 5
            assert rows['高丽菜']['suggested'] == 0.5
            assert set(rows['鸡蛋']['dishes']) == {'糖醋排骨', '高丽菜炒蛋'}

            g.kitchen_broadcast = _fake_broadcast
            g._kitchen_speak = _fake_speak
            asyncio.run(g._kitchen_confirm_finish(speak=False))
            assert g.KITCHEN_CURRENT_STATE['screen'] == 'day_consumption'
            assert getattr(_fake_broadcast, 'last', {}).get('scope') == 'day'

            asyncio.run(g._kitchen_commit_day_consumption([
                {'name': '排骨', 'amount': 1},
                {'name': '鸡蛋', 'amount': 4},
                {'name': '高丽菜', 'amount': 0.5},
            ], speak=False))
            snap = {x['name']: x for x in g._food_snapshot()['items']}
            assert snap['排骨']['quantity'] == 2
            assert snap['鸡蛋']['quantity'] == 6
            assert snap['高丽菜']['quantity'] == 1.5
            assert g.KITCHEN_CURRENT_STATE['screen'] == 'save_private'
            assert getattr(_fake_broadcast, 'last', {}).get('type') == 'kitchen.show_save_private'
    finally:
        g.FOOD_DB_FILE = old_db
        g.KITCHEN_CURRENT_MENU = old_menu
        g.KITCHEN_CURRENT_STATE = old_state
        g.kitchen_broadcast = old_broadcast
        g._kitchen_speak = old_speak
    print('PASS test_kitchen_r45_unified_daily_consumption')


if __name__ == '__main__':
    main()
