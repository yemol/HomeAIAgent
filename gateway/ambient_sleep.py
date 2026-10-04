from __future__ import annotations

"""Sleep ambient command parsing and real-audio loop playback.

Synthetic ambience has been removed from the production path.  Each sound id
maps to a locally installed 16 kHz / 16-bit / mono PCM asset prepared from a
real recording or a CC0 ambient-music track.  The Gateway still owns routing,
NetworkSpeaker volume, timers and interruption policy.
"""

from array import array
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys

AMBIENT_SAMPLE_RATE = 16000
AMBIENT_BYTES_PER_SAMPLE = 2
def ambient_asset_dir() -> Path:
    """Resolve after gateway.env is loaded, not at module import time."""
    return Path(
        os.getenv(
            "HOMEAI_AMBIENT_ASSET_DIR",
            str(Path.home() / ".local" / "share" / "HomeAIAgent" / "ambient_assets"),
        )
    ).expanduser()


def ambient_asset_path(sound_id: str) -> Path:
    return ambient_asset_dir() / f"{sound_id}.pcm"


def ambient_asset_available(sound_id: str) -> bool:
    return sound_id in SOUND_BY_ID and ambient_asset_path(sound_id).is_file()


@dataclass(frozen=True)
class AmbientSound:
    sound_id: str
    label: str
    aliases: tuple[str, ...]
    category: str = "nature"


# Production library: real field recordings and permissively licensed ambient music.
# Procedural noise is intentionally excluded from the production path.
SOUNDS: tuple[AmbientSound, ...] = (
    # Real environmental recordings.
    AmbientSound("rain", "轻柔雨声", ("雨声白噪音", "轻柔雨声", "下雨声", "雨声", "下雨", "雨天")),
    AmbientSound("rain_thunder", "雨夜雷声", ("雨夜雷声", "雷雨声", "雷雨", "雨雷", "下雨打雷")),
    AmbientSound("ocean", "舒缓海浪", ("舒缓海浪", "海浪声", "海浪", "浪声", "海边")),
    AmbientSound("stream", "山间水流", ("山间水流", "溪流水声", "溪流声", "流水声", "溪流", "小溪", "河流声")),
    AmbientSound("fireplace", "壁炉柴火", ("壁炉柴火", "壁炉声", "壁炉", "柴火声", "篝火声", "火炉声")),
    # Full ambient-music tracks.
    AmbientSound("rainy_jazz", "雨夜爵士", ("雨夜爵士", "雨声爵士", "爵士助眠", "爵士音乐"), "music"),
    AmbientSound("foggy_ambient", "雾林氛围音乐", ("雾林氛围音乐", "雾林音乐", "森林氛围音乐", "柔和氛围音乐"), "music"),
    AmbientSound("mountain_ambient", "山间氛围音乐", ("山间氛围音乐", "山间音乐", "山野氛围音乐", "空灵音乐"), "music"),
    AmbientSound("cendence_ambient", "深层氛围音乐", ("深层氛围音乐", "深度助眠音乐", "助眠音乐", "深层音乐"), "music"),
)
SOUND_BY_ID = {item.sound_id: item for item in SOUNDS}
SOUND_NUMBER_TO_ID = {idx: item.sound_id for idx, item in enumerate(SOUNDS, start=1)}
SOUND_ID_TO_NUMBER = {item.sound_id: idx for idx, item in enumerate(SOUNDS, start=1)}


@dataclass(frozen=True)
class AmbientIntent:
    operation: str
    sound_id: str = ""
    duration_sec: int | None = None
    volume_percent: int | None = None
    volume_delta: int = 0
    selection_number: int | None = None


def sound_label(sound_id: str) -> str:
    sound = SOUND_BY_ID.get(str(sound_id or ""))
    return sound.label if sound else str(sound_id or "助眠声音")


def sound_number(sound_id: str) -> int | None:
    return SOUND_ID_TO_NUMBER.get(str(sound_id or ""))


def sound_id_for_number(number: int) -> str:
    return SOUND_NUMBER_TO_ID.get(int(number), "")


def numbered_sound_catalog_text() -> str:
    return "、".join(f"{idx}{item.label}" for idx, item in enumerate(SOUNDS, start=1))


def _compact(text: str) -> str:
    return re.sub(r"[\s，。,.！!？?、：:；;“”\"'（）()]+", "", str(text or "")).lower()


