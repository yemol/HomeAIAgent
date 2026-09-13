#!/usr/bin/env python3
from pathlib import Path
import ast

SOURCE = Path(__file__).with_name("companion_gateway.py")
source = SOURCE.read_text(encoding="utf-8")
tree = ast.parse(source)


def find_async(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing async function {name}")


def find_func(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def dict_literal_keys(fn):
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            out.append({
                key.value for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            })
    return out

background = find_async("_openclaw_background_json")
chat = find_async("openclaw_chat")
cleanup = find_async("_cleanup_openclaw_info_session")
rpc = find_async("_openclaw_gateway_rpc")
find_func("_new_info_session_key")
find_func("_is_safe_info_session_key")
find_func("_openclaw_gateway_ws_url")

background_dicts = dict_literal_keys(background)
chat_dicts = dict_literal_keys(chat)

assert not any("user" in keys and {"model", "messages"} <= keys for keys in background_dicts)
assert any("user" in keys and {"model", "messages"} <= keys for keys in chat_dicts)
assert '"x-openclaw-session-key": session_key' in source
assert 'attempts=1' in ast.get_source_segment(source, background)
assert '"sessions.delete"' in ast.get_source_segment(source, cleanup)
assert '"deleteTranscript": True' in ast.get_source_segment(source, cleanup)
assert '"operator.admin"' in ast.get_source_segment(source, rpc)
assert '"gateway-client"' in ast.get_source_segment(source, rpc)
assert '"backend"' in ast.get_source_segment(source, rpc)
assert 'homeai-info-' in source
assert 'status=deleted transport=gateway-rpc' in source
assert 'OPENCLAW_INFO_SESSION_CLEANUP' in source
assert 'OPENCLAW_INFO_USER' not in source
assert '_run_info_cleanup_ssh' not in source
assert 'OPENCLAW_INFO_CLEANUP_SSH_HOST' not in source
assert 'OPENCLAW_INFO_CLEANUP_SSH_USER' not in source

print("HomeAIAgent Info Skill ephemeral-session Gateway-RPC cleanup self-test: PASS")
