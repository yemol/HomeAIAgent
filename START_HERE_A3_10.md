# HomeAIAgent P0-A3.10｜Persistent Python Runtime

日期：2026-09-03

## A3.9 已通过实机听感验收

用户确认：

```text
长回答可以完整播放
分段衔接自然
明显停顿已经消失
```

因此 A3.9 的 Gapless TTS 双缓冲/预取方案冻结，不回退。

## A3.10 只解决升级体验

之前：

```text
项目目录/gateway/.venv
```

整包覆盖后 `.venv` 会消失，于是每个版本都要重新：

```text
python3 -m venv .venv
pip install ...
```

A3.10 改成：

```text
~/.local/share/HomeAIAgent/venv
```

正式分层：

```text
程序：
任意 HomeAIAgent_xxx/

Mac配置：
~/.config/HomeAIAgent/gateway.env

Python运行环境：
~/.local/share/HomeAIAgent/venv

StickS3配置：
NVS / homeai_cfg
```

以后整包覆盖项目时：

```text
API Key / Token       保留
Tunnel配置             保留
Python依赖             保留
StickS3 Wi-Fi/Gateway  保留
```

## 第一次从 A3.9 升 A3.10

在新 A3.10 的 `gateway`：

```bash
chmod +x *.sh
./setup_mac.sh
```

它会：

```text
1. 创建 ~/.local/share/HomeAIAgent/venv
2. 安装 requirements.txt
3. 读取已有 ~/.config/HomeAIAgent/gateway.env
4. 不覆盖任何 API Key / Token
```

这是最后一次因为“运行环境持久化”而需要 setup。

以后日常只要：

Terminal 1：

```bash
./openclaw_air_tunnel.sh
```

Terminal 2：

```bash
./run_full.sh
```

如果未来依赖发生变化，`run_full.sh` 发现持久 venv 不存在时会自动 bootstrap；正常版本覆盖不会碰它。

查看状态：

```bash
./runtime_status.sh
```

A3.10 通过后，旧项目目录里的 `.venv` 已经没有用途，可以显式清理：

```bash
./cleanup_legacy_runtime.sh
```

## 固件

A3.10 **不修改 StickS3 固件**。

继续使用已经验证自然播放的 A3.9 固件即可，不需要重新刷机。

## 稳定基线继续保留

- Streaming ASR 2.0
- OpenClaw
- Doubao TTS 2.0
- Gapless TTS 双缓冲
- A3.7 Brownout root fix
- Mac 持久配置
- StickS3 NVS 持久配置
