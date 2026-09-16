## A3.0b FIX1 R16 FULL CLEAN

- 将“每道菜第 1 步固定为备菜”从菜单生成约定升级为 Gateway 永久数据层规则。
- `kitchen_menu.ensure_recipe_prep_first()` 对菜谱对象做幂等规范化，旧菜单、漏写备菜的菜单和未来替代调用路径都不能绕过。
- 新菜单即使完全缺少 `统一备菜/提前备菜` 区块，也会生成第 1 步备菜，至少列出食材、调味与“无需额外处理”的明确说明。
- 若旧菜单把清洗、切配、泡发、解冻、腌制、焯水、调汁等准备动作写进正式做法，Gateway 只从原文保守回收这些动作到备菜页，不凭空创造处理方式。
- 新增 prep-required 回归测试，未来版本一旦丢失第 1 步备菜，`audit_gateway.sh` 会直接失败。

# HomeAIAgent Gateway CHANGELOG

## A3.0b FIX1 R15 FULL CLEAN

- 基于 R14 FULL CLEAN 完整基线修改，保留 HomePod / AirPlay、备菜、问逐光、NetworkSpeaker、Info、OpenClaw 等现有功能。
- 恢复 KitchenTerminal 顶部安全留白：使用 `safe-area-inset-top + 18px`，避免 iPad 状态栏紧贴应用头部。
- 等待首页在“加载今日菜单”下方新增独立计时器，无需加载菜单即可使用。
- 独立计时器支持 5 / 10 / 15 分钟快捷启动、自定义时间、暂停、继续、加 1 分钟、重新设置和取消。
- 独立计时器由 Gateway 持久化，使用 `kitchen_timers.json` version 3，并与菜谱计时器并存；新建独立计时只替换旧独立计时，不影响菜谱计时。
- 时间到后，通过 KitchenTerminal 同一个持久 `HTMLAudioElement` 循环播放专用提示音，直到用户主动“结束提醒”；可沿用当前 iPadOS / HomePod / AirPlay 播放目标。
- 新增 `/kitchen/alarm.wav` 媒体端点，支持与厨房 TTS 相同的 HTTP byte-range 播放路径。
- 当独立计时警报响铃时，新到的普通厨房 TTS 会暂存，结束提醒后再继续播放，避免互相抢占。
- 新增独立计时器回归测试，并把安全留白、持续提醒、媒体链纳入发布前审核。

## A3.0b FIX1 R14 FULL CLEAN

- 基于 R13 FULL CLEAN 完整基线修改，保留备菜、计时器、问逐光、NetworkSpeaker、Info、OpenClaw 等现有功能。
- KitchenTerminal TTS 输出从 Web Audio `AudioContext.decodeAudioData()` 改为持久 `HTMLAudioElement`，让 iPadOS 系统媒体路由接管输出。
- 顶部新增「播放设备」按钮；在支持的 WebKit 环境调用 `webkitShowPlaybackTargetPicker()`，可直接选择 HomePod / AirPlay。
- 监听 `webkitCurrentPlaybackTargetIsWireless`，无线目标启用时顶部显示 `AirPlay 已连接`。
- `/kitchen/audio` 增加 HTTP byte-range / 206 Partial Content 支持，兼容 Safari / iPadOS 标准媒体加载。
- 首次触碰页面仍会预激活音频，但不再创建 TTS `AudioContext`；问逐光麦克风采集的 AudioContext 保持独立。
- 新增 HTML media/AirPlay UI 与 byte-range 回归检查。

## A3.0b FIX1 R13 FULL CLEAN

- 直接基于用户最新上传的 Gateway 完整目录修改，不回退到此前生成包。
- KitchenTerminal 每道菜固定新增第 1 步「备菜」，开火前集中显示食材、调味和明确的提前处理事项。
- 支持菜谱内 `#### 备菜`，同时兼容 `## 统一备菜` / `## 提前备菜`；旧菜单没有专门备菜结构时仍会从食材、调味生成安全的准备清单，不猜测不存在的处理动作。
- 原烹饪步骤整体顺延一位，计时提示同步顺延。
- `kitchen_progress.json` schema 1→2、`kitchen_timers.json` version 1→2 自动迁移，避免升级后已有进度/计时器错位。
- 私房菜保存时将备菜单独写入 `## 🔪 备菜`，正式做法仍只保存烹饪步骤。
- UI 对备菜页显示 `备菜 · 1 / N`，菜单继续进度也会显示「继续 · 备菜」。

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
