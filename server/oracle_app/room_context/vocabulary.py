from __future__ import annotations

import re
from typing import Any

from oracle_app.config import get_source_registry, load_home_assistant_cache
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings


def build_room_alias_pattern(alias_text: str) -> str:
    chars = [char for char in alias_text.lower() if char.isalnum()]
    if len(chars) < 4:
        body = re.escape(alias_text)
    else:
        body = r"[\s\-']*".join(re.escape(char) for char in chars)
    return rf"(?<![a-z0-9]){body}(?![a-z0-9])"


def get_room_vocabulary(
    household_settings: HouseholdRuntimeSettings | None = None,
) -> list[dict[str, Any]]:
    if household_settings is not None:
        vocabulary = []
        for room in household_settings.rooms.values():
            if not room.enabled:
                continue
            spoken_name = str(room.display_name).strip().lower()
            aliases = {
                str(room.id).replace("_", " ").strip().lower(),
                spoken_name,
                *(str(alias).strip().lower() for alias in room.aliases),
            }
            vocabulary.append(
                {
                    "spoken_name": spoken_name,
                    "aliases": sorted(alias for alias in aliases if alias),
                }
            )
        vocabulary.sort(key=lambda item: str(item["spoken_name"]))
        return vocabulary

    cache = load_home_assistant_cache()
    rooms = cache.get("rooms", [])
    if not isinstance(rooms, list):
        rooms = []
    vocabulary_by_name: dict[str, set[str]] = {}
    for room in rooms:
        if not isinstance(room, dict):
            continue
        spoken_name = str(room.get("spoken_name", "")).strip().lower()
        aliases = room.get("aliases", [])
        if not spoken_name or not isinstance(aliases, list):
            continue
        normalized_aliases = sorted(
            {
                str(alias).strip().lower()
                for alias in aliases
                if str(alias).strip()
            }
            | {spoken_name}
        )
        vocabulary_by_name.setdefault(spoken_name, set()).update(normalized_aliases)
    for entry in get_source_registry().values():
        if not isinstance(entry, dict) or not bool(entry.get("fixed")):
            continue
        room_name = str(entry.get("default_room") or "").strip().lower()
        if not room_name:
            continue
        vocabulary_by_name.setdefault(room_name, set()).add(room_name)
    vocabulary = [
        {"spoken_name": spoken_name, "aliases": sorted(aliases | {spoken_name})}
        for spoken_name, aliases in vocabulary_by_name.items()
    ]
    vocabulary.sort(key=lambda item: str(item["spoken_name"]))
    return vocabulary


def canonical_room_name(
    text: str | None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return None
    candidates: list[tuple[int, str]] = []
    for room in get_room_vocabulary(household_settings):
        spoken_name = str(room.get("spoken_name") or "").strip().lower()
        aliases = room.get("aliases") or []
        for alias in aliases:
            alias_text = str(alias).strip().lower()
            if not alias_text:
                continue
            if re.fullmatch(build_room_alias_pattern(alias_text), normalized):
                candidates.append((len(alias_text), spoken_name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def canonical_pending_room_reply_name(
    text: str | None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return None
    direct = canonical_room_name(normalized, household_settings)
    if direct is not None:
        return direct

    trimmed = normalized
    trimmed = re.sub(r"^(?:the|in the|in)\s+", "", trimmed)
    trimmed = re.sub(r"\s+(?:please|pls|now|right now)$", "", trimmed)
    trimmed = re.sub(r"^(?:the|in the|in)\s+", "", trimmed)
    trimmed = " ".join(trimmed.split())
    if not trimmed:
        return None
    return canonical_room_name(trimmed, household_settings)


def room_name_known(
    text: str | None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> bool:
    return canonical_room_name(text, household_settings) is not None
