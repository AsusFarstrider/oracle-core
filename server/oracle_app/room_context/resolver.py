from __future__ import annotations

from dataclasses import dataclass
import re

from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.routing_helpers import extract_room_phrase

from .classifier import classify_room_sensitive_home_command
from .source_registry import get_source_entry
from .vocabulary import canonical_room_name


@dataclass(frozen=True)
class RoomResolutionResult:
    resolved_text: str
    resolved_room: str | None
    resolution_source: str
    room_required: bool
    needs_clarification: bool


def resolve_room_context(
    text: str,
    *,
    source: str | None = None,
    active_room_ref: str | None = None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> RoomResolutionResult:
    normalized = " ".join(str(text).strip().lower().split())
    explicit_room_phrase = _extract_explicit_room_phrase(normalized)
    if household_settings is None:
        explicit_room_phrase = extract_room_phrase(normalized) or explicit_room_phrase
    explicit_room = _resolve_room_id(
        explicit_room_phrase,
        household_settings=household_settings,
    )
    if explicit_room:
        return RoomResolutionResult(
            resolved_text=normalized,
            resolved_room=explicit_room,
            resolution_source="explicit_room",
            room_required=True,
            needs_clarification=False,
        )

    sensitivity = classify_room_sensitive_home_command(
        normalized,
        household_settings=household_settings,
    )
    if sensitivity is None:
        return RoomResolutionResult(
            resolved_text=normalized,
            resolved_room=None,
            resolution_source="not_needed",
            room_required=False,
            needs_clarification=False,
        )

    if _contains_deictic_room(normalized):
        associated_room = _associated_room_for_source(
            source,
            household_settings=household_settings,
        )
        if associated_room:
            return RoomResolutionResult(
                resolved_text=normalized,
                resolved_room=associated_room,
                resolution_source=(
                    "deictic_source_association"
                    if household_settings is not None
                    else "deictic_source_room"
                ),
                room_required=True,
                needs_clarification=False,
            )
        return RoomResolutionResult(
            resolved_text=normalized,
            resolved_room=None,
            resolution_source="unresolved",
            room_required=True,
            needs_clarification=True,
        )

    session_room = _resolve_room_id(
        active_room_ref,
        household_settings=household_settings,
    )
    if session_room:
        return RoomResolutionResult(
            resolved_text=normalized,
            resolved_room=session_room,
            resolution_source="session_room",
            room_required=True,
            needs_clarification=False,
        )

    associated_room = _associated_room_for_source(
        source,
        household_settings=household_settings,
    )
    if associated_room:
        return RoomResolutionResult(
            resolved_text=normalized,
            resolved_room=associated_room,
            resolution_source=(
                "source_association_fallback"
                if household_settings is not None
                else "source_default"
            ),
            room_required=True,
            needs_clarification=False,
        )

    return RoomResolutionResult(
        resolved_text=normalized,
        resolved_room=None,
        resolution_source="unresolved",
        room_required=True,
        needs_clarification=True,
    )


def _contains_deictic_room(text: str) -> bool:
    return " here" in f" {text} " or "in this room" in text or text.endswith("this room")


def _extract_explicit_room_phrase(text: str) -> str:
    light_match = re.fullmatch(r"(?:turn|switch) (?:on|off) (?:the )?(.+?) lights", text)
    if light_match is not None:
        subject = str(light_match.group(1) or "").strip()
        if subject.lower() != "the":
            return subject
    brightness_match = re.fullmatch(r"set the lights in the (.+?) to .+", text)
    if brightness_match is not None:
        return str(brightness_match.group(1) or "").strip()
    climate_match = re.fullmatch(r"set the temperature (?:lower|higher) in the (.+)", text)
    if climate_match is not None:
        return str(climate_match.group(1) or "").strip()
    subject_match = re.match(r"^(?:it is|it's|the)? ?([a-z0-9][a-z0-9 -]{1,40}) (?:is|feels) ", text)
    if subject_match is not None:
        subject = str(subject_match.group(1) or "").strip()
        if subject not in {"it", "here", "there", "inside", "outside"}:
            return subject
    marker = " in the "
    if marker in text:
        return text.split(marker)[-1].strip()
    marker = " in "
    if marker in text:
        return text.split(marker)[-1].strip()
    return ""


def _associated_room_for_source(
    source: str | None,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    if household_settings is not None:
        return household_settings.configured_associated_room_id(source)
    entry = get_source_entry(source)
    if not isinstance(entry, dict):
        return None
    if not bool(entry.get("fixed")):
        return None
    return canonical_room_name(str(entry.get("default_room") or "").strip())


def _resolve_room_id(
    value: str | None,
    *,
    household_settings: HouseholdRuntimeSettings | None,
) -> str | None:
    if household_settings is not None:
        return household_settings.resolve_room_id(value)
    return canonical_room_name(value)
