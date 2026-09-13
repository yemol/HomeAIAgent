# Cyber Expression

HomeAIAgent 使用统一的赛博终端视觉语言，不为每个状态切换成完全不同的图标。

固定视觉锚点：

1. 中央 AI CORE
2. 横向 VISOR
3. 不完整机械/数据轨道
4. 固定方向定位标记

## 状态

### Idle

低速轨道漂移、轻微 CORE 呼吸和稀疏扫描。

### Wake -> Listening

唤醒后约 560 ms 完成 CORE 点亮、VISOR 展开和轨道收束，然后进入 Listening。

### Listening

运动方向向内收敛，让输入状态一眼可辨。

### Thinking

多层 compute lane 以不同速度旋转，并加入稀疏节点和扫描针。

### Speaking

Speaking 不使用麦克风回环。固件从当前播放的 TTS PCM 尾部小窗口估算短期能量，并把最新能量从 CORE 向左右传播。停顿时波形自然收敛，强调音节产生更高波峰。

Steady-state cadence 约 24 FPS。

### Success / Error

Success 使用机械结构对齐表达完成；Error 使用红色强调、结构错位和短 glitch bar，不使用整屏频闪。

## Flicker-free rendering

StickS3 135×240 画面先在 RGB565 `M5Canvas` 中完整绘制，再单次 `pushSprite()` 到 LCD。物理屏幕在下一帧完成前继续显示上一帧，不执行可见的 `clear -> redraw`。

## Frame caps

```text
Idle       ~6 FPS
Listening  ~6 FPS
Thinking   20 FPS
Speaking   ~24 FPS
Success    12.5 FPS
Error      10 FPS
```

状态切换期间短时间提高刷新率，完成后立即回到对应 steady-state cap。

UI 动画不得抢占 Mic/I2S 或 Gapless TTS 的关键调度资源。
