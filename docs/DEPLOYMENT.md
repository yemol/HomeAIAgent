# Deployment

## 1. StickS3 固件

生产 PlatformIO 环境：

```text
m5stack-sticks3-wake
```

工程依赖 `.pio-local/` 中已经准备好的 Arduino 3.3.7 / ESP-SR 本地依赖。源码包不包含 `.pio-local/`。

### 构建和刷写

```bash
pio run -e m5stack-sticks3-wake -t clean
pio run -e m5stack-sticks3-wake -t upload
pio device monitor
```

不要 Erase Flash。

当前启动 banner：

```text
=== HomeAIAgent A1R24 / Wake=你好逐光 ===
```

唤醒初始化成功应看到：

```text
[WAKE] local keyword engine ready: 你好逐光
```

### NVS 配置

Wi-Fi 和 Gateway 地址优先从 StickS3 NVS 读取。需要配置时可使用：

```bash
python tools/configure_terminal.py
```

或串口命令：

```text
CFG SHOW
CFG {json}
CFG RESET
```

`include/secrets.h` 仅用于本地可选 fallback，不进入提交包。

## 2. Gateway 首次准备

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent/gateway
./setup_mac.sh
```

持久配置：

```text
~/.config/HomeAIAgent/gateway.env
```

持久 runtime：

```text
~/.local/share/HomeAIAgent/
```

### 日常启动

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent/gateway
./run_full.sh
```

不要同时运行旧的手工 OpenClaw tunnel。

正常 transport 日志应包含：

```text
[OPENCLAW-SSH] SSH connected
[OPENCLAW-SSH] tunnel ready 127.0.0.1:18790 -> 127.0.0.1:18789
[OPENCLAW-TRANSPORT] ready before startup preflight
```

## 3. 正常语音行为

```text
你好逐光
  -> 本地“在的”
  -> command capture
  -> Thinking
  -> ASR / OpenClaw / TTS
  -> Speaking
  -> Idle
```

固定采集参数：

- Wake listen PGA：9 dB
- Command capture PGA：6 dB
- Wake 后等待说话：5 秒
- 连续静音结束：3 秒
- 最大 utterance：20 秒
- PSRAM capture buffer：21 秒

## 4. Info / Glass2

Gateway 启动时只加载 last-good cache，不主动触发 Info refresh。

固定刷新时段（Asia/Taipei）：

```text
01:00, 09:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00, 23:00
```

Glass2 每条信息默认停留 15 秒。

## 5. 夜间显示

Gateway 负责时间策略：

- 01:05：进入 display sleep
- 09:00：恢复正常显示
- 手动按键可临时唤醒显示
- 音频、Wi-Fi、Gateway 连接保持工作

## 6. Direct Overwrite 规则

覆盖源码时保留：

```text
.pio-local/
~/.config/HomeAIAgent/gateway.env
~/.local/share/HomeAIAgent/
```

不要把 Gateway runtime capture、token、API key 或 `.DS_Store/__MACOSX` 带进提交包。
