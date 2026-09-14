# HomeAIAgent Gateway CHANGELOG

## A3.0b FIX1 R11 FULL CLEAN

- 基于 R10 PRECOMMIT CLEAN + NetworkSpeaker repeated reconnect recovery 整合为完整替换包。
- NetworkSpeaker 播报期间 WebSocket 1006 断线后保持原输出设备，不切回 HomeAgent 本机。
- 单条回答支持最多 3 次断线重连恢复，并从最后确认完成的 TTS 分段继续。
- 保留 NetworkSpeaker 256 KiB 分段、4 KiB chunk 与温和发送节奏。
- 继承 R10 全部提交前清理、代理隔离、KitchenTerminal、Info、Notification、OpenClaw Transport 与审计脚本。
- 本版本为完整目录替换基线，不依赖旧项目目录中的任何源码文件。

# HomeAIAgent Gateway Changelog

## A3.0b FIX1 R10 PRECOMMIT CLEAN

- 提交前全量清理与冻结候选。
- 删除确认未引用的函数、常量、import 和旧 startup Info refresh 路径。
- 历史 Kitchen 版本说明合并到本文件，不再保留大量阶段性 Markdown。
- 删除 P0 mock、旧 subscriptions、一次性 wake-ack 生成器等已脱离当前运行链的工具。
- 源码/示例配置移除个人 SSH/Tailscale 地址与绝对用户路径。
- 默认日志收敛，高频 transport/UI 细节仅在 `HOMEAI_LOG_VERBOSE=true` 时输出。
- Python traceback 默认关闭，仅在 `HOMEAI_LOG_TRACEBACK=true` 时输出。
- 保留 OpenClaw loopback proxy bypass、分离超时预算、运行诊断目录外置等 R9 修复。

## A3.0b FIX1 R9 AUDIT CLEAN

- OpenClaw loopback HTTP 自动绕过环境代理。
- 主 Agent / Kitchen / Info 请求超时分别配置为 180 / 180 / 300 秒。
- runtime WAV/文本移到 `~/.local/share/HomeAIAgent/debug/`。
- 清理已删除 Kitchen 麦克风测试 UI 的死接口与残留。
- 修复 Kitchen action 非法参数处理、后台 task shutdown 回收。
- 增加发布前离线回归入口。

## A3.0b FIX1 R8

- 修复 KitchenTerminal 问逐光结束录音时未定义 `stopTracks` 导致的 JavaScript 异常。

## A3.0b FIX1 R7

- iPad 问逐光触控/状态恢复加强。
- 顶部计时器字号和可读性提升。

## A3.0b FIX1 R6

- NetworkSpeaker 长 TTS 使用更小分段与更温和发送节奏，降低 ESP32-C3 WebSocket/I2S 压力。

## A3.0b FIX1 R5

- 单轮语音回答锁定 audio sink。
- NetworkSpeaker 短暂断线时等待同一设备重连，不再自动跳回本机播放。

## A3.0b FIX1 R3–R4

- 厨房顶部测试按钮改为状态显示并加入帮助。
- 放大“问逐光”和默认页“加载今日菜单”按钮。
- 移除未同步页录音测试按钮。

## A3.0b / FIX1

- iPad PTT 问逐光链路：录音 → Gateway → Volcengine ASR → Kitchen context → OpenClaw → TTS → iPad 本地播放。
- Kitchen deterministic fast-path 扩充“推送/发送/同步菜单”等表达。

## A2.x

- HTTP polling 成为 KitchenTerminal authoritative display channel。
- Gateway-owned 持久计时器、iPad 本地厨房语音、结束烹饪、私房菜保存、进度恢复。
- Kitchen deterministic voice fast-path，OpenClaw 异常时仍可完成基础操作。

## A1

- 建立 Gateway ↔ KitchenTerminal 基本实时显示与控制链路。


## R12 FULL CLEAN
- Fixes the R11 persistent-config migration gap for existing installs.
- `setup_mac.sh` now detects an existing but incomplete `gateway.env` and launches a one-time repair instead of silently accepting it.
- `run_full.sh` validates the required persistent keys before starting the Gateway.
- Keeps machine-specific SSH routing outside the Git/release tree while making complete project replacement safe.
