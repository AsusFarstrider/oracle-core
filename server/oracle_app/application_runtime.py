from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from stt import attempt_stt_provider_warmup
from tts import maintain_tts_cache, tts_cache_diagnostics

from .alert_scheduler import alert_scheduler_loop
from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    BrainApplicationComposition,
    CanonicalBrainApplicationComposition,
)
from .config_reporting import findings_have_errors
from .configuration.bootstrap import (
    resolve_brain_configuration_startup,
    start_brain_configuration_host_local_runtime,
)
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .facts_cache import facts_cache_diagnostics, maintain_facts_cache
from .handlers.fallback_router import attempt_fallback_router_warmup
from .health_routes import canonical_health
from .home_automation import (
    home_automation_scheduler_loop,
    home_automation_scheduler_required,
)
from .installation_runtime import finalize_verified_startup
from .memory.identity_reconciliation import reconcile_identities
from .memory.correlation import correlation_context
from .memory.orchestrations import safe_reconcile_interrupted_orchestration_runs
from .memory.runtime import safe_record_event
from .memory.sources import default_internal_sources, seed_sources
from .network_control_local_restart import safe_complete_pending_local_host_restart
from .network_control_local_service_restart import safe_complete_pending_local_service_restart
from .network_control_results import (
    safe_reconcile_interrupted_network_controls,
    safe_restore_network_control_results_from_memory,
)
from .notifications.external_worker import (
    external_delivery_worker_loop,
    external_delivery_worker_required,
)
from .orchestration_routines import routine_scheduler_loop
from .satellite_projection_routes import configure_satellite_projection_routes
from .version import CORE_VERSION
from .wake_capture_upload_routes import (
    configure_wake_capture_upload_routes,
    wake_capture_archive_root_from_environment,
)


logger = logging.getLogger("oracle-brain.application.runtime")


def safe_seed_memory_sources(
    household_settings: HouseholdRuntimeSettings,
    satellite_settings: object | None = None,
) -> bool:
    if satellite_settings is not None:
        try:
            reconcile_identities(household_settings, satellite_settings)
        except Exception:
            logger.exception("oracle_memory_identity_reconciliation_failed")
            return False
        return True
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
        definitions.append(
            {
                "source_id": source.id,
                "source_type": "satellite" if source.type == "satellite" else "ui",
                "display_name": source.id,
                "payload": payload,
            }
        )
    try:
        seed_sources(definitions)
    except Exception:
        logger.exception("oracle_memory_source_seed_failed")
        return False
    return True


def install_brain_application_composition(
    target_app: FastAPI,
    composition: BrainApplicationComposition,
) -> None:
    setattr(target_app.state, BRAIN_APPLICATION_COMPOSITION_STATE_KEY, composition)
    configure_satellite_projection_routes(target_app, composition.projection_resolver)
    configure_wake_capture_upload_routes(
        target_app,
        composition.projection_resolver,
        wake_capture_archive_root_from_environment(),
    )


def brain_application_composition(target_app: FastAPI) -> BrainApplicationComposition:
    composition = getattr(target_app.state, BRAIN_APPLICATION_COMPOSITION_STATE_KEY, None)
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise RuntimeError("Brain application composition is not installed.")
    return composition


def _facts_cache_settings(composition: BrainApplicationComposition) -> dict[str, object] | object:
    information = composition.runtime.information
    if information is None:
        return {"cache_enabled": False, "cache_ttl_seconds": 0}
    return information.facts


def maintain_runtime_caches(composition: BrainApplicationComposition) -> dict[str, object]:
    """Run bounded domain maintenance without creating a background daemon."""

    facts = maintain_facts_cache(settings=_facts_cache_settings(composition))
    tts = maintain_tts_cache()
    return {"facts": facts.as_dict(), "tts": tts.as_dict()}


def admin_cache_diagnostics() -> dict[str, object]:
    composition = brain_application_composition(app)
    facts = facts_cache_diagnostics(settings=_facts_cache_settings(composition))
    tts = tts_cache_diagnostics()
    return {
        "status": "ok" if facts.healthy and tts.healthy else "degraded",
        "caches": {"facts": facts.as_dict(), "tts": tts.as_dict()},
        "cutover_dry_run": {
            "destructive": False,
            "tts_discard_entries": tts.entry_count,
            "tts_discard_bytes": tts.total_bytes,
            "facts_prune_candidates": (
                facts.expired_entries
                + facts.malformed_entries
                + facts.legacy_entries
                + max(0, facts.entry_count - facts.limit_entries)
            ),
        },
    }


@asynccontextmanager
async def lifespan(target_app: FastAPI):
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
        safe_seed_memory_sources(
            startup_composition.runtime.household,
            startup_composition.runtime.satellites,
        )
        reconciled_orchestration_interruptions = safe_reconcile_interrupted_orchestration_runs()
        reconciled_network_control_interruptions = safe_reconcile_interrupted_network_controls()
        restored_network_control_results = safe_restore_network_control_results_from_memory()
        findings: list[dict[str, object]] = []
        if findings_have_errors(findings):
            raise RuntimeError("Brain config validation failed")
        local_restart_completion = safe_complete_pending_local_host_restart(
            canonical_execution=startup_composition.network_execution,
        )
        attempt_stt_provider_warmup(startup_composition.core_consumers.stt_provider)
        attempt_fallback_router_warmup(startup_composition.core_consumers.inference)
        cache_maintenance = maintain_runtime_caches(startup_composition)
        configuration_host_local_runtime = start_brain_configuration_host_local_runtime(startup=startup)
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
                "config_warning_count": sum(
                    1 for finding in findings if str(finding.get("severity") or "").lower() == "warning"
                ),
                "config_error_count": sum(
                    1 for finding in findings if str(finding.get("severity") or "").lower() == "error"
                ),
                "fallback_router_enabled": True,
                "warmup_path": "fallback_router",
                "configuration_mode": startup_composition.mode,
                "config_revision": startup_composition.runtime.effective_config.config_revision,
                "reconciled_network_control_interruption_count": reconciled_network_control_interruptions,
                "reconciled_orchestration_interruption_count": reconciled_orchestration_interruptions,
                "restored_network_control_result_count": restored_network_control_results,
                "local_restart_completion_status": str(local_restart_completion.get("status") or "none"),
                "cache_maintenance": cache_maintenance,
            },
        )
        safe_complete_pending_local_service_restart()
        try:
            if startup.installation_layout is not None:
                if canonical_health(startup_composition).status != "ok":
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
                        required_config_revision=startup_composition.routine_execution.settings.config_revision,
                    )
                )
            )
        if startup_composition.audiobook_execution is not None:
            background_tasks.append(
                asyncio.create_task(
                    alert_scheduler_loop(
                        audiobook_execution=startup_composition.audiobook_execution,
                        satellites=startup_composition.runtime.satellites,
                    )
                )
            )
        if home_automation_scheduler_required(
            startup_composition.runtime.home_assistant
        ):
            background_tasks.append(
                asyncio.create_task(
                    home_automation_scheduler_loop(
                        home_assistant_settings=startup_composition.runtime.home_assistant,
                        notification_submitter=startup_composition.notification_execution.submit,
                    )
                )
            )
        if external_delivery_worker_required(
            startup_composition.notification_execution
        ):
            background_tasks.append(
                asyncio.create_task(
                    external_delivery_worker_loop(
                        canonical_execution=startup_composition.notification_execution,
                    )
                )
            )
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
    description=(
        "A small routing service that decides whether Oracle should use Home "
        "Assistant, music, or Ollama."
    ),
    lifespan=lifespan,
)
