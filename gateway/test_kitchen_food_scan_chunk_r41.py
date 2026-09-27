#!/usr/bin/env python3
import asyncio
import json
import companion_gateway as g


def test_ui_chunking_contract():
    h = g._kitchen_html().decode('utf-8')
    assert 'foodScanSendChunks' in h
    assert 'chunkSize=256*1024' in h
    assert "maxBuffered=768*1024" in h
    assert "正在上传图片…" in h
    assert "foodScanPrepare" in h
    assert "maxW=textSource?1800:2200" in h
    assert "maxH=textSource?10000:2200" in h
    # The old one-frame upload must be gone.
    assert 'foodScanSocket.send(buf);' not in h


class FakeWS:
    request = type('Req', (), {'path':'/kitchen/ws'})()
    def __init__(self, frames): self.frames=frames; self.sent=[]; self.remote_address=('127.0.0.1', 1)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self.frames: raise StopAsyncIteration
        return self.frames.pop(0)
    async def send(self, payload): self.sent.append(payload)


async def test_server_accepts_multiframe_image():
    captured={}
    old=g._process_food_scan
    async def fake_process(session):
        captured['bytes']=len(session.food_scan_image)
        captured['data']=bytes(session.food_scan_image)
        session.food_scan_processing=False
        session.food_scan_receiving=False
    g._process_food_scan=fake_process
    try:
        rid='fs-chunk-test'
        data=(b'a'*(256*1024))+(b'b'*(256*1024))+(b'c'*12345)
        frames=[
            json.dumps({'type':'kitchen.hello','device_id':'test-ipad'}),
            json.dumps({'type':'kitchen.food.scan.start','request_id':rid,'source':'网上APP','mime':'image/jpeg','chunked':True,'total_bytes':len(data)}),
            data[:256*1024], data[256*1024:512*1024], data[512*1024:],
            json.dumps({'type':'kitchen.food.scan.stop','request_id':rid}),
        ]
        ws=FakeWS(frames)
        await g.handle_kitchen_connection(ws)
        assert captured['bytes']==len(data)
        assert captured['data']==data
    finally:
        g._process_food_scan=old


if __name__=='__main__':
    test_ui_chunking_contract()
    asyncio.run(test_server_accepts_multiframe_image())
    print('PASS test_kitchen_food_scan_chunk_r41')
