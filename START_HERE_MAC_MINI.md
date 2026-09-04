# HomeAIAgent P0-A3.1｜从零开始设置指南（Mac mini + StickS3）

这版新增的是 **Wi-Fi 非阻塞退避重连**，真语音链路保持：

`StickS3 → ASR → OpenClaw → TTS → StickS3`

Reset Forensics 继续保留。

---

## 一、这次 Wi-Fi 重连改了什么

旧版：
- 启动时阻塞等待 Wi-Fi 15 秒；
- 失败后基本停在 `connect failed`；
- 如果 AP 临时拒绝关联，反复刷机/重启很容易继续撞 AP。

P0-A3.1：
- 启动后设备 UI 不再被 Wi-Fi 阻塞；
- 每次连接给 15 秒正常关联窗口；
- 失败后自动进入退避：
  - 30 秒
  - 60 秒
  - 120 秒
  - 240 秒
- 达到 240 秒后继续按 240 秒重试；
- 一旦成功连接，退避立即清零；
- 关闭 Arduino 自己的 auto reconnect，避免它和我们的重连逻辑同时“敲门”；
- Wi-Fi 掉线后 PTT 自动不可用，但屏幕和本地 UI 仍继续运行；
- WebSocket 只在 Wi-Fi 真正在线后启动/运行。

正常日志可能是：

```text
[WIFI] connecting to yemo_home_2.4 (timeout=15 s)
[WIFI] connect timeout (...)
[WIFI] retry scheduled in 30 s
...
[WIFI] retry scheduled in 60 s
...
[WIFI] IP: 192.168.71.22
[WS] client initialized -> ws://192.168.71.xxx:8765/companion
[WS] connected
```

如果 AP 出现类似 200 多秒的 comeback time，240 秒这一档就是专门为了避免持续撞 AP。

---

# 二、StickS3 端设置

## 1. 解压工程

打开：

```text
HomeAIAgent_P0_A3_1_WIFI_BACKOFF_FULL_VOICE
```

## 2. 编辑

```text
include/secrets.h
```

填写：

```cpp
#define WIFI_SSID "你的2.4GHz Wi-Fi名称"
#define WIFI_PASSWORD "你的Wi-Fi密码"

#define GATEWAY_HOST "Mac mini的局域网IP"
#define GATEWAY_PORT 8765
#define GATEWAY_PATH "/companion"
```

### 查 Mac mini IP

Mac Terminal：

```bash
ipconfig getifaddr en0
```

例如：

```text
192.168.71.10
```

那么 StickS3：

```cpp
#define GATEWAY_HOST "192.168.71.10"
```

如果 `en0` 没有输出，再试：

```bash
ipconfig getifaddr en1
```

## 3. PlatformIO 上传

在项目根目录：

```bash
platformio run --target upload
```

串口：

```bash
platformio device monitor -b 115200
```

---

# 三、Mac mini：OpenClaw 设置

HomeAIAgent Gateway 和 OpenClaw 都在 Mac mini 上运行，所以 OpenClaw 保持 loopback 即可。

## 1. 先确认 OpenClaw Gateway 正常

```bash
openclaw gateway status --require-rpc
```

## 2. 启用 OpenAI-compatible Chat Completions

执行：

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
```

然后：

```bash
openclaw gateway restart --safe
```

## 3. 如果 OpenClaw 使用 token auth

显示当前 token：

```bash
openclaw gateway auth-token --show
```

这个 token 是机密，不要发到聊天里。

如果提示没有 token，可执行：

```bash
openclaw doctor --generate-gateway-token
openclaw gateway restart --safe
openclaw gateway auth-token --show
```

---

# 四、Mac mini：HomeAIAgent Gateway 设置

进入工程：

```bash
cd HomeAIAgent_P0_A3_1_WIFI_BACKOFF_FULL_VOICE/gateway
```

## 最推荐：直接运行交互式设置

```bash
chmod +x setup_mac.sh run_full.sh
./setup_mac.sh
```

脚本会：

1. 建立 `.venv`
2. 安装 Python 依赖
3. 让你粘贴 `OPENAI_API_KEY`
4. 让你粘贴 OpenClaw token
5. 自动生成隐藏的 `gateway/.env`
6. 自动运行 `--check`

因此你不需要自己在 Finder 里找 `.env` 隐藏文件。

### OpenAI API Key

只填在 Mac mini 的：

```text
gateway/.env
```

**不要**写进 StickS3 `secrets.h`。

---

# 五、启动 Gateway

完成设置后：

```bash
./run_full.sh
```

或者：

```bash
source .venv/bin/activate
python companion_gateway.py
```

成功应看到：

```text
[MODE] full
[WS] listening on ws://0.0.0.0:8765/companion
```

StickS3 连上后：

```text
[WIFI] IP: ...
[WS] connected
[GATEWAY] ready mode=full
```

---

# 六、第一句话测试

按住 StickS3 A 键：

> 你好，你是谁？

松开。

Mac 应依次看到：

```text
[ASR] 你好，你是谁？
[AGENT] ...
[TTS] ...
[LATENCY] asr=... agent=... tts=...
```

StickS3 会通过当前已经实机通过的：

```text
Speaker master = 255
Speaker magnification = 4
```

直接说出答案。

---

# 七、当前仍保留的重启取证

偶发 reset 尚未删除诊断代码。

如果以后再次重启，继续抓：

```text
[BOOT-DIAG] reset_reason=...
[BOOT-DIAG] last_checkpoint=... seq=...
```

功能继续推进，但证据链不会丢。
