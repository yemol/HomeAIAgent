# KitchenTerminal A3.0d VOICE1

## Primary voice namespace

External HomeAI devices may address KitchenTerminal with `小K`:

- `小K获取今日菜单`
- `小K显示购物清单`
- `小K打开河虾`
- `小K下一步`
- `小K结束今天的烹饪`

`厨房` remains a backward-compatible alias.

The KitchenTerminal iPad microphone is already scoped to the kitchen terminal,
so it may continue to use direct commands such as `下一步` without saying `小K`.

## Scope

Only Gateway KitchenTerminal command parsing, local fast-path tests, user-facing
KitchenTerminal confirmations and documentation are changed.
