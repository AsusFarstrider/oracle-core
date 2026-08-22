from __future__ import annotations

import re
from typing import Any
from urllib import request

from oracle_app import state
from oracle_app.text_normalization import normalize_text
from oracle_app.configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.constants import SAFE_TEMPERATURE_MAX, SAFE_TEMPERATURE_MIN
from oracle_app.provider_bridges.home_assistant import (
    HomeAssistantBridge,
    HomeAssistantBridgeHttpError,
    HomeAssistantBridgeUnreachableError,
    extract_success_entity_ids,
)
from oracle_app.home_assistant_policy import (
    detect_failed_success_targets,
    expected_target_outcome,
    fetch_entity_state_with_retry,
    serialize_expected_outcome,
    state_verification_failed,
)
from oracle_app.room_context import canonical_pending_room_reply_name, canonical_room_name, inject_room_into_home_command
from oracle_app.runtime_contracts import build_failure_result
from oracle_app.schemas import DispatchPlan


_RETIRED_ROOM_NAME_PATTERNS = (
    re.compile(r"(?<![a-z0-9])man[\s\-']*cave(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])mancave(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])kid[\s\-']*s[\s\-']*room(?![a-z0-9])"),
    re.compile(r"(?<![a-z0-9])kids[\s\-']*room(?![a-z0-9])"),
)


def _contains_retired_room_name(command_text: str) -> bool:
    normalized = normalize_text(command_text)
    return any(pattern.search(normalized) for pattern in _RETIRED_ROOM_NAME_PATTERNS)


def check_confirmation_required(command_text: str) -> dict[str, Any] | None:
    normalized = normalize_text(command_text)

    if re.search(r"(?<![a-z])unlock(?![a-z])", normalized):
        return {
            "reason": "Unlock commands require confirmation",
            "prompt": "This will unlock a door. Say 'confirm' to proceed or 'cancel' to stop.",
        }

    if "temperature" in normalized or "thermostat" in normalized:
        match = re.search(r"\bto\s+(\d{2})\b", normalized)
        if match:
            target_temp = int(match.group(1))
            if target_temp < SAFE_TEMPERATURE_MIN or target_temp > SAFE_TEMPERATURE_MAX:
                return {
                    "reason": "Requested temperature is outside the safe range",
                    "prompt": (
                        f"This will set the temperature to {target_temp}, outside the "
                        f"{SAFE_TEMPERATURE_MIN}-{SAFE_TEMPERATURE_MAX} range. "
                        "Say 'confirm' to proceed or 'cancel' to stop."
                    ),
                    "target_temperature": target_temp,
                }

    return None


def store_pending_confirmation(dispatch: DispatchPlan, prompt: str, reason: str) -> DispatchPlan:
    stored = state.store_pending_confirmation(
        dispatch.payload.get("source"),
        dispatch.payload.get("session_id"),
        {
            "dispatch": {
                "target": dispatch.target,
                "hook": dispatch.hook,
                "payload": dict(dispatch.payload),
            },
            "prompt": prompt,
            "reason": reason,
        },
    )
    if not stored:
        dispatch.status = "failed"
        dispatch.result = {
            "error": "pending_state_requires_context",
            "detail": "Pending confirmation requires both source and session_id.",
        }
        return dispatch

    dispatch.status = "pending_confirmation"
    dispatch.result = {
        "reason": reason,
        "prompt": prompt,
    }
    return dispatch


