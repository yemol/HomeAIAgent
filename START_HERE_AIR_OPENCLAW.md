# HomeAIAgent P0-A3.2｜正确拓扑：Air 跑 OpenClaw，Mac mini 是语音桥接客户端

## 0. 正确架构

现在正式按你的实际环境冻结：

```text
StickS3 / Glass2
       │ Wi-Fi / WebSocket :8765
       ▼
Mac mini
HomeAIAgent Gateway
  ├─ OpenAI ASR
  ├─ OpenAI TTS
  └─ 调用远程 OpenClaw
       │
       ▼
MacBook Air
OpenClaw Master Gateway :18789
Agent / Session / Tools
```

所以：

- **Air** 才运行 OpenClaw Gateway。
- **Mini 不运行 OpenClaw Gateway。**
- Mini 上 `openclaw gateway status --require-rpc` 不是 HomeAIAgent 的启动前置条件。
- Mini 只需要能访问 Air 的 OpenClaw `/v1/models` 与 `/v1/chat/completions`。
- StickS3 仍然只连接 Mini，不直接访问 Air。

---

# 1. Air 上需要做的事情

这些 OpenClaw 服务端设置全部在 **Air** 上执行。

## 1.1 确认 Gateway

```bash
cd ~
openclaw gateway status --require-rpc
```

## 1.2 启用 Chat Completions HTTP API

```bash
openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json
openclaw gateway restart --safe
```

该接口和 OpenClaw Gateway 使用同一端口，默认是 `18789`。

## 1.3 不要为了 HomeAIAgent 随便暴露公网

推荐优先级：

1. 现有安全 Tailnet / LAN direct endpoint
2. SSH tunnel
3. 不建议直接公开公网 18789

如果 Air 当前 Gateway 仍绑定 `loopback`，最稳的是 SSH tunnel，不需要改变 Air 的 bind。

---

# 2. Mini 如何连接 Air

## 方案 A：你现有的远程 OpenClaw 已经是 LAN / Tailnet direct

在 Mini 的 HOME 目录可以查看远程 URL：

```bash
cd ~
openclaw config get gateway.mode
openclaw config get gateway.remote.url
```

如果看到例如：

```text
ws://100.88.12.34:18789
```

HomeAIAgent 里使用：

```text
http://100.88.12.34:18789
```

如果看到：

```text
wss://xxx.ts.net
```

HomeAIAgent 使用：

```text
https://xxx.ts.net
```

注意：`/v1/*` HTTP API 仍然遵循 OpenClaw Gateway 的正常 token/password 身份验证。

## 方案 B：推荐的 SSH tunnel

如果你的 Air Gateway 保持默认 loopback：

在 Mini：

```bash
cd HomeAIAgent_P0_A3_2_REMOTE_OPENCLAW/gateway
./openclaw_air_tunnel.sh
```

脚本会建立：

```text
Mini 127.0.0.1:18790
      │ SSH
      ▼
Air 127.0.0.1:18789
```

这个终端窗口保持运行。

然后 HomeAIAgent 使用：

```text
OPENCLAW_BASE_URL=http://127.0.0.1:18790
```

这也是 P0-A3.2 的默认值。

---

# 3. Mini 安装 HomeAIAgent Gateway

在 Mini：

```bash
cd HomeAIAgent_P0_A3_2_REMOTE_OPENCLAW/gateway
chmod +x setup_mac.sh run_full.sh openclaw_air_tunnel.sh
./setup_mac.sh
```

它会问：

1. `OPENAI_API_KEY`
2. Air 上 OpenClaw 的 HTTP base URL
3. Air OpenClaw Gateway token

然后自动生成：

```text
gateway/.env
```

并执行：

```bash
python companion_gateway.py --check
```

成功：

```text
[OK] OPENAI_API_KEY is present
[OK] OpenClaw reachable
[READY] Gateway configuration is ready for a real voice turn
```

---

# 4. 启动 Mini HomeAIAgent Gateway

```bash
./run_full.sh
```

期待：

```text
[MODE] full
[WS] listening on ws://0.0.0.0:8765/companion
```

---

# 5. StickS3 设置

`include/secrets.h`：

```cpp
#define WIFI_SSID "yemo_home_2.4"
#define WIFI_PASSWORD "你的 Wi-Fi 密码"

#define GATEWAY_HOST "Mac mini 的局域网 IP"
#define GATEWAY_PORT 8765
#define GATEWAY_PATH "/companion"
```

注意这里永远填 **Mini IP**，不是 Air IP。

因为设备拓扑是：

```text
StickS3 → Mini → Air/OpenClaw
```

---

# 6. 第一轮真语音

按住 A：

> 你好，你是谁？

松开。

Mini：

```text
[ASR] 你好，你是谁？
[AGENT] ...
[TTS] ...
[LATENCY] ...
```

然后 StickS3 播放回答。

---

# 7. 关于刚才 Mini 上的 uv_cwd / EPERM

那条错误是 Mini 上 OpenClaw CLI 在当前 `gateway` 工作目录读取 cwd 时被 macOS 拒绝。

但在正确拓扑下，它并不意味着 Air 上的 OpenClaw Gateway 有问题。

如果只是想查看 Mini 已保存的远程配置，先：

```bash
cd ~
```

再执行：

```bash
openclaw config get gateway.mode
openclaw config get gateway.remote.url
```

HomeAIAgent 本身不要求 Mini 启动 OpenClaw Gateway。
