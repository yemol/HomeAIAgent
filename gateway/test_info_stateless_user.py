#!/usr/bin/env python3
from pathlib import Path
import ast

SOURCE = Path(__file__).with_name("companion_gateway.py")
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))

def find_async(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing async function {name}")

def dict_keys_in_function(fn):
    keys = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            literal_keys = []
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    literal_keys.append(key.value)
            keys.append(set(literal_keys))
    return keys

background = find_async("_openclaw_background_json")
chat = find_async("openclaw_chat")

background_dicts = dict_keys_in_function(background)
chat_dicts = dict_keys_in_function(chat)

assert any({"model", "stream", "messages", "tools", "tool_choice"} <= keys for keys in background_dicts)
assert not any("user" in keys and {"model", "messages"} <= keys for keys in background_dicts), (
    "Info Skill background payload must not send fixed user"
)
assert any("user" in keys and {"model", "messages"} <= keys for keys in chat_dicts), (
    "Normal voice chat must retain OPENCLAW_USER continuity"
)

source = SOURCE.read_text(encoding="utf-8")
assert "OPENCLAW_INFO_USER" not in source
assert "info_skill_openclaw_session=ephemeral-explicit" in source

print("HomeAIAgent Info Skill stateless-user self-test: PASS")
