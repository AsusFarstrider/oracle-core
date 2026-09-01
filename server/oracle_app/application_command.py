from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .alerts import classify_alert_operation
from .audiobook_runtime.parsing import (
    parse_audiobook_intent,
    parse_bare_audiobook_sleep_timer_intent,
)
from .application_runtime import app, brain_application_composition
from .command_events import list_command_interim_events
from .configuration.generations import GenerationStoreError
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.playback_target_resolution import PlaybackTargetResolutionError
from .configuration.request_source_resolution import (
    EPHEMERAL_HTTP_SOURCE_ID,
    RequestSourceAuthenticationError,
    ResolvedRequestSource,
)
from .conversation import append_turn, set_dispatch_context
from .dispatch import build_dispatch_plan, execute_dispatch
from .handlers.registry import HandlerRegistry
from .memory.correlation import get_correlation_id
from .memory.sessions import safe_record_session, safe_update_session_status, utc_now_iso as memory_utc_now_iso
from .memory.transcripts import safe_enrich_transcripts_for_correlation
from .orchestration_routine_canonical import CanonicalRoutineExecution
from .replies import build_reply_text
from .room_context import apply_room_context_to_home_text
from .routing import choose_route, validate_fallback_reentry
from .runtime_contracts import (
    ContractValidationError,
    build_failure_result,
    build_command_contract_failure_response,
    validate_command_response_contract,
)
from .schemas import (
    CommandInterimEventsResponse,
    CommandRequest,
    CommandResponse,
    DispatchPlan,
    RouteRequest,
    RouteResponse,
)
from .session_state import (
    clear_active_context,
    clear_utility_context_for_topic_change,
    inspect_session,
    refresh_session,
    resolve_request_session,
    set_active_context,
    set_user_context,
    set_utility_context,
)
from . import state
from .text_normalization import normalize_text
from .ui_audio import ui_audio_search_impl
from .ui_context import handle_pending_ui_context as _handle_pending_ui_context
from .user_context import analyze_user_directive, get_user_entry, resolve_effective_user


logger = logging.getLogger("oracle-brain.application.command")


IGNORED_TRANSCRIPT_REASON = "Ignored empty transcript after wake-word cleanup"


def build_ignored_command_response(
    payload: CommandRequest,
    *,
    registry: HandlerRegistry,
) -> CommandResponse:
    route = RouteResponse(
        target="system",
        confidence=1.0,
        reason=IGNORED_TRANSCRIPT_REASON,
        normalized_text="",
    )
    dispatch = build_dispatch_plan(payload, route)
    dispatch = execute_dispatch(dispatch, registry=registry)
    return CommandResponse(
        route=route,
        dispatch=dispatch,
        reply_text="",
        session_id=payload.session_id,
        effective_session_id=payload.session_id,
    )


def _search_pending_ui_audio(payload):
    composition = brain_application_composition(app)
    return ui_audio_search_impl(
        payload,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
        household_settings=composition.runtime.household,
    )


_NON_ANCHORING_SYSTEM_ACTIONS = {
    "current_time",
    "current_date",
    "current_time_date",
    "calculation",
    "temporal",
    "repeat",
    "help",
    "courtesy",
    "unsupported_utility",
}

_NON_REPEATABLE_TARGETS = {"facts", "fallback_router", "home_assistant", "news"}
_NON_REPEATABLE_ACTIONS = {
    "ignore", "repeat", "refresh_cache", "routine_start", "commit_event",
    "play", "pause", "resume", "stop", "next", "previous", "restart",
    "set_volume", "volume_up", "volume_down",
}
_NON_REPEATABLE_ALERT_OPERATIONS = {
    "create", "adjust", "restart", "cancel", "cancel_all", "dismiss",
    "edit", "delete", "skip", "snooze", "acknowledge",
}
_SENSITIVE_REPLY_RE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~-]+|api[_ -]?key\s*[:=]|password\s*[:=]|secret\s*[:=])"
)


def record_repeat_eligible_output(
    *,
    source: str | None,
    session_id: str | None,
    route_target: str,
    dispatch: DispatchPlan,
    reply_text: str,
) -> bool:
    text = str(reply_text or "").strip()
    result = dict(dispatch.result or {})
    action = str(result.get("action") or dispatch.payload.get("action") or "").strip()
    if not text or route_target in _NON_REPEATABLE_TARGETS or action in _NON_REPEATABLE_ACTIONS:
        return False
    if _SENSITIVE_REPLY_RE.search(text):
        return False
    if action == "alerts":
        alert_result = result.get("alerts") or {}
        operation = str((alert_result or {}).get("operation") or "").strip()
        if operation in _NON_REPEATABLE_ALERT_OPERATIONS:
            return False
    return set_utility_context(
        source,
        session_id,
        kind="repeat_output",
        payload={
            "reply_text": text,
            "route_target": str(route_target or dispatch.target),
            "action": action,
            "status": str(dispatch.status or ""),
            "error": str(result.get("error") or ""),
        },
    )