def _parse_zh_int(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    total = 0
    current = 0
    seen = False
    for ch in text:
        if ch in digits:
            current = digits[ch]
            seen = True
        elif ch in units:
            seen = True
            unit = units[ch]
            if current == 0:
                current = 1
            total += current * unit
            current = 0
        else:
            return None
    if not seen:
        return None
    return total + current


def parse_duration_sec(text: str) -> int | None:
    compact = _compact(text)
    if not compact:
        return None
    if "一个半小时" in compact or "1个半小时" in compact or "一小时半" in compact or "1小时半" in compact:
        return 90 * 60
    if "两个半小时" in compact or "两小时半" in compact or "2个半小时" in compact or "2小时半" in compact:
        return 150 * 60
    if "半小时" in compact:
        return 30 * 60
    m = re.search(r"([0-9零〇一二两三四五六七八九十百千]+)(?:个)?(?:小时|钟头)", compact)
    if m:
        value = _parse_zh_int(m.group(1))
        if value is not None:
            return value * 3600
    m = re.search(r"([0-9零〇一二两三四五六七八九十百千]+)(?:分钟|分)", compact)
    if m:
        value = _parse_zh_int(m.group(1))
        if value is not None:
            return value * 60
    return None


def _find_sound(compact: str) -> str:
    aliases = sorted(
        ((alias, sound.sound_id) for sound in SOUNDS for alias in sound.aliases),
        key=lambda item: len(item[0]), reverse=True,
    )
    for alias, sound_id in aliases:
        if alias in compact:
            return sound_id
    return ""


def _selection_number(compact: str) -> int | None:
    match = re.search(r"第([0-9零〇一二两三四五六七八九十百千]+)(?:个|种|号)?", compact)
    if not match:
        return None
    return _parse_zh_int(match.group(1))


def parse_ambient_intent(text: str, *, active: bool = False) -> AmbientIntent | None:
    compact = _compact(text)
    if not compact:
        return None
    sound_id = _find_sound(compact)
    duration_sec = parse_duration_sec(compact)
    selection_number = _selection_number(compact)
    if not sound_id and selection_number is not None:
        sound_id = sound_id_for_number(selection_number)
    ambient_context = bool(sound_id) or selection_number is not None or any(
        token in compact for token in ("助眠", "环境音", "睡眠声音", "睡觉声音", "助眠音乐")
    )

    stop_tokens = ("停止", "关闭", "关掉", "停掉", "不要播", "别播", "结束播放", "别放", "关了")
    if any(token in compact for token in stop_tokens):
        if ambient_context or active or compact in {"停", "停掉", "不要播了", "别播了", "关掉", "关闭"}:
            return AmbientIntent("stop")

    status_tokens = ("还剩多久", "还有多久", "多久结束", "什么时候结束", "在播什么", "播放什么", "什么声音")
    if active and any(token in compact for token in status_tokens):
        return AmbientIntent("status")

    list_tokens = (
        "有哪些助眠", "有什么助眠", "助眠声音有哪些", "助眠声有哪些",
        "可以播放什么", "助眠菜单", "助眠声音菜单", "助眠音乐菜单",
        "打开助眠", "选择助眠", "选助眠声音", "有哪些音乐", "有什么助眠音乐",
    )
    if any(token in compact for token in list_tokens):
        return AmbientIntent("list")

    if selection_number is not None and not sound_id:
        if any(token in compact for token in ("播放", "放", "听", "换成", "切换", "改成", "第")):
            return AmbientIntent("invalid_selection", duration_sec=duration_sec, selection_number=selection_number)

    if active and sound_id and any(token in compact for token in ("换成", "切换", "改成", "换为")):
        return AmbientIntent("switch", sound_id=sound_id, selection_number=selection_number)
    if active and any(token in compact for token in ("换一个", "下一种", "下一个", "换一种")):
        return AmbientIntent("next")
    if active and duration_sec is not None and any(token in compact for token in ("再放", "再播", "延长", "加时", "多放")):
        return AmbientIntent("extend", duration_sec=duration_sec)

    if active and (ambient_context or any(token in compact for token in ("声音", "音量"))):
        m = re.search(r"(?:音量|声音).{0,8}?(?:调到|调成|调整到|设为|设置为|到)?(?:百分之)?(\d{1,3})%?", compact)
        if m:
            return AmbientIntent("volume", volume_percent=max(0, min(100, int(m.group(1)))))
        if any(token in compact for token in ("小声一点", "声音小一点", "音量小一点", "轻一点", "调小一点", "调低一点", "再小一点", "太大了")):
            return AmbientIntent("volume", volume_delta=-5)
        if any(token in compact for token in ("大声一点", "声音大一点", "音量大一点", "调大一点", "调高一点", "再大一点", "太小了")):
            return AmbientIntent("volume", volume_delta=5)

    start_tokens = ("播放", "放一下", "放点", "放个", "来点", "来个", "听", "助眠")
    if sound_id and any(token in compact for token in start_tokens):
        return AmbientIntent("start", sound_id=sound_id, duration_sec=duration_sec, selection_number=selection_number)
    return None


def format_duration_zh(seconds: int) -> str:
    sec = max(0, int(seconds))
    minutes = max(1, int(round(sec / 60.0))) if sec else 0
    if minutes == 0:
        return "不到一分钟"
    if minutes % 60 == 0:
        return f"{minutes // 60}小时"
    if minutes > 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours}小时{rest}分钟"
    return f"{minutes}分钟"


