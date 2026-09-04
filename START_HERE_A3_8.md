# HomeAIAgent P0-A3.8｜一次解决：Long TTS + 配置持久化

日期：2026-09-03

## 这版合并了两个问题

### 1. 长回答 TTS 不再因为 1.5 MiB 单段上限失败

之前：

```text
[AUDIO] invalid TTS size: 2132800
[AUDIO] empty TTS payload
```

A3.8 继续使用 A3.6.3 的 Gateway 分段播放：

```text
完整 TTS PCM
→ Mini 按自然低能量位置切段
→ 每段 ≤ 768 KiB
→ StickS3 播完一段回 playback.done
→ 再发下一段
```

不砍回答、不提高设备单段风险上限、不需要多次调用豆包 TTS。

---

## 2. Mac mini 配置永久放在项目目录之外

正式位置：

```text
~/.config/HomeAIAgent/gateway.env
```

保存：

```text
豆包 Speech API Key
OpenClaw token
ASR/TTS endpoint/resource
OpenClaw model/user
SSH/Tailscale user/IP/ports
Gateway port/path
Long-TTS 参数
```

以后：

```text
删除整个项目
重新解压新版本
A3.8 → A3.9 → A4
```

都不会影响这个文件。

Gateway 启动顺序：

```text
1. ~/.config/HomeAIAgent/gateway.env
2. 如果不存在，自动寻找旧 gateway/.env
3. 找到旧 .env 时自动迁移到持久目录
```

`setup_mac.sh` 如果发现持久配置已经存在，默认只保留，不会重新询问和覆盖 Key。

显式修改配置才运行：

```bash
./configure_mac.sh
```

并自动先备份旧配置。

---

## 3. StickS3 配置进入 NVS / Preferences

设备端现在也不再依赖每个固件版本都重新填写网络配置。

NVS namespace：

```text
homeai_cfg
```

持久保存：

```text
Wi-Fi SSID
Wi-Fi password
Mac mini Gateway host
Gateway port
Gateway path
device name
schema version
```

普通 PlatformIO 固件升级不会清除 NVS，所以后续刷 A3.9/A4 配置继续存在。

只有两个动作会清除：

```text
显式 CFG RESET
或整片 flash erase
```

第一次刷 A3.8：

- 如果旧 `secrets.h` 已经包含真实 Wi-Fi/Gateway，A3.8 会自动一次性迁移进 NVS。
- 如果 `secrets.h` 是占位符，则用串口配置工具配置一次。

工具：

```bash
python3 -m pip install -r tools/requirements.txt
python3 tools/configure_terminal.py
```

以后修改 Wi-Fi 或 Mini IP 也不需要重新编译固件。

查看：

```bash
python3 tools/configure_terminal.py --show
```

恢复出厂：

```bash
python3 tools/configure_terminal.py --reset
```

---

## 4. 本版同时包含 A3.7 Brownout 根修

保留：

```text
PTT 期间 PCM 写 PSRAM
Mic/I2S 停止后再 Wi-Fi 上传
Wi-Fi max TX power = 10 dBm
640-byte TX chunk + 4 ms pacing
Brownout detector 保留
Reset Forensics 保留
```

所以如果你还没有刷 A3.7，可以直接跳过 A3.7，刷 A3.8。

---


## 最重要：这次升级前先迁移当前正在工作的 `.env`

因为你现在的旧项目已经能正常完整对话，里面的 `.env` 就是我们最应该保留的配置。

先解压 A3.8，但**先不要删除当前工作项目**。

在 A3.8 的 gateway 目录运行：

```bash
./migrate_existing_config.sh /你当前正在工作的/HomeAIAgent/gateway
```

看到：

```text
[SAFE] You can now delete/replace the whole old HomeAIAgent project.
```

之后再完整覆盖项目也不会丢配置。

这个迁移只需要做一次。以后所有版本都直接读取：

```text
~/.config/HomeAIAgent/gateway.env
```


## 推荐升级顺序

### Mac mini

把新包解压到任意新目录。

如果当前旧项目还有 `gateway/.env`，最省事：

```bash
cd HomeAIAgent_P0_A3_8_PERSISTENT_CONFIG_LONG_TTS/gateway
./setup_mac.sh
```

如果旧 `.env` 在旧工程目录，不在新目录，可以先：

```bash
mkdir -p ~/.config/HomeAIAgent
cp /旧项目/gateway/.env ~/.config/HomeAIAgent/gateway.env
chmod 600 ~/.config/HomeAIAgent/gateway.env
```

之后所有新版都自动读取。

检查：

```bash
python config_status.py
```

### StickS3

本版改了设备端 NVS 与 A3.7 Brownout 逻辑，因此需要刷一次 A3.8。

以后正常升级不 erase flash，NVS 会继续保留。

---

## 以后版本打包硬规则

HomeAIAgent 从 A3.8 起：

```text
程序包 ≠ 配置
```

Mac：

```text
程序：任意 HomeAIAgent_xxx/gateway/
配置：~/.config/HomeAIAgent/gateway.env
```

StickS3：

```text
程序：firmware partition
配置：NVS namespace homeai_cfg
```

项目整包覆盖和普通固件升级都不得清除用户/机器配置。
