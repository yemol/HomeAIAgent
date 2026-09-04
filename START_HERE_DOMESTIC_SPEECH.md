# HomeAIAgent P0-A3.4｜国内语音引擎配置与操作手册

日期：2026-09-03

## 1. 这一版改了什么

语音链从：

```text
OpenAI ASR → OpenClaw → OpenAI TTS
```

正式改为：

```text
火山引擎豆包 ASR
        ↓
Air / OpenClaw
        ↓
火山引擎豆包 TTS
```

OpenAI 不再是语音链的必需项。

Provider 已经从架构上解耦：

```text
ASR_PROVIDER=volcengine
TTS_PROVIDER=volcengine
ASR_FALLBACK=none
TTS_FALLBACK=none
```

以后换腾讯、讯飞、阿里时，不需要再重写 OpenClaw 或 StickS3。

---

## 2. 你的正式网络拓扑保持不变

```text
StickS3
  │ Wi-Fi / WS :8765
  ▼
Mac mini / HomeAIAgent Gateway
  ├─ 豆包 ASR
  ├─ 豆包 TTS
  └─ OpenClaw HTTP bridge
        │
        │ Mini 127.0.0.1:18790
        ▼
SSH Tunnel over Tailscale
        ▼
MacBook Air 100.105.66.46
        ▼
Air 127.0.0.1:18789
OpenClaw Master Gateway
```

StickS3 仍然只连接 Mini。

---

## 3. 火山引擎要开通的两个能力

### ASR

使用：

```text
豆包语音 → 语音识别大模型 → 大模型录音文件极速版
```

HomeAIAgent 使用资源：

```text
volc.bigasr.auc_turbo
```

接口：

```text
POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash
```

StickS3 的 16 kHz mono PCM 会先由 Mini 包成 WAV，再直接 Base64 上传。

### TTS

使用：

```text
豆包语音 → 语音合成大模型
```

HomeAIAgent 使用 V3 SSE：

```text
https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse
```

默认：

```text
Resource ID = seed-tts-2.0
Voice = zh_female_vv_uranus_bigtts
Output = PCM16 mono 16 kHz
```

这样 TTS 返回的 PCM 可以直接发给 StickS3，不再做 MP3 解码或重采样。

---

## 4. 获取火山引擎凭据

新版语音控制台优先使用：

```text
VOLCENGINE_API_KEY
```

新版 API Key 的好处是 ASR/TTS 都可以走 `X-Api-Key`。

如果你的账号仍是旧版语音控制台，也支持：

```text
VOLCENGINE_APP_ID
VOLCENGINE_ACCESS_KEY
```

HomeAIAgent 会自动选择对应鉴权格式。

机密只放：

```text
Mac mini / gateway/.env
```

不要放进 StickS3。

---

## 5. Mini 第一次配置

先保持 OpenClaw Tunnel：

```bash
cd HomeAIAgent_P0_A3_4_DOMESTIC_SPEECH/gateway
./openclaw_air_tunnel.sh
```

另开 Terminal：

```bash
cd HomeAIAgent_P0_A3_4_DOMESTIC_SPEECH/gateway
chmod +x setup_mac.sh check_connection.sh run_full.sh openclaw_air_tunnel.sh
./setup_mac.sh
```

它会问：

```text
VOLCENGINE_API_KEY
OpenClaw token
```

如果 API Key 留空，会自动询问旧版：

```text
VOLCENGINE_APP_ID
VOLCENGINE_ACCESS_KEY
```

---

## 6. 检查配置

```bash
./check_connection.sh
```

正常应看到：

```text
[OK] SSH tunnel endpoint 127.0.0.1:18790 is open.
[CFG] ASR=volcengine fallback=none
[CFG] TTS=volcengine fallback=none
[OK] Volcengine credentials present
[OK] OpenClaw reachable
[READY] Domestic speech configuration is ready for a real voice turn.
```

---

## 7. 启动

Terminal 1：

```bash
./openclaw_air_tunnel.sh
```

Terminal 2：

```bash
./run_full.sh
```

预期：

```text
[MODE] full: volcengine ASR -> OpenClaw -> volcengine TTS
[WS] listening on ws://0.0.0.0:8765/companion
```

---

## 8. 真语音测试

按住 StickS3 A：

> 你好，你是谁？

松开。

Mini：

```text
[AUDIO] captured ...
[ASR:volcengine] 你好，你是谁？
[AGENT] ...
[TTS:volcengine] ... bytes @ 16000 Hz
[LATENCY] ...
```

然后 StickS3 直接播放豆包 TTS。

---

## 9. 语音音色

默认：

```text
VOLCENGINE_TTS_VOICE=zh_female_vv_uranus_bigtts
```

这是为了先把链路跑通。

以后选择 HomeAIAgent 的“宠物人格声线”只需要改：

```text
VOLCENGINE_TTS_VOICE=...
```

不用改程序。

---

## 10. Provider 架构

当前已经支持：

```text
ASR_PROVIDER=volcengine
ASR_PROVIDER=openai

TTS_PROVIDER=volcengine
TTS_PROVIDER=openai
```

也支持独立 fallback，例如：

```text
ASR_PROVIDER=volcengine
ASR_FALLBACK=openai

TTS_PROVIDER=volcengine
TTS_FALLBACK=none
```

默认 fallback 为 `none`，避免国内服务偶发失败时偷偷消耗另一家 API 费用。

---

## 11. 当前冻结参数

```text
StickS3 Mic:
PGA +6 dB
digital mag 16
noise filter 64

Speaker:
master 255
magnification 4

Wi-Fi reconnect:
30 / 60 / 120 / 240 sec

OpenClaw:
Air 127.0.0.1:18789
Mini tunnel 127.0.0.1:18790

Tailscale Air:
100.105.66.46

TTS:
PCM16 mono 16000 Hz
```

Reset Forensics 继续保留。
