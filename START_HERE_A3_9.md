# HomeAIAgent P0-A3.9｜Gapless TTS

## 为什么 A3.8 会有明显停顿

A3.8 的逻辑是：

```text
播完第1段
→ playback.done
→ Mini 才开始传第2段
→ StickS3 收完整段
→ 重新启动 Speaker
→ 播第2段
```

所以停顿并不是豆包合成出来的，而是“上一段结束后才传下一段”的协议等待。

## A3.9

改成真正的双缓冲流水线：

```text
StickS3 播 segment 1
同时 PSRAM 已经装好 segment 2
同时 M5Unified Speaker channel 0 已把 segment 2 放入 next queue

segment 1 结束
→ Speaker 内部直接切 segment 2
→ 释放 buffer 1
→ 通知 Mini playback.slot_ready
→ Mini 把 segment 3 填进刚释放的 buffer 1
```

两个 PSRAM buffer 交替使用，理论上可以无限延长回答，不需要把整段语音一次性塞进设备。

M5Unified 的 `playRaw()` 本身提供 current/next 队列，并且其官方头文件明确建议运行时生成音频时使用两个 buffer 交替播放。

## 预期日志

```text
[TTS-PIPE] total_bytes=2132800 ... segments=3
[TTS-PIPE] queued-to-device 1/3 ...
[TTS-PIPE] queued-to-device 2/3 ...

[AUDIO] TTS queued ... seg=1/3
[AUDIO] TTS queued ... seg=2/3

...第1段播放...

[AUDIO] seamless handoff ... seg=2/3
[AUDIO] device TTS slot ready
[TTS-PIPE] queued-to-device 3/3 ...

...直接接第2、3段...

[AUDIO] gapless playback done
[TTS-PIPE] gapless sequence complete
```

## 升级

这次修改了 StickS3 TTS 播放队列，所以：

```text
Mini Gateway：需要更新
StickS3 固件：需要刷 A3.9
```

配置不用重填。

Mac：

```text
~/.config/HomeAIAgent/gateway.env
```

不动。

StickS3：

```text
NVS namespace homeai_cfg
```

正常刷固件继续保留。

## 注意

A3.9 会在播放当前 TTS 时通过 Wi-Fi 接收下一段。由于设备以 Wi-Fi RX 为主、TX 功率仍保持 A3.7 的 10 dBm 限制，预计功耗峰值明显低于之前已经修掉的 Mic + Wi-Fi TX 同时工作场景。

如果实机重新出现 `BROWNOUT`，保留 Reset Forensics 日志，我们会直接看到触发阶段。
