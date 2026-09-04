# HomeAIAgent P0-A3.3｜你的实际环境配置与操作手册

日期：2026-09-03

---

## 1. 当前正式网络拓扑

你的实际环境不是“Mini 本地运行 OpenClaw”，而是：

```text
StickS3 + Glass2
        │
        │ 2.4 GHz Wi-Fi
        │ WebSocket :8765
        ▼
Mac mini
HomeAIAgent Gateway
  ├─ 接收 StickS3 PCM
  ├─ OpenAI ASR
  ├─ OpenAI TTS
  └─ 调用 OpenClaw
        │
        │ HTTP http://127.0.0.1:18790
        ▼
Mini 本地 SSH Tunnel
        │
        │ Tailscale VPN
        ▼
MacBook Air
Tailscale IP: 100.105.66.46
SSH user: yuanxiang
        │
        ▼
Air 本机 127.0.0.1:18789
OpenClaw Master Gateway
  ├─ Agent
  ├─ Session
  ├─ Tools
  └─ 其它 OpenClaw 能力
```

因此有三条铁规则：

1. **StickS3 永远连接 Mac mini，不直接连接 Air。**
2. **Mini 不需要启动第二个 OpenClaw Gateway。**
3. **HomeAIAgent 在 Mini 上只访问 `http://127.0.0.1:18790`。**

---

# 2. Air 端：OpenClaw 服务端设置

以下命令都在 **MacBook Air** 上执行。

## 2.1 确认 Air 上 OpenClaw Gateway 正常

```bash
cd ~
openclaw gateway status --require-rpc
```

Mini 上不需要执行这一条。

## 2.2 开启 HomeAIAgent 需要的 Chat Completions HTTP API

在 Air：

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
```

然后：

```bash
openclaw gateway restart --safe
```

HomeAIAgent 使用的是：

```text
POST /v1/chat/completions
```

OpenClaw 自己的远程客户端主要使用 WebSocket RPC，这两者不是同一调用方式。

## 2.3 Gateway 继续保持 localhost 即可

Air 没必要把 `18789` 暴露给家庭 LAN。

我们使用：

```text
Tailscale
+
SSH local port forwarding
```

Mini 通过 SSH 去访问 Air 自己的：

```text
127.0.0.1:18789
```

这样更干净。

## 2.4 OpenClaw token

如果 Air 的 Gateway 使用 token auth，HomeAIAgent 需要这个 token。

**不要把 token 发到聊天里。**

保存下来，稍后只填入 Mini 的：

```text
HomeAIAgent/gateway/.env
```

---

# 3. Mini 端：确认 Tailscale 到 Air

你的 Air Tailscale 地址已经确定：

```text
100.105.66.46
```

SSH 用户：

```text
yuanxiang
```

Mini Terminal：

```bash
ping 100.105.66.46
```

更推荐 Tailscale 自己的检查：

```bash
tailscale ping 100.105.66.46
```

如果能通，就说明：

```text
Mini → Tailscale → Air
```

正常。

还可以直接测试 SSH：

```bash
ssh yuanxiang@100.105.66.46
```

第一次可能询问 host key，确认是你自己的 Air 后输入：

```text
yes
```

能进入 Air 后：

```bash
exit
```

回到 Mini。

---

# 4. Mini 端：建立 OpenClaw SSH Tunnel

进入工程：

```bash
cd HomeAIAgent_P0_A3_3_TAILSCALE_AIR/gateway
```

赋予执行权限：

```bash
chmod +x openclaw_air_tunnel.sh setup_mac.sh check_connection.sh run_full.sh
```

启动：

```bash
./openclaw_air_tunnel.sh
```

这个脚本已经固定为你的真实环境：

```text
Air user = yuanxiang
Air Tailscale IP = 100.105.66.46
Air OpenClaw = 127.0.0.1:18789
Mini local tunnel = 127.0.0.1:18790
```

等价于：

```bash
ssh -N \
  -L 18790:127.0.0.1:18789 \
  yuanxiang@100.105.66.46
```

这个 Terminal 窗口需要保持运行。

现在网络变成：

```text
Mini 127.0.0.1:18790
        │
        │ SSH over Tailscale
        ▼
Air 127.0.0.1:18789
```

---

# 5. Mini 端：配置 HomeAIAgent Gateway

另外开一个 Terminal。

进入：

```bash
cd HomeAIAgent_P0_A3_3_TAILSCALE_AIR/gateway
```

运行：

```bash
./setup_mac.sh
```

它会自动：

1. 创建 `.venv`
2. 安装 Python 依赖
3. 让你输入 `OPENAI_API_KEY`
4. 让你输入 Air 的 OpenClaw token
5. 创建 `gateway/.env`

你的 `.env` 核心配置最终应当是：

```text
P0_MODE=full

OPENAI_API_KEY=你的OpenAI API Key
OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=coral

