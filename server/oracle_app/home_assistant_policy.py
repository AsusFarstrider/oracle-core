from __future__ import annotations

import re
import time
from typing import Any

from oracle_app.text_normalization import normalize_text


_STATE_VERIFICATION_ATTEMPTS = 8
_STATE_VERIFICATION_POLL_SECONDS = 0.5


def detect_failed_success_targets(bridge, payload: dict[str, Any], *, command_text: str) -> dict[str, Any] | None:
    from oracle_app.provider_bridges.home_assistant import extract_success_entity_ids

    unavailable: list[dict[str, str]] = []
    verification_failed: list[dict[str, Any]] = []
    for entity_id in extract_success_entity_ids(payload):
        expected_outcome = expected_target_outcome(command_text, entity_id)
        state = fetch_entity_state_with_retry(bridge, entity_id, expected_outcome=expected_outcome)
        if state is None:
            continue
        current_state = str(state.get("state") or "").strip().lower()
        friendly_name = str(state.get("attributes", {}).get("friendly_name") or entity_id).strip()
        if current_state == "unavailable":
            unavailable.append({"entity_id": entity_id, "name": friendly_name})
        elif state_verification_failed(state, current_state=current_state, expected_outcome=expected_outcome):
            verification_failed.append(
                {"entity_id": entity_id, "name": friendly_name, "state": current_state, **serialize_expected_outcome(expected_outcome)}
            )
    if unavailable:
        return {
            "error": "home_assistant_target_unavailable",
            "detail": "Requested Home Assistant target is unavailable.",
            "unavailable_targets": unavailable,
        }
    if verification_failed:
        return {
            "error": "home_assistant_state_verification_failed",
            "detail": "Requested Home Assistant target did not reach the expected state.",
            "verification_failed_targets": verification_failed,
        }
    return None


def fetch_entity_state_with_retry(bridge, entity_id: str, *, expected_outcome: dict[str, Any] | None) -> dict[str, Any] | None:
    latest_state: dict[str, Any] | None = None
    for attempt in range(_STATE_VERIFICATION_ATTEMPTS):
        latest_state = bridge.fetch_entity_state(entity_id)
        if latest_state is None:
            return None
        current_state = str(latest_state.get("state") or "").strip().lower()
        if current_state == "unavailable" or not state_verification_failed(
            latest_state, current_state=current_state, expected_outcome=expected_outcome
        ):
            return latest_state
        if attempt < _STATE_VERIFICATION_ATTEMPTS - 1:
            time.sleep(_STATE_VERIFICATION_POLL_SECONDS)
    return latest_state


def expected_target_outcome(command_text: str, entity_id: str) -> dict[str, Any] | None:
    normalized = normalize_text(command_text)
    if "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0].strip().lower()
    state_prefixes = {
        "turn on ": ({"light", "switch", "fan", "input_boolean"}, "on"),
        "turn off ": ({"light", "switch", "fan", "input_boolean"}, "off"),
        "unlock ": ({"lock"}, "unlocked"),
        "lock ": ({"lock"}, "locked"),
        "open ": ({"cover"}, "open"),
        "close ": ({"cover"}, "closed"),
    }
    for prefix, (domains, state) in state_prefixes.items():
        if normalized.startswith(prefix):
            return {"kind": "state", "state": state} if domain in domains else None
    if domain == "climate":
        match = re.search(r"\bto\s+(\d{2})\b", normalized)
        if match and ("temperature" in normalized or "thermostat" in normalized):
            return {"kind": "attribute_numeric", "attribute": "temperature", "value": float(match.group(1)), "description": f"{match.group(1)} degrees"}
    if domain == "light":
        match = re.search(r"\bto\s+(\d{1,3})\s+percent brightness\b", normalized)
        if match:
            percent = max(0, min(100, int(match.group(1))))
            return {"kind": "attribute_numeric", "attribute": "brightness", "value": float(round((percent / 100.0) * 255)), "tolerance": 2.0, "description": f"{percent} percent brightness"}
    return None


def state_verification_failed(state_payload: dict[str, Any], *, current_state: str, expected_outcome: dict[str, Any] | None) -> bool:
    if not expected_outcome:
        return False
    kind = str(expected_outcome.get("kind") or "").strip().lower()
    if kind == "state":
        expected_state = str(expected_outcome.get("state") or "").strip().lower()
        return bool(expected_state and current_state and current_state != expected_state)
    if kind == "attribute_numeric":
        attribute = str(expected_outcome.get("attribute") or "").strip()
        attributes = state_payload.get("attributes") or {}
        if not attribute:
            return False
        if not isinstance(attributes, dict):
            return True
        try:
            actual_numeric = float(attributes.get(attribute))
            expected_numeric = float(expected_outcome.get("value"))
        except (TypeError, ValueError):
            return True
        return abs(actual_numeric - expected_numeric) > float(expected_outcome.get("tolerance") or 0.0)
    return False


def serialize_expected_outcome(expected_outcome: dict[str, Any] | None) -> dict[str, Any]:
    if not expected_outcome:
        return {}
    kind = str(expected_outcome.get("kind") or "").strip().lower()
    if kind == "state":
        expected_state = str(expected_outcome.get("state") or "").strip().lower()
        return {"expected_state": expected_state} if expected_state else {}
    if kind == "attribute_numeric":
        serialized: dict[str, Any] = {}
        attribute = str(expected_outcome.get("attribute") or "").strip()
        description = str(expected_outcome.get("description") or "").strip()
        if attribute:
            serialized["expected_attribute"] = attribute
        if description:
            serialized["expected_description"] = description
        if expected_outcome.get("value") is not None:
            serialized["expected_value"] = expected_outcome["value"]
        return serialized
    return {}
