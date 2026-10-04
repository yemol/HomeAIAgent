
### A6.1.1 Agent auth compile fix
- Fixed declaration order for `webSocket` and `gatewayConnected` used by the A6.1 auth response helper.
- No authentication protocol, NVS, audio, wake-word, or routing behavior changed.
# HomeAIAgent

当前正式源码基线，包含桌面 HomeAIAgent、Gateway、NetworkSpeaker 路由、Glass2、KitchenTerminal（小K）以及 A5.0 Selective Follow-up 连续追问能力。

## 1. 当前基线

- **语音主链路**：Wake / PTT → ASR → OpenClaw → TTS → Audio Route → playback ACK
- **A5.0 Selective Follow-up**：回答真实播放完成后，开放 10 秒当前上下文追问窗口
- **A5.0.1 Long Utterance Fix**：10 秒只限制“开始说话”，窗口内开口后可继续录制到现有 20 秒语音上限再上传
- **Context Judge**：仅允许当前上下文的自然延续进入原 OpenClaw 会话；新话题、歧义或环境语音静默忽略
- **NetworkSpeaker**：只负责网络音频输出，不持有 AI / Session 状态
- **Wake phrase**：`逐光同学`
- **Wake / command PGA**：6 dB / 6 dB
- **Glass2 active brightness**：96
- **Info hold**：15 秒
- **KitchenTerminal**：A3.0b FIX1 R50.10，今日采购库存对照使用规范身份精确匹配

A5.0 已完成实机验证：连续两轮相关追问均进入原会话；故意切换到无关“晚饭”话题时 Context Judge 返回 `ignore`，系统静默丢弃且未创建新会话。

## 2. 架构边界

```text
StickS3 / HomeAIAgent Mini / NetworkSpeaker
                    |
                    | WebSocket
                    v
           HomeAIAgent Gateway (Mac mini)
              |      |       |
             ASR    TTS    Device / Audio Router
                    |
                    +-- embedded SSH/Tailscale --> OpenClaw

KitchenTerminal (iPad) <--> Gateway /kitchen + /kitchen/ws
Glass2                 <-- Gateway pre-rendered info frames
```

职责保持如下：

- **Gateway**：流程、设备连接、会话生命周期、Audio Route、播放完成事件、Follow-up 时间窗口
- **OpenClaw**：对话理解、技能调用、Context Judge 语义判断
- **StickS3 / Mini**：本地唤醒、麦克风、PTT、显示和设备状态
- **NetworkSpeaker**：音频 sink
- **KitchenTerminal**：厨房专用 UI / 语音终端

原则：**Gateway 负责流程，AI 负责理解。**

## 3. A5.0 Selective Follow-up

```text
主动唤醒 / PTT
  -> ASR
  -> 原 OpenClaw 会话
  -> TTS
  -> 真实 playback.done
  -> 0.5 s Audio Fence
  -> FOLLOW_UP_WAIT (10 s)
       -> 无语音：自然过期
       -> 检测到语音：复用现有 VAD / PTT 录音链
            -> ASR
            -> 隔离 Context Judge
                 -> continue：进入原会话
                 -> ignore / error：静默丢弃并结束追问链
```

默认配置位于 `gateway/gateway.env.example`：

```text
HOMEAI_FOLLOWUP_TIMEOUT_SEC=10
HOMEAI_FOLLOWUP_AUDIO_FENCE_SEC=0.5
HOMEAI_FOLLOWUP_JUDGE_TIMEOUT_SEC=12
HOMEAI_FOLLOWUP_JUDGE_CLEANUP_TIMEOUT_SEC=2
HOMEAI_FOLLOWUP_LATCHED_ARRIVAL_GRACE_SEC=30
HOMEAI_FOLLOWUP_JUDGE_SESSION_CLEANUP=true
```

实现特点：

