# Hardware P0

## Frozen combination
- M5Stack StickS3
- M5Stack Unit Glass2

## Connection
Use the normal HY2.0-4P / Grove cable directly between StickS3 and Glass2.

Glass2:
- supply: Grove 5V rail
- I2C address: `0x3C` by default
- SDA: StickS3 GPIO9
- SCL: StickS3 GPIO10

The firmware explicitly calls `M5.Power.setExtOutput(true)` because StickS3's
external 5V rail is not kept in output mode by default after M5Unified init.

## First power-on test
1. Connect Glass2 to StickS3.
2. Flash P0-A1.
3. Glass2 should show a category label and a scrolling Chinese headline.
4. StickS3 LCD should show the companion face.
5. Hold A: interaction screen changes to “正在听”.
6. Release A: demo proceeds “想一想” -> “正在说” -> idle.
7. Short press B: next info item.
8. Hold B > 650 ms: previous info item.

If Glass2 does not initialize, serial monitor prints:
`[P0] Glass2 init FAILED`
