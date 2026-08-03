from __future__ import annotations

import asyncio
import logging
import json
import uuid
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from stt import SttError, SttProvider
from stt import attempt_stt_provider_warmup, attempt_stt_warmup
from tts import TtsError, TtsProvider

from .alerts import consume_due_alerts
from .admin_diagnostics_routes import (
    admin_memory_diagnostics_summary,
    build_log_targets as _build_log_targets,
    read_brain_log_tail as _read_brain_log_tail,
    register_admin_diagnostics_routes,
    ui_log_targets,
    ui_logs,
    ui_playback_authority,
    ui_sources,
)
from .admin_facts_routes import admin_facts_lookup, register_admin_facts_routes
from .admin_home_automation_routes import register_admin_home_automation_routes
from .admin_notifications_routes import register_admin_notifications_routes
from .admin_network_routes import admin_network_status, register_admin_network_routes
from .admin_orchestration_routes import admin_orchestrations, register_admin_orchestration_routes
from .admin_suggestions_routes import (
    admin_generate_suggestions,
    admin_openclaw_status,
    admin_review_suggestion,
    admin_suggestion_detail,
    admin_suggestion_run,
    admin_suggestion_runs,
    admin_suggestions,
    admin_suggestions_last_packet,
    admin_suggestions_last_response,
    register_admin_suggestions_routes,
)
from .browser_routes import (
    register_browser_routes,
    satellite_ui_asset,
    satellite_ui_shell,
)
from .command_processing import IGNORED_TRANSCRIPT_REASON, build_ignored_command_response
from .command_events import list_command_interim_events
from .conversation import append_turn, clear_conversation, set_dispatch_context
from . import state
from .text_normalization import normalize_text
from .config_reporting import (
    findings_have_errors,
)
from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    BrainApplicationComposition,
    CanonicalBrainApplicationComposition,
)
from .configuration.bootstrap import start_brain_configuration_host_local_runtime
from .configuration.bootstrap import resolve_brain_configuration_startup
from .configuration.generations import GenerationStoreError
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.request_source_resolution import (
    EPHEMERAL_HTTP_SOURCE_ID,
    RequestSourceAuthenticationError,
    ResolvedRequestSource,
)
from .configuration.playback_target_resolution import PlaybackTargetResolutionError
from .dispatch import (
    build_dispatch_plan,
    execute_dispatch,
)
from .network_control_results import (
    safe_reconcile_interrupted_network_controls,
    safe_restore_network_control_results_from_memory,
)
from .network_control_local_restart import safe_complete_pending_local_host_restart
from .network_control_local_service_restart import safe_complete_pending_local_service_restart
from .installation_runtime import finalize_verified_startup
from .version import CORE_VERSION
from .notifications.external_worker import external_delivery_worker_loop
from .home_automation import home_automation_scheduler_loop
from .home_automation_routes import register_home_automation_routes
from .orchestration_recovery import register_orchestration_recovery_routes
from .orchestration_routine_routes import register_orchestration_routine_routes
from .orchestration_routines import (
    configure_routine_adapters,
    extract_deferred_session,
    find_routine_trigger,
    routine_scheduler_loop,
    start_routine,
)
from .orchestration_routine_canonical import CanonicalRoutineExecution
from .health_routes import (
    health,
    health_audiobook,
    health_calendar,
    health_config,
    health_home_assistant,
    health_librenms,
    health_music,
    health_news,
    health_ollama,
    health_stt,
    health_tts,
    list_hooks,
    register_health_routes,
)
from .handlers.fallback_router import attempt_fallback_router_warmup
from .home_assistant_actions import (
    execute_home_assistant_ui_action,
    fetch_home_assistant_entity_state,
    resolve_home_assistant_dynamic_ui_action,
)
from .network import build_ui_network_health_snapshot
from .memory.correlation import correlation_context, get_correlation_id
from .memory.provider_status import safe_observe_provider_health
from .memory.orchestrations import safe_reconcile_interrupted_orchestration_runs
from .memory.runtime import safe_record_event
from .memory.sessions import safe_record_session, safe_update_session_status, utc_now_iso as memory_utc_now_iso
from .memory.sources import default_internal_sources, seed_default_sources, seed_sources
from .memory.transcripts import safe_enrich_transcripts_for_correlation, safe_record_transcript
from .media_routes import register_media_routes
from .music_runtime.control import ControlPlaneError, build_control_plane_failure, execute_satellite_command
from .music_runtime.control import fetch_satellite_playback_authority
from .routing import choose_route
from .replies import build_reply_text
from .room_context import apply_room_context_to_home_text
from .runtime_contracts import (
    ContractValidationError,
    build_command_contract_failure_response,
    validate_command_response_contract,
)
from .session_state import (
    clear_active_context,
    inspect_session,
    refresh_session,
    resolve_request_session,
    set_active_context,
    set_user_context,
)
from .satellite_activity_routes import register_satellite_activity_routes, satellite_activity
from .satellite_projection_routes import (
    configure_satellite_projection_routes,
    register_satellite_projection_routes,
)
from .wake_capture_upload_routes import (
    configure_wake_capture_upload_routes,
    register_wake_capture_upload_routes,
    wake_capture_archive_root_from_environment,
)
from .wake_arbitration_routes import register_wake_arbitration_routes
from .user_context import analyze_user_directive, get_user_entry, resolve_effective_user
from .ui_routes import (
    configure_ui_routes,
    register_ui_routes,
    ui_action,
    ui_alarm_cancel,
    ui_audio_control,
    ui_audio_play,
    ui_audio_search,
    ui_audio_sleep_timer,
    ui_calendar_cancel,
    ui_calendar_confirm,
    ui_calendar_draft,
    ui_context_start,
    ui_house_camera_snapshot,
)
from .ui_audio_control import start_current_audiobook_for_user, set_audiobook_sleep_timer_seconds
from .ui_snapshot_cache import get_cached_snapshot, invalidate_cached_snapshots
from .ui_calendar import (
    build_ui_calendar_page_snapshot as _build_ui_calendar_page_snapshot,
    build_ui_calendar_snapshot as _build_ui_calendar_snapshot,
    ui_calendar_cancel_impl as _ui_calendar_cancel_impl,
    ui_calendar_confirm_impl as _ui_calendar_confirm_impl,
    ui_calendar_draft_impl as _ui_calendar_draft_impl,
)
from .ui_audio import (
    build_ui_audio_snapshot,
    resolve_ui_audio_source,
    ui_audio_search_impl,
)
from .ui_audio_control import (
    ui_audio_control_impl,
    ui_audio_play_impl,
    ui_audio_sleep_timer_impl,
)
from .ui_house import (
    build_canonical_ui_home_assistant_snapshot,
    build_ui_house_snapshot,
    ui_house_camera_snapshot_impl,
)
from .ui_context import (
    ui_alarm_cancel_impl as _ui_alarm_cancel_impl,
    handle_pending_ui_context as _handle_pending_ui_context,
    ui_context_start_impl,
)
from .ui_satellite import (
    build_satellite_ui_config,
    build_satellite_ui_home_snapshot,
)
from .ui_weather import build_ui_weather_snapshot as _build_ui_weather_snapshot
from .ui_snapshot_routes import (
    configure_ui_snapshot_routes,
    register_ui_snapshot_routes,
    satellite_ui_config,
    ui_audio,
    ui_calendar,
    ui_home,
    ui_house,
    ui_satellite_home,
    ui_weather,
)
from .voice_routes import register_voice_routes
from .schemas import (
    DispatchPlan,
    CommandInterimEventsResponse,
    CommandRequest,
    CommandResponse,
    PendingAlertsResponse,
    RouteRequest,
    RouteResponse,
    SttResponse,
    TtsRequest,
    UiActionRequest,
    VoiceDeferredResumeRequest,
)
from .weather_current import build_weather_response
from .weather_forecast import fetch_weather_forecast