OPENCLAW_BASE_URL=http://127.0.0.1:18790
OPENCLAW_TOKEN=Air上的OpenClaw token
OPENCLAW_MODEL=openclaw/default
OPENCLAW_USER=home-ai-agent:main
```

注意：

```text
OPENCLAW_BASE_URL
```

**不要填：**

```text
http://100.105.66.46:18789
```

也不要填：

```text
ws://127.0.0.1:18789
```

HomeAIAgent 正确值就是：

```text
http://127.0.0.1:18790
```

---

# 6. Mini 端：测试整条 OpenClaw 通道

Tunnel 窗口保持运行。

HomeAIAgent Gateway 窗口：

```bash
./check_connection.sh
```

正常应该看到：

```text
[OK] SSH tunnel endpoint 127.0.0.1:18790 is open.
[OK] OPENAI_API_KEY is present
[OK] OpenClaw reachable
[READY] Gateway configuration is ready for a real voice turn
```

如果这里失败：

### `127.0.0.1:18790 is closed`

说明 SSH tunnel 没启动。

运行：

```bash
./openclaw_air_tunnel.sh
```

### 能连 tunnel，但 `/v1/models` / OpenClaw check 失败

重点检查 Air：

```bash
openclaw config get gateway.http.endpoints.chatCompletions.enabled
```

需要为：

```text
true
```

以及 OpenClaw token 是否正确。

---

# 7. 启动 HomeAIAgent Gateway

Tunnel 已运行后：

```bash
./run_full.sh
```

它会：

```text
检查 .env
↓
检查 127.0.0.1:18790
↓
运行 preflight
↓
启动 HomeAIAgent WebSocket Gateway
```

期待：

```text
[MODE] full
[WS] listening on ws://0.0.0.0:8765/companion
```

---

# 8. StickS3 端配置

StickS3 **只连接 Mini**。

工程：

```text
include/secrets.h
```

填写：

```cpp
#define WIFI_SSID "yemo_home_2.4"
#define WIFI_PASSWORD "你的Wi-Fi密码"

#define GATEWAY_HOST "你的Mac mini局域网IP"
#define GATEWAY_PORT 8765
#define GATEWAY_PATH "/companion"
```

## 查 Mini 局域网 IP

Mini：

```bash
ipconfig getifaddr en0
```

如果无输出：

```bash
ipconfig getifaddr en1
```

例如得到：

```text
192.168.71.10
```

那么：

```cpp
#define GATEWAY_HOST "192.168.71.10"
```

这里：

**不是 `100.105.66.46`。**

`100.105.66.46` 是 Air 的 Tailscale 地址，只给 Mini → Air 使用。

---

# 9. StickS3 Wi-Fi 重连机制

P0-A3.3 继续保留已经确定的退避重连：

```text
一次连接最长等待 15 秒

失败 1 → 等 30 秒
失败 2 → 等 60 秒
失败 3 → 等 120 秒
失败 4+ → 等 240 秒
```

连接成功后：

```text
backoff → 立即清零
```

这样遇到：

```text
AUTH_EXPIRE
Reason 208
Association refused temporarily
```

不会持续撞 AP。

双屏 UI 在 Wi-Fi 退避期间仍然工作。

---

# 10. 编译上传 StickS3

工程根目录：

```bash
platformio run --target upload
```

监视：

```bash
platformio device monitor -b 115200
```

正常：

```text
[WIFI] connecting to yemo_home_2.4
[WIFI] IP: 192.168.71.xx
[WS] client initialized -> ws://MiniIP:8765/companion
[WS] connected
[GATEWAY] ready mode=full
```

---

# 11. 第一次真语音测试

保证三个东西都已经工作：

```text
① Air OpenClaw Gateway
② Mini SSH Tunnel
③ Mini HomeAIAgent Gateway
```

然后 StickS3：

按住 A：

> 你好，你是谁？

松开。

Mini 应看到：

```text
[ASR] 你好，你是谁？
[AGENT] ...
[TTS] ...
[LATENCY] asr=... agent=... tts=...
```

设备会用当前冻结的：

```text
Speaker master = 255
Speaker magnification = 4
```

直接播放 OpenClaw 的回答。

---

# 12. 当前语音数据流

正式数据路径：

```text
你的声音
↓
StickS3 Mic
↓
PCM16 / WebSocket
↓
Mac mini :8765
↓
OpenAI ASR
↓
文字
↓
Mini 127.0.0.1:18790
↓
SSH / Tailscale
↓
Air OpenClaw :18789
↓
Agent回答
↓
Mini
↓
OpenAI TTS
↓
PCM
↓
StickS3 Speaker
```

OpenAI API Key **永远不进入 StickS3**。

OpenClaw token **永远不进入 StickS3**。

两者只保存在 Mini 的：

```text
gateway/.env
```

---

# 13. 日常启动顺序

以后每天启动 HomeAIAgent，顺序固定为：

## Air

确保：

```text
OpenClaw Gateway 已运行
Tailscale 已连接
```

## Mini Terminal 1

```bash
cd HomeAIAgent_P0_A3_3_TAILSCALE_AIR/gateway
./openclaw_air_tunnel.sh
```

保持窗口。

## Mini Terminal 2

```bash
cd HomeAIAgent_P0_A3_3_TAILSCALE_AIR/gateway
./run_full.sh
```

保持窗口。

## StickS3

开机即可。

---

# 14. 当前暂时保留的 Reset Forensics

偶发 MCU reset 没有再稳定复现，因此不阻塞进度。

诊断代码继续保留。

如果以后再次重启，重点记录：

```text
[BOOT-DIAG] reset_reason=...
[BOOT-DIAG] last_checkpoint=... seq=...
```

目前不会为了“暂时不出现”的 reset 回退已经完成的真语音功能。

---

# 15. 当前正式冻结的 P0-A3.3 环境

```text
项目名：
HomeAIAgent

设备：
M5Stack StickS3 + Glass2

HomeAIAgent Gateway：
Mac mini

OpenClaw Master：
MacBook Air

Mini → Air：
Tailscale VPN

Air Tailscale IP：
100.105.66.46

Air SSH：
yuanxiang@100.105.66.46

Air OpenClaw：
127.0.0.1:18789

Mini HomeAIAgent tunnel：
127.0.0.1:18790

StickS3 → Mini：
WebSocket :8765

OpenAI：
Mini 负责 ASR / TTS

Agent：
Air OpenClaw
```