def _maybe_update_active_context(*, route: RouteResponse, dispatch, result: dict[str, object]) -> None:
    session_id = dispatch.payload.get("session_id")
    source = dispatch.payload.get("source")
    if not source or not session_id:
        return

    status = str(dispatch.status or "")
    action = str(result.get("action")) if result.get("action") is not None else None
    route_target = str(route.target or dispatch.target or "")
    if route_target == "fallback_router" and dispatch.target != "fallback_router":
        route_target = str(dispatch.target or route_target)
    dispatch_hook = str(dispatch.hook or "")

    if status in {"pending_confirmation", "pending_clarification"}:
        room_context = result.get("room_context") or dispatch.payload.get("room_context") or {}
        set_active_context(
            source,
            session_id,
            route_target=route_target,
            dispatch_hook=dispatch_hook,
            action=action,
            anchor_strength="strong",
            context_text=route.normalized_text,
            active_room_ref=str(room_context.get("resolved_room") or "").strip() or None,
        )
        return

    if status != "executed":
        return

    clear_utility_context_for_topic_change(
        source,
        session_id,
        route_target=route_target,
    )

    if route_target == "facts":
        set_active_context(
            source,
            session_id,
            route_target=route_target,
            dispatch_hook=dispatch_hook,
            action=action,
            anchor_strength="weak",
            context_text=route.normalized_text,
        )
        return

    if route_target == "system" and action == "ignore":
        return
    if route_target == "system" and action == "cancel_pending":
        return
    if route_target == "system" and action == "switch_user":
        return
    if route_target == "system" and action in _NON_ANCHORING_SYSTEM_ACTIONS:
        return
    if route_target == "weather":
        return
    if route_target == "network":
        return

    set_active_context(
        source,
        session_id,
        route_target=route_target,
        dispatch_hook=dispatch_hook,
        action=action,
        anchor_strength="strong",
        context_text=route.normalized_text,
        active_room_ref=str((result.get("room_context") or dispatch.payload.get("room_context") or {}).get("resolved_room") or "").strip() or None,
    )


def _build_user_context_error_response(
    *,
    original_session_id: str | None,
    effective_session_id: str | None,
    normalized_text: str,
    error: str,
    detail: str,
) -> CommandResponse:
    route = RouteResponse(
        target="system",
        confidence=1.0,
        reason="User-context preprocessing failed",
        normalized_text=normalized_text,
    )
    dispatch = DispatchPlan(
        target="system",
        hook="system.user_context",
        payload={},
        status="failed",
        result={
            "action": "user_context",
            "error": error,
            "detail": detail,
        },
    )
    return CommandResponse(
        route=route,
        dispatch=dispatch,
        reply_text=detail,
        session_id=original_session_id,
        effective_session_id=effective_session_id,
    )


def _build_routine_voice_response(
    *,
    definition: dict[str, Any],
    payload: CommandRequest,
    effective_payload: CommandRequest,
    session_info: dict[str, Any],
    normalized_text: str,
    routine_execution: CanonicalRoutineExecution | None = None,
) -> CommandResponse:
    orchestration_id = str(definition.get("id") or "")
    display_name = str(definition.get("display_name") or orchestration_id or "Routine")
    route = RouteResponse(
        target="system",
        confidence=1.0,
        reason="Matched configured orchestration voice trigger",
        normalized_text=normalized_text,
    )
    _log_command_event("route_chosen", payload=effective_payload, route=route)
    try:
        if routine_execution is None:
            run = start_routine(
                orchestration_id,
                client_id=f"voice-routine-{effective_payload.source}",
                defer_audible_start=True,
            )
        else:
            run = routine_execution.start(
                orchestration_id,
                client_id=f"voice-routine-{effective_payload.source}",
                defer_audible_start=True,
            )
        deferred_session = extract_deferred_session(run)
        result: dict[str, Any] = {
            "action": "routine_start",
            "orchestration_id": orchestration_id,
            "run_id": run.get("run_id"),
            "run_status": run.get("status"),
            "summary": run.get("summary"),
        }
        if deferred_session is not None:
            result["deferred_audible_start"] = True
            result["deferred_session"] = deferred_session
        run_succeeded = run.get("status") in {"completed", "waiting"}
        dispatch = DispatchPlan(
            target="system",
            hook="orchestration.routine",
            payload={
                "action": "routine_start",
                "orchestration_id": orchestration_id,
                "source": effective_payload.source,
                "session_id": effective_payload.session_id,
            },
            status="executed" if run_succeeded else "failed",
            result=result,
        )
        reply_text = (
            f"Starting {display_name}."
            if run_succeeded
            else str(run.get("summary") or f"{display_name} could not start.")
        )
    except Exception as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else f"{display_name} could not start."
        if not isinstance(exc, HTTPException):
            logger.exception("orchestration_routine_voice_start_failed orchestration_id=%s", orchestration_id)
        dispatch = DispatchPlan(
            target="system",
            hook="orchestration.routine",
            payload={
                "action": "routine_start",
                "orchestration_id": orchestration_id,
                "source": effective_payload.source,
                "session_id": effective_payload.session_id,
            },
            status="failed",
            result={
                "action": "routine_start",
                "orchestration_id": orchestration_id,
                "error": "routine_start_failed",
                "detail": detail,
            },
        )
        reply_text = detail
    _log_command_event(
        "dispatch_planned",
        payload=effective_payload,
        route=route,
        dispatch_hook=dispatch.hook,
        dispatch_status=dispatch.status,
        action="routine_start",
    )
    _log_command_event(
        "dispatch_executed",
        payload=effective_payload,
        route=route,
        dispatch_hook=dispatch.hook,
        dispatch_status=dispatch.status,
        action="routine_start",
        reply_text=reply_text,
    )
    _log_command_event(
        "reply_built",
        payload=effective_payload,
        route=route,
        dispatch_hook=dispatch.hook,
        dispatch_status=dispatch.status,
        action="routine_start",
        reply_text=reply_text,
    )
    return CommandResponse(
        route=route,
        dispatch=dispatch,
        reply_text=reply_text,
        session_id=payload.session_id,
        effective_session_id=str(session_info["effective_session_id"]),
    )


