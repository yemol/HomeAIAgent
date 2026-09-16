# KitchenTerminal A3.0d VOICE1.1

## Root cause

Volcengine ASR returned:

- `小 k 获取今日菜单。`
- `小 K 显示今日菜单。`
- `小 K 获取今日菜单。`

The previous namespace regex accepted `小K` and `小k` only when there was no
space between the Chinese character and the Latin letter. Therefore the local
KitchenTerminal fast-path did not run and the utterance fell through to OpenClaw.

## Fix

The alias matcher now accepts:

- `小K`
- `小k`
- `小 K`
- `小 k`
- full-width `Ｋ/ｋ`
- arbitrary whitespace between `小` and the letter

For `获取今日菜单`, the local path calls `_kitchen_show_today_menu()`, which calls
`_kitchen_load()` every time and then broadcasts a fresh menu payload to the iPad.
