# HomeAIAgent P0-A3.7｜Brownout 根因修复

日期：2026-09-03

## 这次日志已经给出确定结论

实机捕获：

```text
[BOOT-DIAG] reset_reason=BROWNOUT(9)
[BOOT-DIAG] last_checkpoint=WS_BIN_POST(274)
last_ms=101258
micBlocks=4
seq=1
```

这意味着：

```text
开始 PTT
→ Mic 正常开始
→ 第 4 个音频 block
→ WebSocket Binary 刚发送完成
→ 供电电压跌破 ESP32-S3 brownout 门限
→ 硬件复位
```

此时：

```text
heap ≈ 8.6 MB
internal ≈ 261 KB
DMA ≈ 253 KB
PSRAM ≈ 8.37 MB
```

所以已经正式排除：

- 录音太长
- Heap 泄漏
- PSRAM 不足
- DMA RAM 耗尽
- Panic / Watchdog

而且重启发生在 TTS 之前，所以 Speaker V255/MAG4 不是这次 brownout 的触发点。

## A3.7 根修方案

### 1. 不再“边录边发 Wi-Fi”

旧结构：

```text
Mic/I2S capture
+
每 20ms WebSocket sendBIN
```

这会让音频采集与 ESP32-S3 Wi-Fi RF TX 峰值重叠。

A3.7 改为：

```text
按住 A
↓
Mic/I2S
↓
稳定音频块复制进 PSRAM
↓
松开 A
↓
停止 Mic / I2S
↓
再通过 Wi-Fi 上传整段 PCM
```

15 秒 PCM16/16k/mono 只有约：

```text
480 KB
```

StickS3 的 8 MB PSRAM 足够。

这不是临时绕过，而是更符合当前 PTT 架构：
Gateway 本来也要等 `ptt.stop` 才开始 ASR，所以录音过程中提前上传 PCM 并没有降低当前端到端延迟。

### 2. Wi-Fi 最大 TX Power 降至 10 dBm

A3.7：

```text
esp_wifi_set_max_tx_power(40)
```

ESP-IDF 使用 0.25 dBm 单位，因此：

```text
40 / 4 = 10 dBm
```

家庭室内网络通常足够，同时显著降低 RF TX 电流尖峰。

### 3. 上传分块并节流

Mic 停止后：

```text
640 bytes / chunk
4 ms pacing
```

避免一次性把 Wi-Fi 队列灌满。

### 4. Brownout detector 继续保留

**没有关闭 brownout detector。**

关闭 detector 只会把真实供电跌落藏起来，可能造成随机数据损坏，不属于修复。

## 新诊断坐标

新增：

```text
PTT_TX_BEGIN
PTT_TX_PRE
PTT_TX_POST
PTT_TX_DONE
```

如果以后仍 brownout，可以区分：

```text
采集阶段
vs
采集完成后的纯 Wi-Fi 上传阶段
```

## 这次必须重新刷 StickS3

因为 A3.7 修改的是设备端：

- PTT PCM 缓冲策略
- WebSocket 上传时机
- Wi-Fi TX power
- reset breadcrumb

Mac mini Gateway 继续使用 A3.6 的：

```text
Streaming ASR 2.0
+
OpenClaw
+
Doubao TTS 2.0
```

即可。

## 实机测试观察

刷 A3.7 后，按 A 说 3～5 秒。

录音期间应该看不到持续的 PCM WebSocket 发送。

松开 A 后看到：

```text
[AUDIO] post-capture TX begin ...
[AUDIO] post-capture TX done ...
[AUDIO] PTT stop ...
```

如果稳定连续测试 10 次不 reboot，Brownout 软件侧根修可以判定通过。

如果仍然出现：

```text
reset_reason=BROWNOUT
```

下一步就不再改协议，而直接检查硬件供电：

1. StickS3 直接接 Mac / 稳定 USB-C 电源，不经过 Hub。
2. 换短而粗的 USB-C 线。
3. 临时拔掉 Glass2 再做 10 次 PTT，对比总负载。
4. 若仍复现，在 5V/GND 近端增加低 ESR bulk capacitor，再测电源跌落。