logger = logging.getLogger("oracle-brain.api")


def safe_seed_memory_sources(
    household_settings: HouseholdRuntimeSettings,
) -> bool:
    definitions = default_internal_sources()
    for source in household_settings.sources.values():
        if not source.enabled:
            continue
        payload: dict[str, object] = {
            "canonical_source": True,
            "fixed": source.fixed,
            "source_kind": source.type,
        }
        if source.associated_room_id is not None:
            payload["associated_room_id"] = source.associated_room_id
        if source.associated_user_id is not None:
            payload["associated_user_id"] = source.associated_user_id
        definitions.append({
            "source_id": source.id,
            "source_type": "satellite" if source.type == "satellite" else "ui",
            "display_name": source.id,
            "payload": payload,
        })
    try:
        seed_sources(definitions)
    except Exception:
        logger.exception("oracle_memory_source_seed_failed")
        return False
    return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    target_app = _app or app
    startup = resolve_brain_configuration_startup()
    startup_composition = CanonicalBrainApplicationComposition.from_startup(startup)
    install_brain_application_composition(target_app, startup_composition)

    with correlation_context():
        safe_record_event(
            "server_started",
            severity="info",
            source_id="brain",
            domain="system",
            status="starting",
            payload={
                "app": "oracle-brain",
                "version": CORE_VERSION,
                "phase": "lifespan_start",
                "process": "api",
            },
        )
        safe_seed_memory_sources(startup_composition.runtime.household)
        reconciled_orchestration_interruptions = safe_reconcile_interrupted_orchestration_runs()
        reconciled_network_control_interruptions = safe_reconcile_interrupted_network_controls()
        restored_network_control_results = safe_restore_network_control_results_from_memory()
        findings = []
        if findings_have_errors(findings):
            raise RuntimeError("Brain config validation failed")
        local_restart_completion = safe_complete_pending_local_host_restart(
            canonical_execution=startup_composition.network_execution,
            canonical_authority=True,
        )
        attempt_stt_provider_warmup(startup_composition.core_consumers.stt_provider)
        attempt_fallback_router_warmup(startup_composition.core_consumers.inference)
        warmup_path = "fallback_router"
        configuration_host_local_runtime = start_brain_configuration_host_local_runtime(
            startup=startup,
        )
        safe_record_event(
            "application_startup_complete",
            severity="info",
            source_id="brain",
            domain="system",
            status="ok",
            payload={
                "app": "oracle-brain",
                "version": CORE_VERSION,
                "phase": "lifespan_ready",
                "config_findings_count": len(findings),
                "config_warning_count": sum(1 for finding in findings if str(finding.get("severity") or "").lower() == "warning"),
                "config_error_count": sum(1 for finding in findings if str(finding.get("severity") or "").lower() == "error"),
                "fallback_router_enabled": True,
                "warmup_path": warmup_path,
                "configuration_mode": startup_composition.mode,
                "config_revision": (
                    startup_composition.runtime.effective_config.config_revision
                ),
                "reconciled_network_control_interruption_count": reconciled_network_control_interruptions,
                "reconciled_orchestration_interruption_count": reconciled_orchestration_interruptions,
                "restored_network_control_result_count": restored_network_control_results,
                "local_restart_completion_status": str(local_restart_completion.get("status") or "none"),
            },
        )
        safe_complete_pending_local_service_restart()
        verified_standard_activation = None
        try:
            if startup.installation_layout is not None:
                if health().status != "ok":
                    raise RuntimeError("Standard Brain health did not reach ready state.")
                verified_standard_activation = finalize_verified_startup(
                    startup_composition.runtime.effective_config.activation_generation_id,
                    startup.installation_layout,
                )
                if verified_standard_activation is not None:
                    safe_record_event(
                        "standard_activation_verified",
                        severity="info",
                        source_id="brain",
                        domain="system",
                        status="ok",
                        payload={
                            "activation_id": verified_standard_activation.activation_id,
                            "configuration_activation_id": (
                                startup_composition.runtime.effective_config.activation_generation_id
                            ),
                        },
                    )
        except BaseException:
            configuration_host_local_runtime.stop()
            raise
        background_tasks: list[asyncio.Task[None]] = []
        if startup_composition.routine_execution is not None:
            background_tasks.append(
                asyncio.create_task(
                    routine_scheduler_loop(
                        adapters=startup_composition.routine_execution.adapters,
                        required_config_revision=(
                            startup_composition.routine_execution.settings.config_revision
                        ),
                    )
                )
            )
        background_tasks.append(asyncio.create_task(
            home_automation_scheduler_loop(
                home_assistant_settings=(
                    startup_composition.runtime.home_assistant
                ),
                notification_submitter=(
                    startup_composition.notification_execution.submit
                ),
            )
        ))
        background_tasks.append(asyncio.create_task(
            external_delivery_worker_loop(
                canonical_execution=(
                    startup_composition.notification_execution
                ),
            )
        ))
        try:
            yield
        finally:
            try:
                for task in background_tasks:
                    task.cancel()
                for task in background_tasks:
                    with suppress(asyncio.CancelledError):
                        await task
            finally:
                configuration_host_local_runtime.stop()
            safe_record_event(
                "server_stopped",
                severity="info",
                source_id="brain",
                domain="system",
                status="stopping",
                payload={
                    "app": "oracle-brain",
                    "version": CORE_VERSION,
                    "phase": "lifespan_shutdown_start",
                    "process": "api",
                },
            )
            safe_record_event(
                "application_shutdown_complete",
                severity="info",
                source_id="brain",
                domain="system",
                status="ok",
                payload={
                    "app": "oracle-brain",
                    "version": CORE_VERSION,
                    "phase": "lifespan_shutdown_complete",
                    "process": "api",
                },
            )


