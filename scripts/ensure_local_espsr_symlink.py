Import("env")

from pathlib import Path
import json
import os
import shutil

PROJECT = Path(env["PROJECT_DIR"]).resolve()
PIOENV = str(env["PIOENV"])
SOURCE = (PROJECT / ".pio-local" / "ESP-SR-For-M5Unified").resolve()
SOURCE_C = SOURCE / "src" / "esp32-hal-sr-m5.c"
LIBDEPS = PROJECT / ".pio" / "libdeps" / PIOENV
EXPECTED_LINK = LIBDEPS / "ESP_SR_M5Unified"
GUARD_MARKER = "HOMEAI_A4_4_12_MULTINET_ONLY_GUARDED"

print("[ESP-SR-LINK] guard=LOCAL-SOURCE-AUTHORITY")
print(f"[ESP-SR-LINK] source={SOURCE}")

if not SOURCE_C.is_file():
    raise RuntimeError(
        "[ESP-SR-LINK] preserved local ESP-SR wrapper is missing: %s" % SOURCE_C
    )

source_text = SOURCE_C.read_text(encoding="utf-8", errors="replace")
if GUARD_MARKER not in source_text:
    raise RuntimeError(
        "[ESP-SR-LINK] local wrapper is not the guarded HomeAI version; "
        "patch_local_espsr_cn.py must run before this script"
    )

LIBDEPS.mkdir(parents=True, exist_ok=True)

# PlatformIO's file:// protocol may have left a copied dependency behind.  Clean
# removes .pio/build but does not guarantee removal of .pio/libdeps, so replace
# only that generated ESP-SR dependency with a symlink to the preserved source.
def points_to_source(path: Path) -> bool:
    try:
        return path.is_symlink() and path.resolve() == SOURCE
    except OSError:
        return False


def is_espsr_copy(path: Path) -> bool:
    if not path.exists() or path.is_symlink() or not path.is_dir():
        return False
    if (path / "src" / "esp32-hal-sr-m5.c").is_file():
        return True
    manifest = path / "library.json"
    if manifest.is_file():
        try:
            name = str(json.loads(manifest.read_text(encoding="utf-8")).get("name", ""))
            return name in {"ESP_SR_M5Unified", "ESP-SR-For-M5Unified"}
        except Exception:
            pass
    return False

# First repair the canonical package path used by the named lib_deps entry.
if EXPECTED_LINK.exists() or EXPECTED_LINK.is_symlink():
    if not points_to_source(EXPECTED_LINK):
        if EXPECTED_LINK.is_symlink() or EXPECTED_LINK.is_file():
            EXPECTED_LINK.unlink()
        else:
            shutil.rmtree(EXPECTED_LINK)
        os.symlink(str(SOURCE), str(EXPECTED_LINK), target_is_directory=True)
        print(f"[ESP-SR-LINK] replaced stale dependency: {EXPECTED_LINK}")
else:
    os.symlink(str(SOURCE), str(EXPECTED_LINK), target_is_directory=True)
    print(f"[ESP-SR-LINK] created dependency symlink: {EXPECTED_LINK}")

# Remove only extra stale *copies* of this same wrapper from this environment.
# Do not touch unrelated libraries and never touch .pio-local.
for child in list(LIBDEPS.iterdir()):
    if child == EXPECTED_LINK:
        continue
    if is_espsr_copy(child):
        shutil.rmtree(child)
        print(f"[ESP-SR-LINK] removed stale copied wrapper: {child}")

if not points_to_source(EXPECTED_LINK):
    raise RuntimeError(
        "[ESP-SR-LINK] failed to make PlatformIO ESP-SR dependency point at .pio-local"
    )

build_c = EXPECTED_LINK / "src" / "esp32-hal-sr-m5.c"
if not build_c.is_file():
    raise RuntimeError("[ESP-SR-LINK] linked build source is missing: %s" % build_c)
if GUARD_MARKER not in build_c.read_text(encoding="utf-8", errors="replace"):
    raise RuntimeError("[ESP-SR-LINK] linked build source does not contain guarded patch")

print(f"[ESP-SR-LINK] build-source={EXPECTED_LINK} -> {EXPECTED_LINK.resolve()}")
print("[ESP-SR-LINK] validation PASS: PlatformIO compiles patched .pio-local ESP-SR")
