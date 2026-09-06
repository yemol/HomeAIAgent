Import("env")

from pathlib import Path
import shutil
import struct
import urllib.request

PROJECT = Path(env["PROJECT_DIR"])
CACHE = PROJECT / ".srmodels_cache"
MODEL_NAME = "mn5q8_cn"
MODEL_DIR = CACHE / MODEL_NAME
OUT = PROJECT / "srmodels.bin"

BASE_URLS = [
    "https://raw.githubusercontent.com/espressif/esp-sr/master/model/multinet_model",
    "https://cdn.jsdelivr.net/gh/espressif/esp-sr@master/model/multinet_model",
]

FILES = ["_MODEL_INFO_", "mn5q8_data", "mn5q8_index"]


def download(url: str, target: Path) -> None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "HomeAIAgent-A4.3/1.0"},
    )

    with urllib.request.urlopen(req, timeout=45) as response:
        with open(target, "wb") as f:
            shutil.copyfileobj(response, f)


def ensure_model_files() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    for filename in FILES:
        target = MODEL_DIR / filename

        if target.exists() and target.stat().st_size > 0:
            continue

        last_error = None

        for base in BASE_URLS:
            url = f"{base}/{MODEL_NAME}/{filename}"

            try:
                print(f"[WAKE-MODEL] downloading {url}")
                download(url, target)

                if target.stat().st_size <= 0:
                    raise RuntimeError("downloaded file is empty")

                last_error = None
                break

            except Exception as exc:
                last_error = exc

                if target.exists():
                    target.unlink()

                print(f"[WAKE-MODEL] source failed: {exc}")

        if last_error is not None:
            raise RuntimeError(
                f"Could not download {MODEL_NAME}/{filename}: {last_error}"
            )


def pack_ascii(value: str, width: int) -> bytes:
    raw = value.encode("ascii")

    if len(raw) > width:
        raise ValueError(f"value too long: {value}")

    return raw + (b"\x00" * (width - len(raw)))


def build_srmodels() -> None:
    ensure_model_files()

    payloads = [
        (filename, (MODEL_DIR / filename).read_bytes())
        for filename in FILES
    ]

    model_count = 1
    file_count = len(payloads)
    header_len = 4 + model_count * (32 + 4) + file_count * (32 + 4 + 4)

    header = bytearray()
    header += struct.pack("<I", model_count)
    header += pack_ascii(MODEL_NAME, 32)
    header += struct.pack("<I", file_count)

    payload = bytearray()

    for filename, data in payloads:
        header += pack_ascii(filename, 32)
        header += struct.pack("<I", header_len + len(payload))
        header += struct.pack("<I", len(data))
        payload += data

    if len(header) != header_len:
        raise RuntimeError(
            f"srmodels header mismatch: {len(header)} != {header_len}"
        )

    tmp = OUT.with_suffix(".tmp")
    tmp.write_bytes(bytes(header) + bytes(payload))
    tmp.replace(OUT)

    print(
        f"[WAKE-MODEL] ready {OUT.name} "
        f"model={MODEL_NAME} bytes={OUT.stat().st_size}"
    )


build_srmodels()