app = FastAPI(
    title="Oracle Brain",
    version=CORE_VERSION,
    description="A small routing service that decides whether Oracle should use Home Assistant, music, or Ollama.",
    lifespan=lifespan,
)

def install_brain_application_composition(
    target_app: FastAPI,
    composition: BrainApplicationComposition,
) -> None:
    setattr(target_app.state, BRAIN_APPLICATION_COMPOSITION_STATE_KEY, composition)
    configure_satellite_projection_routes(
        target_app,
        composition.projection_resolver,
    )
    configure_wake_capture_upload_routes(
        target_app,
        composition.projection_resolver,
        wake_capture_archive_root_from_environment(),
    )


def brain_application_composition(target_app: FastAPI = app) -> BrainApplicationComposition:
    composition = getattr(target_app.state, BRAIN_APPLICATION_COMPOSITION_STATE_KEY, None)
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise RuntimeError("Brain application composition is not installed.")
    return composition


@app.middleware("http")
async def attach_correlation_id(request: Request, call_next):
    inbound = request.headers.get("X-Oracle-Correlation-Id")
    with correlation_context(inbound) as correlation_id:
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Oracle-Correlation-Id"] = correlation_id
        return response

_NON_ANCHORING_SYSTEM_ACTIONS = {
    "current_time",
    "current_date",
    "current_time_date",
    "calculation",
}