def _should_refresh_session(*, route: RouteResponse, dispatch, result: dict[str, object]) -> bool:
    route_target = str(route.target or dispatch.target or "")
    if route_target == "fallback_router" and dispatch.target != "fallback_router":
        route_target = str(dispatch.target or route_target)
    status = str(dispatch.status or "")
    action = str(result.get("action") or "")

    if status in {"executed", "pending_confirmation", "pending_clarification"}:
        return action != "ignore"

    if status != "failed":
        return False

    if route_target in {"facts", "fallback_router"}:
        return False

    if route_target in {"music", "audiobook", "home_assistant"}:
        return True

    if route_target == "system" and action in {
        "alerts",
        "calculation",
        "cancel_pending",
        "confirm_pending",
        "current_date",
        "current_time",
        "current_time_date",
    }:
        return True

    if route_target == "weather" and action in {
        "current_weather",
        "remote_current_weather",
        "weather_forecast",
        "remote_weather_forecast",
        "weather_history",
    }:
        return True
    if route_target == "network" and action == "network_summary":
        return True

    return False


def _log_command_event(
    event: str,
    *,
    payload: CommandRequest,
    route: RouteResponse | None = None,
    dispatch_hook: str | None = None,
    dispatch_status: str | None = None,
    action: str | None = None,
    reply_text: str | None = None,
    failure_class: str | None = None,
    owning_component: str | None = None,
    room_context: dict[str, object] | None = None,
) -> None:
    context = room_context or {}
    resolved_room = str(context.get("resolved_room") or "-")
    resolution_source = str(context.get("resolution_source") or "-")
    logger.info(
        "%s source=%s session_id=%s route_target=%s dispatch_hook=%s status=%s action=%s failure_class=%s owning_component=%s resolved_room=%s resolution_source=%s text_chars=%d reply_chars=%d",
        event,
        payload.source or "-",
        payload.session_id or "-",
        route.target if route is not None else "-",
        dispatch_hook or "-",
        dispatch_status or "-",
        action or "-",
        failure_class or "-",
        owning_component or "-",
        resolved_room,
        resolution_source,
        len(payload.text),
        len(reply_text or ""),
    )


