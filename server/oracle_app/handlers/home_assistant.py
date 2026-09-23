from __future__ import annotations

import re
from typing import Any

from oracle_app import state
from oracle_app.text_normalization import normalize_text
from oracle_app.configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.home_assistant_actions import (
    ResolvedHomeSemanticRequest,
    execute_resolved_home_semantic_request,
    resolve_home_semantic_request,
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
) -> DispatchPlan:
    source = dispatch.payload.get("source")
    session_id = dispatch.payload.get("session_id")
    pending = state.load_pending_home_request(source, session_id)
    if pending is not None:
        if str(pending.get("injection_kind") or "") == "semantic_target":
            state.clear_pending_home_request(source, session_id)
            base_text = str(pending.get("base_text") or "").strip()
            target_reply = str(dispatch.payload.get("text") or "").strip()
            dispatch.payload["text"] = f"{base_text} {target_reply}".strip()
            pending = None
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

    if home_assistant_settings is None or not home_assistant_settings.enabled:
        dispatch.status = "failed"
        dispatch.result = build_failure_result(
            failure_class="configuration_failure",
            owning_component="brain.home_assistant",
            error="home_assistant_disabled",
            detail="Home Assistant is disabled in the applied configuration.",
        )
        return dispatch
    resolved = resolve_home_semantic_request(
        str(dispatch.payload.get("text") or ""),
        home_assistant_settings=home_assistant_settings,
        household_settings=household_settings,
    )
    if not isinstance(resolved, ResolvedHomeSemanticRequest):
        if resolved.get("error") == "home_target_ambiguous":
            stored = state.store_pending_home_request(
                source,
                session_id,
                {
                    "prompt": str(resolved.get("prompt") or "Which target did you mean?"),
                    "base_text": str(dispatch.payload.get("text") or "").strip(),
                    "injection_kind": "semantic_target",
                },
            )
            if stored:
                dispatch.status = "pending_clarification"
                dispatch.result = dict(resolved)
                return dispatch
            dispatch.status = "failed"
            dispatch.result = {
                "error": "pending_state_requires_context",
                "detail": "Target clarification requires source and session context.",
            }
            return dispatch
        dispatch.status = "failed"
        dispatch.result = dict(resolved)
        return dispatch
    result = execute_resolved_home_semantic_request(
        resolved,
        home_assistant_settings=home_assistant_settings,
        interface="voice",
        confirmed=skip_confirmation,
    )
    if result.get("status") == "pending_confirmation":
        return store_pending_confirmation(
            dispatch,
            prompt=str(result["prompt"]),
            reason=str(result["reason"]),
        )
    if result.get("status") not in {"accepted", "verified"}:
        dispatch.status = "failed"
        dispatch.result = dict(result)
        return dispatch
    dispatch.status = "executed"
    dispatch.result = dict(result)
    dispatch.result["room_context"] = dict(room_context) if isinstance(room_context, dict) else {}
    return dispatch


class HomeAssistantHandler:
    target = "home_assistant"

    def __init__(
        self,
        household_settings: HouseholdRuntimeSettings | None = None,
        home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
    ) -> None:
        self.household_settings = household_settings
        self.home_assistant_settings = home_assistant_settings

    def handle(self, dispatch: DispatchPlan, registry: object) -> DispatchPlan:
        return execute_home_assistant(
            dispatch,
            household_settings=self.household_settings,
            home_assistant_settings=self.home_assistant_settings,
        )

    def handle_confirmed(self, dispatch: DispatchPlan) -> DispatchPlan:
        return execute_home_assistant(
            dispatch,
            skip_confirmation=True,
            household_settings=self.household_settings,
            home_assistant_settings=self.home_assistant_settings,
        )