- 不新增 Gateway 常驻轮询
- 不新增 Follow-up 后台 task
- StickS3 复用已有 wake microphone 和现有 VAD
- Context Judge 使用独立临时 session，不污染稳定 voice session
- Judge / ASR 在批准前失败时 fail-closed
- Follow-up 只延续当前会话，**绝不把新话题变成免唤醒新会话**
- KitchenTerminal iPad 音频没有 companion 侧精确 `playback.done` ACK，因此当前不启用无唤醒追问窗口

## 4. 目录

```text
gateway/
  companion_gateway.py       Gateway 主服务
  core/session.py            ClientSession / 核心会话结构
  context/followup.py        A5.0 Follow-up 状态与 Context Judge
  openclaw_transport.py      OpenClaw 内嵌 SSH/Tailscale transport
  kitchen_full.html          小K前端
  kitchen_menu.py            小K菜单解析
  audit_a5_followup.sh       当前 A5.0 核心回归
  audit_gateway.sh           Gateway 扩展回归
  test_*.py                  历史与当前回归资产

src/main.cpp                  StickS3 主固件
include/app_config.h          固件公共参数
include/wake_ack_voice_pcm.h  冻结的本地“在的”PCM
assets/wake_ack_zaide_tts.wav 对应的批准音频资产
scripts/                      PlatformIO / ESP-SR 构建辅助
tools/                        配置、检查和清包工具
platformio.ini                StickS3 构建配置
esp_sr_8.csv                  8MB ESP-SR 分区表
srmodels.bin                  固定 ESP-SR 模型分区镜像
```

## 5. Gateway 部署

首次准备：

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent/gateway
./setup_mac.sh
```

日常启动：

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent/gateway
./run_full.sh
```

持久配置和运行数据位于源码目录之外：

```text
~/.config/HomeAIAgent/gateway.env
~/.local/share/HomeAIAgent/
```

不要同时运行旧的手工 OpenClaw SSH tunnel。Gateway 会自己维护本地转发。

正常关键日志：

```text
[OPENCLAW-SSH] SSH connected
[OPENCLAW-SSH] tunnel ready ...
[WS] listening on ws://0.0.0.0:8765/companion
```

A5.0 关键日志：

A5.0.1 起，每个追问窗口会带一次性 `arm_id`。StickS3 只要在本地 10 秒窗口内检测到开口，就锁定该 `arm_id`；录音结束后再上传。Gateway 因此不会把 10 秒误解为“整句话必须在 10 秒内说完”。当前自动语音捕获仍沿用 `AUDIO_MAX_PTT_MS=20000`，即单句最长约 20 秒。


```text
[AUDIO] device playback done
[AUDIO-ROUTE] reply complete ...
[FOLLOWUP] armed ... timeout=10.0s
[PTT] start trigger=follow_up
[FOLLOWUP-JUDGE] decision=continue|ignore ...
```

## 6. StickS3 构建与刷写

生产环境：

```text
m5stack-sticks3-wake
```

工程依赖开发 Mac 上已有的 `.pio-local/` 和本地 pioarduino / ESP-SR，正式源码包不包含这些本地依赖。

```bash
pio run -e m5stack-sticks3-wake -t clean
pio run -e m5stack-sticks3-wake -t upload
pio device monitor
```

不要 Erase Flash。当前启动关键日志：

```text
=== HomeAIAgent A6.1 / Device Auth Client / Wake=逐光同学 ===
[AUTH]   device_id=homeai-agent-main-01
[AUTH]   secret=SET|MISSING
[WAKE] local keyword engine ready: 逐光同学
```

### A6.1 主 Agent 设备认证

主 Agent 的公开身份固定为 `homeai-agent-main-01`，角色为 `companion`。设备密钥不写入源码，存放在 ESP32-S3 的 `homeai_auth` NVS namespace。

串口本地维护命令：

```text
AUTH SHOW
AUTH SET <Gateway 为该设备生成的 256-bit base64url secret>
AUTH CLEAR
```

`AUTH SHOW` 只显示密钥是否已配置及当前会话是否已认证，不输出密钥本身。写入密钥后重启或重连设备。收到 Gateway 的 `security.challenge` 后，StickS3 使用 HMAC-SHA256 回应；成功日志为：