register_browser_routes(app)


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
    if get_user_entry(
        candidate,
        household_settings=household_settings,
    ) is None:
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


def _apply_canonical_playback_target(
    payload: CommandRequest,
    *,
    route_target: str,
    request_source: ResolvedRequestSource | None,
) -> tuple[CommandRequest, str | None, str | None]:
    if route_target not in {"music", "audiobook"} or request_source is None:
        return payload, None, None
    composition = brain_application_composition()
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


def _execute_application_dispatch(dispatch: DispatchPlan) -> DispatchPlan:
    composition = brain_application_composition()
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
    next_payload, target_resolution, target_error = _apply_canonical_playback_target(
        next_payload,
        route_target=next_route.target,
        request_source=request_source,
    )
    next_dispatch = build_dispatch_plan(next_payload, next_route, original_text=original_payload.text)
    if target_resolution is not None:
        next_dispatch.payload["playback_target_resolution"] = target_resolution
    if target_error is not None:
        next_dispatch.payload["playback_target_error"] = target_error
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
        mode="voice",
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


register_health_routes(app)


def _normalize_ui_client_id(client_id: str) -> str:
    normalized = str(client_id).strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="client_id cannot be empty")
    if " " in normalized:
        raise HTTPException(status_code=400, detail="client_id must not contain spaces")
    if not normalized.replace("-", "").isalnum() or normalized.startswith("-") or normalized.endswith("-"):
        raise HTTPException(
            status_code=400,
            detail="client_id must be lowercase, hyphen-separated, and contain only letters, numbers, and hyphens",
        )
    return normalized


def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _build_ui_home_weather_payload() -> dict[str, object]:
    composition = brain_application_composition()
    canonical = isinstance(composition, CanonicalBrainApplicationComposition)
    try:
        _speech, weather = (
            composition.weather_execution.build_current_response("")
            if canonical and composition.weather_execution is not None
            else _canonical_weather_ui_unavailable()
            if canonical
            else build_weather_response("")
        )
        try:
            forecast_payload = (
                composition.weather_execution.fetch_forecast()
                if canonical and composition.weather_execution is not None
                else fetch_weather_forecast()
            )
            forecast_periods = list(forecast_payload.get("periods") or [])
        except Exception:
            forecast_periods = []
        primary_forecast = forecast_periods[0] if forecast_periods else {}
        weather_payload = {
            "summary": str(weather.get("speech_summary") or _speech or "").strip() or str(_speech),
            "condition": str(getattr(primary_forecast, "short_forecast", "") or "").strip() or None,
            "temperature_f": weather.get("temperature_f"),
            "freshness_class": weather.get("freshness_class"),
            "humidity_pct": weather.get("humidity_pct"),
            "wind_speed_mph": weather.get("wind_speed_mph"),
            "source_name": weather.get("source_name"),
        }
    except Exception as exc:
        weather_payload = {
            "summary": "Weather is temporarily unavailable.",
            "temperature_f": None,
            "freshness_class": "unavailable",
            "detail": str(exc),
        }
    return weather_payload


def _cached_ui_home_weather_payload() -> dict[str, object]:
    return get_cached_snapshot(
        "ui_home_weather",
        ttl_seconds=60,
        builder=_build_ui_home_weather_payload,
    )


def _cached_ui_weather_snapshot() -> dict[str, object]:
    composition = brain_application_composition()
    if composition.weather_execution is None:
        raise RuntimeError("Weather capability is not configured")
    return get_cached_snapshot(
        "ui_weather_page",
        ttl_seconds=120,
        builder=lambda: _build_ui_weather_snapshot(
            canonical_execution=composition.weather_execution,
        ),
    )


def _canonical_weather_ui_unavailable():
    raise RuntimeError("Weather capability is not configured")


def _cached_ui_network_health_snapshot() -> dict[str, object]:
    composition = brain_application_composition()
    canonical = isinstance(composition, CanonicalBrainApplicationComposition)
    return get_cached_snapshot(
        "ui_network_health",
        ttl_seconds=30,
        builder=lambda: build_ui_network_health_snapshot(
            canonical_execution=composition.network_execution if canonical else None,
            canonical_authority=canonical,
        ),
    )


def _cached_ui_calendar_snapshot(*, limit: int) -> dict[str, object]:
    composition = brain_application_composition()
    if composition.calendar_execution is None:
        return {"events": []}
    return get_cached_snapshot(
        f"ui_calendar_summary:{limit}",
        ttl_seconds=45,
        builder=lambda: _build_ui_calendar_snapshot(
            limit=limit,
            canonical_execution=composition.calendar_execution,
        ),
    )


