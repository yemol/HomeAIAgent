# OpenClaw Notification Listener

## 目标

把 OpenClaw reminder / background assistant output 送到 HomeAIAgent，同时不增加 webhook、反向 SSH 或额外入站端口。

## Transport

Gateway 通过自身管理的本地 OpenClaw endpoint：

```text
Mac mini 127.0.0.1:18790
  -> SSH/Tailscale
  -> OpenClaw 127.0.0.1:18789
```

Notification listener 复用这条 outbound transport。

## 主 voice session

默认 voice user：

```text
home-ai-agent:main
```

默认 OpenClaw session key：

```text
agent:main:openai-user:home-ai-agent:main
```

Notification listener 只跟踪主 HomeAIAgent 会话，不把 HomeAIAgent Mini 的对话当成主设备通知。

## Listener lifecycle

1. 连接 OpenClaw Gateway WebSocket。
2. 订阅主 voice session。
3. `session.message` / `sessions.changed` 仅作为 invalidation signal。
4. 重新读取有界 `chat.history` 作为权威数据源。
5. 第一次部署先建立 baseline，不播报历史消息。
6. 重连后做 history reconciliation，补回断线期间真正遗漏的 async message。
7. 新 async assistant message 进入 durable notification queue。
8. 设备空闲且音频 sink 可用时再通过现有 TTS/playback 链路投递。

## Voice Turn Fence

同步语音请求执行期间，OpenClaw 可能先写入多条 assistant progress row，再写最终回复。

Gateway 会在当前 user row 与最终同步 assistant row 之间建立 fence：

- progress row 标记为 consumed，不进入 notification queue；
- 最终同步回复也不会被 listener 再播一次；
- user row 之前或该轮结束之后真正异步产生的 assistant message 仍可正常投递；
- 如果最终行尚未写入 history，listener 会等待，不提前把 progress 当通知。

## Durable state

```text
~/.local/share/HomeAIAgent/openclaw_voice_listener_state.json
~/.local/share/HomeAIAgent/notification_queue.json
```

这些文件位于工程目录之外，Direct Overwrite 不会删除。

## 验证

只读 listener 检查：

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent/gateway
./check_notification_listener.sh
```

离线 reconciliation regression：

```bash
python test_notification_listener_offline.py
```
