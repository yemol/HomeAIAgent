# HomeAIAgent A4.1.3｜Glass2 Gold Status

Changes:
- Remove bottom `WIFI` text
- Keep left `LIVE <count>`
- Add right-aligned live RMB gold value
- Display format: `金 ¥xxxx.x/g`
- Source: goldprice.dev `price_gram_24k`
- Meaning: 24K international spot-equivalent gold value converted to CNY per gram
- Refresh: every 300 seconds by default
- Gold refresh is independent from the news schedule
- News remains: 00:00, 01:00, 09:00..23:00
- If quote fetch fails, keep last-good cached quote
- Gold cache: `~/.local/share/HomeAIAgent/gold_quote_cache.json`

This is NOT jewellery-shop retail gold and is not the Shanghai Gold Exchange
Au99.99 transaction quote. If SGE Au99.99 is preferred later, only the Mini
quote provider needs to be changed; StickS3 does not need another display
protocol change.

No firmware change relative to A4.1.2. Glass2 frames are rendered on Mini.
