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


def _section_first(body: str, title_fragments: list[str]) -> str:
    """Return the first matching level-2 section from a small alias set."""
    for fragment in title_fragments:
        content = _section(body, fragment)
        if content:
            return content
    return ""


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
            "prep_items": _bullets(subs.get("备菜", "")),
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
    """Parse meal-level prep notes.

    Preferred format uses ``### 菜名`` headings.  Older dinner notes may only
    contain bullets; those are kept as an unnamed entry and matched to a dish
    conservatively by ingredient names.
    """
    entries: list[dict[str, Any]] = []
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", prep, re.M))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(prep)
        items = _bullets(prep[start:end])
        if items:
            name = re.sub(r"^\d+[.)、]\s*", "", match.group(1).strip())
            entries.append({"name": name, "items": items})
    if not entries:
        items = _bullets(prep)
        if items:
            entries.append({"name": "", "items": items})
    return entries


def _prep_keyword(ingredient: str) -> str:
    """Extract a conservative ingredient name for matching legacy prep notes."""
    text = re.sub(r"[（(].*?[）)]", "", str(ingredient or "")).strip()
    text = re.split(r"\s+|\d", text, maxsplit=1)[0].strip("：:，,、")
    return text if len(text) >= 1 else ""


_PREP_ACTION_HINTS = (
    "洗净", "洗好", "切片", "切块", "切段", "切丝", "切丁", "切末",
    "切碎", "剁碎", "拍碎", "去皮", "去壳", "去籽", "去蒂", "去根",
    "泡发", "泡软", "浸泡", "解冻", "回温", "沥干", "腌制", "腌一下",
    "焯水", "焯一下", "调成", "调匀", "拌匀", "称量", "提前烧", "烧开备用",
)


def _prep_actions_from_cook_steps(recipe: dict[str, Any]) -> list[str]:
    """Recover explicit prep actions already present in source cooking text.

    This is deliberately conservative: it only copies preparation wording which
    already exists in the recipe.  It never invents a cut, soak, blanch or
    marination instruction which the source did not contain.
    """
    recovered: list[str] = []
    seen: set[str] = set()
    for raw_step in recipe.get("steps") or []:
        text = str(raw_step or "").strip()
        if not text:
            continue
        for clause in re.split(r"[。；;！!]+", text):
            clause = clause.strip(" ，,：:\t")
            if not clause or not any(hint in clause for hint in _PREP_ACTION_HINTS):
                continue
            # Keep only reasonably short source clauses.  Long active cooking
            # instructions may contain words such as '切段' incidentally and
            # should not be copied wholesale into the prep screen.
            if len(clause) > 60:
                continue
            if clause not in seen:
                seen.add(clause)
                recovered.append(clause)
    return recovered


def _prep_items_for_recipe(recipe: dict[str, Any], entries: list[dict[str, Any]]) -> list[str]:
    name = str(recipe.get("name") or "").strip()
    selected: list[str] = []
    unnamed: list[str] = []
    for entry in entries:
        entry_name = str(entry.get("name") or "").strip()
        items = [str(x).strip() for x in (entry.get("items") or []) if str(x).strip()]
        if not items:
            continue
        if not entry_name:
            unnamed.extend(items)
            continue
        if entry_name == name or entry_name in name or name in entry_name:
            selected.extend(items)

    # Legacy unheaded prep blocks are shared meal notes. Only attach bullets
    # which mention an ingredient of this dish so unrelated prep never leaks
    # into every recipe.
    keywords = [_prep_keyword(x) for x in (recipe.get("ingredients") or [])]
    keywords = [x for x in keywords if x]
    for item in unnamed:
        if any(keyword in item for keyword in keywords):
            selected.append(item)

    # Last-resort compatibility for menus which were generated without the
    # mandatory prep section.  Reuse only prep wording already present in the
    # recipe's source steps; never guess missing preparation.
    recovered = _prep_actions_from_cook_steps(recipe)

    merged: list[str] = []
    seen: set[str] = set()
    for item in list(recipe.get("prep_items") or []) + selected + recovered:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            merged.append(text)
    return merged


def _build_prep_step(recipe: dict[str, Any]) -> str:
    """Build the mandatory first KitchenTerminal step for every dish."""
    lines = ["开火前先完成这道菜的备菜："]
    ingredients = [str(x).strip() for x in (recipe.get("ingredients") or []) if str(x).strip()]
    seasoning = [str(x).strip() for x in (recipe.get("seasoning") or []) if str(x).strip()]
    prep_items = [str(x).strip() for x in (recipe.get("prep_items") or []) if str(x).strip()]
    if ingredients:
        lines.append("【食材】" + "；".join(ingredients))
    if seasoning:
        lines.append("【调味】" + "；".join(seasoning))
    if prep_items:
        lines.append("【提前处理】")
        lines.extend("• " + item for item in prep_items)
    else:
        lines.append("【提前处理】无需额外处理，确认食材、调味和所需厨具已备齐。")
    lines.append("全部完成后，再进入下一步开火烹饪。")
    return "\n".join(lines)


def ensure_recipe_prep_first(recipe: dict[str, Any]) -> dict[str, Any]:
    """Enforce the project invariant: recipe step 0 is always prep.

    This guard intentionally lives below the UI layer so a stale/legacy menu
    object, an older generated menu file, or a future alternate caller cannot
    bypass the prep-first rule.  The function is idempotent.
    """
    steps = list(recipe.get("steps") or [])
    kinds = list(recipe.get("step_kinds") or [])
    timers = list(recipe.get("step_timers") or [])
    if steps and kinds and kinds[0] == "prep":
        recipe["prep_required"] = True
        return recipe

    cook_steps = list(recipe.get("cook_steps") or steps)
    cook_timers = list(recipe.get("cook_step_timers") or timers)
    recipe["cook_steps"] = cook_steps
    recipe["cook_step_timers"] = cook_timers
    recipe["steps"] = [_build_prep_step(recipe)] + cook_steps
    recipe["step_timers"] = [None] + cook_timers
    recipe["step_kinds"] = ["prep"] + ["cook"] * len(cook_steps)
    recipe["prep_required"] = True
    return recipe


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
    prep_entries = _parse_prep(_section_first(body, ["统一备菜", "提前备菜"]))
    for recipe in recipes:
        recipe["prep_items"] = _prep_items_for_recipe(recipe, prep_entries)
        cook_steps = list(recipe.get("steps") or [])
        cook_timers = list(recipe.get("step_timers") or [])
        recipe["cook_steps"] = cook_steps
        recipe["cook_step_timers"] = cook_timers
        ensure_recipe_prep_first(recipe)
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
        "prep_policy": "required-v1",
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
        "prep": prep_entries,
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
            return ensure_recipe_prep_first(recipe)
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
            return ensure_recipe_prep_first(recipe)
    # Query contained in dish name (e.g. 河虾 -> 葱姜盐水河虾).
    candidates = []
    for recipe in recipes:
        name = re.sub(r"[\s，。！？、,.!?]", "", str(recipe.get("name") or "")).lower()
        if q in name or name in q:
            candidates.append(recipe)
    if len(candidates) == 1:
        return ensure_recipe_prep_first(candidates[0])
    return None