def _resolve_router_user_override(
    *,
    proposed_domain: str,
    proposed_user_id: str,
    dispatch_payload: dict[str, object],
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    candidate = str(proposed_user_id or "").strip().lower()
    if proposed_domain != "audiobook" or not candidate:
        return None
    if str(dispatch_payload.get("requested_user_name") or "").strip():
        return None
    entry = get_user_entry(
        candidate,
        household_settings=household_settings,
    )
    if entry is None:
        return None
    original_text = normalize_text(str(dispatch_payload.get("prompt") or ""))
    explicit_terms = (
        str(entry.get("id") or ""),
        str(entry.get("display_name") or ""),
        *(str(value) for value in (entry.get("aliases") or [])),
    )
    if not any(
        term.strip()
        and re.search(rf"(?<![a-z0-9]){re.escape(normalize_text(term))}(?![a-z0-9])", original_text)
        for term in explicit_terms
    ):
        return None
    return candidate


def _build_fallback_router_next_route(route: RouteResponse, dispatch: DispatchPlan) -> RouteResponse:
    result = dispatch.result or {}
    proposed_domain = str(result.get("proposed_domain") or "").strip()
    proposed_text = str(result.get("normalized_text") or "").strip() or route.normalized_text
    return RouteResponse(
        target=proposed_domain,  # type: ignore[arg-type]
        confidence=route.confidence,
        reason=f"Fallback router proposed {proposed_domain}",
        normalized_text=proposed_text,
    )


def _apply_resolved_playback_target(
    payload: CommandRequest,
    *,
    route_target: str,
    request_source: ResolvedRequestSource | None,
) -> tuple[CommandRequest, str | None, str | None]:
    if route_target not in {"music", "audiobook"} or request_source is None:
        return payload, None, None
    composition = brain_application_composition(app)
    try:
        resolved = composition.playback_target_resolver.resolve(
            explicit_source_id=payload.playback_target_source_id,
            request_source=request_source,
        )
    except PlaybackTargetResolutionError as exc:
        return payload, None, exc.code
    return (
        payload.model_copy(
            update={"playback_target_source_id": resolved.source_id}
        ),
        resolved.resolution,
        None,
    )


def _apply_canonical_alert_target(
    payload: CommandRequest,
    *,
    route_target: str,
    request_source: ResolvedRequestSource | None,
) -> tuple[CommandRequest, str | None]:
    if request_source is None:
        return payload, None
    if route_target == "audiobook":
        intent = parse_audiobook_intent(payload.text)
        if intent is None:
            intent = parse_bare_audiobook_sleep_timer_intent(payload.text)
        creates_alert = bool(
            intent is not None
            and (
                intent.intent == "sleep_timer"
                or intent.sleep_timer_seconds is not None
            )
        )
        if not creates_alert:
            return payload, None
        if not request_source.stable:
            return payload, "ephemeral_alert_creation_forbidden"
        target = str(payload.playback_target_source_id or "").strip()
        target_satellite = brain_application_composition(app).runtime.satellites.satellite_for_source(
            target
        )
        if target_satellite is None or not target_satellite.alert_capable:
            return payload, "invalid_alert_delivery_target"
        return payload.model_copy(update={"alert_delivery_target_source_id": target}), None
    if route_target != "system":
        return payload, None
    operation = classify_alert_operation(payload.text)
    if operation is None:
        return payload, None
    composition = brain_application_composition(app)
    explicit = str(payload.alert_delivery_target_source_id or "").strip() or None
    request_satellite = composition.runtime.satellites.satellite_for_source(
        request_source.request_source_id
    )
    if operation == "create" and not request_source.stable:
        return payload, "ephemeral_alert_creation_forbidden"
    target = explicit
    if target is None and request_satellite is not None and request_source.stable:
        target = request_source.request_source_id
    if target is None:
        return payload, "alert_delivery_target_required"
    target_satellite = composition.runtime.satellites.satellite_for_source(target)
    if target_satellite is None or not target_satellite.alert_capable:
        return payload, "invalid_alert_delivery_target"
    return payload.model_copy(update={"alert_delivery_target_source_id": target}), None


def _execute_application_dispatch(dispatch: DispatchPlan) -> DispatchPlan:
    composition = brain_application_composition(app)
    return execute_dispatch(dispatch, registry=composition.dispatch_registry)


def _continue_from_fallback_router(
    *,
    original_payload: CommandRequest,
    effective_payload: CommandRequest,
    route: RouteResponse,
    dispatch: DispatchPlan,
    household_settings: HouseholdRuntimeSettings | None = None,
    request_source: ResolvedRequestSource | None = None,
) -> tuple[RouteResponse, DispatchPlan]:
    next_route = _build_fallback_router_next_route(route, dispatch)
    next_command_text = next_route.normalized_text
    next_payload = effective_payload.model_copy(update={"text": next_command_text})
    if next_route.target != "facts":
        composition = brain_application_composition(app)
        validated_route = validate_fallback_reentry(
            proposed_target=next_route.target,
            original_text=route.normalized_text,
            normalized_text=next_command_text,
            source=effective_payload.source,
            session_id=effective_payload.session_id,
            registry=composition.route_registry,
            household_settings=household_settings or composition.runtime.household,
            playback_state=composition.music_execution or composition.audiobook_execution,
        )
        if validated_route is None:
            rejected_dispatch = build_dispatch_plan(
                next_payload,
                next_route,
                original_text=original_payload.text,
            )
            rejected_dispatch.status = "failed"
            rejected_dispatch.result = build_failure_result(
                action="fallback_reentry",
                failure_class="router_failure",
                owning_component="brain.fallback_router",
                error="fallback_router_unvalidated_proposal",
                detail=(
                    f"The canonical {next_route.target} owner did not accept the fallback "
                    "router's normalized request."
                ),
            )
            return next_route, rejected_dispatch
        next_route = validated_route
    next_payload, target_resolution, target_error = _apply_resolved_playback_target(
        next_payload,
        route_target=next_route.target,
        request_source=request_source,
    )
    next_payload, alert_target_error = _apply_canonical_alert_target(
        next_payload,
        route_target=next_route.target,
        request_source=request_source,
    )
    next_dispatch = build_dispatch_plan(next_payload, next_route, original_text=original_payload.text)
    if target_resolution is not None:
        next_dispatch.payload["playback_target_resolution"] = target_resolution
    if target_error is not None:
        next_dispatch.payload["playback_target_error"] = target_error
    if alert_target_error is not None:
        next_dispatch.payload["alert_delivery_target_error"] = alert_target_error
    for key in (
        "requested_user_name",
        "user_resolution_error",
        "user_resolution_source",
        "effective_user_id",
    ):
        if key in dispatch.payload:
            next_dispatch.payload[key] = dispatch.payload[key]

    proposed_user_id = _resolve_router_user_override(
        proposed_domain=next_route.target,
        proposed_user_id=str((dispatch.result or {}).get("user_id") or ""),
        dispatch_payload=dispatch.payload,
        household_settings=household_settings,
    )
    if proposed_user_id is not None:
        next_dispatch.payload["effective_user_id"] = proposed_user_id
        next_dispatch.payload["user_resolution_source"] = "fallback_router"

    next_dispatch = _execute_application_dispatch(next_dispatch)
    return next_route, next_dispatch


def _memory_dispatch_action(dispatch: DispatchPlan) -> str | None:
    result = dispatch.result or {}
    action = str(result.get("action") or dispatch.payload.get("action") or "").strip()
    if action:
        return action
    hook = str(dispatch.hook or "").strip()
    return hook or None


def _memory_route_payload(route: RouteResponse | None) -> dict[str, object] | None:
    if route is None:
        return None
    return {
        "target": route.target,
        "confidence": route.confidence,
        "reason": route.reason,
        "normalized_text": route.normalized_text,
    }


def _memory_dispatch_payload(dispatch: DispatchPlan | None) -> dict[str, object] | None:
    if dispatch is None:
        return None
    return {
        "target": dispatch.target,
        "hook": dispatch.hook,
        "status": dispatch.status,
        "action": _memory_dispatch_action(dispatch),
    }


def _memory_fallback_payload(*, fallback_used: bool, fallback_dispatch: DispatchPlan | None) -> dict[str, object]:
    result = fallback_dispatch.result if fallback_dispatch is not None and isinstance(fallback_dispatch.result, dict) else {}
    return {
        "used": fallback_used,
        "status": str(fallback_dispatch.status) if fallback_dispatch is not None else None,
        "proposed_domain": str(result.get("proposed_domain") or "") or None,
        "failure_code": str(result.get("error") or "") or None,
    }


def _memory_failure_stage(
    *,
    route: RouteResponse,
    dispatch: DispatchPlan,
    contract_failure: bool = False,
) -> str | None:
    if contract_failure:
        return "response_contract"
    if dispatch.status != "failed":
        return None
    if route.target == "fallback_router":
        return "fallback_router"
    return "dispatch"


def _memory_fallback_reason(
    *,
    fallback_used: bool,
    initial_route: RouteResponse | None,
    fallback_dispatch: DispatchPlan | None,
) -> str | None:
    if not fallback_used:
        return None
    result = fallback_dispatch.result if fallback_dispatch is not None and isinstance(fallback_dispatch.result, dict) else {}
    failure_code = str(result.get("error") or "").strip()
    if failure_code:
        return failure_code
    if initial_route is not None:
        return initial_route.reason
    return None


def _memory_route_result(
    *,
    initial_route: RouteResponse | None,
    final_route: RouteResponse,
    dispatch: DispatchPlan,
    fallback_used: bool,
    fallback_dispatch: DispatchPlan | None,
) -> dict[str, object]:
    return {
        "initial_route": _memory_route_payload(initial_route),
        "final_route": _memory_route_payload(final_route),
        "dispatch": _memory_dispatch_payload(dispatch),
        "fallback": _memory_fallback_payload(
            fallback_used=fallback_used,
            fallback_dispatch=fallback_dispatch,
        ),
    }


def _memory_record_command_session_start(
    *,
    effective_payload: CommandRequest,
    session_info: dict[str, object],
    correlation_id: str | None,
) -> None:
    safe_record_session(
        session_id=str(effective_payload.session_id or ""),
        mode="conversation",
        correlation_id=correlation_id,
        source_id=effective_payload.source,
        payload={
            "client_session_id": session_info.get("client_session_id"),
            "effective_session_id": effective_payload.session_id,
            "created_new_session": bool(session_info.get("created_new_session")),
        },
    )


def _memory_observe_command_outcome(
    *,
    original_payload: CommandRequest,
    effective_payload: CommandRequest,
    session_info: dict[str, object],
    response: CommandResponse,
    initial_route: RouteResponse | None,
    fallback_dispatch: DispatchPlan | None = None,
    fallback_used: bool = False,
    normalized_text: str | None = None,
    user_id: str | None = None,
    contract_failure: bool = False,
) -> None:
    correlation_id = get_correlation_id()
    result = response.dispatch.result or {}
    action = _memory_dispatch_action(response.dispatch)
    failure_stage = _memory_failure_stage(
        route=response.route,
        dispatch=response.dispatch,
        contract_failure=contract_failure,
    )
    route_result = _memory_route_result(
        initial_route=initial_route,
        final_route=response.route,
        dispatch=response.dispatch,
        fallback_used=fallback_used,
        fallback_dispatch=fallback_dispatch,
    )
    fallback_reason = _memory_fallback_reason(
        fallback_used=fallback_used,
        initial_route=initial_route,
        fallback_dispatch=fallback_dispatch,
    )
    matched_transcripts = safe_enrich_transcripts_for_correlation(
        correlation_id,
        session_id=effective_payload.session_id,
        source_id=effective_payload.source,
        user_id=user_id,
        normalized_text=normalized_text,
        route_result=route_result,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        final_domain=response.route.target,
        final_intent=action,
        final_status=response.dispatch.status,
        failure_stage=failure_stage,
    )
    safe_update_session_status(
        str(effective_payload.session_id or ""),
        ended_at=memory_utc_now_iso(),
        final_status=response.dispatch.status,
        payload={
            "client_session_id": original_payload.session_id,
            "effective_session_id": effective_payload.session_id,
            "created_new_session": bool(session_info.get("created_new_session")),
            "route_target": response.route.target,
            "dispatch_hook": response.dispatch.hook,
            "dispatch_status": response.dispatch.status,
            "action": action,
            "failure_class": str(result.get("failure_class") or "") or None,
            "owning_component": str(result.get("owning_component") or "") or None,
            "matching_transcript_count": matched_transcripts,
        },
    )


def _maybe_cancel_pending_calendar_write(*, source: str | None, session_id: str | None, route: RouteResponse) -> None:
    pending = state.load_pending_calendar_write_request(source, session_id)
    confirmation = state.load_pending_confirmation(source, session_id)
    confirmation_target = ""
    if confirmation is not None:
        confirmation_target = str(((confirmation.get("dispatch") or {}).get("target")) or "").strip()

    if pending is None and confirmation_target != "calendar":
        return
    if route.target == "calendar":
        return
    if route.target == "system" and route.normalized_text in {"confirm", "cancel", "never mind", "stop", "forget it"}:
        return
    state.clear_pending_calendar_write_request(source, session_id)
    if confirmation_target == "calendar":
        state.clear_pending_confirmation(source, session_id)
    clear_active_context(source, session_id, reason="calendar_pending_abandoned")

def _request_payload_and_household(
    payload: RouteRequest | CommandRequest,
    *,
    request_source: ResolvedRequestSource | None,
) -> tuple[
    RouteRequest | CommandRequest,
    HouseholdRuntimeSettings | None,
    ResolvedRequestSource | None,
]:
    composition = brain_application_composition(app)
    established_source = request_source or ResolvedRequestSource(
        request_source_id="ephemeral_internal",
        kind="ephemeral",
        authentication="none",
    )
    source_id = established_source.request_source_id
    session_id = payload.session_id
    if source_id == EPHEMERAL_HTTP_SOURCE_ID and not str(session_id or "").strip():
        session_id = f"ephemeral-{uuid.uuid4().hex}"
    return (
        payload.model_copy(update={"source": source_id, "session_id": session_id}),
        composition.runtime.household,
        established_source,
    )


def _canonical_http_request_source(
    payload_source: str | None,
    request: Request,
) -> ResolvedRequestSource | None:
    try:
        composition = brain_application_composition(request.app)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Canonical application composition is unavailable.",
            headers={"Cache-Control": "no-store"},
        ) from exc
    authorization = str(request.headers.get("Authorization") or "")
    scheme, separator, token = authorization.partition(" ")
    credential = token.strip() if separator and scheme.casefold() == "bearer" else None
    if authorization and not credential:
        raise _canonical_request_authentication_error()
    try:
        resolved = composition.request_source_resolver.resolve(
            claimed_source_id=payload_source,
            credential=credential,
            peer_address=request.client.host if request.client is not None else None,
        )
    except RequestSourceAuthenticationError as exc:
        raise _canonical_request_authentication_error() from exc
    except (GenerationStoreError, OSError) as exc:
        logger.error(
            "canonical_request_source_unavailable error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Canonical request source authentication is unavailable.",
            headers={"Cache-Control": "no-store"},
        ) from exc
    return resolved


