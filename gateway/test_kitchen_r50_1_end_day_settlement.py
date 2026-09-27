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
    assert 'A3.0b FIX1 R50.1 · 小K END-DAY FOOD SETTLEMENT' in html
    assert "r50-finish-day','✓ 结束今日厨房'" in html
    assert '进入今日食材结算' in html
    assert "isDay?'今日食材结算':'登记消耗'" in html
    assert "isDay?'确认食材消耗并继续'" in html
    assert "backDash=el('button','mock-cook-menu-back','厨房中台')" in html

    old_db = g.FOOD_DB_FILE
    old_menu = g.KITCHEN_CURRENT_MENU
    old_state = dict(g.KITCHEN_CURRENT_STATE)
    old_broadcast = g.kitchen_broadcast
    old_speak = g._kitchen_speak
    try:
        with tempfile.TemporaryDirectory() as td:
            g.FOOD_DB_FILE = Path(td) / 'food.sqlite3'
            g._food_add('排骨', 3, '份', '肉类', '菜市场')
            g._food_add('娃娃菜', 2, '份', '蔬菜', '菜市场')
            menu = {
                'date': '2026-09-19',
                'items': [{'name': '糖醋排骨'}, {'name': '醋溜娃娃菜'}],
                'recipes': [
                    {'name': '糖醋排骨', 'ingredients': ['排骨 1份'], 'steps': ['备菜', '烧熟'], 'step_kinds': ['prep', 'cook']},
                    {'name': '醋溜娃娃菜', 'ingredients': ['娃娃菜 1份'], 'steps': ['备菜', '炒熟'], 'step_kinds': ['prep', 'cook']},
                ],
            }
            g.KITCHEN_CURRENT_MENU = menu
            g.kitchen_broadcast = _fake_broadcast
            g._kitchen_speak = _fake_speak

            asyncio.run(g._kitchen_begin_finish(speak=False))
            assert g.KITCHEN_CURRENT_STATE['screen'] == 'finish'
            assert _fake_broadcast.last['type'] == 'kitchen.show_finish'

            asyncio.run(g._kitchen_confirm_finish(speak=False))
            assert g.KITCHEN_CURRENT_STATE['screen'] == 'day_consumption'
            assert _fake_broadcast.last['type'] == 'kitchen.show_consumption'
            assert _fake_broadcast.last['scope'] == 'day'
            assert _fake_broadcast.last['title'] == '今日食材结算'
            names = {x['name'] for x in _fake_broadcast.last['items']}
            assert {'排骨', '娃娃菜'} <= names

            asyncio.run(g._kitchen_commit_day_consumption([
                {'name': '排骨', 'amount': 1},
                {'name': '娃娃菜', 'amount': 1},
            ], speak=False))
            snap = {x['name']: x for x in g._food_snapshot()['items']}
            assert snap['排骨']['quantity'] == 2
            assert snap['娃娃菜']['quantity'] == 1
            assert g.KITCHEN_CURRENT_STATE['screen'] == 'save_private'
            assert _fake_broadcast.last['type'] == 'kitchen.show_save_private'
    finally:
        g.FOOD_DB_FILE = old_db
        g.KITCHEN_CURRENT_MENU = old_menu
        g.KITCHEN_CURRENT_STATE = old_state
        g.kitchen_broadcast = old_broadcast
        g._kitchen_speak = old_speak
    print('PASS test_kitchen_r50_1_end_day_settlement')


if __name__ == '__main__':
    main()
