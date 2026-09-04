# HomeAIAgent A4.1.3 Validation

PASS:
- Gateway Python syntax compile
- Old `WIFI` status text removed
- Left `LIVE <count>` retained
- Right-aligned gold status added
- Gold quote cache added
- 300 s default quote refresh
- Gold quote refresh independent of 01:00-09:00 news quiet period
- last-good gold quote retained on failure
- A4.1.2 hourly news schedule retained
- HomeAI Info v1.1 retained
- Glass2 header 12 px / body 11 px retained
- A3.9 voice/GAPLESS pipeline untouched
- No StickS3 firmware change relative to A4.1.2

External provider:
- `https://api.goldprice.dev/v1/carat?currency=CNY`
- reads `price_gram_24k`

NOT RUN:
- live provider request from artifact runtime (no Internet)
- real Glass2 visual test
