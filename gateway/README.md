# HomeAIAgent Gateway

当前完整替换基线：**A3.0b FIX1 R12 FULL CLEAN**

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
加载今日菜单
→ 选择菜品 / 步骤
→ 需要时启动计时器
→ 问逐光处理复杂烹饪问题
→ iPad 本地播放厨房回答
→ 结束今日烹饪 / 可选保存私房菜
```

确定性厨房操作优先走 Gateway 本地 Fast-path，不依赖 OpenClaw：今日菜单、上一/下一步、上一/下一道菜、购物清单、烧菜顺序、计时器、结束烹饪等。

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

R12 作为当前阶段完整替换基线。冻结期间只接受明确缺陷修复，不做大范围架构拆分或功能扩张。下一阶段再考虑把 `companion_gateway.py` 中 Device Router、Kitchen、Info、Audio Router 等职责拆成独立模块。

## Persistent configuration and full replacement

Runtime secrets and machine-specific OpenClaw SSH routing live outside the source tree at `~/.config/HomeAIAgent/gateway.env`. They are intentionally not bundled into release ZIPs or Git. `./configure_mac.sh` writes/updates this file atomically and keeps unrelated keys. The values survive reboot and complete deletion/replacement of the `gateway` project directory.

For an existing installation upgraded from an older package, run `./setup_mac.sh` once. If the persistent file is present but missing the newer embedded-SSH fields, setup now detects the schema gap and invokes the repair flow automatically. `./run_full.sh` also validates required fields before starting and gives a direct repair instruction instead of failing later inside the transport manager.
