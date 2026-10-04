# HomeAIAgent Gateway

HomeAIAgent 的统一 Gateway。当前代码同时负责主 Agent、Mini、KitchenTerminal、NetworkSpeaker / Dock 的会话、设备路由、A6 认证、ASR/TTS、提醒与助眠音频等运行链路。

## 当前提交基线

- Mini、主 Agent 与 KitchenTerminal 保持独立会话和提醒域。
- NetworkSpeaker / Dock 使用现有 WebSocket PCM 协议与 A6 设备认证。
- Dock 助眠功能由 Gateway 控制，不要求修改 Dock 固件。
- 助眠默认音量为 **5%**；结束或手动停止后恢复进入助眠前的正常 Dock 音量。
- 助眠流使用双 slot 预填，并继续复用 Dock 的 Jitter Buffer。
- 正式助眠库使用真实录音 / 氛围音乐，不使用程序合成环境声。

## 目录

```text
companion_gateway.py      Gateway 主入口
ambient_sleep.py          助眠命令解析与真实音频循环
context/                  Follow-up / 上下文状态
core/                     会话基础模块
security/                 A6 设备认证
tools/                    素材安装与维护工具
test_*.py                 回归测试
run_full.sh               正式启动脚本
gateway.env.example       配置示例
```

## 部署

正式运行目录建议固定为 HomeAIAgent 根目录下的 `gateway/`。停止旧 Gateway 后，用本仓库内容完整替换现有 `gateway/` 目录；正式配置继续放在：

```bash
~/.config/HomeAIAgent/gateway.env
```

不要把设备密钥、API Key 或真实 `.env` 提交到仓库。

启动：

```bash
cd <HOMEAI_ROOT>/gateway
./run_full.sh
```

检查 A6 设备状态：

```bash
~/.local/share/HomeAIAgent/venv/bin/python security_cli.py devices
```

## 助眠声音

正式声音库：

1. 轻柔雨声
2. 雨夜雷声
3. 舒缓海浪
4. 山间水流
5. 壁炉柴火
6. 雨夜爵士
7. 雾林氛围音乐
8. 山间氛围音乐
9. 深层氛围音乐

素材不提交到 Git，而是持久保存在：

```bash
~/.local/share/HomeAIAgent/ambient_assets/
```

首次部署或素材库变化时安装：

```bash
brew install ffmpeg              # 仅缺少 ffmpeg 时执行
./tools/install_ambient_assets.sh --list
./tools/install_ambient_assets.sh
```

素材来源、作者与许可证固化在 `tools/fetch_ambient_assets.py`，安装后也会写入 `manifest.json`。正式清单只使用 Public Domain / CC0 来源。

常用语音：

```text
有哪些助眠声音？
播放第3个30分钟
播放雨夜爵士一个小时
换成第8个
声音小一点
助眠音量调到3%
还剩多久？
停止播放
```

未指定时长时默认 60 分钟；未指定助眠音量时默认 5%。

## 关键配置

助眠相关配置位于 `gateway.env.example`：

```text
HOMEAI_AMBIENT_DEFAULT_DURATION_SEC=3600
HOMEAI_AMBIENT_MAX_DURATION_SEC=28800
HOMEAI_AMBIENT_DEFAULT_VOLUME_PERCENT=5
HOMEAI_AMBIENT_ASSET_DIR=~/.local/share/HomeAIAgent/ambient_assets
HOMEAI_AMBIENT_SEGMENT_SEC=1.0
HOMEAI_AMBIENT_PREFILL_SEGMENTS=2
HOMEAI_AMBIENT_FADE_IN_SEC=2.0
HOMEAI_AMBIENT_FADE_OUT_SEC=5.0
```

如果正式 `gateway.env` 已经写过旧的助眠音量值，它会覆盖程序默认值。

## 提交前检查

至少执行：

```bash
python3 precommit_static_audit.py
python3 test_ambient_sleep.py
python3 test_ambient_real_assets.py
python3 test_ambient_gapless_runtime.py
python3 test_ambient_stream_runtime.py
python3 test_security_auth.py
python3 test_security_gateway_integration.py
python3 test_multi_device_router.py
python3 test_networkspeaker_transport_profile.py
python3 test_long_tts_segmentation.py
python3 test_reminder_device_routing.py
python3 test_terminal_domain_routing.py
python3 test_kitchen_r50_10_shopping_identity_match.py
```

`audit_gateway.sh` 仍包含部分历史 UI 回归项；如果历史测试与当前 KitchenTerminal 基线不一致，应单独处理测试债务，不要为了全绿改动已冻结的业务代码。

## Git 提交原则

- 不提交 `.env`、API Key、A6 密钥、真实设备 secret。
- 不提交 `__pycache__`、日志、调试录音和下载后的 PCM 素材。
- 助眠素材使用安装器维护，不和 Gateway 源码包绑定。
- 功能修改必须保留 Main / Mini / KitchenTerminal 的会话隔离和设备路由。