def _cached_ui_calendar_page_snapshot() -> dict[str, object]:
    composition = brain_application_composition()
    if composition.calendar_execution is None:
        raise RuntimeError("Calendar capability is not configured")
    return get_cached_snapshot(
        "ui_calendar_page",
        ttl_seconds=45,
        builder=lambda: _build_ui_calendar_page_snapshot(
            canonical_execution=composition.calendar_execution,
        ),
    )


def _ui_calendar_confirm_impl_cached(payload):
    composition = brain_application_composition()
    if composition.calendar_execution is None:
        raise HTTPException(status_code=409, detail="Calendar is disabled in canonical configuration.")
    result = _ui_calendar_confirm_impl(
        payload,
        canonical_execution=composition.calendar_execution,
    )
    if bool(result.get("ok")):
        invalidate_cached_snapshots("ui_calendar_")
    return result


def _build_ui_home_snapshot() -> dict[str, object]:
    composition = brain_application_composition()
    home_assistant = build_canonical_ui_home_assistant_snapshot(
        composition.runtime.home_assistant
    )
    return {
        "generated_at": _build_ui_generated_at(),
        "weather": _cached_ui_home_weather_payload(),
        "calendar": _cached_ui_calendar_snapshot(limit=3),
        "network_health": _cached_ui_network_health_snapshot(),
        **home_assistant,
        "escape_hatches": {
            page: [item.model_dump(mode="json") for item in links]
            for page, links in composition.runtime.household.ui.escape_hatches.items()
        },
        "refresh_after_seconds": 60,
    }

def _build_ui_house_snapshot() -> dict[str, object]:
    composition = brain_application_composition()
    return build_ui_house_snapshot(
        home_assistant_settings=composition.runtime.home_assistant,
    )


def _ui_house_camera_snapshot_impl(camera_id: str) -> Response:
    composition = brain_application_composition()
    return ui_house_camera_snapshot_impl(
        camera_id,
        home_assistant_settings=composition.runtime.home_assistant,
    )


def _build_satellite_ui_config(satellite_id: str | None) -> dict[str, object]:
    composition = brain_application_composition()
    return build_satellite_ui_config(
        satellite_id,
        fleet_settings=composition.runtime.satellite_ui,
        household_settings=composition.runtime.household,
    )


def _audio_ui_dependencies():
    composition = brain_application_composition()
    return composition


def _resolve_ui_audio_source(source: str | None):
    composition = _audio_ui_dependencies()
    return resolve_ui_audio_source(
        source,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
    )


def _build_ui_audio_snapshot(source: str | None = None, user_id: str | None = None) -> dict[str, object]:
    composition = _audio_ui_dependencies()
    return build_ui_audio_snapshot(
        source,
        user_id,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
        household_settings=composition.runtime.household,
    )


def _ui_audio_search_impl(payload):
    composition = _audio_ui_dependencies()
    return ui_audio_search_impl(
        payload,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
        household_settings=composition.runtime.household,
    )


def _ui_audio_play_impl(payload):
    composition = _audio_ui_dependencies()
    return ui_audio_play_impl(
        payload,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
        household_settings=composition.runtime.household,
    )


def _ui_audio_control_impl(payload):
    composition = _audio_ui_dependencies()
    return ui_audio_control_impl(
        payload,
        music_execution=composition.music_execution,
        audiobook_execution=composition.audiobook_execution,
    )


def _ui_audio_sleep_timer_impl(payload):
    composition = _audio_ui_dependencies()
    return ui_audio_sleep_timer_impl(
        payload,
        audiobook_execution=composition.audiobook_execution,
    )


def _build_application_satellite_ui_home_snapshot(satellite_id: str | None) -> dict[str, object]:
    composition = brain_application_composition()
    return build_satellite_ui_home_snapshot(
        satellite_id,
        build_ui_home_snapshot=lambda: {"weather": _cached_ui_home_weather_payload()},
        build_ui_audio_snapshot=_build_ui_audio_snapshot,
        build_ui_calendar_snapshot=_cached_ui_calendar_snapshot,
        home_assistant_settings=composition.runtime.home_assistant,
        fleet_settings=composition.runtime.satellite_ui,
        household_settings=composition.runtime.household,
        routine_settings=composition.runtime.routines,
    )


def _validate_ui_action_source(source: str | None) -> str:
    if source is None:
        raise HTTPException(status_code=400, detail="A playback-capable source is required for this action")
    selected_source, _configured_sources = _resolve_ui_audio_source(source)
    if selected_source is None:
        raise HTTPException(status_code=400, detail="A playback-capable source is required for this action")
    return selected_source


