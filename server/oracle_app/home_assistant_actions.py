from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .configuration.domain_models import HomeAssistantObjectMapping
from .configuration.home_assistant_action_semantics import (
    CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS,
    DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS,
    collapse_equivalent_home_mappings,
)
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .provider_bridges.home_assistant import HomeAssistantBridge, HomeAssistantBridgeServiceError
from .capabilities.semantic import (
    CapabilityOutcome,
    CapabilityResult,
    ConfirmationClass,
    SEMANTIC_CAPABILITY_REGISTRY,
)
from .text_normalization import normalize_text


@dataclass(frozen=True)
class HomeSemanticRequest:
    """Provider-neutral public request resolved before the HA mapping edge."""

    capability_id: str
    target_id: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ResolvedHomeSemanticRequest:
    request: HomeSemanticRequest
    mapping_id: str
    operation: str


def resolve_home_semantic_request(
    command_text: str,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> ResolvedHomeSemanticRequest | dict[str, object]:
    """Resolve bounded Oracle language; never return provider identifiers."""
    if home_assistant_settings is None or not home_assistant_settings.enabled:
        return {"error": "home_assistant_disabled", "detail": "Home Assistant is disabled."}
    text = normalize_text(command_text)
    if text in home_assistant_settings.callable_alias_collisions:
        return {
            "error": "home_callable_alias_ambiguous",
            "prompt": "That phrase names both an Oracle routine and a configured provider action. Which one did you mean?",
        }
    operation = ""
    desired: object = ""
    capability_hint = ""
    temperature_match = re.search(r"\b(?:set|change)\b.*?\b(?:to|at)\s+(-?\d+(?:\.\d+)?)\s*(?:degrees?)?\b", text)
    if temperature_match and any(word in text for word in ("temperature", "thermostat", "air conditioner", " ac ")):
        operation = "set_temperature"
        desired = float(temperature_match.group(1))
        capability_hint = "home.environment.setpoint"
    elif re.search(r"\bturn\s+on\b|\bswitch\s+on\b|\bturn\b.{1,80}\bon\b", text):
        operation, desired = "turn_on", "on"
    elif re.search(r"\bturn\s+off\b|\bswitch\s+off\b|\bturn\b.{1,80}\boff\b", text):
        operation, desired = "turn_off", "off"
    elif re.search(r"\b(?:run|activate|start)\b", text):
        operation, desired = "invoke", ""
        capability_hint = "home.provider_action.invoke"
    else:
        access = {
            "unlock": ("unlock", "unlocked"), "lock": ("lock", "locked"),
            "open": ("open", "open"), "close": ("close", "closed"),
            "disarm": ("disarm", "disarmed"), "arm": ("arm", "armed"),
        }
        for word, pair in access.items():
            if re.search(rf"\b{word}\b", text):
                operation, desired = pair
                capability_hint = "home.access.set"
                break
    if not operation:
        return {"error": "home_action_unsupported", "detail": "That is not a configured finite home action."}

    scored_candidates: list[tuple[int, str, HomeAssistantObjectMapping]] = []
    for mapping_id, mapping in sorted(home_assistant_settings.mappings.items()):
        if not isinstance(mapping, HomeAssistantObjectMapping) or mapping.kind != "action":
            continue
        mapped_operation = _mapping_operation(mapping)
        if operation == "set_temperature":
            if mapped_operation not in CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS:
                continue
        elif mapped_operation != operation:
            continue
        if mapped_operation == "invoke":
            terms = {normalize_text(item) for item in mapping.aliases}
        else:
            terms = {
                normalize_text(mapping.oracle_id.replace("_", " ")),
                normalize_text(mapping_id.replace("_", " ").removesuffix(f" {mapped_operation}")),
                *(normalize_text(item) for item in mapping.aliases),
            }
        room = None if household_settings is None else household_settings.room(mapping.oracle_id)
        if room is not None:
            terms.update(normalize_text(item) for item in (room.id, room.display_name, *room.aliases))
        matched_terms = [
            term for term in terms
            if term and (
                (mapped_operation == "invoke" and term == text)
                or (
                    mapped_operation != "invoke"
                    and re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)
                )
            )
        ]
        if matched_terms:
            scored_candidates.append((max(map(len, matched_terms)), mapping_id, mapping))
    best_score = max((item[0] for item in scored_candidates), default=0)
    candidates = [(mapping_id, mapping) for score, mapping_id, mapping in scored_candidates if score == best_score]
    candidates = collapse_equivalent_home_mappings(candidates)
    if not candidates:
        return {"error": "home_action_unconfigured", "detail": "No configured target matches that action."}
    if len(candidates) != 1:
        labels = sorted({_canonical_action_label(mapping) for _, mapping in candidates})
        return {
            "error": "home_target_ambiguous",
            "prompt": f"I found multiple configured targets: {', '.join(labels)}. Which one did you mean?",
        }
    mapping_id, mapping = candidates[0]
    domain = mapping.entity_id.split(".", 1)[0]
    capability_id = capability_hint or ("home.lights.set" if domain == "light" else "home.power.set")
    arguments: dict[str, object] = {"target_id": mapping.oracle_id}
    if capability_id == "home.environment.setpoint":
        policy = _climate_policy(mapping, home_assistant_settings)
        if policy is None:
            return {"error": "home_climate_policy_unconfigured", "detail": "That climate target has no configured normal bounds."}
        unit = policy[2]
        arguments.update(setpoint=desired, temperature_unit=unit)
    elif capability_id == "home.provider_action.invoke":
        arguments["action_id"] = mapping.oracle_id
    else:
        arguments["state"] = desired
    SEMANTIC_CAPABILITY_REGISTRY.require(capability_id).validate_arguments(arguments)
    return ResolvedHomeSemanticRequest(
        request=HomeSemanticRequest(capability_id, mapping.oracle_id, arguments),
        mapping_id=mapping_id,
        operation=operation,
    )


