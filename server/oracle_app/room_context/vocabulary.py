from __future__ import annotations

import re
from typing import Any

from oracle_app.config import load_home_assistant_cache
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings


def build_room_alias_pattern(alias_text: str) -> str:
    chars = [char for char in alias_text.lower() if char.isalnum()]
    if len(chars) < 4:
        body = re.escape(alias_text)
    else:
        body = r"[\s\-']*".join(re.escape(char) for char in chars)
    return rf"(?<![a-z0-9]){body}(?![a-z0-9])"


def get_room_vocabulary(
    household_settings: HouseholdRuntimeSettings,
) -> list[dict[str, Any]]:
    vocabulary = [
        {
            "spoken_name": str(room.display_name).strip().lower(),
            "aliases": sorted(
                alias
                for alias in {
                    str(room.id).replace("_", " ").strip().lower(),
                    str(room.display_name).strip().lower(),
                    *(str(alias).strip().lower() for alias in room.aliases),
                }
                if alias
            ),
        }
        for room in household_settings.rooms.values()
        if room.enabled
    ]
    vocabulary.sort(key=lambda item: str(item["spoken_name"]))
    return vocabulary


def canonical_room_name(
    text: str | None,
    household_settings: HouseholdRuntimeSettings,
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
    household_settings: HouseholdRuntimeSettings,
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
    household_settings: HouseholdRuntimeSettings,
) -> bool:
    return canonical_room_name(text, household_settings) is not None
