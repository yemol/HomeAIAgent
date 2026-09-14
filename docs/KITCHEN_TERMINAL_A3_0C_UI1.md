# KitchenTerminal A3.0c UI1

## UI changes

- Visible terminal name: `小K`
- Top layout uses one safe-area on `#app`:
  `calc(env(safe-area-inset-top, 0px) + 18px)`

The safe-area is applied only to the outer application container so panels,
cards, timers, recipe steps and the Q&A dock are not independently pushed down.

## Intentionally unchanged

- Internal `KitchenTerminal` protocol names
- `KitchenTerminal-iPadMini` device ID
- `/kitchen` routes and WebSocket protocol
- Kitchen voice command namespace
- iPad microphone / HTTPS path
- recipe progress persistence
- timers
- Gateway/OpenClaw kitchen Q&A behavior
