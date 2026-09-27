#!/usr/bin/env python3
import asyncio
import base64
import io
import json
from PIL import Image

import companion_gateway as g


def test_image_prepare():
    im = Image.new('RGB', (2600, 1800), 'white')
    bio = io.BytesIO(); im.save(bio, 'PNG')
    data, mime = g._food_scan_image_payload(bio.getvalue())
    assert mime == 'image/jpeg'
    assert data.startswith(b'\xff\xd8')
    with Image.open(io.BytesIO(data)) as out:
        assert max(out.size) <= g.FOOD_SCAN_MAX_SIDE


def test_normalize():
    a = g._food_scan_normalize_item({'name':'澳洲牛腩','category':'肉类','amount':2,'unit':'份','confidence':.9}, '网上APP')
    assert a['mode']=='quantity' and a['amount']==2 and a['unit']=='份'
    b = g._food_scan_normalize_item({'name':'料酒','category':'佐料/粮油','amount':1,'unit':'瓶'}, '超市')
    assert b['mode']=='status' and b['unit']=='状态' and b['status']=='充足'


def test_json_extract():
    x = g._food_scan_extract_json('```json\n{"items":[{"name":"鸡蛋"}]}\n```')
    assert x['items'][0]['name']=='鸡蛋'


class FakeResponse:
    def json(self):
        return {'choices':[{'message':{'content':json.dumps({'items':[{'name':'鸡蛋','category':'蛋类','mode':'quantity','amount':12,'unit':'个','raw':'鲜鸡蛋12枚','confidence':.99}]}, ensure_ascii=False)}}]}

class FakeClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False

async def test_openclaw_payload():
    old_client=g._openclaw_http_client
    old_post=g._post_with_retry
    old_cleanup=g._cleanup_openclaw_info_session
    captured={}
    def fake_client(timeout): return FakeClient()
    async def fake_post(client,url,**kwargs):
        captured['payload']=kwargs['json']
        return FakeResponse()
    async def fake_cleanup(*args,**kwargs): return {'status':'deleted'}
    g._openclaw_http_client=fake_client
    g._post_with_retry=fake_post
    g._cleanup_openclaw_info_session=fake_cleanup
    try:
        im=Image.new('RGB',(200,120),'white'); bio=io.BytesIO(); im.save(bio,'JPEG')
        result=await g._food_scan_with_openclaw(bio.getvalue(),source='网上APP')
        assert result['items'][0]['name']=='鸡蛋'
        content=captured['payload']['messages'][0]['content']
        assert content[0]['type']=='text'
        assert content[1]['type']=='image_url'
        assert content[1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    finally:
        g._openclaw_http_client=old_client
        g._post_with_retry=old_post
        g._cleanup_openclaw_info_session=old_cleanup


def test_ui():
    h=g._kitchen_html().decode('utf-8')
    for token in ['拍照 / 选择图片','全部确认入库','kitchen.food.scan.start','foodScanResults']:
        assert token in h, token

if __name__=='__main__':
    test_image_prepare(); test_normalize(); test_json_extract(); asyncio.run(test_openclaw_payload()); test_ui()
    print('PASS test_kitchen_food_photo_scan_r28')
