# HomeAIAgent / OpenClaw Info Skill Interface v1.1

协议版本保持：

```text
homeai-info/1.1
```

本次只是把 **触发方式** 和 **文本长度硬限制** 定稿，不升级协议版本。

## 一、OpenClaw Skill 名称与接口

Skill：

```text
HomeAI Info
```

必须支持：

```text
homeai_info.get_feed
homeai_info.get_item
```

---

## 二、get_feed 固定触发方式

HomeAIAgent Gateway 不直接运行 Skill 脚本。

Gateway 会通过 OpenClaw `/v1/chat/completions` 发出一个固定后台控制块：

```text
[HOMEAI_INFO_CALL]
tool=homeai_info.get_feed
protocol=homeai-info/1.1
payload={...get_feed JSON...}
[/HOMEAI_INFO_CALL]
```

OpenClaw / Skill 对这个控制块的处理要求：

1. 看到 `HOMEAI_INFO_CALL` 且 `tool=homeai_info.get_feed` 时，必须触发 HomeAI Info Skill 的 `get_feed`。
2. `payload` 原样作为 v1.1 输入。
3. 不允许 OpenClaw 主模型自己替代 Skill 编造资讯。
4. 最终只返回 Skill 的 JSON。
5. JSON 前后不加解释、Markdown、代码围栏或自然语言。

### get_feed payload

```json
{
  "protocol_version": "homeai-info/1.1",
  "operation": "get_feed",
  "request_id": "unique-string",
  "locale": "zh-CN",
  "timezone": "Asia/Taipei",
  "max_items": 20,
  "category_limits": {
    "game": 10,
    "finance": 10
  },
  "categories": ["game", "finance"],
  "max_age_hours": 96
}
```

目标：

```text
游戏 10 条
金融 10 条
总计最多 20 条
```

如果某一类没有足够新鲜、可靠内容，允许少于 10 条，不得拿重复、过期或低质量内容凑数。

---

## 三、get_item 固定触发方式

`get_item` 用于用户针对当前 Glass2 资讯追问。

典型语音：

```text
这个讲讲
这条详细说说
为什么会这样
有什么影响
后面怎么看
刚才那条再说一下
```

HomeAIAgent 会把当前 / 上一条 / 下一条的 `item_id` 放进 OpenClaw 对话上下文。

OpenClaw 的固定调用语义：

```text
tool=homeai_info.get_item
protocol=homeai-info/1.1
item_id=<当前被指代资讯的 item_id>
```

要求：

1. 必须先调用 `homeai_info.get_item` 获取完整事实背景。
2. 不允许只凭 Glass2 的 headline / summary 自己猜细节。
3. Skill 返回 detail_context / facts / sources 后，OpenClaw 主 Agent 再根据用户问题生成自然中文语音回答。

### get_item 输入

```json
{
  "protocol_version": "homeai-info/1.1",
  "operation": "get_item",
  "request_id": "unique-string",
  "item_id": "game_20260903_xxxxx",
  "locale": "zh-CN",
  "timezone": "Asia/Taipei"
}
```

---

## 四、headline / summary 最终长度限制

### headline

硬上限：

```text
≤ 30 个 Unicode 字符
```

Glass2 实际显示目标：

```text
优先 18～22 个汉字左右
```

说明：

- 30 是接口硬上限，不是推荐每条都写满 30。
- 当前 Glass2 正文为 11px、两行显示。
- 18～22 个汉字通常更适合完整显示和快速阅读。
- 游戏名、公司名等专有名词较长时可以接近 30。
- 不允许为了压缩字数改变事实。

### summary

硬上限：

```text
≤ 115 个 Unicode 字符
```

要求：

- 中文 1～2 句。
- summary 主要供 OpenClaw 快速理解和本地缓存使用，不直接完整显示在 Glass2。
- 只陈述来源支持的事实。
- 不加入模型猜测。

Gateway 也会做防御性长度检查：

```text
headline > 30 → 记录 WARN 并截到 30
summary > 115 → 记录 WARN 并截到 115
```

正常情况下 Skill 应自行满足限制，不应依赖 Gateway 截断。

---

## 五、get_feed 输出关键结构

```json
{
  "protocol_version": "homeai-info/1.1",
  "operation": "get_feed",
  "request_id": "same-as-request",
  "status": "ok",
  "generated_at": "2026-09-04T00:00:00+08:00",
  "next_refresh_after_sec": 900,
  "category_counts": {
    "game": 10,
    "finance": 10
  },
  "items": [
    {
      "id": "game_xxxxx",
      "category": "game",
      "subtype": "update",
      "headline": "中文短标题",
      "summary": "中文事实摘要。",
      "priority": 86,
      "published_at": "2026-09-03T23:20:00+08:00",
      "source_name": "Source",
      "source_url": "https://example.com/...",
      "tags": [],
      "detail_available": true,
      "content_hash": "stable-hash"
    }
  ]
}
```

---

## 六、Gateway 调用周期

默认：

```text
每 15 分钟调用一次 homeai_info.get_feed
```

Skill 可以通过：

```json
"next_refresh_after_sec": 900
```

建议下次刷新时间。

Gateway 会把建议值限制在：

```text
最短 300 秒
最长 7200 秒
```

---

## 七、失败保护

Skill 返回：

```text
status=error
```

或者 OpenClaw / Skill 暂时不可用时：

```text
不得清空 Glass2
继续使用 last-good cache
```

缓存位置：

```text
~/.local/share/HomeAIAgent/info_skill_feed_cache.json
```

只有拿到新的合法 `ok / partial` feed 且内容 revision 发生变化时，才同步设备。

---

## 八、设备显示约束

已经冻结：

```text
最多 20 条
游戏目标 10 条
金融目标 10 条

Glass2 单条停留：10 秒

分类字号：12px
正文字号：11px
正文位置：沿用当前实机确认布局
```

Skill 不负责设备显示和 10 秒计时。

---

## 九、最终职责边界

HomeAI Info Skill：

```text
资讯抓取
去重
可信度与时效过滤
游戏 / 金融配额
中文 headline / summary
priority
get_item 完整事实详情
```

HomeAIAgent Gateway：

```text
定时触发 OpenClaw Skill
协议校验
30 / 115 字符防御性限制
last-good cache
设备同步
当前 item_id 上下文
```

StickS3 / Glass2：

```text
20 条缓存
10 秒自动轮换
B 上/下一条
PTT 时冻结当前资讯
显示
```

Gapless TTS 与语音链不属于本接口，不在此协议中修改。