def _ui_context_start_impl(payload: UiContextStartRequest, request: Request | None = None) -> dict[str, object]:
    target_source_id = str(payload.target_source_id or payload.source or "").strip()
    if payload.action in {"music_search", "audiobook_search"}:
        target_source_id = _validate_ui_action_source(target_source_id or None)

    request_source_id = str(payload.source or "").strip()
    if request is not None:
        resolved_source = _canonical_http_request_source(payload.source, request)
        if resolved_source is not None:
            request_source_id = resolved_source.request_source_id

    return ui_context_start_impl(
        payload,
        request_source_id=request_source_id,
        target_source_id=target_source_id,
    )


register_admin_diagnostics_routes(app)
register_admin_facts_routes(app)
register_admin_home_automation_routes(app)
register_admin_notifications_routes(app)
register_admin_network_routes(app)
register_admin_orchestration_routes(app)

register_satellite_activity_routes(app)
register_satellite_projection_routes(app)
register_wake_capture_upload_routes(app)
register_wake_arbitration_routes(app)
register_home_automation_routes(app)
register_admin_suggestions_routes(app)
register_media_routes(app)


def _request_payload_and_household(
    payload: RouteRequest | CommandRequest,
    *,
    request_source: ResolvedRequestSource | None,
) -> tuple[
    RouteRequest | CommandRequest,
    HouseholdRuntimeSettings | None,
    ResolvedRequestSource | None,
]:
    composition = brain_application_composition()
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        return payload, None, None

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
    composition = brain_application_composition(request.app)
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        return None
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
    if household_settings is None:
        route = choose_route(
            effective_payload.text,
            source=effective_payload.source,
            session_id=effective_payload.session_id,
        )
    else:
        route = choose_route(
            effective_payload.text,
            source=effective_payload.source,
            session_id=effective_payload.session_id,
            registry=brain_application_composition().route_registry,
            household_settings=household_settings,
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
    if session_info.get("created_new_session"):
        clear_conversation(effective_payload.source, effective_payload.session_id)
    normalized = normalize_text(payload.text)
    if not normalized:
        response = build_ignored_command_response(effective_payload)
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

    pending_ui_response = _handle_pending_ui_context(
        payload.text,
        effective_payload.source,
        effective_payload.session_id,
        audio_search=_ui_audio_search_impl,
    )
    if pending_ui_response is not None:
        pending_ui_response.session_id = payload.session_id
        pending_ui_response.effective_session_id = str(session_info["effective_session_id"])
        append_turn(effective_payload.source, effective_payload.session_id, "user", payload.text)
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
    composition = brain_application_composition()
    routine_execution = composition.routine_execution
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
    if household_settings is None:
        route = choose_route(
            command_text,
            source=effective_payload.source,
            session_id=effective_payload.session_id,
        )
    else:
        route = choose_route(
            command_text,
            source=effective_payload.source,
            session_id=effective_payload.session_id,
            registry=brain_application_composition().route_registry,
            household_settings=household_settings,
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
    effective_payload, target_resolution, target_error = _apply_canonical_playback_target(
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


def deferred_resume(payload: VoiceDeferredResumeRequest) -> dict[str, object]:
    source = str(payload.source or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required")
    deferred_session = payload.deferred_session if isinstance(payload.deferred_session, dict) else {}
    resume_action = str(deferred_session.get("resume_action") or "").strip()
    if resume_action not in {"resume_longform_audio", "play_media"}:
        raise HTTPException(status_code=400, detail="Unsupported deferred resume action")
    resume_args = deferred_session.get("resume_args")
    if resume_action == "resume_longform_audio":
        command_args = None
    elif isinstance(resume_args, dict):
        command_args = resume_args
    else:
        raise HTTPException(status_code=400, detail="Deferred music resume requires resume_args")

    try:
        satellite = execute_satellite_command(source, resume_action, command_args)
    except ControlPlaneError as exc:
        return {
            "ok": False,
            "source": source,
            "deferred_session": {
                "kind": str(deferred_session.get("kind") or ""),
                "backend_type": str(deferred_session.get("backend_type") or ""),
                "session_id": str(deferred_session.get("session_id") or ""),
                "resume_action": resume_action,
            },
            "result": build_control_plane_failure(action=resume_action, exc=exc),
        }

    return {
        "ok": True,
        "source": source,
        "deferred_session": {
            "kind": str(deferred_session.get("kind") or ""),
            "backend_type": str(deferred_session.get("backend_type") or ""),
            "session_id": str(deferred_session.get("session_id") or ""),
            "resume_action": resume_action,
        },
        "satellite": satellite,
    }


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


def pending_alerts(source: str | None = None) -> PendingAlertsResponse:
    composition = brain_application_composition()
    decisions = composition.notification_execution.build_delivery_decisions(source)
    return PendingAlertsResponse(
        alerts=consume_due_alerts(source, notification_decisions=decisions)
    )


def _ui_action_impl(payload: UiActionRequest) -> dict[str, object]:
    action_id = str(payload.action_id).strip()
    client_id = _normalize_ui_client_id(payload.client_id)
    composition = brain_application_composition()
    home_assistant_settings = composition.runtime.home_assistant
    direct_result = execute_home_assistant_ui_action(
        action_id,
        home_assistant_settings=home_assistant_settings,
        canonical_authority=True,
    )
    if direct_result is not None:
        return direct_result
    action_spec = resolve_home_assistant_dynamic_ui_action(
        action_id,
        home_assistant_settings=home_assistant_settings,
        canonical_authority=True,
    )
    if not action_spec:
        raise HTTPException(status_code=404, detail=f"Unknown ui action {action_id}")
    command_text = str(action_spec["command_text"])
    requires_source = bool(action_spec.get("requires_source"))
    action_source = _validate_ui_action_source(payload.source) if requires_source else None
    refresh_pages = list(action_spec.get("refresh_pages") or ["home"])
    response = command_request(
        CommandRequest(
            text=command_text,
            source=action_source or client_id,
            session_id=f"ui-action:{client_id}" if action_source is None else f"ui-action:{client_id}:{action_source}",
            playback_target_source_id=action_source,
        )
    )
    status = str(response.dispatch.status or "")
    if status == "pending_confirmation" and bool(action_spec.get("auto_confirm_pending")):
        response = command_request(
            CommandRequest(
                text="confirm",
                source=action_source or client_id,
                session_id=f"ui-action:{client_id}" if action_source is None else f"ui-action:{client_id}:{action_source}",
                playback_target_source_id=action_source,
            )
        )
        status = str(response.dispatch.status or "")
    ok = status in {"executed", "pending_confirmation", "pending_clarification"}
    result_payload: dict[str, object] = {
        "status": status,
        "message": response.reply_text or ("Action executed." if ok else "Action failed."),
    }
    if response.reply_text:
        result_payload["reply_text"] = response.reply_text

    output: dict[str, object] = {
        "ok": ok,
        "action_id": action_id,
        "result": result_payload,
        "refresh": {"refresh_pages": refresh_pages},
    }
    if not ok:
        dispatch_result = response.dispatch.result or {}
        output["error"] = str(dispatch_result.get("error") or "action_failed")
        output["detail"] = str(dispatch_result.get("detail") or response.reply_text or "Action failed.")
    return output


def _routine_ui_action_adapter(
    *,
    action_id: str,
    client_id: str,
    source_id: str | None = None,
) -> dict[str, object]:
    return _ui_action_impl(
        UiActionRequest(
            action_id=action_id,
            client_id=client_id,
            source=source_id,
        )
    )


def _routine_audiobook_start_adapter(
    *,
    source_id: str,
    user_id: str,
    client_id: str,
    defer_audible_start: bool = False,
    sleep_timer_seconds: int | None = None,
) -> dict[str, object]:
    composition = brain_application_composition()
    return start_current_audiobook_for_user(
        client_id=client_id,
        source_id=source_id,
        user_id=user_id,
        defer_audible_start=defer_audible_start,
        sleep_timer_seconds=sleep_timer_seconds,
        audiobook_execution=composition.audiobook_execution,
    )


def _routine_sleep_timer_adapter(
    *,
    source_id: str,
    duration_seconds: int,
    client_id: str,
) -> dict[str, object]:
    composition = brain_application_composition()
    return set_audiobook_sleep_timer_seconds(
        client_id=client_id,
        source_id=source_id,
        duration_seconds=duration_seconds,
        audiobook_execution=composition.audiobook_execution,
    )


def _routine_state_check_adapter(
    *,
    check_id: str,
    expected_state: str,
    client_id: str,
) -> dict[str, object]:
    del client_id
    return {
        "ok": False,
        "error": "unknown_state_check",
        "detail": f"Unknown canonical routine state check {check_id}.",
    }


def _routine_playback_check_adapter(
    *,
    source_id: str,
    check_id: str,
    client_id: str,
) -> dict[str, object]:
    del client_id
    if check_id != "routine_audiobook_stopped":
        return {
            "ok": False,
            "error": "unknown_playback_check",
            "detail": f"Unknown curated routine playback check {check_id}.",
        }
    authority = fetch_satellite_playback_authority(source_id)
    owner = authority.get("output_owner") if isinstance(authority, dict) else None
    owner = owner if isinstance(owner, dict) else {}
    media_kind = str(owner.get("media_kind") or "").strip().lower()
    state_name = str(owner.get("state") or "").strip().lower()
    active = media_kind == "audiobook" and state_name not in {"", "idle", "stopped", "ended", "closed"}
    return {
        "ok": not active,
        "status": "passed" if not active else "failed",
        "media_kind": media_kind or None,
        "playback_state": state_name or None,
        "detail": "Audiobook playback is stopped." if not active else "Audiobook playback is still active.",
    }


configure_ui_snapshot_routes(
    build_ui_home_snapshot=_build_ui_home_snapshot,
    build_satellite_ui_config=_build_satellite_ui_config,
    build_satellite_ui_home_snapshot=_build_application_satellite_ui_home_snapshot,
    build_ui_weather_snapshot=_cached_ui_weather_snapshot,
    build_ui_calendar_page_snapshot=_cached_ui_calendar_page_snapshot,
    build_ui_audio_snapshot=_build_ui_audio_snapshot,
    build_ui_house_snapshot=_build_ui_house_snapshot,
)
register_ui_snapshot_routes(app)
configure_ui_routes(
    ui_calendar_draft=_ui_calendar_draft_impl,
    ui_calendar_confirm=_ui_calendar_confirm_impl_cached,
    ui_calendar_cancel=_ui_calendar_cancel_impl,
    ui_audio_search=_ui_audio_search_impl,
    ui_audio_play=_ui_audio_play_impl,
    ui_audio_control=_ui_audio_control_impl,
    ui_audio_sleep_timer=_ui_audio_sleep_timer_impl,
    ui_house_camera_snapshot=_ui_house_camera_snapshot_impl,
    ui_action=_ui_action_impl,
    ui_context_start=_ui_context_start_impl,
    ui_alarm_cancel=_ui_alarm_cancel_impl,
)
register_ui_routes(app)
register_orchestration_recovery_routes(app)
register_orchestration_routine_routes(app)
configure_routine_adapters(
    ui_action=_routine_ui_action_adapter,
    audiobook_start=_routine_audiobook_start_adapter,
    sleep_timer=_routine_sleep_timer_adapter,
    state_check=_routine_state_check_adapter,
    playback_check=_routine_playback_check_adapter,
)


def synthesize_speech(payload: TtsRequest) -> Response:
    return _synthesize_speech_with_provider(
        payload,
        brain_application_composition().tts_provider(),
    )


def _synthesize_speech_with_provider(payload: TtsRequest, provider: TtsProvider) -> Response:
    try:
        result = provider.synthesize(payload.text)
    except TtsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=result.audio_bytes,
        media_type=result.media_type,
        headers={"X-Oracle-TTS-Provider": result.provider},
    )


async def transcribe_audio(audio: UploadFile = File(...), source: str | None = Form(default=None)) -> SttResponse:
    return await _transcribe_audio_with_provider(
        audio,
        brain_application_composition().stt_provider(),
        source=source,
    )


async def _transcribe_audio_with_provider(
    audio: UploadFile,
    provider: SttProvider,
    *,
    source: str | None = None,
) -> SttResponse:
    audio_bytes = b""
    filename = audio.filename or "audio.wav"
    try:
        audio_bytes = await audio.read()
        result = provider.transcribe(audio_bytes, filename)
    except SttError as exc:
        safe_record_transcript(
            source_id=source,
            raw_transcript=None,
            normalized_text=None,
            stt_provider=_memory_stt_provider_name(provider),
            stt_model=_memory_stt_model(provider),
            confidence=None,
            fallback_used=False,
            final_status="failed",
            failure_stage="stt",
            payload=_memory_stt_payload(filename=filename, audio_bytes=audio_bytes, error_type=type(exc).__name__),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    safe_record_transcript(
        source_id=source,
        raw_transcript=result.text,
        normalized_text=None,
        stt_provider=result.provider,
        stt_model=_memory_stt_model(provider),
        confidence=None,
        fallback_used=False,
        final_status="succeeded",
        failure_stage=None,
        payload=_memory_stt_payload(filename=filename, audio_bytes=audio_bytes),
    )
    return SttResponse(text=result.text, provider=result.provider)


register_voice_routes(
    app,
    route_request=route_http_request,
    command_request=command_http_request,
    deferred_resume=deferred_resume,
    ingest_text=ingest_text_http_request,
    session_lookup=session_lookup,
    pending_alerts=pending_alerts,
    command_events=command_events,
    synthesize_speech=synthesize_speech,
    transcribe_audio=transcribe_audio,
)


def _memory_stt_model(provider: object | None) -> str | None:
    if provider is None:
        return None
    model = getattr(provider, "model", None)
    if model:
        return str(model)
    source_model = getattr(provider, "source_model", None)
    return str(source_model) if source_model else None


def _memory_stt_provider_name(provider: object | None) -> str | None:
    if provider is None:
        return None
    provider_name = getattr(provider, "provider", None)
    if provider_name:
        return str(provider_name)
    class_name = provider.__class__.__name__
    return class_name if class_name and class_name != "object" else None


def _memory_stt_payload(*, filename: str, audio_bytes: bytes, error_type: str | None = None) -> dict[str, object]:
    suffix = Path(filename or "audio.wav").suffix.lower() or ".wav"
    payload: dict[str, object] = {
        "endpoint": "/stt",
        "filename_suffix": suffix,
        "audio_bytes": len(audio_bytes),
    }
    if error_type:
        payload["error_type"] = error_type
    return payload