def execute_resolved_home_semantic_request(
    resolved: ResolvedHomeSemanticRequest,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings,
    interface: str,
    confirmed: bool = False,
) -> dict[str, object]:
    mapping = _canonical_action_mapping(home_assistant_settings, resolved.mapping_id)
    if mapping is None and resolved.request.capability_id == "home.environment.setpoint":
        candidate = home_assistant_settings.mapping(resolved.mapping_id)
        if (
            isinstance(candidate, HomeAssistantObjectMapping)
            and candidate.kind == "entity"
            and candidate.allowed_operations == ["read"]
            and str(candidate.entity_id).startswith("climate.")
        ):
            mapping = candidate
    if mapping is None:
        return {"ok": False, "status": "unsupported", "error": "home_action_unconfigured"}
    _direction, confirmation = SEMANTIC_CAPABILITY_REGISTRY.assess(
        resolved.request.capability_id,
        resolved.request.arguments,
        interface=interface,
    )
    setpoint = resolved.request.arguments.get("setpoint")
    if setpoint is not None:
        policy = _climate_policy(mapping, home_assistant_settings)
        if policy is None:
            return {"ok": False, "status": "unsupported", "error": "home_climate_policy_unconfigured"}
        normal_min, normal_max, _unit = policy
        if not normal_min <= float(setpoint) <= normal_max:
            confirmation = ConfirmationClass.CONSEQUENTIAL
    if confirmation is ConfirmationClass.CONSEQUENTIAL and not confirmed:
        return {
            "ok": interface != "runbook",
            "status": "pending_confirmation",
            "reason": "consequential_home_action",
            "prompt": "This home action is consequential. Say 'confirm' to proceed or 'cancel' to stop.",
        }
    bridge = _canonical_bridge(home_assistant_settings)
    if bridge is None:
        return {"ok": False, "status": "unavailable", "error": "home_assistant_unavailable"}
    expected_state = str(resolved.request.arguments.get("state") or "")
    try:
        if resolved.request.capability_id in {"home.lights.set", "home.power.set"}:
            bridge.set_power(entity_id=mapping.entity_id, enabled=expected_state == "on")
        elif resolved.request.capability_id == "home.access.set":
            bridge.set_access(entity_id=mapping.entity_id, state=expected_state)
        elif resolved.request.capability_id == "home.environment.setpoint":
            current = bridge.fetch_entity_state(mapping.entity_id)
            attributes = (current or {}).get("attributes") or {}
            provider_min = attributes.get("min_temp") if isinstance(attributes, dict) else None
            provider_max = attributes.get("max_temp") if isinstance(attributes, dict) else None
            provider_unit = str(attributes.get("temperature_unit") or "").strip() if isinstance(attributes, dict) else ""
            configured_unit = str(resolved.request.arguments["temperature_unit"])
            unit_matches = not provider_unit or (
                configured_unit == "fahrenheit" and provider_unit in {"F", "°F", "fahrenheit"}
            ) or (
                configured_unit == "celsius" and provider_unit in {"C", "°C", "celsius"}
            )
            if not unit_matches or provider_min is None or provider_max is None or not float(provider_min) <= float(setpoint) <= float(provider_max):
                return {"ok": False, "status": "unsupported", "error": "home_climate_provider_bound"}
            bridge.set_climate_temperature(entity_id=mapping.entity_id, temperature=float(setpoint))
        else:
            bridge.invoke_configured_unit(entity_id=mapping.entity_id)
    except HomeAssistantBridgeServiceError:
        return {"ok": False, "status": "rejected", "error": "home_assistant_request_failed"}

    if resolved.request.capability_id == "home.provider_action.invoke":
        result = CapabilityResult(resolved.request.capability_id, CapabilityOutcome.ACCEPTED, "home_action_accepted")
        message = f"Home Assistant accepted {_canonical_action_label(mapping)}."
        return {
            "ok": True,
            "status": CapabilityOutcome.ACCEPTED.value,
            "result": result.sanitized_audit(),
            "message": message,
            "response": {"speech": {"plain": {"speech": message}}},
        }
    verified = _verify_semantic_result(bridge, mapping, resolved)
    outcome = CapabilityOutcome.VERIFIED if verified else CapabilityOutcome.UNKNOWN
    result = CapabilityResult(resolved.request.capability_id, outcome, "home_action_verified" if verified else "home_action_unverified")
    label = _canonical_action_label(mapping)
    message = f"{label} updated." if verified else f"Home Assistant accepted the {label} action, but Oracle could not verify the result."
    return {
        "ok": verified,
        "status": outcome.value,
        "result": result.sanitized_audit(),
        "message": message,
        "response": {"speech": {"plain": {"speech": message}}},
        **({} if verified else {"error": "home_assistant_state_verification_failed"}),
    }


