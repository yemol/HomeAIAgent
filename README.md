# HomeAIAgent P0-A4.0 Dynamic Info Channel

A3.9 Gapless TTS 已冻结，不回退。

A4.0 把 Glass2 从固件静态 demo 改成 Mini 动态资讯：

- `~/.config/HomeAIAgent/subscriptions.json`
- Mini 使用本机中文字体预渲染 128×64 黑白画面
- StickS3 缓存最多 12 条
- B 短按下一条，长按上一条
- 5 秒自动切换
- PTT 时当前条目保持冻结
- “这个讲讲”继续绑定当前 item，并补充 summary/source 给 OpenClaw

A4.0 先验证动态链路，A4.1 再接真实游戏/金融数据源。
