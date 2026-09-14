#!/usr/bin/env python3
"""KitchenTerminal dinner-menu parser.

Parses the human-first Kitchen Schema v1 Markdown saved by the OpenClaw
``dinner-recipe-save`` skill.  It intentionally uses only the Python standard
library so the Gateway doesn't gain another runtime dependency.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any


class KitchenMenuError(RuntimeError):
    pass


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            pass
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        raise KitchenMenuError("YAML front matter is not closed")

    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    data: dict[str, Any] = {}
    menu: list[dict[str, Any]] = []
    tags: list[str] = []
    mode = ""
    current_menu: dict[str, Any] | None = None

    for raw in fm_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        if not raw.startswith(" ") and ":" in raw:
            key, value = raw.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "menu":
                mode = "menu"
                data["menu"] = menu
                current_menu = None
                continue
            if key == "tags":
                mode = "tags"
                data["tags"] = tags
                continue
            mode = ""
            data[key] = _scalar(value)
            continue

        if mode == "menu":
            if stripped.startswith("- "):
                current_menu = {}
                menu.append(current_menu)
                item = stripped[2:].strip()
                if ":" in item:
                    key, value = item.split(":", 1)
                    current_menu[key.strip()] = _scalar(value)
            elif current_menu is not None and ":" in stripped:
                key, value = stripped.split(":", 1)
                current_menu[key.strip()] = _scalar(value)
            continue

        if mode == "tags" and stripped.startswith("- "):
            tags.append(stripped[2:].strip())

    return data, body


def _section(body: str, title_fragment: str) -> str:
    # Match a level-2 heading containing the stable fragment and capture until
    # the next level-2 heading.  Emoji/Chinese numbering are intentionally
    # ignored so minor presentation tweaks don't break parsing.
    pattern = re.compile(
        rf"^##\s+[^\n]*{re.escape(title_fragment)}[^\n]*\n(?P<content>.*?)(?=^##\s+|\Z)",
        re.M | re.S,
    )
    match = pattern.search(body)
    return (match.group("content").strip() if match else "")


def _bullets(text: str) -> list[str]:
    return [
        re.sub(r"^[-*+]\s+", "", line).strip()
        for line in text.splitlines()
        if re.match(r"^\s*[-*+]\s+", line)
    ]


def _numbered(text: str) -> list[str]:
    items: list[str] = []
    current = ""
    for line in text.splitlines():
        match = re.match(r"^\s*\d+[.)、]\s*(.+)$", line)
        if match:
            if current:
                items.append(current.strip())
            current = match.group(1).strip()
        elif current and line.strip() and not line.lstrip().startswith("#"):
            current += " " + line.strip()
    if current:
        items.append(current.strip())
    return items


def _split_subsections(block: str) -> dict[str, str]:
    result: dict[str, str] = {}
    matches = list(re.finditer(r"^####\s+(.+?)\s*$", block, re.M))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(block)
        result[match.group(1).strip()] = block[start:end].strip()
    return result


def _strip_bold_field(block: str, label: str) -> str:
    pattern = re.compile(rf"^\*\*{re.escape(label)}\*\*[：:]\s*(.+)$", re.M)
    match = pattern.search(block)
    return match.group(1).strip() if match else ""




def _duration_seconds(value: float, unit: str) -> int:
    unit = unit.strip()
    if unit in {"小时", "时"}:
        return max(1, int(round(value * 3600)))
    if unit in {"分钟", "分"}:
        return max(1, int(round(value * 60)))
    return max(1, int(round(value)))


def _duration_candidates(text: str) -> list[dict[str, Any]]:
    """Extract explicit cooking durations from human Chinese text.

    Only second/minute/hour units are recognized, so quantities such as
    700ml, 180℃ and 3mm are never mistaken for timers.
    """
    normalized = text.replace("－", "-").replace("—", "-")
    out: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    range_re = re.compile(
        r"(?P<a>\d+(?:\.\d+)?)\s*(?:～|~|-|至|到)\s*"
        r"(?P<b>\d+(?:\.\d+)?)\s*(?P<u>小时|分钟|秒|分|时)"
    )
    for m in range_re.finditer(normalized):
        a = float(m.group("a")); b = float(m.group("b")); unit = m.group("u")
        lo, hi = sorted((_duration_seconds(a, unit), _duration_seconds(b, unit)))
        out.append({"default_sec": lo, "max_sec": hi, "text": m.group(0)})
        occupied.append(m.span())

    single_re = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s*(?P<u>小时|分钟|秒|分|时)")
    for m in single_re.finditer(normalized):
        if any(not (m.end() <= a or m.start() >= b) for a, b in occupied):
            continue
        sec = _duration_seconds(float(m.group("a")), m.group("u"))
        out.append({"default_sec": sec, "max_sec": sec, "text": m.group(0)})
    return out


def _timer_hint_from_step(raw_step: str) -> tuple[str, dict[str, Any] | None]:
    """Return display step text plus an optional countdown suggestion.

    Preferred source is the human-readable nested directive emitted by the
    dinner Skill, e.g. ``- ⏱️ 计时：15分钟，可延长至20分钟``.  Older menus
    are supported by conservative inference from duration phrases in the step.
    """
    directive_re = re.compile(
        r"(?:^|\s)-?\s*⏱(?:️)?\s*计时[：:]\s*(?P<body>.+?)"
        r"(?=(?:\s+-?\s*⏱(?:️)?\s*计时[：:])|$)"
    )
    m = directive_re.search(raw_step)
    if m:
        body = m.group("body").strip()
        candidates = _duration_candidates(body)
        clean = (raw_step[:m.start()] + raw_step[m.end():]).strip()
        clean = re.sub(r"\s+", " ", clean)
        if candidates:
            default = candidates[0]["default_sec"]
            maximum = max(c["max_sec"] for c in candidates)
            return clean, {
                "default_sec": default,
                "max_sec": maximum,
                "label": body,
                "source": "explicit",
            }
        return clean, None

    candidates = _duration_candidates(raw_step)
    if not candidates:
        return raw_step.strip(), None
    # Legacy fallback: the longest wait in a step is usually the useful timer.
    # Explicit Skill metadata overrides this for all newly generated menus.
    chosen = max(candidates, key=lambda c: (c["default_sec"], c["max_sec"]))
    return raw_step.strip(), {
        "default_sec": int(chosen["default_sec"]),
        "max_sec": int(chosen["max_sec"]),
        "label": str(chosen["text"]),
        "source": "inferred",
    }


def _parse_recipes(cooking: str) -> list[dict[str, Any]]:
    recipes: list[dict[str, Any]] = []
    heading_re = re.compile(r"^###\s+(?:\d+[.)、]\s*)?(.+?)\s*$", re.M)
    matches = list(heading_re.finditer(cooking))
    for idx, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(cooking)
        block = cooking[start:end].strip()
        subs = _split_subsections(block)
        raw_steps = _numbered(subs.get("做法", ""))
        steps: list[str] = []
        step_timers: list[dict[str, Any] | None] = []
        for raw_step in raw_steps:
            clean_step, timer_hint = _timer_hint_from_step(raw_step)
            steps.append(clean_step)
            step_timers.append(timer_hint)
        recipe = {
            "name": name,
            "type_label": _strip_bold_field(block, "类型"),
            "estimated_text": _strip_bold_field(block, "预计用时"),
            "ingredients": _bullets(subs.get("食材", "")),
            "seasoning": _bullets(subs.get("调味", "")),
            "steps": steps,
            "step_timers": step_timers,
            "key_points": _bullets(subs.get("关键点", "")),
        }
        if not recipe["steps"]:
            raise KitchenMenuError(f"recipe has no numbered steps: {name}")
        recipes.append(recipe)
    return recipes


def _parse_shopping(shopping: str) -> list[dict[str, Any]]:
    categories: list[dict[str, Any]] = []
    matches = list(re.finditer(r"^\*\*(.+?)\*\*\s*$", shopping, re.M))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(shopping)
        items = _bullets(shopping[start:end])
        if items:
            categories.append({"name": match.group(1).strip(), "items": items})
    if not categories:
        items = _bullets(shopping)
        if items:
            categories.append({"name": "购物清单", "items": items})
    return categories


def _parse_prep(prep: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", prep, re.M))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(prep)
        items = _bullets(prep[start:end])
        if items:
            entries.append({"name": match.group(1).strip(), "items": items})
    return entries


def parse_kitchen_menu(text: str, *, source: str = "") -> dict[str, Any]:
    front, body = _parse_front_matter(text)
    if front.get("type") != "dinner-menu":
        raise KitchenMenuError("front matter type is not dinner-menu")
    if front.get("schema") != "kitchen-menu-v1":
        raise KitchenMenuError("unsupported schema; expected kitchen-menu-v1")
    if not front.get("date"):
        raise KitchenMenuError("front matter date is missing")

    cooking = _section(body, "烹饪步骤")
    recipes = _parse_recipes(cooking)
    recipe_by_name = {recipe["name"]: recipe for recipe in recipes}

    menu_items = front.get("menu") if isinstance(front.get("menu"), list) else []
    normalized_menu: list[dict[str, Any]] = []
    for idx, raw in enumerate(menu_items):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        normalized_menu.append({
            "name": name,
            "category": str(raw.get("category") or "other"),
            "cook_order": int(raw.get("cook_order") or (idx + 1)),
        })
    normalized_menu.sort(key=lambda item: item["cook_order"])
    if not normalized_menu:
        normalized_menu = [
            {"name": recipe["name"], "category": "other", "cook_order": idx + 1}
            for idx, recipe in enumerate(recipes)
        ]

    missing = [item["name"] for item in normalized_menu if item["name"] not in recipe_by_name]
    if missing:
        raise KitchenMenuError("menu item missing matching recipe section: " + ", ".join(missing))

    menu = {
        "schema": "kitchen-menu-v1",
        "source": source,
        "date": str(front.get("date")),
        "weekday": str(front.get("weekday") or ""),
        "servings": front.get("servings"),
        "style": str(front.get("style") or ""),
        "estimated_minutes": front.get("estimated_minutes"),
        "status": str(front.get("status") or "planned"),
        "items": normalized_menu,
        "recipes": recipes,
        "shopping": _parse_shopping(_section(body, "超市购物单")),
        "prep": _parse_prep(_section(body, "统一备菜")),
        "timeline": _numbered(_section(body, "省事操作时间线")),
        "body": body,
    }
    return menu


def read_menu_file(path: Path, *, retries: int = 3, delay: float = 0.6) -> str:
    """Best-effort iCloud-safe read.

    Normal reads are preferred.  If iCloud temporarily reports a lock/deadlock,
    retry, then copy to a local temporary sibling under /tmp and read the copy.
    """
    last_exc: OSError | None = None
    for attempt in range(max(1, retries)):
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay * (attempt + 1))

    tmp = Path("/tmp") / f"homeai_kitchen_{path.stem}_{int(time.time()*1000)}.md"
    try:
        shutil.copyfile(path, tmp)
        return tmp.read_text(encoding="utf-8")
    except OSError as exc:
        last_exc = exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    raise KitchenMenuError(f"unable to read menu file {path}: {last_exc}")


def load_kitchen_menu(menu_dir: Path, date_text: str) -> dict[str, Any]:
    path = menu_dir / f"{date_text}.md"
    if not path.exists():
        raise KitchenMenuError(f"menu file not found: {path}")
    text = read_menu_file(path)
    return parse_kitchen_menu(text, source=str(path))


def recipe_for(menu: dict[str, Any], name: str) -> dict[str, Any] | None:
    target = name.strip()
    for recipe in menu.get("recipes") or []:
        if str(recipe.get("name") or "") == target:
            return recipe
    return None


def match_recipe(menu: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Small deterministic matcher for voice commands such as '打开河虾'."""
    q = re.sub(r"[\s，。！？、,.!?]", "", query).lower()
    if not q:
        return None
    recipes = menu.get("recipes") or []
    # Exact normalized name first.
    for recipe in recipes:
        name = re.sub(r"[\s，。！？、,.!?]", "", str(recipe.get("name") or "")).lower()
        if q == name:
            return recipe
    # Query contained in dish name (e.g. 河虾 -> 葱姜盐水河虾).
    candidates = []
    for recipe in recipes:
        name = re.sub(r"[\s，。！？、,.!?]", "", str(recipe.get("name") or "")).lower()
        if q in name or name in q:
            candidates.append(recipe)
    if len(candidates) == 1:
        return candidates[0]
    return None
