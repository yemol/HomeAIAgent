#!/usr/bin/env python3
import tempfile
from pathlib import Path
import companion_gateway as g


def main() -> int:
    menu = {
        'date': '2026-09-17',
        'shopping': [
            {'name': '蔬菜', 'items': ['番茄 3个', '青椒 2个']},
            {'name': '肉类', 'items': ['鸡胸肉 500克']},
        ],
    }
    old_file = g.KITCHEN_SHOPPING_STATE_FILE
    try:
        with tempfile.TemporaryDirectory() as td:
            g.KITCHEN_SHOPPING_STATE_FILE = Path(td) / 'shopping.json'
            g.KITCHEN_SHOPPING_CHECKED.clear()
            payload = g._kitchen_shopping_payload(menu)
            first = payload['groups'][0]['items'][0]
            assert first['text'] == '番茄 3个'
            assert first['checked'] is False
            item_id = first['id']
            payload = g._kitchen_set_shopping_item(menu, item_id, True)
            assert payload['groups'][0]['items'][0]['checked'] is True
            assert g.KITCHEN_SHOPPING_STATE_FILE.exists()
            g.KITCHEN_SHOPPING_CHECKED.clear()
            g.load_kitchen_shopping_state()
            payload = g._kitchen_shopping_payload(menu)
            assert payload['groups'][0]['items'][0]['checked'] is True
            payload = g._kitchen_set_shopping_item(menu, item_id, False)
            assert payload['groups'][0]['items'][0]['checked'] is False

        html = g._kitchen_html().decode('utf-8')
        assert 'shopping_toggle' in html
        assert "ck.checked=!!(item&&item.checked)" in html
        assert '勾选后自动保存' in html
        assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in html
    finally:
        g.KITCHEN_SHOPPING_STATE_FILE = old_file
        g.KITCHEN_SHOPPING_CHECKED.clear()
    print('KitchenTerminal R39 shopping checklist persistence: PASS')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