def execute_home_assistant(
    dispatch: DispatchPlan,
    *,
    skip_confirmation: bool = False,
    household_settings: HouseholdRuntimeSettings | None = None,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> DispatchPlan:
    source = dispatch.payload.get("source")
    session_id = dispatch.payload.get("session_id")
    pending = state.load_pending_home_request(source, session_id)
    if pending is not None:
        room_name = canonical_pending_room_reply_name(
            dispatch.payload.get("text"),
            household_settings,
        )
        if room_name:
            state.clear_pending_home_request(source, session_id)
            injection_kind = str(pending.get("injection_kind") or "generic_in_the_room")
            base_text = str(pending.get("base_text") or "").strip()
            dispatch.payload["text"] = inject_room_into_home_command(base_text, room=room_name, injection_kind=injection_kind)
            dispatch.payload["room_context"] = {
                "room_required": True,
                "resolved_room": room_name,
                "resolution_source": "pending_clarification",
                "needs_clarification": False,
                "injection_kind": injection_kind,
                "base_text": base_text,
            }

    if _contains_retired_room_name(str(dispatch.payload.get("text") or "")):
        dispatch.status = "failed"
        dispatch.result = {
            "error": "retired_home_room_name",
            "detail": "That room name is no longer active in Oracle.",
        }
        return dispatch

    room_context = dispatch.payload.get("room_context") or {}
    if bool(room_context.get("room_required")) and not str(room_context.get("resolved_room") or "").strip():
        if bool(room_context.get("needs_clarification")):
            prompt = (
                "I don't know what room this device is in. Which room did you mean?"
                if not str(room_context.get("resolution_source") or "").strip()
                or str(room_context.get("resolution_source") or "").strip() == "unresolved"
                else "Which room did you mean?"
            )
            stored = state.store_pending_home_request(
                source,
                session_id,
                {
                    "prompt": prompt,
                    "base_text": str(room_context.get("base_text") or dispatch.payload.get("text") or "").strip(),
                    "injection_kind": str(room_context.get("injection_kind") or "generic_in_the_room"),
                    "resolved_room": None,
                },
            )
            if stored:
                dispatch.status = "pending_clarification"
                dispatch.result = {
                    "prompt": prompt,
                    "error": "home_room_clarification_required",
                }
                return dispatch
        dispatch.status = "failed"
        dispatch.result = {
            "error": "home_room_unresolved",
            "detail": "Room-sensitive home command cannot execute without a resolved room.",
        }
        return dispatch

    if not skip_confirmation:
        confirmation = check_confirmation_required(dispatch.payload["text"])
        if confirmation is not None:
            return store_pending_confirmation(
                dispatch,
                prompt=str(confirmation["prompt"]),
                reason=str(confirmation["reason"]),
            )

    if home_assistant_settings is None or not home_assistant_settings.enabled:
        dispatch.status = "failed"
        dispatch.result = build_failure_result(
            failure_class="configuration_failure",
            owning_component="brain.home_assistant",
            error="home_assistant_disabled",
            detail="Home Assistant is disabled in the applied configuration.",
        )
        return dispatch
    bridge = HomeAssistantBridge(
        base_url=home_assistant_settings.base_url or "",
        token=home_assistant_settings.credential or "",
        timeout_seconds=home_assistant_settings.timeout_seconds,
    )
    try:
        bridge_result = bridge.execute_command(
            str(dispatch.payload.get("text") or ""),
            source=str(source) if source is not None else None,
            session_id=str(session_id) if session_id is not None else None,
        )
    except HomeAssistantBridgeHttpError as exc:
        dispatch.status = "failed"
        dispatch.result = build_failure_result(
            failure_class="transport_failure",
            owning_component="brain.home_assistant",
            error="home_assistant_http_error",
            detail=exc.detail,
            status_code=exc.status_code,
        )
        return dispatch
    except HomeAssistantBridgeUnreachableError as exc:
        dispatch.status = "failed"
        dispatch.result = build_failure_result(
            failure_class="transport_failure",
            owning_component="brain.home_assistant",
            error="home_assistant_unreachable",
            detail=exc.detail,
        )
        return dispatch

    dispatch.status = "executed"
    verification_failure = detect_failed_success_targets(
        bridge,
        bridge_result.payload,
        command_text=str(dispatch.payload.get("text") or ""),
    )
    if verification_failure is not None:
        dispatch.status = "failed"
        dispatch.result = {
            **verification_failure,
            "room_context": dict(room_context) if isinstance(room_context, dict) else {},
        }
        return dispatch
    bridge.commit_conversation_id(
        bridge_result.returned_conversation_id,
        source=str(source) if source is not None else None,
        session_id=str(session_id) if session_id is not None else None,
    )
    dispatch.result = bridge_result.payload
    dispatch.result["room_context"] = dict(room_context) if isinstance(room_context, dict) else {}
    return dispatch


class HomeAssistantHandler:
    target = "home_assistant"

    def __init__(
        self,
        household_settings: HouseholdRuntimeSettings | None = None,
        home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
        canonical_authority: bool = False,
    ) -> None:
        self.household_settings = household_settings
        self.home_assistant_settings = home_assistant_settings
        self.canonical_authority = canonical_authority

    def handle(self, dispatch: DispatchPlan, registry: object) -> DispatchPlan:
        return execute_home_assistant(
            dispatch,
            household_settings=self.household_settings,
            home_assistant_settings=self.home_assistant_settings,
            canonical_authority=self.canonical_authority,
        )

    def handle_confirmed(self, dispatch: DispatchPlan) -> DispatchPlan:
        return execute_home_assistant(
            dispatch,
            skip_confirmation=True,
            household_settings=self.household_settings,
            home_assistant_settings=self.home_assistant_settings,
            canonical_authority=self.canonical_authority,
        )


def _detect_failed_success_targets(
    payload: dict[str, Any],
    *,
    command_text: str,
    base_url: str,
    token: str,
) -> dict[str, Any] | None:
    return detect_failed_success_targets(
        HomeAssistantBridge(base_url=base_url, token=token),
        payload,
        command_text=command_text,
    )


def _extract_success_entity_ids(payload: dict[str, Any]) -> list[str]:
    return extract_success_entity_ids(payload)


def _fetch_entity_state(base_url: str, token: str, entity_id: str) -> dict[str, Any] | None:
    return HomeAssistantBridge(base_url=base_url, token=token).fetch_entity_state(entity_id)


def _fetch_entity_state_with_retry(
    base_url: str,
    token: str,
    entity_id: str,
    *,
    expected_outcome: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return fetch_entity_state_with_retry(
        HomeAssistantBridge(base_url=base_url, token=token),
        entity_id,
        expected_outcome=expected_outcome,
    )


def _expected_target_outcome(command_text: str, entity_id: str) -> dict[str, Any] | None:
    return expected_target_outcome(command_text, entity_id)


def _state_verification_failed(
    state_payload: dict[str, Any],
    *,
    current_state: str,
    expected_outcome: dict[str, Any] | None,
) -> bool:
    return state_verification_failed(
        state_payload,
        current_state=current_state,
        expected_outcome=expected_outcome,
    )


def _serialize_expected_outcome(expected_outcome: dict[str, Any] | None) -> dict[str, Any]:
    return serialize_expected_outcome(expected_outcome)