class AmbientAssetLoop:
    """Stateful real-audio looper producing 16-bit mono PCM chunks.

    Assets are raw little-endian signed 16-bit PCM at 16 kHz.  A short
    crossfade is applied only at the loop boundary so long recordings and
    music tracks can repeat without a hard click or sudden seam.
    """

    def __init__(self, sound_id: str, *, sample_rate: int = AMBIENT_SAMPLE_RATE,
                 asset_dir: Path | str | None = None, crossfade_sec: float = 2.0):
        if sound_id not in SOUND_BY_ID:
            raise ValueError(f"unsupported ambient sound: {sound_id}")
        if int(sample_rate) != AMBIENT_SAMPLE_RATE:
            raise ValueError(f"ambient assets require {AMBIENT_SAMPLE_RATE} Hz")
        self.sound_id = sound_id
        self.sample_rate = AMBIENT_SAMPLE_RATE
        self.asset_dir = Path(asset_dir).expanduser() if asset_dir is not None else ambient_asset_dir()
        self.path = self.asset_dir / f"{sound_id}.pcm"
        if not self.path.exists():
            raise FileNotFoundError(
                f"ambient asset missing: {self.path}; run tools/fetch_ambient_assets.py"
            )
        raw = self.path.read_bytes()
        if len(raw) < AMBIENT_SAMPLE_RATE * AMBIENT_BYTES_PER_SAMPLE * 3:
            raise ValueError(f"ambient asset too short: {self.path}")
        if len(raw) % 2:
            raw = raw[:-1]
        samples = array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        self.samples = samples
        self.total = len(samples)
        requested = max(0, int(round(float(crossfade_sec) * self.sample_rate)))
        self.crossfade = min(requested, max(0, self.total // 4))
        self.pos = 0

    @staticmethod
    def _clip_int16(value: float) -> int:
        return max(-32768, min(32767, int(round(value))))

    def _next_sample(self) -> int:
        if self.crossfade <= 0:
            value = self.samples[self.pos]
            self.pos += 1
            if self.pos >= self.total:
                self.pos = 0
            return value

        fade_start = self.total - self.crossfade
        if self.pos < fade_start:
            value = self.samples[self.pos]
            self.pos += 1
            return value

        i = self.pos - fade_start
        denom = max(1, self.crossfade - 1)
        alpha = i / denom
        tail = self.samples[self.pos]
        head = self.samples[i]
        value = self._clip_int16((1.0 - alpha) * tail + alpha * head)
        self.pos += 1
        if self.pos >= self.total:
            # The first crossfade window is already blended into the tail.
            self.pos = self.crossfade
        return value

    def render(self, duration_sec: float, *, gain_start: float = 1.0, gain_end: float = 1.0) -> bytes:
        count = max(1, int(round(float(duration_sec) * self.sample_rate)))
        out = array("h")
        append = out.append
        start = max(0.0, float(gain_start))
        end = max(0.0, float(gain_end))
        denom = max(1, count - 1)
        for i in range(count):
            gain = start + (end - start) * (i / denom)
            append(self._clip_int16(self._next_sample() * gain))
        if sys.byteorder != "little":
            out.byteswap()
        return out.tobytes()


def sound_catalog_text() -> str:
    return "、".join(item.label for item in SOUNDS)


def sound_catalog_numbered_text() -> str:
    return numbered_sound_catalog_text()
