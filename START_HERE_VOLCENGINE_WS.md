# HomeAIAgent P0-A3.5｜豆包语音新版 API Key + V3 WebSocket

日期：2026-09-03

## 已按你提供的官方文档修正

TTS 正式使用：

```text
wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream
```

握手 Header：

```text
X-Api-Key
X-Api-Resource-Id
X-Api-Connect-Id
X-Control-Require-Usage-Tokens-Return
```

默认：

```text
X-Api-Resource-Id = seed-tts-2.0
```

因此你现在这个新版豆包语音控制台只需要创建：

```text
Speech API Key
```

P0-A3.5 不再要求 TTS APP ID / Access Token。

## TTS 二进制流程

```text
建立 WSS
→ SendText: 11 10 10 00 + payload长度 + JSON
→ 350 TTSSentenceStart
→ 352 TTSResponse 音频块 × N
→ 351 TTSSentenceEnd
→ 152 SessionFinished
→ FinishConnection event=2
→ close
```

输出固定：

```text
PCM16
mono
16000 Hz
```

与 StickS3 当前播放链一致。

## ASR 当前保持“极速版”

HomeAIAgent 是 PTT：

```text
按住 A → 说完 → 松开 → 本地已有短 WAV
```

所以当前继续使用：

```text
POST /api/v3/auc/bigmodel/recognize/flash
X-Api-Resource-Id: volc.bigasr.auc_turbo
```

这个路径可以直接上传本地短音频，最适合当前 1～15 秒对话。

你已经开通“录音文件识别2.0”后，请在服务详情里确认“极速版”权限可用。
如果你只看到标准 2.0：

```text
volc.seedasr.auc
```

不要只改 Resource ID，因为标准版可能需要 submit/query 和音频 URL/TOS，这不是同一个调用流程。

## Mini 第一次设置

```bash
cd HomeAIAgent_P0_A3_5_VOLCENGINE_WS/gateway
chmod +x *.sh
./setup_mac.sh
```

只输入：

```text
VOLCENGINE_API_KEY
OpenClaw token
```

然后：

Terminal 1：

```bash
./openclaw_air_tunnel.sh
```

Terminal 2：

```bash
./check_speech.sh
./run_full.sh
```

启动预期：

```text
[MODE] full: volcengine ASR -> OpenClaw -> volcengine TTS
[TTS] Volcengine V3 unidirectional WebSocket
```

## 真语音测试

按住 A：

> 你好，你是谁？

预期：

```text
[ASR:volcengine] 你好，你是谁？
[AGENT] ...
[TTS:volcengine] ... bytes @ 16000 Hz
```

然后 StickS3 播放回答。

## 当前拓扑不变

```text
StickS3
→ Mini :8765
→ Mini 127.0.0.1:18790
→ SSH over Tailscale
→ Air 100.105.66.46
→ Air OpenClaw 127.0.0.1:18789
```

## 保持
- Wi-Fi backoff 30 / 60 / 120 / 240 秒
- Mic PGA +6 dB / mag16 / NF64
- Speaker 255 / mag4
- Glass2 正常资讯逻辑
- Reset Forensics
