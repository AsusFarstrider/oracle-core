from __future__ import annotations

import re

from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.routing_helpers import extract_room_phrase, match_cached_entity


_DEICTIC_PATTERNS = (
    r"\bhere\b",
    r"\bin this room\b",
    r"\bthis room\b",
)


def _has_explicit_room_or_entity(
    text: str,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> bool:
    normalized = f" {text.strip().lower()} "
    if extract_room_phrase(text, household_settings=household_settings):
        return True
    light_match = re.fullmatch(r"(?:turn|switch) (?:on|off) (?:the )?(.+?) lights", text)
    if light_match is not None:
        subject = str(light_match.group(1) or "").strip().lower()
        if subject and subject != "the":
            return True
    if re.fullmatch(r"set the temperature (?:lower|higher) in the [a-z0-9][a-z0-9 ':-]{1,40}", text):
        return True
    if re.search(r"\bin (?:the )?[a-z0-9][a-z0-9 ':-]{1,40}\b", normalized):
        return True
    if match_cached_entity(text, domains={"light", "switch", "climate", "fan"}) is not None:
        return True
    if any(token in normalized for token in (" lamp ", " lamps ", " thermostat ")):
        return True
    return False


def _contains_deictic_room(text: str) -> bool:
    normalized = text.strip().lower()
    return any(re.search(pattern, normalized) is not None for pattern in _DEICTIC_PATTERNS)


def classify_room_sensitive_home_command(
    text: str,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None

    if _contains_deictic_room(normalized):
        if any(token in normalized for token in ("light", "lights", "warmer", "cooler", "temperature")):
            return "deictic_room"

    if _has_explicit_room_or_entity(
        normalized,
        household_settings=household_settings,
    ):
        return None

    if any(token in normalized for token in ("light", "lights")):
        if any(token in normalized for token in ("turn on", "turn off", "switch on", "switch off", "set the lights")):
            return "lights_room"

    if any(token in normalized for token in ("warmer", "cooler", "temperature lower", "temperature higher")):
        return "climate_room"

    return None
