#!/usr/bin/env python3
from pathlib import Path
import ast

SOURCE = Path(__file__).with_name("companion_gateway.py")
source = SOURCE.read_text(encoding="utf-8")
tree = ast.parse(source)

names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
for required in {
    "_openclaw_gateway_ws_url",
    "_gateway_rpc_recv_response",
    "_openclaw_gateway_rpc",
    "_cleanup_openclaw_info_session",
}:
    assert required in names, required

assert 'OPENCLAW_GATEWAY_WS_URL' in source
assert 'OPENCLAW_GATEWAY_PROTOCOL' in source
assert 'connect.challenge' in source
assert '"method": "connect"' in source
assert '"operator.admin"' in source
assert '"sessions.delete"' in source
assert 'transport=gateway-rpc' in source
assert 'ssh' not in source.lower() or 'ssh=disabled' in source.lower()
assert 'OPENCLAW_INFO_CLEANUP_SSH_HOST' not in source
assert 'OPENCLAW_INFO_CLEANUP_SSH_USER' not in source
assert '_run_info_cleanup_ssh' not in source

print("HomeAIAgent Info cleanup Gateway-RPC portability self-test: PASS")
