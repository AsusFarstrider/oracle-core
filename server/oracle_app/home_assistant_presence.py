from __future__ import annotations

from typing import Literal

from .configuration.domain_models import HomeAssistantObjectMapping
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .provider_bridges.home_assistant import HomeAssistantBridge
from .presence_intents import PresenceQuery


PresenceState = Literal["home", "away", "unknown"]


def normalize_person_presence(provider_state: str | None) -> PresenceState:
    normalized = str(provider_state or "").strip().casefold()
    if normalized == "home":
        return "home"
    if normalized in {"", "unknown", "unavailable"}:
        return "unknown"
    # Home Assistant may expose a named non-home zone. Oracle intentionally
    # collapses every such provider-specific location to coarse away state.
    return "away"


def read_home_assistant_presence(
    query: PresenceQuery,
    *,
    household_settings: HouseholdRuntimeSettings,
    home_assistant_settings: HomeAssistantRuntimeSettings,
) -> dict[str, object]:
    if (
        not home_assistant_settings.enabled
        or not home_assistant_settings.base_url
        or not home_assistant_settings.credential
    ):
        return _failure("presence_unavailable", "Presence is unavailable because Home Assistant is not enabled.")

    mappings = _presence_mappings(home_assistant_settings)
    bridge = HomeAssistantBridge(
        base_url=str(home_assistant_settings.base_url),
        token=str(home_assistant_settings.credential),
        timeout_seconds=home_assistant_settings.timeout_seconds,
    )
    if query.kind == "person":
        user_id = household_settings.resolve_user_id(query.requested_user_name)
        if user_id is None:
            return _failure(
                "unknown_user",
                f"I don't know a configured user named {query.requested_user_name}.",
                requested_user_name=query.requested_user_name,
            )
        mapping = mappings.get(user_id)
        user = household_settings.user(user_id)
        display_name = user.display_name if user is not None else user_id
        if mapping is None:
            return _failure(
                "presence_mapping_unavailable",
                f"I don't have a configured presence mapping for {display_name}.",
                user_id=user_id,
                display_name=display_name,
            )
        state = _read_mapping(mapping, bridge)
        speech = _person_speech(display_name, state, query.desired_state)
        return {
            "ok": True,
            "speech": speech,
            "query_kind": "person",
            "desired_state": query.desired_state,
            "person": {"user_id": user_id, "display_name": display_name, "state": state},
        }

    records: list[dict[str, str]] = []
    for user_id, mapping in sorted(mappings.items()):
        user = household_settings.user(user_id)
        if user is None:
            continue
        records.append(
            {
                "user_id": user_id,
                "display_name": user.display_name,
                "state": _read_mapping(mapping, bridge),
            }
        )
    if not records:
        return _failure("presence_mapping_unavailable", "I don't have any configured presence mappings.")
    matching = [item for item in records if item["state"] == query.desired_state]
    unknown = [item for item in records if item["state"] == "unknown"]
    return {
        "ok": True,
        "speech": _list_speech(query.desired_state, matching, unknown),
        "query_kind": "list",
        "desired_state": query.desired_state,
        "people": records,
    }


def _presence_mappings(
    settings: HomeAssistantRuntimeSettings,
) -> dict[str, HomeAssistantObjectMapping]:
    return {
        mapping.oracle_id: mapping
        for mapping in settings.mappings_for_kind("person_presence")
        if isinstance(mapping, HomeAssistantObjectMapping)
    }


def _read_mapping(
    mapping: HomeAssistantObjectMapping,
    bridge: HomeAssistantBridge,
) -> PresenceState:
    payload = bridge.fetch_entity_state(mapping.entity_id)
    return normalize_person_presence(None if payload is None else str(payload.get("state") or ""))


def _person_speech(display_name: str, state: PresenceState, desired_state: str) -> str:
    if state == "unknown":
        return f"I can't determine whether {display_name} is {desired_state} right now."
    if desired_state == "home":
        return f"{display_name} is {'home' if state == 'home' else 'away'}."
    return f"{display_name} is {'away' if state == 'away' else 'home'}."


def _list_speech(
    desired_state: str,
    matching: list[dict[str, str]],
    unknown: list[dict[str, str]],
) -> str:
    names = [item["display_name"] for item in matching]
    if names:
        speech = f"{_join_names(names)} {'is' if len(names) == 1 else 'are'} {desired_state}."
    else:
        speech = f"No configured household member is known to be {desired_state}."
    if unknown:
        unknown_names = [item["display_name"] for item in unknown]
        speech += f" I can't determine the current presence of {_join_names(unknown_names)}."
    return speech


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _failure(code: str, speech: str, **details: object) -> dict[str, object]:
    return {"ok": False, "error": code, "speech": speech, **details}
