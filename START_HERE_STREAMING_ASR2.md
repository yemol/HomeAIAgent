# HomeAIAgent P0-A3.6｜流式语音识别 2.0

日期：2026-09-03

## 当前默认链路

```text
StickS3
→ Mac mini
→ 豆包 流式语音识别2.0
→ Air / OpenClaw
→ 豆包 语音合成2.0
→ StickS3
```

同一个新版豆包语音 Speech API Key 同时给 ASR/TTS 使用。

## ASR 2.0

```text
Endpoint:
wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async

Resource ID:
volc.seedasr.sauc.duration
```

使用优化双向流式接口，并开启：

```text
enable_nonstream=true
```

即实时流式结果 + 二遍识别，提高最终准确率。

HomeAIAgent 当前仍保持原 PTT 体验：

```text
按住 A
→ 设备录音
→ 松开 A
→ Mini 已拿到完整 PCM
→ Mini 以 200ms 音频包喂给 ASR 2.0
→ 获取 final
```

所以这次 **不需要重新刷 StickS3 固件**。

以后要进一步降低延迟，再把“Mini 缓冲完成后发送”升级成“设备录到一包就立即转发 ASR”。

## 配置

```bash
cd HomeAIAgent_P0_A3_6_STREAMING_ASR2/gateway
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

## 单独测试 ASR 2.0

如果旧 gateway 目录中已经有 `latest_input.wav`，复制到新 gateway 后：

```bash
python test_asr2_live.py
```

成功：

```text
[ASR:volcengine] connected logid=...
[PASS] transcript=你好，你是谁？
```

这一步可以不经过 OpenClaw 和 TTS，先确认 ASR 2.0 权限。

## 预期完整日志

```text
[MODE] full: volcengine ASR2 streaming -> OpenClaw -> volcengine TTS
[PTT] stop ...
[AUDIO] captured ...
[ASR:volcengine] connected logid=...
[ASR:volcengine] 你好，你是谁？
[AGENT] ...
[TTS:volcengine] ... bytes @ 16000 Hz
[LATENCY] ...
```

## 冻结不变

- Air OpenClaw
- Mini ↔ Air：Tailscale + SSH
- Mini tunnel：127.0.0.1:18790
- StickS3 → Mini：8765
- Mic PGA +6 dB / mag16 / NF64
- Speaker 255 / mag4
- Wi-Fi backoff 30 / 60 / 120 / 240 秒
- Reset Forensics
