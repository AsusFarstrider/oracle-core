from __future__ import annotations

import re
from typing import Any

from oracle_app import state
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.routing_helpers import canonicalize_home_command
from oracle_app.session_state import get_active_context

from .classifier import classify_room_sensitive_home_command
from .resolver import resolve_room_context
from .vocabulary import canonical_room_name


def apply_room_context_to_home_text(
    text: str,
    *,
    source: str | None = None,
    session_id: str | None = None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> tuple[str, dict[str, Any]]:
    canonical_text = canonicalize_home_command(
        text,
        household_settings=household_settings,
    )
    pending_room = (
        household_settings.resolve_room_id(canonical_text)
        if household_settings is not None
        else canonical_room_name(canonical_text)
    )
    if state.load_pending_home_request(source, session_id) is not None and pending_room:
        return canonical_text, {
            "room_required": False,
            "resolved_room": None,
            "resolution_source": "pending_home_candidate",
            "needs_clarification": False,
            "injection_kind": "generic_in_the_room",
            "base_text": canonical_text,
        }
    active_context = get_active_context(source, session_id)
    active_room_ref = None
    if isinstance(active_context, dict):
        if (
            str(active_context.get("route_target") or "").strip().lower() == "home_assistant"
            and str(active_context.get("anchor_strength") or "").strip().lower() == "strong"
        ):
            active_room_ref = str(active_context.get("active_room_ref") or "").strip() or None

    resolution = resolve_room_context(
        canonical_text,
        source=source,
        active_room_ref=active_room_ref,
        household_settings=household_settings,
    )
    injection_kind = _resolve_injection_kind(
        canonical_text,
        household_settings=household_settings,
    )
    resolved_text = canonical_text
    if (
        resolution.room_required
        and not resolution.needs_clarification
        and resolution.resolved_room
        and resolution.resolution_source != "explicit_room"
    ):
        command_room = resolution.resolved_room
        if household_settings is not None:
            configured_room = household_settings.room(resolution.resolved_room)
            if configured_room is not None:
                command_room = str(configured_room.display_name).strip().lower()
        resolved_text = inject_room_into_home_command(
            canonical_text,
            room=command_room,
            injection_kind=injection_kind,
        )

    return resolved_text, {
        "room_required": resolution.room_required,
        "resolved_room": resolution.resolved_room,
        "resolution_source": resolution.resolution_source,
        "needs_clarification": resolution.needs_clarification,
        "injection_kind": injection_kind,
        "base_text": _strip_deictic_phrases(canonical_text),
    }


def inject_room_into_home_command(text: str, *, room: str, injection_kind: str) -> str:
    normalized = _strip_deictic_phrases(" ".join(str(text).strip().lower().split()))
    room_name = str(room).strip().lower()
    if not normalized or not room_name:
        return normalized

    if injection_kind == "lights_room":
        if re.fullmatch(r"(turn|switch) (on|off) the lights", normalized):
            return re.sub(r"the lights$", f"the {room_name} lights", normalized, count=1)
        if re.fullmatch(r"set the lights to .+", normalized):
            return normalized.replace("set the lights to ", f"set the lights in the {room_name} to ", 1)
        if " lights " in f" {normalized} " and f" in the {room_name}" not in normalized:
            return normalized.replace(" lights", f" lights in the {room_name}", 1)
        return f"turn on the {room_name} lights"

    if injection_kind == "climate_room":
        if re.fullmatch(r"set the temperature (lower|higher)", normalized):
            return f"{normalized} in the {room_name}"
        if "thermostat" in normalized and f" in the {room_name}" not in normalized:
            return f"{normalized} in the {room_name}"
        return f"set the temperature lower in the {room_name}"

    if f" in the {room_name}" not in normalized:
        return f"{normalized} in the {room_name}"
    return normalized


def _resolve_injection_kind(
    text: str,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str:
    sensitivity = classify_room_sensitive_home_command(
        text,
        household_settings=household_settings,
    )
    if sensitivity in {"lights_room", "deictic_room"} and any(token in text for token in ("light", "lights")):
        return "lights_room"
    if sensitivity in {"climate_room", "deictic_room"}:
        return "climate_room"
    return "generic_in_the_room"


def _strip_deictic_phrases(text: str) -> str:
    normalized = " ".join(str(text).strip().lower().split())
    normalized = re.sub(r"\bin this room\b", "", normalized)
    normalized = re.sub(r"\bhere\b", "", normalized)
    normalized = re.sub(r"\bthis room\b", "", normalized)
    return " ".join(normalized.split())
