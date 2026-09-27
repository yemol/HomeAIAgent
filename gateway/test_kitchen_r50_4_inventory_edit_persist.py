#!/usr/bin/env python3
import asyncio, json, tempfile
from pathlib import Path
from urllib.parse import quote
import companion_gateway as g

class Req:
    def __init__(self,path):
        self.path=path; self.headers={}

async def main():
    old=g.FOOD_DB_FILE
    try:
        with tempfile.TemporaryDirectory() as td:
            g.FOOD_DB_FILE=Path(td)/'food.sqlite3'
            g._food_add('西红市',4,'个','蔬菜','菜市场')
            snap=g._food_snapshot(); item=snap['items'][0]
            assert isinstance(item['id'], int)
            path='/kitchen/food/action?action=edit&item_id=%s&name=%s&new_name=%s&quantity=3' % (item['id'],quote('错误旧名'),quote('西红柿'))
            resp=await g.gateway_http_request(None,Req(path))
            assert resp.status_code==200, resp.status_code
            data=json.loads(resp.body.decode())
            assert data['edited']['id']==item['id']
            assert data['edited']['name']=='西红柿'
            snap2=g._food_snapshot()
            names={x['name']:x for x in snap2['items']}
            assert '西红市' not in names
            assert names['西红柿']['quantity']==3
            # Direct independent DB read proves commit persisted after request returns.
            with g._food_db() as conn:
                row=conn.execute('SELECT name,quantity FROM food_items WHERE id=?',(item['id'],)).fetchone()
                assert row['name']=='西红柿' and float(row['quantity'])==3
        h=g._kitchen_html().decode('utf-8')
        assert 'R50.4' in h
        assert 'params.item_id=foodEditItem.id' in h
        assert 'data&&data.edited' in h
    finally:
        g.FOOD_DB_FILE=old

if __name__=='__main__':
    asyncio.run(main())
    print('PASS test_kitchen_r50_4_inventory_edit_persist')
