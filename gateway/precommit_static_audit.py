#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BANNED_FILES = {
    '.env', '.DS_Store', 'latest_input.wav', 'latest_input_kitchen.wav',
    'latest_tts.wav', 'latest_answer.txt', 'latest_transcript.txt',
}
BANNED_SUFFIXES = {'.pyc', '.wav', '.pcm', '.raw', '.log'}
BANNED_PATTERNS = {
    r'/Users/[A-Za-z0-9._-]+': 'absolute macOS user path',
    r'/Volumes/[^/\s]+': 'absolute macOS volume path',
    r'100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}': 'literal CGNAT/Tailscale IPv4 address',
}
SECRET_KEYS = ('VOLCENGINE_API_KEY', 'OPENCLAW_TOKEN', 'OPENAI_API_KEY')

errors: list[str] = []

for p in ROOT.rglob('*'):
    rel = p.relative_to(ROOT)
    if '__pycache__' in rel.parts:
        errors.append(f'generated cache directory present: {rel}')
        continue
    if not p.is_file():
        continue
    if p.name in BANNED_FILES or p.suffix.lower() in BANNED_SUFFIXES:
        errors.append(f'runtime/generated artifact present: {rel}')

for p in ROOT.glob('KITCHEN_A*.md'):
    errors.append(f'historical version note should be consolidated: {p.name}')

for p in ROOT.glob('*.py'):
    try:
        ast.parse(p.read_text(encoding='utf-8'), filename=str(p), feature_version=(3, 9))
    except Exception as exc:
        errors.append(f'python parse failed: {p.name}: {type(exc).__name__}: {exc}')

text_files = [
    p for p in ROOT.rglob('*')
    if p.is_file() and p.suffix.lower() in {'.py', '.sh', '.md', '.txt', '.json', '.json5', '.example'}
]
for p in text_files:
    try:
        text = p.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        continue
    if p.name != 'precommit_static_audit.py':
        for pattern, label in BANNED_PATTERNS.items():
            match = re.search(pattern, text)
            if match:
                errors.append(f'{label} found in {p.relative_to(ROOT)}: {match.group(0)}')

    # Shell/example config may contain only empty values or variable substitutions.
    if p.suffix.lower() in {'.sh', '.example', '.env'} or p.name.endswith('.env.example'):
        for key in SECRET_KEYS:
            for m in re.finditer(rf'(?m)^[ \t]*(?:export[ \t]+)?{re.escape(key)}[ \t]*=[ \t]*([^#\n]*)', text):
                value = m.group(1).strip().strip('"\'')
                if value and not value.startswith('${'):
                    errors.append(f'non-empty secret-like assignment in {p.relative_to(ROOT)}: {key}')
    elif p.suffix.lower() == '.py':
        for key in SECRET_KEYS:
            m = re.search(rf'(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(["\'])(.+?)\1[ \t]*$', text)
            if m and m.group(2).strip():
                errors.append(f'hard-coded secret-like literal in {p.relative_to(ROOT)}: {key}')

main = ROOT / 'companion_gateway.py'
if main.exists():
    source = main.read_text(encoding='utf-8')
    tree = ast.parse(source)
    repo_text = '\n'.join(
        p.read_text(encoding='utf-8', errors='ignore')
        for p in ROOT.iterdir() if p.is_file()
    )
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            refs = len(re.findall(rf'\b{re.escape(node.name)}\b', repo_text))
            if refs <= 1:
                errors.append(f'unreferenced top-level definition: {node.name} line {node.lineno}')

    if 'KITCHEN_UI_VERSION = "A3.0b FIX1 R12"' not in source:
        errors.append('Kitchen UI version marker is not R12')

if errors:
    print('[STATIC-AUDIT] FAIL')
    for item in errors:
        print(f' - {item}')
    raise SystemExit(1)

print('[STATIC-AUDIT] PASS: repository is clean for pre-commit review')
