# HomeAIAgent P0-A4.0｜Dynamic Info Channel

日期：2026-09-03

现在基础链已经稳定：

```text
ASR ✅
OpenClaw ✅
TTS ✅
Gapless 长回答 ✅
Mac 配置持久化 ✅
Python Runtime 持久化 ✅
StickS3 NVS ✅
```

因此开始原计划第二阶段：**游戏 + 金融资讯订阅**。

## A4.0 先做动态资讯通道

旧版 Glass2 的资讯是写死在固件里的 4 条 demo。

A4.0 改为：

```text
~/.config/HomeAIAgent/subscriptions.json
        ↓
Mac mini Gateway
        ↓
用 Mac 自己的中文字体预渲染 128×64 黑白画面
        ↓
WebSocket
        ↓
StickS3 缓存最多 12 条
        ↓
Glass2
```

这样真实新闻里出现任意中文时，不再受 StickS3 固件中文字库限制。

HomeAIAgent **不会打包或复制 Mac 的字体文件**，只是运行时使用系统已经安装的字体生成 1-bit 画面。

## 为什么这样做

如果把完整中文字库塞进 StickS3：

```text
固件体积变大
字体修改要重刷设备
新闻出现新字符还要考虑字库覆盖
```

现在字体属于 Mini 的表现层。以后字体大小、粗细、布局都可以只改 Mini，不必重刷 StickS3。

## 升级

A4.0 修改了 StickS3 的资讯协议，所以这次要刷一次 A4.0 固件。

Mac Gateway 覆盖后直接：

```bash
chmod +x *.sh
./run_full.sh
```

持久 Python runtime 会发现 `requirements.txt` 新增 Pillow，并自动安装一次。
不需要删除 venv，也不需要重新输入 Key / Token。

第一次启动会自动建立：

```text
~/.config/HomeAIAgent/subscriptions.json
```

并写入 3 条明确的 A4.0 测试资讯。

Mini 正常日志：

```text
[INFO] feed loaded count=3 ...
[DEVICE] hello
[INFO] sync sent ... count=3
[INFO] device ack ...
```

StickS3 正常日志：

```text
[INFO] sync begin ...
[INFO] staged 1/3 ...
[INFO] staged 2/3 ...
[INFO] staged 3/3 ...
[INFO] sync committed ...
```

## 验收

Glass2 应该不再显示旧的 4 条 demo，而是 A4.0 的 3 条动态测试条目。

操作：

```text
B 短按 = 下一条
B 长按 = 上一条
不操作 = 5 秒自动下一条
A 说“这个讲讲” = 解释当前条目
```

PTT 时当前资讯继续冻结，所以“这个”不会在录音过程中漂移。

## A4.0 的边界

这版**先不接真实互联网资讯源**。

原因是要把：

```text
动态显示
任意中文
传输
设备缓存
B键导航
上下文绑定
```

单独验收。

这些通过以后，A4.1 直接在这条稳定管线上接真实游戏和金融数据，不再同时排查显示和抓取两类问题。
