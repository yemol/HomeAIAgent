# HomeAIAgent P0-A3 — 真 AI 语音对话

这一版从 loopback 正式推进到：

StickS3 Mic
→ 16 kHz PCM16
→ HomeAIAgent Gateway
→ OpenAI ASR
→ OpenClaw Agent / Tools
→ OpenAI TTS
→ StickS3 Speaker

## 已保留
- A2.7.2 的 reset reason / RTC breadcrumb / internal-DMA memory diagnostics 全部保留。
- Speaker：master 255 / magnification 4。
- Mic：PGA +6 dB / digital mag 16 / noise filter 64。
- PTT 期间 Glass2 继续冻结当前资讯，确保“这个讲讲”不漂移。

## 已结束
A2.6 的 Glass2 NORMAL/FREEZE/POWER_OFF 实机听感基本一致。
因此 B 键恢复正常资讯控制：
- 短按 B：下一条
- 长按 B：上一条

## Mac mini 第一次配置

进入：

```bash
cd gateway
cp .env.example .env
```

编辑 `.env`：

```text
P0_MODE=full
OPENAI_API_KEY=你的 OpenAI API Key

OPENCLAW_BASE_URL=http://127.0.0.1:18789
OPENCLAW_TOKEN=你的 OpenClaw Gateway Token   # 如果 auth.mode 不需要 token，可留空
OPENCLAW_MODEL=openclaw/default
```

OpenClaw 需要启用：

```json5
gateway: {
  http: {
    endpoints: {
      chatCompletions: { enabled: true }
    }
  }
}
```

项目中已经提供：
`gateway/openclaw_config_snippet.json5`

## 先做 preflight

```bash
source .venv/bin/activate
python companion_gateway.py --check
```

期待最后看到：

```text
[OK] OPENAI_API_KEY is present
[OK] OpenClaw reachable
[READY] Gateway configuration is ready for a real voice turn
```

或者直接：

```bash
./run_full.sh
```

它会：
1. 创建/使用 `.venv`
2. 安装 requirements
3. 运行 preflight
4. 启动 Gateway

## 真正测试

StickS3：
1. 按住 A。
2. 说：
   “你好，你是谁？”
3. 松开 A。

Gateway 应依次出现：

```text
[ASR] 你好，你是谁？
[AGENT] ...
[TTS] ...
[LATENCY] asr=... agent=... tts=...
```

StickS3 串口也会显示：

```text
[ASR] ...
[AGENT] ...
```

然后板载扬声器直接说出 OpenClaw 的答案。

第二个测试：

当 Glass2 正显示某条资讯时，说：

“这个讲讲。”

Gateway 会把当前 / 上一条 / 下一条资讯上下文一起交给 OpenClaw。

## 调试留档

每轮会保留：
- `gateway/latest_input.wav`
- `gateway/latest_transcript.txt`
- `gateway/latest_answer.txt`
- `gateway/latest_tts.wav`

如果后面出现识别错误、Agent 答非所问、TTS 不自然，可以逐层拆开看，不需要重新猜整条链路。

## 当前 P0 限制
- Push-to-talk，暂未做唤醒词/VAD。
- 回答音频先完整生成，再发送给 StickS3，暂未做流式边生成边播放。
- Glass2 当前仍是 demo 资讯，真实订阅服务是下一阶段。