def execute_home_semantic_capability(
    capability_id: str,
    arguments: dict[str, object],
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None,
    confirmed: bool,
) -> dict[str, object]:
    """Execute one already-resolved Oracle capability without text inference."""
    if home_assistant_settings is None or not home_assistant_settings.enabled:
        return {"ok": False, "status": "unavailable", "error": "home_assistant_disabled"}
    SEMANTIC_CAPABILITY_REGISTRY.require(capability_id).validate_arguments(arguments)
    target_id = str(arguments.get("target_id") or "")
    operation = ""
    if capability_id in {"home.lights.set", "home.power.set"}:
        operation = "turn_on" if arguments.get("state") == "on" else "turn_off"
    elif capability_id == "home.access.set":
        operation = {
            "armed": "arm", "closed": "close", "disarmed": "disarm",
            "locked": "lock", "open": "open", "unlocked": "unlock",
        }[str(arguments["state"])]
    elif capability_id == "home.provider_action.invoke":
        operation = "invoke"
    elif capability_id == "home.environment.setpoint":
        operation = "set_temperature"
    candidates: list[tuple[str, HomeAssistantObjectMapping]] = []
    for mapping_id, mapping in sorted(home_assistant_settings.mappings.items()):
        if not isinstance(mapping, HomeAssistantObjectMapping) or mapping.oracle_id != target_id:
            continue
        if capability_id == "home.environment.setpoint":
            if mapping.kind == "entity" and mapping.allowed_operations == ["read"] and str(mapping.entity_id).startswith("climate."):
                candidates.append((mapping_id, mapping))
        elif mapping.kind == "action" and mapping.allowed_operations == [operation]:
            candidates.append((mapping_id, mapping))
    candidates = collapse_equivalent_home_mappings(candidates)
    if len(candidates) != 1:
        return {"ok": False, "status": "unsupported", "error": "home_action_mapping_ambiguous"}
    mapping_id, _mapping = candidates[0]
    return execute_resolved_home_semantic_request(
        ResolvedHomeSemanticRequest(
            HomeSemanticRequest(capability_id, target_id, arguments),
            mapping_id,
            operation,
        ),
        home_assistant_settings=home_assistant_settings,
        interface="runbook",
        confirmed=confirmed,
    )


def _climate_policy(mapping: HomeAssistantObjectMapping, settings: HomeAssistantRuntimeSettings) -> tuple[float, float, str] | None:
    candidates = (mapping, *(item for item in settings.mappings.values() if isinstance(item, HomeAssistantObjectMapping) and item.entity_id == mapping.entity_id))
    for item in candidates:
        if item.normal_temperature_min is not None and item.normal_temperature_max is not None and item.temperature_unit:
            return item.normal_temperature_min, item.normal_temperature_max, item.temperature_unit
    return None


