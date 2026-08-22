from __future__ import annotations

import re

from .config import load_home_assistant_cache
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .constants import (
    DATE_QUERY_PHRASES,
    DEFAULT_NORMAL_LIGHT_BRIGHTNESS_PERCENT,
    DEFAULT_NORMAL_LIGHT_COLOR_TEMPERATURE_KELVIN,
    HOME_KEYWORDS,
    SYSTEM_CACHE_REFRESH_PHRASES,
    SYSTEM_CANCEL_PHRASES,
    SYSTEM_CONFIRM_PHRASES,
    TIME_QUERY_PHRASES,
)

_LIGHT_COLOR_PHRASES = (
    "warmer white",
    "cooler white",
    "warm white",
    "cool white",
    "blue",
    "red",
    "green",
    "purple",
    "orange",
    "pink",
    "yellow",
    "amber",
    "white",
)

_BRIGHTER_BRIGHTNESS_PERCENT = 75
_DIMMER_BRIGHTNESS_PERCENT = 25
_SLIGHT_BRIGHTER_BRIGHTNESS_PERCENT = 60
_SLIGHT_DIMMER_BRIGHTNESS_PERCENT = 40


def detect_system_cache_refresh(text: str) -> bool:
    normalized = text.strip().lower()
    if any(phrase in normalized for phrase in SYSTEM_CACHE_REFRESH_PHRASES):
        return True
    if "home assistant" not in normalized:
        return False
    if not any(action in normalized for action in ("refresh", "sync", "update")):
        return False
    return "cache" in normalized or any(target in normalized for target in ("device", "devices", "room", "rooms"))


def detect_system_confirm(text: str) -> bool:
    return text in SYSTEM_CONFIRM_PHRASES


def detect_system_cancel(text: str) -> bool:
    return text in SYSTEM_CANCEL_PHRASES


