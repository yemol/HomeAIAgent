# KitchenTerminal command set

The Gateway keeps one stable command vocabulary while allowing natural-language variants.

## Voice namespace rule

From normal HomeAI companions, kitchen controls use **`厨房`** as the domain prefix:

- `逐光，厨房显示今天的菜单`
- `逐光，厨房打开河虾`
- `逐光，厨房结束今天的烹饪`

ASR may remove the wake word before the Gateway sees the text; both `逐光，厨房…` and `厨房…` are valid inputs.

The KitchenTerminal iPad microphone source is identified as `KitchenTerminal-*`, so it may omit the `厨房` prefix. The current KitchenTerminal uses this for the `🎙 问逐光` PTT experience.

| Canonical intent | HTTP action | Natural examples after `厨房` |
|---|---|---|
| Load today's menu | `menu.today` / `today` | 显示今天的菜单、拉取今日菜单、把今天晚饭调出来 |
| Open/resume recipe | `recipe.open` / `recipe` | 打开河虾、继续做河虾、回到山药 |
| Next step | `step.next` / `next` | 下一步、接下来、然后呢、下一步怎么做 |
| Previous step | `step.prev` / `prev` | 上一步、刚才那一步、退一步 |
| Back to menu | `menu.back` / `menu` | 返回菜单、回到今天菜单 |
| Shopping list | `menu.shopping` / `shopping` | 显示购物清单 |
| Cooking timeline | `menu.timeline` / `timeline` | 烧菜顺序、烹饪顺序 |
| Start timer | `timer_start` | 开始计时、计时30秒 |
| Pause/resume timer | `timer_pause` / `timer_resume` | 暂停计时、继续计时 |
| Adjust timer | `timer_adjust` | 再加10秒、减少1分钟 |
| Timer status | voice state query | 还有多久、还剩多久 |
| Cancel timer | `timer_cancel` | 取消计时、停止计时 |
| End cooking | `day.finish` / `finish_start` | 结束今天的烹饪、今天做完了 |
| Confirm end | `day.finish.confirm` / `finish_confirm` | 确认结束 |
| Save private recipes | `day.finish.save` / `finish_save` | 保存河虾、全部保存 |
| End without saving | `day.finish.none` / `finish_no_save` | 都不保存、直接结束 |

`kitchen_send.py` remains a debug tool only. Production speech should resolve natural language into these intents.

## Routing rule

Deterministic controls are **Gateway-local fast paths** and do not require OpenClaw. Free-form recipe reasoning/Q&A may use OpenClaw.

Expected external-device log:

```text
[KITCHEN-INTENT] intent=menu.today source=local-fastpath namespace=厨房 transcript='厨房显示今天的菜单。'
[KITCHEN-VOICE] handled locally transcript='厨房显示今天的菜单。'
```

There should be no `[STAGE] OpenClaw begin` between those lines for deterministic controls.


## Kitchen Q&A

The iPad `🎙 问逐光` button is primarily for cooking questions and troubleshooting, not for replacing obvious touch controls. Free-form questions go through Volcengine ASR -> Kitchen context -> OpenClaw -> iPad TTS. The same microphone path may still resolve the deterministic commands above locally when the user happens to say them.
