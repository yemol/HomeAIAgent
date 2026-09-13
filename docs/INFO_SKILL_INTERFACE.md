# HomeAI Info Skill Interface

协议：

```text
homeai-info/1.1
```

OpenClaw Skill：

```text
HomeAI Info
```

支持：

```text
homeai_info.get_feed
homeai_info.get_item
```

## get_feed

Gateway 通过 OpenClaw `/v1/chat/completions` 发送后台控制块，不直接运行 Skill 脚本：

```text
[HOMEAI_INFO_CALL]
tool=homeai_info.get_feed
protocol=homeai-info/1.1
payload={...}
[/HOMEAI_INFO_CALL]
```

要求：

- payload 原样交给 Skill。
- 主模型不能自行编造 feed。
- 最终返回 Skill JSON，不包 Markdown 或解释文本。
- 游戏/金融分别维护 last-good 数据，单类失败不清空另一类。

目标数量：游戏最多 10 条、金融最多 10 条，总计最多 20 条。宁缺毋滥。

## get_item

用户针对当前 Glass2 资讯追问时，OpenClaw 应先调用 `homeai_info.get_item` 获取完整事实和来源，再生成自然语言回答。

Gateway 会把当前/上一条/下一条 `item_id` 放入对话上下文，供“这条”“上一条”等指代解析。

## 文本长度

Skill 推荐目标：

```text
headline: 18~22 个汉字为宜，尽量 <= 30 Unicode 字符
summary:  中文 1~2 句，尽量 <= 115 Unicode 字符
```

Gateway 当前防御性硬限制：

```text
headline <= 36 Unicode 字符
summary  <= 160 Unicode 字符
```

Skill 应主动满足推荐长度，不依赖 Gateway 截断。

## 调度

Gateway 启动时只读取 last-good cache，不触发刷新。

固定时段（Asia/Taipei）：

```text
01:00, 09:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00, 23:00
```

每个分类独立请求；失败才重试，合法空 feed 不重试。

## 缓存和失败保护

缓存：

```text
~/.local/share/HomeAIAgent/info_skill_feed_cache.json
```

OpenClaw/Skill 异常时继续使用 last-good cache，不清空 Glass2。只有拿到新的合法 feed 且 revision 变化时才同步设备。
