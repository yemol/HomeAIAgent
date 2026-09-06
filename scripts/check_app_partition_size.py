Import("env")

from pathlib import Path

APP0_LIMIT = 0x230000  # esp_sr_8.csv app0 size
MIN_HEADROOM = 256


def _firmware_path(build_env):
    return Path(build_env.subst("$BUILD_DIR")) / f"{build_env.subst('$PROGNAME')}.bin"


def _check_size(target, source, env):
    firmware = _firmware_path(env)
    if not firmware.exists():
        return
    size = firmware.stat().st_size
    headroom = APP0_LIMIT - size
    print(f"[SIZE-GATE] firmware={size} app0={APP0_LIMIT} headroom={headroom}")
    if size > APP0_LIMIT:
        raise RuntimeError(
            f"Firmware image is {size - APP0_LIMIT} bytes larger than app0; upload blocked."
        )
    if headroom < MIN_HEADROOM:
        print(f"[SIZE-GATE-WARN] app0 headroom is only {headroom} bytes")


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", _check_size)
env.AddPreAction("upload", _check_size)
