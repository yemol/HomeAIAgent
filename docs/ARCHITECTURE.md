# Architecture

## 1. 总体结构

```text
StickS3 / HomeAIAgent Mini / NetworkSpeaker
                    |
                    | WebSocket
                    v
           HomeAIAgent Gateway (Mac mini)
              |      |       |
             ASR    TTS    device router
                    |
                    +-- embedded SSH/Tailscale --> OpenClaw
```

Gateway 是本地服务层。OpenClaw 仍是对话与工具执行权威，StickS3 负责本地唤醒、采集、播放和显示。

## 2. StickS3

职责：

- Chinese MultiNet 本地唤醒：`你好逐光`
- Button A PTT
- ES8311 半双工麦克风/扬声器切换
- PSRAM 语音缓存与捕获后发送
- 两槽 Gapless TTS 播放
- Cyber Expression
- Glass2 帧缓存与显示
- NVS 网络/Gateway 配置
- 夜间显示执行与 ACK

语音链保持半双工。麦克风采集结束后才进行 Wi-Fi 音频上传；播放结束后再恢复唤醒监听。

## 3. Gateway

职责：

- 维护设备 WebSocket
- 火山 ASR/TTS
- OpenClaw 对话调用
- 内嵌 SSH/Tailscale transport
- 多设备会话隔离
- NetworkSpeaker 音频 sink 路由和音量状态
- OpenClaw Notification listener
- Info Skill 调度与缓存
- Glass2 预渲染
- Gold 状态
- 夜间显示策略

持久配置和运行状态都在项目目录之外，Direct Overwrite 不会删除用户凭据或运行队列。

## 4. OpenClaw 会话

主 HomeAIAgent 使用稳定 voice user：

```text
home-ai-agent:main
```

HomeAIAgent Mini 使用独立 voice user。未知 future companion 默认按 `device_id` 隔离。

NetworkSpeaker 不拥有 OpenClaw 对话，通过 `parent_device_id` 绑定 companion，仅作为音频输出节点。

## 5. Voice flow

```text
Wake / Button A
  -> PCM16 16 kHz capture
  -> Gateway ASR
  -> OpenClaw
  -> Gateway TTS
  -> segmented PCM16
  -> selected audio sink
  -> playback ACK
```

当前本地 Wake ACK `在的` 不经过这条链路，直接由 StickS3 Flash 中的 PCM 播放。

## 6. Display flow

### StickS3 LCD

135×240 RGB565 全帧离屏渲染，完成后一次 `pushSprite()`，避免清屏闪烁。

### Glass2

Gateway 预渲染 128×64 1-bit frame。设备使用两阶段同步，整批接收完成后才切换 active feed。

## 7. OpenClaw transport

Mac mini Gateway 自己维护到 OpenClaw 主机的 SSH/Tailscale 本地转发：

```text
127.0.0.1:18790
  -> SSH/Tailscale
  -> OpenClaw 127.0.0.1:18789
```

不需要单独运行 `ssh -N -L`。SSH 链路异常只降级 OpenClaw 相关能力，不应让 StickS3-facing Gateway 退出。

## 8. Reliability rules

- 音频采集/播放优先于 UI 动画。
- Mic 活跃期间避免应用级 Wi-Fi 音频发送。
- 保留 Info 各分类 last-good 数据。
- 显示状态以设备 ACK 为准。
- Background OpenClaw session 有界且用后清理。
- `.pio-local`、凭据和 runtime state 不进入源码包。
