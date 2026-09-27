#!/usr/bin/env python3
from __future__ import annotations

import ast
import asyncio
import json
from types import SimpleNamespace

import companion_gateway as cg


def _function_node():
    tree = ast.parse(open(cg.__file__, 'r', encoding='utf-8').read())
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'gateway_http_request':
            return node
    raise AssertionError('gateway_http_request not found')


async def _runtime_check():
    cg.KITCHEN_CURRENT_STATE = {'screen': 'idle', 'date': '', 'dish': '', 'step': 0}
    req = SimpleNamespace(path=cg.KITCHEN_HTTP_PATH + '/view', headers={})
    response = await cg.gateway_http_request(None, req)
    assert response is not None
    assert int(response.status_code) == 200
    payload = json.loads(bytes(response.body).decode('utf-8'))
    assert payload['state']['screen'] == 'idle'


def main():
    node = _function_node()
    globals_declared = {name for item in node.body if isinstance(item, ast.Global) for name in item.names}
    assert 'KITCHEN_CURRENT_STATE' in globals_declared, 'gateway_http_request must declare global KITCHEN_CURRENT_STATE'
    asyncio.run(_runtime_check())
    print('KitchenTerminal R24 HTTP state scope regression: PASS')


if __name__ == '__main__':
    main()
