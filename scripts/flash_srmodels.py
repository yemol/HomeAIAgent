Import("env")

from pathlib import Path
import csv
import subprocess

PROJECT = Path(env["PROJECT_DIR"])
PARTITIONS = PROJECT / "esp_sr_8.csv"
SRMODELS = PROJECT / "srmodels.bin"


def find_model_offset() -> int:
    with open(PARTITIONS, newline="") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().startswith("#"):
                continue

            if row[0].strip() == "model":
                value = row[3].strip()

                if not value:
                    raise RuntimeError("model partition has no offset")

                return int(value, 0)

    raise RuntimeError("model partition not found")


def flash_model(source, target, env):
    if not SRMODELS.exists():
        raise RuntimeError("srmodels.bin missing")

    offset = find_model_offset()
    port = env.subst("$UPLOAD_PORT")
    speed = env.subst("$UPLOAD_SPEED")

    python = env.subst("$PYTHONEXE")
    esptool_dir = Path(
        env.PioPlatform().get_package_dir("tool-esptoolpy")
    )
    esptool = esptool_dir / "esptool.py"

    cmd = [
        python,
        str(esptool),
        "--chip", "esp32s3",
        "--port", port,
        "--baud", str(speed),
        "write_flash",
        hex(offset),
        str(SRMODELS),
    ]

    print(
        f"[WAKE-MODEL] flashing model "
        f"offset={hex(offset)} port={port}"
    )

    subprocess.check_call(cmd)


env.AddPostAction("upload", flash_model)