def detect_time_query(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if any(phrase in normalized for phrase in TIME_QUERY_PHRASES):
        return True
    return bool(re.search(r"\btime\b", normalized)) and any(
        token in normalized for token in ("what", "tell me", "current", "right now")
    )


def detect_date_query(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if any(phrase in normalized for phrase in DATE_QUERY_PHRASES):
        return True
    return bool(re.search(r"\bdate\b", normalized)) or "day is it" in normalized


def detect_unit_conversion_query(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if normalized.startswith("remind me"):
        return False
    if normalized.startswith("convert "):
        return True
    if normalized.startswith("how many ") and (" is " in normalized or " are " in normalized):
        return True
    return normalized.startswith(("what is ", "what's ")) and (" in " in normalized or " to " in normalized) and bool(
        re.search(r"-?\d+(?:\.\d+)?", normalized)
    )


def detect_math_query(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if detect_unit_conversion_query(normalized):
        return False
    if re.search(r"\d+\s*[\+\-\*\/]\s*\d+", normalized):
        return True
    if any(phrase in normalized for phrase in ("plus", "minus", "times", "divided by", "multiplied by")):
        return True
    return normalized.startswith(("what is ", "what's ", "calculate ", "compute ")) and bool(
        re.search(r"\d", normalized)
    )


def detect_date_calculation_query(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False
    return bool(
        re.match(
            r"^(?:how many days|how long) until .+|^how many days since .+|^what day of the week is .+",
            normalized,
        )
    )


def detect_alert_query(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    return any(
        phrase in normalized
        for phrase in (
            "timer",
            "countdown",
            "alarm",
            "remind me",
            "reminder",
        )
    )


def has_home_keyword(text: str) -> tuple[bool, str | None]:
    for keyword in HOME_KEYWORDS:
        if _matches_home_keyword(text, keyword):
            return True, keyword
    return False, None


def _matches_home_keyword(text: str, keyword: str) -> bool:
    normalized_keyword = " ".join(str(keyword).strip().lower().split())
    if not normalized_keyword:
        return False
    body = r"\s+".join(re.escape(part) for part in normalized_keyword.split(" "))
    pattern = rf"(?<![a-z0-9]){body}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def build_room_alias_pattern(alias_text: str) -> str:
    chars = [char for char in alias_text.lower() if char.isalnum()]
    if len(chars) < 4:
        body = re.escape(alias_text)
    else:
        body = r"[\s\-']*".join(re.escape(char) for char in chars)
    return rf"(?<![a-z0-9]){body}(?![a-z0-9])"


def _match_cached_alias(
    text: str,
    items: list[dict[str, object]],
    *,
    spoken_key: str,
) -> tuple[str, dict[str, object]] | None:
    candidates: list[tuple[int, str, str, dict[str, object]]] = []

    for item in items:
        spoken_name = str(item.get(spoken_key, "")).strip()
        aliases = item.get("aliases", [])
        if not spoken_name or not isinstance(aliases, list):
            continue

        for alias in aliases:
            alias_text = str(alias).strip().lower()
            if not alias_text:
                continue
            pattern = build_room_alias_pattern(alias_text)
            if re.search(pattern, text):
                candidates.append((len(alias_text), alias_text, spoken_name, item))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    _, _, spoken_name, item = candidates[0]
    return spoken_name, item


def _room_vocabulary(
    household_settings: HouseholdRuntimeSettings | None,
    *,
    cache: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    if household_settings is not None:
        return [
            {
                "spoken_name": str(room.display_name).strip().lower(),
                "aliases": sorted(
                    {
                        str(room.id).replace("_", " ").strip().lower(),
                        str(room.display_name).strip().lower(),
                        *(str(alias).strip().lower() for alias in room.aliases),
                    }
                ),
            }
            for room in household_settings.rooms.values()
            if room.enabled
        ]
    effective_cache = load_home_assistant_cache() if cache is None else cache
    rooms = effective_cache.get("rooms", [])
    if not isinstance(rooms, list):
        return []
    return [room for room in rooms if isinstance(room, dict)]


def match_cached_room(
    text: str,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    rooms = _room_vocabulary(household_settings)
    matched = _match_cached_alias(text, rooms, spoken_key="spoken_name")
    if matched is None:
        return None
    return matched[0]


def match_cached_entity(
    text: str,
    *,
    domains: set[str] | None = None,
    excluded_aliases: set[str] | None = None,
) -> tuple[str, str] | None:
    cache = load_home_assistant_cache()
    entities = cache.get("entities", [])
    if not isinstance(entities, list):
        return None

    filtered_entities = []
    for entity in entities:
        domain = str(entity.get("domain", "")).strip().lower()
        if domains is not None and domain not in domains:
            continue
        if excluded_aliases:
            entity = dict(entity)
            entity["aliases"] = [
                alias
                for alias in entity.get("aliases", [])
                if str(alias).strip().lower() not in excluded_aliases
            ]
        filtered_entities.append(entity)

    matched = _match_cached_alias(text, filtered_entities, spoken_key="friendly_name")
    if matched is None:
        return None
    spoken_name, entity = matched
    return spoken_name.lower(), str(entity.get("domain", "")).strip().lower()


def _is_generic_room_reference(value: str) -> bool:
    normalized = " ".join(str(value).strip().lower().split())
    if not normalized:
        return True
    return normalized in {
        "it",
        "here",
        "there",
        "inside",
        "outside",
        "in here",
        "in there",
        "around here",
        "around there",
    }


def extract_room_phrase(
    text: str,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    cached_room = match_cached_room(text, household_settings=household_settings)
    if cached_room is not None:
        return cached_room

    in_match = re.search(r"\bin (?:the )?([a-z0-9][a-z0-9 -]{1,40})$", text)
    if in_match:
        room = in_match.group(1).strip()
        if not _is_generic_room_reference(room):
            return room

    subject_match = re.search(
        r"^(?:it is|it's|the)? ?([a-z0-9][a-z0-9 -]{1,40}) (?:is|feels) ",
        text,
    )
    if subject_match:
        room = subject_match.group(1).strip()
        if not _is_generic_room_reference(room):
            cached_subject_room = match_cached_room(
                room,
                household_settings=household_settings,
            )
            if cached_subject_room is not None:
                return cached_subject_room

    return None


def extract_make_subject_phrase(text: str) -> str | None:
    match = re.search(
        r"\bmake (?:the )?([a-z0-9][a-z0-9 ':-]{1,40}?)"
        r"(?: (?:a little|a bit|slightly|somewhat|more))?"
        r" (?:brighter|dimmer|darker|warmer|cooler|less bright|more bright|more dim)\b",
        text,
    )
    if match is None:
        return None
    subject = match.group(1).strip()
    if _is_generic_room_reference(subject):
        return None
    return subject


def _has_brightness_modifier(text: str) -> bool:
    return any(phrase in text for phrase in ("a little", "a bit", "slightly", "somewhat"))


def _brightness_percent_for_increase(text: str) -> int:
    if _has_brightness_modifier(text):
        return _SLIGHT_BRIGHTER_BRIGHTNESS_PERCENT
    return _BRIGHTER_BRIGHTNESS_PERCENT


def _brightness_percent_for_decrease(text: str) -> int:
    if _has_brightness_modifier(text):
        return _SLIGHT_DIMMER_BRIGHTNESS_PERCENT
    return _DIMMER_BRIGHTNESS_PERCENT


def _build_room_brightness_command(room: str, percent: int) -> str:
    return f"set the {room} lights to {percent} percent brightness"


def _build_entity_brightness_command(entity_name: str, percent: int) -> str:
    return f"set the {entity_name} to {percent} percent brightness"


def canonicalize_home_command(
    text: str,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str:
    cache = load_home_assistant_cache()
    rooms = _room_vocabulary(household_settings, cache=cache)
    normalized = text
    replacements: set[tuple[int, str, str]] = set()
    canonical_room_terms: set[str] = set()

    for room in rooms:
        spoken_name = str(room.get("spoken_name", "")).strip().lower()
        aliases = room.get("aliases", [])
        if not spoken_name or not isinstance(aliases, list):
            continue

        replacements.add((len(spoken_name), spoken_name, spoken_name))
        canonical_room_terms.add(spoken_name)

        for alias in aliases:
            alias_text = str(alias).strip().lower()
            if not alias_text:
                continue
            replacements.add((len(alias_text), alias_text, spoken_name))
            canonical_room_terms.add(alias_text)

    ordered_replacements = sorted(replacements, reverse=True)

    for _, alias_text, spoken_name in ordered_replacements:
        pattern = build_room_alias_pattern(alias_text)
        normalized = re.sub(pattern, spoken_name, normalized)

    entities = cache.get("entities", [])
    if isinstance(entities, list):
        entity_replacements: set[tuple[int, str, str]] = set()
        for entity in entities:
            spoken_name = str(entity.get("friendly_name", "")).strip().lower()
            aliases = entity.get("aliases", [])
            if not spoken_name or not isinstance(aliases, list):
                continue
            if household_settings is None or spoken_name not in canonical_room_terms:
                entity_replacements.add((len(spoken_name), spoken_name, spoken_name))
            for alias in aliases:
                alias_text = str(alias).strip().lower()
                if not alias_text:
                    continue
                if household_settings is not None and alias_text in canonical_room_terms:
                    continue
                entity_replacements.add((len(alias_text), alias_text, spoken_name))
        for _, alias_text, spoken_name in sorted(entity_replacements, reverse=True):
            pattern = build_room_alias_pattern(alias_text)
            normalized = re.sub(pattern, spoken_name, normalized)

    return normalized


def detect_implied_home_command(
    text: str,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> tuple[str, str] | None:
    room = extract_room_phrase(text, household_settings=household_settings)
    make_subject = extract_make_subject_phrase(text)
    room_aliases = (
        {
            str(alias).strip().lower()
            for item in _room_vocabulary(household_settings)
            for alias in item.get("aliases", [])
            if str(alias).strip()
        }
        if household_settings is not None
        else set()
    )
    light_entity = match_cached_entity(
        text,
        domains={"light", "switch"},
        excluded_aliases=room_aliases if household_settings is not None else None,
    )
    climate_entity = match_cached_entity(
        text,
        domains={"climate", "fan"},
        excluded_aliases=room_aliases if household_settings is not None else None,
    )
    normalized_text = f" {text} "
    has_light_subject = any(
        phrase in text
        for phrase in (
            " light",
            " lights",
            " lamp",
            " lamps",
        )
    )
    has_cover_subject = any(
        phrase in text
        for phrase in (
            "blind",
            "blinds",
            "curtain",
            "curtains",
            "shade",
            "shades",
        )
    )
    starts_with = text.startswith
    matched_light_color = next((phrase for phrase in _LIGHT_COLOR_PHRASES if phrase in text), None)

    normal_light_request = (
        "back to normal" in text
        or "normal again" in text
        or re.search(r"\b(?:set|put|make) .+ to normal\b", text) is not None
    )
    if normal_light_request:
        if room:
            return (
                f"set the lights in the {room} to "
                f"{DEFAULT_NORMAL_LIGHT_BRIGHTNESS_PERCENT} percent brightness and "
                f"{DEFAULT_NORMAL_LIGHT_COLOR_TEMPERATURE_KELVIN} kelvin",
                f"Detected normal lighting request for room: {room}",
            )
        if light_entity is not None:
            entity_name, _ = light_entity
            return (
                f"set the {entity_name} to "
                f"{DEFAULT_NORMAL_LIGHT_BRIGHTNESS_PERCENT} percent brightness and "
                f"{DEFAULT_NORMAL_LIGHT_COLOR_TEMPERATURE_KELVIN} kelvin",
                f"Detected normal lighting request for entity: {entity_name}",
            )

    if matched_light_color is not None and (
        has_light_subject
        or room is not None
        or make_subject is not None
    ):
        if room:
            return (
                f"set the lights in the {room} to {matched_light_color}",
                f"Detected lighting color request for room: {room}",
            )
        if light_entity is not None:
            entity_name, _ = light_entity
            return (
                f"set the {entity_name} to {matched_light_color}",
                f"Detected lighting color request for entity: {entity_name}",
            )
        if make_subject and any(token in make_subject for token in ("light", "lights", "lamp", "lamps")):
            return (
                f"set the {make_subject} to {matched_light_color}",
                f"Detected lighting color request for entity phrase: {make_subject}",
            )

    if has_light_subject and any(phrase in text for phrase in (" softer", " warmer", " cooler")):
        white_target = None
        if "softer" in text or "warmer" in text:
            white_target = "warm white"
        elif "cooler" in text:
            white_target = "cool white"
        if white_target is not None:
            if room:
                return (
                    f"set the lights in the {room} to {white_target}",
                    f"Detected lighting white-tone request for room: {room}",
                )
            if light_entity is not None:
                entity_name, _ = light_entity
                return (
                    f"set the {entity_name} to {white_target}",
                    f"Detected lighting white-tone request for entity: {entity_name}",
                )
            if make_subject and any(token in make_subject for token in ("light", "lights", "lamp", "lamps")):
                return (
                    f"set the {make_subject} to {white_target}",
                    f"Detected lighting white-tone request for entity phrase: {make_subject}",
                )

    if has_cover_subject and any(
        phrase in text or starts_with(phrase.lstrip())
        for phrase in (" raise the ", "raise ", " lower the ", "lower ")
    ):
        if any(phrase in text or starts_with(phrase.lstrip()) for phrase in (" raise the ", "raise ")):
            return (
                canonicalize_home_command(
                    text.replace("raise", "open", 1),
                    household_settings=household_settings,
                ),
                "Detected implied cover request",
            )
        if any(phrase in text or starts_with(phrase.lstrip()) for phrase in (" lower the ", "lower ")):
            return (
                canonicalize_home_command(
                    text.replace("lower", "close", 1),
                    household_settings=household_settings,
                ),
                "Detected implied cover request",
            )

    if has_light_subject and any(
        phrase in text or starts_with(phrase.lstrip())
        for phrase in (
            " lights down",
            " light down",
            "turn down ",
            " lower the ",
            "bring down ",
            " bring the ",
            " set the ",
        )
    ):
        if any(
            phrase in text or starts_with(phrase.lstrip())
            for phrase in (" lights down", " light down", "turn down ", " lower the ", "bring down ", " bring the ")
        ):
            if room:
                return (
                    _build_room_brightness_command(room, _brightness_percent_for_decrease(text)),
                    f"Detected implied lighting request for room: {room}",
                )
            if light_entity is not None:
                entity_name, _ = light_entity
                return (
                    _build_entity_brightness_command(entity_name, _brightness_percent_for_decrease(text)),
                    f"Detected implied lighting request for entity: {entity_name}",
                )
        if starts_with("set the ") and any(
            phrase in text
            for phrase in (
                " lights lower",
                " light lower",
                " lights a little lower",
                " light a little lower",
                " lights a bit lower",
                " light a bit lower",
                " lights slightly lower",
                " light slightly lower",
            )
        ):
            if room:
                return (
                    _build_room_brightness_command(room, _brightness_percent_for_decrease(text)),
                    f"Detected implied lighting request for room: {room}",
                )
            if light_entity is not None:
                entity_name, _ = light_entity
                return (
                    _build_entity_brightness_command(entity_name, _brightness_percent_for_decrease(text)),
                    f"Detected implied lighting request for entity: {entity_name}",
                )

    if has_light_subject and any(
        phrase in text or starts_with(phrase.lstrip())
        for phrase in (
            " lights up",
            " light up",
            "turn up ",
            "bring up ",
            " raise the ",
            " set the ",
        )
    ):
        if any(
            phrase in text or starts_with(phrase.lstrip())
            for phrase in (" lights up", " light up", "turn up ", "bring up ", " raise the ")
        ):
            if room:
                return (
                    _build_room_brightness_command(room, _brightness_percent_for_increase(text)),
                    f"Detected implied lighting request for room: {room}",
                )
            if light_entity is not None:
                entity_name, _ = light_entity
                return (
                    _build_entity_brightness_command(entity_name, _brightness_percent_for_increase(text)),
                    f"Detected implied lighting request for entity: {entity_name}",
                )
        if starts_with("set the ") and any(
            phrase in text
            for phrase in (
                " lights higher",
                " light higher",
                " lights a little higher",
                " light a little higher",
                " lights a bit higher",
                " light a bit higher",
                " lights slightly higher",
                " light slightly higher",
            )
        ):
            if room:
                return (
                    _build_room_brightness_command(room, _brightness_percent_for_increase(text)),
                    f"Detected implied lighting request for room: {room}",
                )
            if light_entity is not None:
                entity_name, _ = light_entity
                return (
                    _build_entity_brightness_command(entity_name, _brightness_percent_for_increase(text)),
                    f"Detected implied lighting request for entity: {entity_name}",
                )

    if any(phrase in text for phrase in ("brighter", "brighten", "more bright")):
        if light_entity is not None:
            entity_name, _ = light_entity
            if room and entity_name == room:
                return (
                    _build_room_brightness_command(room, _brightness_percent_for_increase(text)),
                    f"Detected implied lighting request for room: {room}",
                )
            return (
                _build_entity_brightness_command(entity_name, _brightness_percent_for_increase(text)),
                f"Detected implied lighting request for entity: {entity_name}",
            )
        if make_subject and any(token in make_subject for token in ("light", "lights", "lamp", "lamps")):
            return (
                _build_entity_brightness_command(make_subject, _brightness_percent_for_increase(text)),
                f"Detected implied lighting request for entity phrase: {make_subject}",
            )
        if room:
            return (
                _build_room_brightness_command(room, _brightness_percent_for_increase(text)),
                f"Detected implied lighting request for room: {room}",
            )
        if make_subject:
            return (
                _build_room_brightness_command(make_subject, _brightness_percent_for_increase(text)),
                f"Detected implied lighting request for room: {make_subject}",
            )

    if any(phrase in text for phrase in ("dimmer", "darker", "less bright", "more dim")) or re.search(r"\bdim(?:\s|$)", text):
        if light_entity is not None:
            entity_name, _ = light_entity
            if room and entity_name == room:
                return (
                    _build_room_brightness_command(room, _brightness_percent_for_decrease(text)),
                    f"Detected implied lighting request for room: {room}",
                )
            return (
                _build_entity_brightness_command(entity_name, _brightness_percent_for_decrease(text)),
                f"Detected implied lighting request for entity: {entity_name}",
            )
        if make_subject and any(token in make_subject for token in ("light", "lights", "lamp", "lamps")):
            return (
                _build_entity_brightness_command(make_subject, _brightness_percent_for_decrease(text)),
                f"Detected implied lighting request for entity phrase: {make_subject}",
            )
        if room:
            return (
                _build_room_brightness_command(room, _brightness_percent_for_decrease(text)),
                f"Detected implied lighting request for room: {room}",
            )
        if make_subject:
            return (
                _build_room_brightness_command(make_subject, _brightness_percent_for_decrease(text)),
                f"Detected implied lighting request for room: {make_subject}",
            )

    if (
        room
        and (re.search(r"\bdark\b", text) or re.search(r"\bdim\b", text))
        and " more dim" not in normalized_text
        and " dimmer " not in normalized_text
        and not re.search(r"\bdim (?:the )?(?:light|lights|lamp|lamps)\b", text)
    ):
        return (
            f"turn on the lights in the {room}",
            f"Detected implied lighting request for room: {room}",
        )

    if (
        room
        and "bright" in text
        and not any(phrase in normalized_text for phrase in (" brighter ", " brighten ", " more bright ", " less bright "))
    ):
        return (
            f"turn off the lights in the {room}",
            f"Detected implied lighting request for room: {room}",
        )

    if any(phrase in text for phrase in ("cooler", "cool down")) or re.search(r"\bcool (?:the )?.+ down\b", text):
        if room:
            return (
                f"set the temperature lower in the {room}",
                f"Detected implied climate request for room: {room}",
            )
        if make_subject:
            return (
                f"set the temperature lower in the {make_subject}",
                f"Detected implied climate request for room: {make_subject}",
            )
        if climate_entity is not None:
            entity_name, _ = climate_entity
            return (
                f"set the temperature lower for the {entity_name}",
                f"Detected implied climate request for entity: {entity_name}",
            )

    if any(phrase in text for phrase in ("colder", "cool off")):
        if room:
            return (
                f"set the temperature lower in the {room}",
                f"Detected implied climate request for room: {room}",
            )
        if make_subject:
            return (
                f"set the temperature lower in the {make_subject}",
                f"Detected implied climate request for room: {make_subject}",
            )

    if any(phrase in text for phrase in ("warmer", "warm up")) or re.search(r"\bwarm (?:the )?.+ up\b", text):
        if room:
            return (
                f"set the temperature higher in the {room}",
                f"Detected implied climate request for room: {room}",
            )
        if make_subject:
            return (
                f"set the temperature higher in the {make_subject}",
                f"Detected implied climate request for room: {make_subject}",
            )
        if climate_entity is not None:
            entity_name, _ = climate_entity
            return (
                f"set the temperature higher for the {entity_name}",
                f"Detected implied climate request for entity: {entity_name}",
            )

    if any(phrase in text for phrase in ("hotter", "heat up")):
        if room:
            return (
                f"set the temperature higher in the {room}",
                f"Detected implied climate request for room: {room}",
            )
        if make_subject:
            return (
                f"set the temperature higher in the {make_subject}",
                f"Detected implied climate request for room: {make_subject}",
            )

    if any(phrase in text for phrase in ("temperature down", "temperature lower")):
        if room:
            return (
                f"set the temperature lower in the {room}",
                f"Detected implied climate request for room: {room}",
            )
    if any(phrase in text for phrase in ("temperature up", "temperature higher")):
        if room:
            return (
                f"set the temperature higher in the {room}",
                f"Detected implied climate request for room: {room}",
            )

    if any(phrase in text for phrase in ("too hot", "very hot", "warm")) and room:
        return (
            f"set the temperature lower in the {room}",
            f"Detected implied climate request for room: {room}",
        )

    if any(phrase in text for phrase in ("too cold", "very cold", "chilly")) and room:
        return (
            f"set the temperature higher in the {room}",
            f"Detected implied climate request for room: {room}",
        )

    return None