def _canonical_request_authentication_error() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Canonical request source authentication failed.",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


def route_http_request(payload: RouteRequest, request: Request) -> RouteResponse:
    return route_request(
        payload,
        request_source=_canonical_http_request_source(payload.source, request),
    )


def command_http_request(payload: CommandRequest, request: Request) -> CommandResponse:
    return command_request(
        payload,
        request_source=_canonical_http_request_source(payload.source, request),
    )


def route_request(
    payload: RouteRequest,
    *,
    request_source: ResolvedRequestSource | None = None,
) -> RouteResponse:
    effective_payload, household_settings, _established_source = _request_payload_and_household(
        payload,
        request_source=request_source,
    )
    if not isinstance(effective_payload, RouteRequest):
        raise TypeError("Route request composition returned the wrong payload type.")
    normalized = normalize_text(payload.text)
    if not normalized:
        return RouteResponse(
            target="system",
            confidence=1.0,
            reason=IGNORED_TRANSCRIPT_REASON,
            normalized_text="",
        )
    composition = brain_application_composition(app)
    route = choose_route(
        effective_payload.text,
        source=effective_payload.source,
        session_id=effective_payload.session_id,
        registry=composition.route_registry,
        household_settings=household_settings,
        playback_state=composition.music_execution or composition.audiobook_execution,
    )
    if route.target == "home_assistant":
        resolved_text, _room_context = apply_room_context_to_home_text(
            route.normalized_text,
            source=effective_payload.source,
            session_id=effective_payload.session_id,
            household_settings=household_settings,
        )
        route = route.model_copy(update={"normalized_text": resolved_text})
    return route


