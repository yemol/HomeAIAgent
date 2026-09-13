# Hardware

## 主设备

- M5Stack StickS3
- M5Stack Unit Glass2

## Glass2

连接：HY2.0-4P / Grove。

```text
5V   -> Grove 5V
SDA  -> GPIO9
SCL  -> GPIO10
ADDR -> 0x3C
```

固件在初始化 Glass2 前显式开启 StickS3 外部供电。

## StickS3 Audio

板载 ES8311 按半双工使用：

- 唤醒/录音期间关闭 speaker path
- 播放期间关闭 Mic capture
- capture 完成后才发送 Wi-Fi PCM

当前参数：

```text
Wake listen PGA:      9 dB
Command capture PGA:  6 dB
Speaker volume:       255
Normal TTS MAG:       5
Wake ACK MAG:         6
```

Wake ACK 使用冻结的 `在的` PCM，16 kHz / mono / PCM16。

## Display roles

### StickS3 LCD

显示 Cyber Expression 状态动画。Speaking 使用播放中的 PCM 能量驱动波形，约 24 FPS。

### Glass2

显示 Gateway 预渲染的游戏/金融资讯和底部状态。

## First verification

刷入当前基线后确认：

1. StickS3 正常启动，无 brownout/reset loop。
2. Cyber Expression 无整屏闪烁。
3. Glass2 正常初始化。
4. Button A 可以完成一轮语音对话。
5. `你好逐光` 可以进入 hands-free path。
6. 本地 `在的` 正常播放。
7. 语音结束后 wake listener 能重新 armed。

Glass2 初始化失败会在串口输出明确错误。
