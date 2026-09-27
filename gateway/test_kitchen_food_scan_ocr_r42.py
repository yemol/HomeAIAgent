#!/usr/bin/env python3
import asyncio, io
from PIL import Image
import companion_gateway as g


def test_long_order_screenshot_preserves_text_resolution():
    im=Image.new('RGB',(1200,6000),'white')
    bio=io.BytesIO(); im.save(bio,'JPEG',quality=80)
    data=g._food_scan_ocr_image_payload(bio.getvalue())
    with Image.open(io.BytesIO(data)) as out:
        assert out.size[0] >= 1100, out.size
        assert out.size[1] >= 5000, out.size


def test_swift_helper_shipped():
    p=g.BASE_DIR/'food_ocr.swift'
    text=p.read_text(encoding='utf-8')
    assert 'VNRecognizeTextRequest' in text
    assert 'zh-Hans' in text and 'zh-Hant' in text


async def test_online_app_prefers_ocr_pipeline():
    old_ocr=g._food_scan_macos_ocr
    old_parse=g._food_scan_from_ocr_text
    old_vision=g._food_scan_from_vision
    calls=[]
    async def fake_ocr(image):
        calls.append('ocr')
        return '订单\n鲜鸡蛋12枚 x1\n澳洲牛腩480g x1\n配送费 5元'
    async def fake_parse(text, *, source):
        calls.append('parse')
        return {'ok':True,'items':[{'name':'鸡蛋','category':'蛋类','mode':'quantity','amount':12,'unit':'个','status':'','source':source,'raw':'鲜鸡蛋12枚','confidence':.99}], 'note':''}
    async def fake_vision(*args, **kwargs):
        raise AssertionError('online APP should not call vision when OCR succeeds')
    g._food_scan_macos_ocr=fake_ocr
    g._food_scan_from_ocr_text=fake_parse
    g._food_scan_from_vision=fake_vision
    try:
        im=Image.new('RGB',(600,1000),'white'); bio=io.BytesIO(); im.save(bio,'JPEG')
        result=await g._food_scan_with_openclaw(bio.getvalue(),source='网上APP')
        assert result['pipeline']=='ocr'
        assert result['items'][0]['name']=='鸡蛋'
        assert calls==['ocr','parse']
    finally:
        g._food_scan_macos_ocr=old_ocr
        g._food_scan_from_ocr_text=old_parse
        g._food_scan_from_vision=old_vision


async def test_empty_ocr_falls_back_to_vision():
    old_ocr=g._food_scan_macos_ocr
    old_vision=g._food_scan_from_vision
    async def fake_ocr(image): return ''
    async def fake_vision(image,mime,*,source):
        return {'ok':True,'items':[{'name':'牛奶','category':'奶制品','mode':'quantity','amount':2,'unit':'盒','status':'','source':source,'raw':'','confidence':.8}], 'note':''}
    g._food_scan_macos_ocr=fake_ocr
    g._food_scan_from_vision=fake_vision
    try:
        im=Image.new('RGB',(300,300),'white'); bio=io.BytesIO(); im.save(bio,'JPEG')
        result=await g._food_scan_with_openclaw(bio.getvalue(),source='网上APP')
        assert result['pipeline']=='vision'
        assert result['items'][0]['name']=='牛奶'
    finally:
        g._food_scan_macos_ocr=old_ocr
        g._food_scan_from_vision=old_vision


def test_frontend_preserves_tall_order_pages():
    h=g._kitchen_html().decode('utf-8')
    assert "textSource=(foodSource==='网上APP'||foodSource==='超市')" in h
    assert 'maxH=textSource?10000:2200' in h
    assert "textSource?0.92:0.88" in h


if __name__=='__main__':
    test_long_order_screenshot_preserves_text_resolution()
    test_swift_helper_shipped()
    asyncio.run(test_online_app_prefers_ocr_pipeline())
    asyncio.run(test_empty_ocr_falls_back_to_vision())
    test_frontend_preserves_tall_order_pages()
    print('PASS test_kitchen_food_scan_ocr_r42')
