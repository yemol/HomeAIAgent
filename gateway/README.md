# HomeAIAgent Gateway

Gateway 是 StickS3 / HomeAIAgent Mini / NetworkSpeaker 与 OpenClaw 之间的本地服务层。

## Responsibilities

- 设备 WebSocket
- 火山 ASR/TTS
- OpenClaw 对话调用
- 内嵌 SSH/Tailscale transport
- 多设备 voice session 路由
- NetworkSpeaker parent binding、音频 sink 路由和音量控制
- Gapless TTS 分段传输
- Notification listener + Voice Turn Fence
- Info Skill / Gold
- Glass2 预渲染与同步
- 夜间 display policy / ACK

## Persistent paths

配置：

```text
~/.config/HomeAIAgent/gateway.env
```

运行状态：

```text
~/.local/share/HomeAIAgent/
```

工程整体替换不会删除这两处数据。

## First setup

```bash
./setup_mac.sh
```

## Daily start

```bash
./run_full.sh
```

只启动这一项。`companion_gateway.py` 自己负责 OpenClaw transport、preflight、retry、device server、speech、notification 和 Info。

不要同时启动旧的手工 `openclaw_air_tunnel.sh`。

## Device/session routing

- 主 StickS3：`OPENCLAW_USER`
- HomeAIAgent Mini：独立 voice user
- 未知 companion：按 `device_id` 自动隔离
- `device_role=speaker`：不拥有 LLM session，只通过 `parent_device_id` 绑定 companion

同一 parent 有多个 speaker 时，优先 `audio_priority` 较高者；同优先级使用更新连接。

## Audio routing

Gateway 可以在本机 speaker 与绑定 NetworkSpeaker 之间路由 TTS。相对音量命令使用固定 10% step，NetworkSpeaker 负责 ACK 当前音量。

## Voice flow

```text
PCM16/16k
 -> ASR
 -> OpenClaw
 -> TTS
 -> segmented PCM16
 -> selected audio sink
 -> playback.done
```

## Runtime diagnostics

运行时可能生成 `latest_*` 音频/文本捕获，用于排查 ASR/TTS，但这些文件不属于源码提交包。

真实 token / API key 不得放入工程目录。