def _verify_semantic_result(bridge: HomeAssistantBridge, mapping: HomeAssistantObjectMapping, resolved: ResolvedHomeSemanticRequest) -> bool:
    expected = resolved.request.arguments.get("state")
    if expected is not None:
        payload = bridge.wait_for_entity_state(mapping.entity_id, str(expected), timeout_seconds=30.0 if resolved.request.capability_id == "home.access.set" else 3.0)
        return str((payload or {}).get("state") or "").casefold() == str(expected).casefold()
    payload = bridge.fetch_entity_state(mapping.entity_id)
    attributes = (payload or {}).get("attributes") or {}
    try:
        return abs(float(attributes.get("temperature")) - float(resolved.request.arguments["setpoint"])) < 0.01
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def fetch_home_assistant_entity_state(
    base_url: str,
    token: str,
    entity_id: str,
) -> dict[str, Any] | None:
    return HomeAssistantBridge(base_url=base_url, token=token).fetch_entity_state(entity_id)


def execute_home_assistant_ui_action(
    action_id: str,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
    confirmed: bool = False,
    interface: str = "ui",
) -> dict[str, object] | None:
    action_id = str(action_id or "").strip()
    mapping = _canonical_action_mapping(home_assistant_settings, action_id)
    if mapping is None:
        return None
    operation = _mapping_operation(mapping)
    if operation not in DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS | CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS:
        return None
    domain = mapping.entity_id.split(".", 1)[0]
    if operation in CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS:
        bridge = _canonical_bridge(home_assistant_settings)
        if bridge is None:
            return {"ok": False, "status": "unavailable", "error": "home_assistant_unavailable"}
        state = bridge.fetch_entity_state(mapping.entity_id) or {}
        attributes = state.get("attributes") or {}
        try:
            desired: object = round(float(attributes["temperature"])) + (-1 if operation == "cooler" else 1)
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "status": "unknown", "error": "home_assistant_target_unavailable"}
        capability_id = "home.environment.setpoint"
        policy = _climate_policy(mapping, home_assistant_settings)
        if policy is None:
            return {"ok": False, "status": "unsupported", "error": "home_climate_policy_unconfigured"}
        arguments = {"target_id": mapping.oracle_id, "setpoint": desired, "temperature_unit": policy[2]}
    elif operation == "invoke":
        desired = ""
        capability_id = "home.provider_action.invoke"
        arguments = {"target_id": mapping.oracle_id, "action_id": mapping.oracle_id}
    else:
        states = {
            "turn_on": "on", "turn_off": "off", "lock": "locked", "unlock": "unlocked",
            "open": "open", "close": "closed", "arm": "armed", "disarm": "disarmed",
        }
        desired = states[operation]
        capability_id = "home.access.set" if operation in {"arm", "close", "disarm", "lock", "open", "unlock"} else ("home.lights.set" if domain == "light" else "home.power.set")
        arguments = {"target_id": mapping.oracle_id, "state": desired}
    result = execute_resolved_home_semantic_request(
        ResolvedHomeSemanticRequest(HomeSemanticRequest(capability_id, mapping.oracle_id, arguments), action_id, operation),
        home_assistant_settings=home_assistant_settings,
        interface=interface,
        confirmed=confirmed,
    )
    semantic_result = result.get("result")
    if isinstance(semantic_result, dict):
        result["semantic_result"] = semantic_result
        result["result"] = {
            "status": str(result.get("status") or ""),
            "message": str(result.get("message") or "Action complete."),
        }
    elif result.get("status") == "pending_confirmation":
        result["result"] = {
            "status": "pending_confirmation",
            "message": str(result.get("prompt") or "Confirmation required."),
        }
    result.setdefault("action_id", action_id)
    result.setdefault("refresh", {"refresh_pages": ["home", "house"]})
    return result


def _mapping_operation(mapping: HomeAssistantObjectMapping) -> str:
    return mapping.allowed_operations[0] if len(mapping.allowed_operations) == 1 else ""


def _canonical_action_mapping(
    settings: HomeAssistantRuntimeSettings | None,
    action_id: str,
) -> HomeAssistantObjectMapping | None:
    if settings is None or not settings.enabled:
        return None
    mapping = settings.mapping(action_id)
    if not isinstance(mapping, HomeAssistantObjectMapping) or mapping.kind != "action":
        return None
    return mapping


def _canonical_bridge(
    settings: HomeAssistantRuntimeSettings | None,
) -> HomeAssistantBridge | None:
    if settings is None or not settings.enabled or not settings.base_url or not settings.credential:
        return None
    return HomeAssistantBridge(
        base_url=settings.base_url,
        token=settings.credential,
        timeout_seconds=settings.timeout_seconds,
    )


def _canonical_action_label(mapping: HomeAssistantObjectMapping) -> str:
    words = str(mapping.oracle_id or "").replace("_", " ").split()
    return " ".join(
        word.upper() if word.lower() in {"ac", "led"} else word.capitalize()
        for word in words
    )
