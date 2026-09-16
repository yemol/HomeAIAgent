# HomeAIAgent Gateway

当前完整替换基线：**A3.0b FIX1 R16 FULL CLEAN**

Gateway 运行在 Mac mini，是 HomeAIAgent 设备、KitchenTerminal、NetworkSpeaker 与上游 AI/语音服务之间的本地调度中心。

## 当前职责

- `/companion` 设备 WebSocket：HomeAIAgent 主终端、Mini、NetworkSpeaker
- KitchenTerminal：`/kitchen`、HTTP polling、触摸操作、计时器、问逐光
- Volcengine Streaming ASR 2.0 / TTS
- OpenClaw Chat Completions，通过 Gateway 自管 Embedded SSH tunnel 访问
- 多设备 OpenClaw session 路由
- NetworkSpeaker 音频 sink 路由、固定单轮输出、短断线恢复
- Info Skill 定时刷新、last-good cache、手动强制刷新
- Glass2 资讯/状态同步与夜间屏保策略
- Notification listener + Voice Turn Fence

## 持久化目录

配置：

```text
~/.config/HomeAIAgent/gateway.env
```

运行状态、缓存、录音诊断：

```text
~/.local/share/HomeAIAgent/
```

源码更新或整目录替换不会删除以上两处数据。

## 首次部署

```bash
./setup_mac.sh
```

首次配置时会要求：

- Volcengine API Key
- OpenClaw token
- OpenClaw 主机 SSH user
- OpenClaw 主机 Tailscale IP / hostname

敏感信息只写入持久化 `gateway.env`，不写入仓库。

OpenClaw 需要启用 OpenAI-compatible Chat Completions endpoint：

```text
/v1/chat/completions
```

## 日常启动

```bash
./run_full.sh
```

只需要启动这一项。不要同时运行旧的手工 SSH tunnel。

## KitchenTerminal

推荐通过 Tailscale Serve HTTPS 访问：

```text
https://<Mac-mini>.ts.net/kitchen
```

核心流程：

```text
等待首页可直接使用独立计时器
或加载今日菜单
→ 选择菜品
→ 每道菜先进入「备菜」步骤，一次确认食材 / 调味 / 提前处理
→ 再进入正式烹饪步骤
→ 需要时启动计时器
→ 问逐光处理复杂烹饪问题
→ iPad 系统媒体播放厨房回答，可路由到 HomePod / AirPlay
→ 结束今日烹饪 / 可选保存私房菜
```

确定性厨房操作优先走 Gateway 本地 Fast-path，不依赖 OpenClaw：今日菜单、上一/下一步、上一/下一道菜、购物清单、烧菜顺序、菜谱计时器、独立计时器、结束烹饪等。

### 独立计时器

等待首页在“加载今日菜单”下方提供独立计时器，可直接选择 5 / 10 / 15 分钟或自定义时间，不需要加载任何菜单。独立计时状态由 Gateway 持久化，刷新页面后仍可恢复。时间到后，iPad 使用同一个系统媒体 `HTMLAudioElement` 循环播放提示音，直到用户主动点击“结束提醒”。因此已选择的 HomePod / AirPlay 输出仍可沿用。

### KitchenTerminal 音频输出

厨房 TTS 使用一个长期存在的 `HTMLAudioElement` 播放，不再用 `AudioContext.decodeAudioData()` 输出。这样声音进入 iPadOS 的系统媒体链，可以跟随系统当前输出设备，并在支持的 WebKit 环境中通过顶部「播放设备」按钮调用系统 AirPlay 选择器。

`/kitchen/audio` 支持 HTTP byte range（206 Partial Content），用于 Safari / iPadOS 媒体栈和 AirPlay 的标准媒体请求。问逐光的麦克风录音仍使用独立 Web Audio 采集链，输出设备切换不会改动录音链。

## NetworkSpeaker

NetworkSpeaker 通过 `/companion` 注册为 `role=speaker` 并绑定 `parent_device_id`。

一条回答开始后会锁定当前 audio sink。NetworkSpeaker 短暂断开时，Gateway 等待同一设备重连，不会中途自动切回主终端本机播放。

长 TTS 对 NetworkSpeaker 使用更温和的分段/发送节奏，避免 ESP32-C3 长时间播放时的 WebSocket 压力。

## Info Skill

正常定时刷新：

```text
09 / 11 / 13 / 15 / 17 / 19 / 21 / 23 / 01
```

手动强制刷新：

```bash
./force_info_refresh.sh
```

启动 Gateway 默认只读取 last-good cache，不额外消耗一次刷新。

## 本地 OpenClaw 与系统代理

当 `OPENCLAW_BASE_URL` 是 `127.0.0.1 / localhost / ::1` 时，Gateway 会自动禁用 HTTPX 的环境代理继承，避免系统全局代理误劫持本机 SSH tunnel。

## 日志

默认日志只保留状态变化、请求阶段、错误和关键路由信息。高频 UI/传输细节默认关闭。

需要临时诊断时在持久化配置中打开：

```bash
HOMEAI_LOG_VERBOSE=true
HOMEAI_LOG_TRACEBACK=true
```

排查完建议恢复为 `false`。

## 发布前审核

```bash
./audit_gateway.sh
```

审核包括 Python 编译、Shell 语法、静态洁净度、KitchenTerminal、Info、Notification、多设备路由、NetworkSpeaker 长 TTS/固定路由、OpenClaw loopback proxy bypass 等离线回归。

## 关键文件

```text
companion_gateway.py       Gateway 主服务
openclaw_transport.py      Embedded SSH transport
kitchen_menu.py            kitchen-menu-v1 解析
kitchen_send.py            KitchenTerminal 本机控制/调试客户端
force_info_refresh.py      Info 手动强刷
force_info_refresh.sh      Info 手动强刷入口
run_full.sh                日常启动入口
setup_mac.sh               首次部署
configure_mac.sh           重新配置持久化参数
runtime_status.sh          当前持久化运行环境状态
KITCHEN_COMMANDS.md        厨房命令约定
audit_gateway.sh           发布前离线审核
precommit_static_audit.py  仓库洁净度与静态检查
```

## 冻结原则

R16 作为当前 KitchenTerminal 完整替换基线。R16 继承 R15 的 AirPlay、顶部安全留白与独立计时器，并把“每道菜第 1 步必须是备菜”提升为 Gateway 数据层硬规则。即使菜单文件漏写统一备菜、旧菜单对象残留或未来调用路径绕过生成 Skill，KitchenTerminal 仍会在打开菜谱前强制规范化为 prep-first；仅复用来源中已经存在的清洗、切配、泡发、腌制等文字，不凭空猜测处理方式。冻结期间只接受明确缺陷修复，不做大范围架构拆分或无关功能扩张。下一阶段再考虑把 `companion_gateway.py` 中 Device Router、Kitchen、Info、Audio Router 等职责拆成独立模块。

## Persistent configuration and full replacement

Runtime secrets and machine-specific OpenClaw SSH routing live outside the source tree at `~/.config/HomeAIAgent/gateway.env`. They are intentionally not bundled into release ZIPs or Git. `./configure_mac.sh` writes/updates this file atomically and keeps unrelated keys. The values survive reboot and complete deletion/replacement of the `gateway` project directory.

For an existing installation upgraded from an older package, run `./setup_mac.sh` once. If the persistent file is present but missing the newer embedded-SSH fields, setup now detects the schema gap and invokes the repair flow automatically. `./run_full.sh` also validates required fields before starting and gives a direct repair instruction instead of failing later inside the transport manager.
