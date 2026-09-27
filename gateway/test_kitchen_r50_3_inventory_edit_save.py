#!/usr/bin/env python3
import asyncio
import json
import tempfile
from pathlib import Path
from urllib.parse import quote
import companion_gateway as g

class Req:
    def __init__(self, path):
        self.path = path
        self.headers = {}

async def main_async():
    old = g.FOOD_DB_FILE
    try:
        with tempfile.TemporaryDirectory() as td:
            g.FOOD_DB_FILE = Path(td) / 'food.sqlite3'
            g._food_add('西红市', 4, '个', '蔬菜', '菜市场')
            path = '/kitchen/food/action?action=edit&name=%s&new_name=%s&quantity=2&_=%s' % (
                quote('西红市'), quote('西红柿'), '1')
            resp = await g.gateway_http_request(None, Req(path))
            assert resp.status_code == 200
            payload = json.loads(resp.body.decode('utf-8'))
            by = {x['name']: x for x in payload['items']}
            assert '西红市' not in by
            assert by['西红柿']['quantity'] == 2

        h = g._kitchen_html().decode('utf-8')
        for token in ['R50.3', "btn.textContent='保存中…'", 'x.timeout=12000', "if(done)done(null,data);try{renderFood();}", 'loadFood();']:
            assert token in h, token
    finally:
        g.FOOD_DB_FILE = old

if __name__ == '__main__':
    asyncio.run(main_async())
    print('PASS test_kitchen_r50_3_inventory_edit_save')