def command_request(
    payload: CommandRequest,
    *,
    request_source: ResolvedRequestSource | None = None,
) -> CommandResponse:
    composed_payload, household_settings, established_source = _request_payload_and_household(
        payload,
        request_source=request_source,
    )
    if not isinstance(composed_payload, CommandRequest):
        raise TypeError("Command request composition returned the wrong payload type.")
    payload = composed_payload
    memory_correlation_id = get_correlation_id()
    _log_command_event("command_received", payload=payload)
    session_info = resolve_request_session(payload.source, payload.session_id)
    effective_payload = payload.model_copy(
        update={"source": session_info["source"], "session_id": session_info["effective_session_id"]}
    )
    _memory_record_command_session_start(
        effective_payload=effective_payload,
        session_info=session_info,
        correlation_id=memory_correlation_id,
    )
    normalized = normalize_text(payload.text)
    if not normalized:
        response = build_ignored_command_response(
            effective_payload,
            registry=brain_application_composition(app).dispatch_registry,
        )
        response.session_id = payload.session_id
        response.effective_session_id = str(session_info["effective_session_id"])
        _log_command_event(
            "dispatch_executed",
            payload=effective_payload,
            route=response.route,
            dispatch_hook=response.dispatch.hook,
            dispatch_status=response.dispatch.status,
            action=str((response.dispatch.result or {}).get("action") or ""),
            reply_text=response.reply_text,
            room_context=(response.dispatch.result or {}).get("room_context") or response.dispatch.payload.get("room_context") or {},
        )
        _log_command_event(
            "reply_built",
            payload=effective_payload,
            route=response.route,
            dispatch_hook=response.dispatch.hook,
            dispatch_status=response.dispatch.status,
            action=str((response.dispatch.result or {}).get("action") or ""),
            reply_text=response.reply_text,
            room_context=(response.dispatch.result or {}).get("room_context") or response.dispatch.payload.get("room_context") or {},
        )
        _memory_observe_command_outcome(
            original_payload=payload,
            effective_payload=effective_payload,
            session_info=session_info,
            response=response,
            initial_route=response.route,
            normalized_text="",
        )
        return response

    composition = brain_application_composition(app)
    routine_execution = composition.routine_execution
    pending_ui_response = _handle_pending_ui_context(
        payload.text,
        effective_payload.source,
        effective_payload.session_id,
        audio_search=_search_pending_ui_audio,
        routine_start=(
            None
            if routine_execution is None
            else lambda *, routine_id, client_id, inputs: routine_execution.start(
                routine_id,
                client_id=client_id,
                inputs=inputs,
            )
        ),
        household_settings=composition.runtime.household,
        satellite_settings=composition.runtime.satellites,
    )
    if pending_ui_response is not None:
        pending_ui_response.session_id = payload.session_id
        pending_ui_response.effective_session_id = str(session_info["effective_session_id"])
        append_turn(effective_payload.source, effective_payload.session_id, "user", payload.text)
        record_repeat_eligible_output(
            source=effective_payload.source,
            session_id=effective_payload.session_id,
            route_target=str(pending_ui_response.route.target),
            dispatch=pending_ui_response.dispatch,
            reply_text=pending_ui_response.reply_text,
        )
        append_turn(effective_payload.source, effective_payload.session_id, "assistant", pending_ui_response.reply_text)
        _memory_observe_command_outcome(
            original_payload=payload,
            effective_payload=effective_payload,
            session_info=session_info,
            response=pending_ui_response,
            initial_route=pending_ui_response.route,
            normalized_text=normalized,
        )
        return pending_ui_response

    routine_definition = None
    source_is_configured = bool(
        household_settings is not None
        and household_settings.source(effective_payload.source) is not None
    )
    if source_is_configured:
        routine_definition = (
            routine_execution.resolve_voice_trigger(
                normalized,
                source_id=effective_payload.source,
            )
            if routine_execution is not None
            else None
        )
    if routine_definition is not None:
        append_turn(effective_payload.source, effective_payload.session_id, "user", payload.text)
        response = _build_routine_voice_response(
            definition=routine_definition,
            payload=payload,
            effective_payload=effective_payload,
            session_info=session_info,
            normalized_text=normalized,
            routine_execution=routine_execution,
        )
        append_turn(effective_payload.source, effective_payload.session_id, "assistant", response.reply_text)
        _memory_observe_command_outcome(
            original_payload=payload,
            effective_payload=effective_payload,
            session_info=session_info,
            response=response,
            initial_route=response.route,
            normalized_text=normalized,
        )
        return response

    user_directive = analyze_user_directive(
        normalized,
        source=effective_payload.source,
        session_id=effective_payload.session_id,
    )
    if user_directive.error == "no_active_context_for_execute_as":
        response = _build_user_context_error_response(
            original_session_id=payload.session_id,
            effective_session_id=str(session_info["effective_session_id"]),
            normalized_text=normalized,
            error="no_active_context_for_execute_as",
            detail="I don't have a recent request to rerun as that user.",
        )
        record_repeat_eligible_output(
            source=effective_payload.source,
            session_id=effective_payload.session_id,
            route_target="system",
            dispatch=response.dispatch,
            reply_text=response.reply_text,
        )
        _memory_observe_command_outcome(
            original_payload=payload,
            effective_payload=effective_payload,
            session_info=session_info,
            response=response,
            initial_route=response.route,
            normalized_text=normalized,
        )
        return response

    command_text = user_directive.rewritten_text or normalized

    append_turn(effective_payload.source, effective_payload.session_id, "user", payload.text)
    composition = brain_application_composition(app)
    route = choose_route(
        command_text,
        source=effective_payload.source,
        session_id=effective_payload.session_id,
        registry=composition.route_registry,
        household_settings=household_settings,
        playback_state=composition.music_execution or composition.audiobook_execution,
    )
    initial_route = route
    fallback_dispatch: DispatchPlan | None = None
    fallback_used = initial_route.target == "fallback_router"
    _maybe_cancel_pending_calendar_write(
        source=effective_payload.source,
        session_id=effective_payload.session_id,
        route=route,
    )
    room_context: dict[str, object] | None = None
    if route.target == "home_assistant":
        resolved_text, room_context = apply_room_context_to_home_text(
            route.normalized_text,
            source=effective_payload.source,
            session_id=effective_payload.session_id,
            household_settings=household_settings,
        )
        route = route.model_copy(update={"normalized_text": resolved_text})
    effective_payload, target_resolution, target_error = _apply_resolved_playback_target(
        effective_payload,
        route_target=route.target,
        request_source=established_source,
    )
    effective_payload, alert_target_error = _apply_canonical_alert_target(
        effective_payload,
        route_target=route.target,
        request_source=established_source,
    )
    _log_command_event("route_chosen", payload=effective_payload, route=route, room_context=room_context or {})
    dispatch_payload = effective_payload.model_copy(update={"text": command_text})
    dispatch = build_dispatch_plan(dispatch_payload, route, original_text=payload.text)
    if room_context is not None:
        dispatch.payload["room_context"] = room_context
    if target_resolution is not None:
        dispatch.payload["playback_target_resolution"] = target_resolution
    if target_error is not None:
        dispatch.payload["playback_target_error"] = target_error
    if alert_target_error is not None:
        dispatch.payload["alert_delivery_target_error"] = alert_target_error
    if user_directive.requested_user_name is not None:
        dispatch.payload["requested_user_name"] = user_directive.requested_user_name

    user_resolution = resolve_effective_user(
        source=effective_payload.source,
        session_id=effective_payload.session_id,
        requested_user_name=user_directive.requested_user_name,
        household_settings=household_settings,
    )
    if user_resolution.get("ok"):
        dispatch.payload["effective_user_id"] = user_resolution.get("user_id")
        dispatch.payload["user_resolution_source"] = user_resolution.get("resolution_source")
    else:
        dispatch.payload["user_resolution_error"] = user_resolution.get("error")
        dispatch.payload["requested_user_name"] = (
            str(user_resolution.get("requested_user_name") or user_directive.requested_user_name or "").strip()
        )
    _log_command_event(
        "dispatch_planned",
        payload=effective_payload,
        route=route,
        dispatch_hook=dispatch.hook,
        dispatch_status=dispatch.status,
        action=str(dispatch.payload.get("action") or ""),
        room_context=room_context or {},
    )
    dispatch = _execute_application_dispatch(dispatch)
    if route.target == "fallback_router" and dispatch.status == "executed":
        fallback_dispatch = dispatch.model_copy(deep=True)
        route, dispatch = _continue_from_fallback_router(
            original_payload=payload,
            effective_payload=effective_payload,
            route=route,
            dispatch=dispatch,
            household_settings=household_settings,
            request_source=established_source,
        )
    elif route.target == "fallback_router" and dispatch.status == "failed":
        fallback_dispatch = dispatch.model_copy(deep=True)
        logger.info(
            "router_failure_path_taken source=%s session_id=%s failure_code=%s",
            effective_payload.source or "-",
            effective_payload.session_id or "-",
            str((dispatch.result or {}).get("error") or "-"),
        )
    result = dispatch.result or {}
    reply_text = build_reply_text(dispatch)
    try:
        validate_command_response_contract(route=route, dispatch=dispatch, reply_text=reply_text)
    except ContractValidationError as exc:
        response = build_command_contract_failure_response(route=route, dispatch=dispatch, exc=exc)
        response.session_id = payload.session_id
        response.effective_session_id = effective_payload.session_id
        failed_result = response.dispatch.result or {}
        _log_command_event(
            "failure_path_selected",
            payload=effective_payload,
            route=response.route,
            dispatch_hook=response.dispatch.hook,
            dispatch_status=response.dispatch.status,
            action=str(failed_result.get("action") or ""),
            reply_text=response.reply_text,
            failure_class=str(failed_result.get("failure_class") or ""),
            owning_component=str(failed_result.get("owning_component") or ""),
            room_context=failed_result.get("room_context") or response.dispatch.payload.get("room_context") or {},
        )
        _memory_observe_command_outcome(
            original_payload=payload,
            effective_payload=effective_payload,
            session_info=session_info,
            response=response,
            initial_route=initial_route,
            fallback_dispatch=fallback_dispatch,
            fallback_used=fallback_used,
            normalized_text=command_text,
            contract_failure=True,
        )
        return response
    if (
        route.target == "audiobook"
        and user_resolution.get("ok")
        and user_directive.directive_type in {"explicit_request_user", "execute_as"}
    ):
        set_user_context(
            effective_payload.source,
            effective_payload.session_id,
            user_id=str(user_resolution.get("user_id") or ""),
            resolution_source=str(user_directive.directive_type),
        )
    if _should_refresh_session(route=route, dispatch=dispatch, result=result):
        refresh_session(effective_payload.source, effective_payload.session_id)
    _maybe_update_active_context(route=route, dispatch=dispatch, result=result)
    record_repeat_eligible_output(
        source=effective_payload.source,
        session_id=effective_payload.session_id,
        route_target=str(route.target or dispatch.target),
        dispatch=dispatch,
        reply_text=reply_text,
    )
    _log_command_event(
        "dispatch_executed",
        payload=effective_payload,
        route=route,
        dispatch_hook=dispatch.hook,
        dispatch_status=dispatch.status,
        action=str(result.get("action") or ""),
        failure_class=str(result.get("failure_class") or ""),
        owning_component=str(result.get("owning_component") or ""),
        room_context=result.get("room_context") or dispatch.payload.get("room_context") or {},
    )
    set_dispatch_context(
        effective_payload.source,
        effective_payload.session_id,
        target=dispatch.target,
        action=str(result.get("action")) if result.get("action") is not None else None,
    )
    append_turn(effective_payload.source, effective_payload.session_id, "assistant", reply_text)
    _log_command_event(
        "reply_built",
        payload=effective_payload,
        route=route,
        dispatch_hook=dispatch.hook,
        dispatch_status=dispatch.status,
        action=str(result.get("action") or ""),
        reply_text=reply_text,
        failure_class=str(result.get("failure_class") or ""),
        owning_component=str(result.get("owning_component") or ""),
        room_context=result.get("room_context") or dispatch.payload.get("room_context") or {},
    )
    response = CommandResponse(
        route=route,
        dispatch=dispatch,
        reply_text=reply_text,
        session_id=payload.session_id,
        effective_session_id=effective_payload.session_id,
    )
    _memory_observe_command_outcome(
        original_payload=payload,
        effective_payload=effective_payload,
        session_info=session_info,
        response=response,
        initial_route=initial_route,
        fallback_dispatch=fallback_dispatch,
        fallback_used=fallback_used,
        normalized_text=command_text,
    )
    return response


def ingest_text(
    payload: CommandRequest,
    *,
    request_source: ResolvedRequestSource | None = None,
) -> CommandResponse:
    return command_request(payload, request_source=request_source)


def ingest_text_http_request(payload: CommandRequest, request: Request) -> CommandResponse:
    return ingest_text(
        payload,
        request_source=_canonical_http_request_source(payload.source, request),
    )

def session_lookup(source: str | None = None, session_id: str | None = None) -> Response:
    payload = inspect_session(source, session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(payload)


def command_events(
    source: str | None = None,
    session_id: str | None = None,
    after_event_id: int = 0,
) -> CommandInterimEventsResponse:
    return CommandInterimEventsResponse(
        events=list_command_interim_events(
            source=source,
            session_id=session_id,
            after_event_id=max(0, int(after_event_id or 0)),
        )
    )
