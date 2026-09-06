# HomeAIAgent P0 A4.4.18 RC1R8

## SCons size-gate callback fix

RC1R8 is based directly on RC1R7. It fixes only the PlatformIO/SCons callback signature in `scripts/check_app_partition_size.py`.

RC1R7 used a callback that did not accept SCons keyword `env`, causing:

```text
TypeError: _check_size() got an unexpected keyword argument 'env'
```

RC1R8 uses the canonical callback signature:

```python
def _check_size(target, source, env):
```

The firmware-size hard gate remains enabled for the fixed `0x230000` app0 partition. No partition-table change, no `.pio-local` change, and no wake/PTT/trigger/screen/info behavior change.

## Recovery

Use PlatformIO `Clean -> Upload -> Monitor`. Erase Flash is not required. Gateway does not need to be restarted if it is already running RC1R6 or newer.