```text
[AUTH] challenge answered
[AUTH] authenticated with Gateway
```

Gateway 侧可随时查看所有设备状态：

```bash
~/.local/share/HomeAIAgent/venv/bin/python gateway/security_cli.py devices
```

迁移期间保持 `HOMEAI_SECURITY_MODE=observe`；只有所有正式设备均显示 `AUTHENTICATED` 或曾验证后的 `OFFLINE_VERIFIED`，才进入 enforce 阶段。

## 7. 关键硬件与音频参数

### StickS3 + Glass2

```text
Glass2 5V  -> Grove 5V
Glass2 SDA -> GPIO9
Glass2 SCL -> GPIO10
Glass2 I2C -> 0x3C
```

当前音频基线：

```text
Wake listen PGA:      6 dB
Command capture PGA:  6 dB
Wake cooldown:        5 s
Wake 后等待说话:       5 s
连续静音结束:          3 s
Wake ACK:             本地冻结“在的” PCM
```

StickS3 使用 ES8311 半双工音频路径。录音结束后才上传 PCM，播放完成后恢复唤醒监听。

### NetworkSpeaker

当前验证基线：ESP32-C3 SuperMini + MAX98357A，`GPIO4=BCLK`、`GPIO5=LRCLK/WS`、`GPIO6=DIN`。NetworkSpeaker 仅作为 Gateway 路由的音频输出端。

## 8. KitchenTerminal / 小K

当前正式厨房基线：**R50.10**。

长期规则：每道菜第 1 步固定为“备菜”，且只包含不开火的清洗、切配、泡发、解冻、腌制、调汁、称量等准备动作；焯水、预热、烧水和正式烹饪属于后续步骤。

R50.10 根修今日采购库存误匹配：

```text
采购行提取食材名
 -> 食材身份规范化
 -> 规范身份精确匹配
```

不再使用字符串包含匹配，因此“牛肉”与“牛肉片”、“猪肉”与“猪肉丝”保持不同食材身份。

KitchenTerminal 页面：

```text
http://<gateway-lan-ip>:8765/kitchen
ws://<gateway-lan-ip>:8765/kitchen/ws
```

iPad 麦克风应通过已验证的 HTTPS / Tailscale Serve 安全上下文使用。

## 9. Info / Glass2

Gateway 启动只读取 last-good cache。固定刷新时段（Asia/Taipei）：

```text
01:00, 09:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00, 23:00
```

目标最多 10 条游戏 + 10 条金融。单分类失败保留 last-good，不清空 Glass2。

## 10. 协议要点

设备到 Gateway 麦克风：PCM16 LE / mono / 16 kHz。

Gateway TTS 使用：

```text
tts.start -> binary PCM -> tts.end
```

最终真实播放完成由设备返回：

```json
{"type":"playback.done"}
```

A5.0 追问窗口由 Gateway 下发：

```json
{"type":"followup.arm","timeout_ms":10000}
```

详细字段以 `companion_gateway.py`、`src/main.cpp` 和协议自测代码为最终权威，避免再维护一套容易过期的独立协议说明。

## 11. 检查与清包

A5.0 核心回归：

```bash
cd gateway
./audit_a5_followup.sh
```

Gateway 扩展回归：

```bash
cd gateway
./audit_gateway.sh
```

源码编码检查：

```bash
cd gateway
./check_source_encoding.sh
```

正式打包前清理运行产物：

```bash
./tools/clean_submission_artifacts.sh
```

清理脚本不会删除 `.pio-local/`、`~/.config/HomeAIAgent/` 或 `~/.local/share/HomeAIAgent/`。

## 12. 文档规则

正式源码包只保留：

- `README.md`：当前架构、部署、关键参数与当前功能基线
- `CHANGELOG.md`：历史变更
- `THIRD_PARTY_NOTICES.md`：第三方许可声明

阶段性实验说明、旧 BUILD_INFO、独立部署说明和一次性审核报告不再随正式包累积。需要判断当前行为时，以 **README + 当前源码 + 回归测试** 为准。
