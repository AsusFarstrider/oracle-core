from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .home_automation.state import list_canonical_states
from .runbook_kernel import RunbookRepository


_KIND = "routine"
_DOMAIN = "home_automation"


def admin_home_automation_runbooks(
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
) -> dict[str, object]:
    definitions = _canonical_definition_views(home_assistant_settings)
    version = 1
    repository = RunbookRepository()
    states = list_canonical_states()
    recent_runs = repository.list_runs(kind=_KIND, domain=_DOMAIN, limit=100)
    runs_by_definition: dict[str, list[dict[str, Any]]] = {}
    for run in recent_runs:
        runs_by_definition.setdefault(str(run.get("orchestration_id") or ""), []).append(
            _public_run(run)
        )
    definition_summaries = [
        _definition_summary(definition, runs_by_definition.get(runbook_id, []), states)
        for runbook_id, definition in sorted(definitions.items())
        if isinstance(definition, dict)
    ]
    return {
        "ok": True,
        "version": version,
        "summary": {
            "total": len(definition_summaries),
            "enabled": sum(1 for item in definition_summaries if item.get("enabled") is True),
            "runbook_mode": sum(
                1
                for item in definition_summaries
                if item.get("migration_mode") == "runbook"
            ),
            "direct_notification_mode": sum(
                1
                for item in definition_summaries
                if item.get("migration_mode") == "direct_notification"
            ),
            "active_runs": sum(1 for run in recent_runs if _run_is_active(run)),
            "run_count": len(recent_runs),
        },
        "definitions": definition_summaries,
    }


def admin_home_automation_runbook_detail(
    runbook_id: str,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
) -> dict[str, object]:
    definitions = _canonical_definition_views(home_assistant_settings)
    definition = definitions.get(str(runbook_id or "").strip())
    if not isinstance(definition, dict):
        raise HTTPException(status_code=404, detail="Home-automation runbook was not found.")
    repository = RunbookRepository()
    runs = [
        _public_run(run)
        for run in repository.list_runs(
            definition_id=str(definition.get("id") or runbook_id),
            kind=_KIND,
            domain=_DOMAIN,
            limit=25,
        )
    ]
    summary = _definition_summary(definition, runs, list_canonical_states())
    return {
        "ok": True,
        "definition": summary,
        "runs": runs,
        "summary": {
            "run_count": len(runs),
            "active_runs": sum(1 for run in runs if _run_is_active(run)),
            "last_status": runs[0].get("status") if runs else "never_run",
        },
    }


def register_admin_home_automation_routes(app: FastAPI) -> None:
    app.get("/api/admin/home-automation/runbooks")(
        admin_home_automation_runbooks_http
    )
    app.get("/api/admin/home-automation/runbooks/{runbook_id}")(
        admin_home_automation_runbook_detail_http
    )


def admin_home_automation_runbooks_http(request: Request) -> dict[str, object]:
    canonical = _canonical_composition(request)
    return admin_home_automation_runbooks(
        home_assistant_settings=canonical.runtime.home_assistant,
    )


def admin_home_automation_runbook_detail_http(
    runbook_id: str,
    request: Request,
) -> dict[str, object]:
    canonical = _canonical_composition(request)
    return admin_home_automation_runbook_detail(
        runbook_id,
        home_assistant_settings=canonical.runtime.home_assistant,
    )


def _canonical_composition(
    request: Request,
) -> CanonicalBrainApplicationComposition:
    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical configuration is unavailable.")
    return composition


def _canonical_definition_views(
    settings: HomeAssistantRuntimeSettings | None,
) -> dict[str, dict[str, object]]:
    definitions: dict[str, dict[str, object]] = {}
    if settings is not None and settings.enabled:
        for automation_id, runtime in settings.automations.items():
            definition = runtime.definition
            definitions[automation_id] = {
                "id": definition.id,
                "enabled": True,
                "migration_mode": definition.migration_mode,
                "subject": runtime.event_mapping.subject,
                "notification_type": definition.notification_type,
                "notification_delivery_enabled": definition.notification_delivery_enabled,
                "delay_seconds": definition.delay_seconds,
                "repeat_interval_seconds": int(definition.repeat_interval_seconds or 0),
                "max_notifications": definition.max_notifications,
                "max_lateness_seconds": definition.max_lateness_seconds,
                "provider_retry_seconds": definition.provider_retry_seconds,
                "max_provider_failures": definition.max_provider_failures,
            }
    return definitions


def _definition_summary(
    definition: dict[str, Any],
    runs: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    subject = str(definition.get("subject") or "")
    active_run = next((run for run in runs if _run_is_active(run)), None)
    return {
        "id": str(definition.get("id") or ""),
        "enabled": definition.get("enabled") is True,
        "migration_mode": str(definition.get("migration_mode") or ""),
        "subject": subject,
        "notification_type": str(definition.get("notification_type") or ""),
        "notification_delivery_enabled": definition.get("notification_delivery_enabled")
        is True,
        "delay_seconds": int(definition.get("delay_seconds") or 0),
        "repeat_interval_seconds": int(definition.get("repeat_interval_seconds") or 0),
        "max_notifications": int(definition.get("max_notifications") or 0),
        "latest_state": states.get(subject),
        "run_count": len(runs),
        "active_run": active_run,
        "latest_run": runs[0] if runs else None,
    }


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    state = dict(run.get("controller_state") or {})
    payload = dict(run.get("payload") or {})
    return {
        "run_id": str(run.get("run_id") or ""),
        "runbook_id": str(run.get("orchestration_id") or ""),
        "status": str(run.get("status") or ""),
        "summary": str(run.get("summary") or ""),
        "started_at": str(run.get("started_at") or ""),
        "completed_at": str(run.get("completed_at") or ""),
        "correlation_key": str(run.get("correlation_key") or ""),
        "cancellation_reason": str(run.get("cancellation_reason") or ""),
        "controller_state": {
            "phase": state.get("phase"),
            "next_due_at": state.get("next_due_at"),
            "cycle": state.get("cycle"),
            "notification_count": state.get("notification_count"),
            "submission_count": state.get("submission_count"),
            "provider_failure_count": state.get("provider_failure_count"),
        },
        "trigger_event_id": str(payload.get("trigger_event_id") or ""),
        "steps": [_public_step(step) for step in run.get("steps") or []],
    }


def _public_step(step: dict[str, Any]) -> dict[str, Any]:
    payload = dict(step.get("payload") or {})
    return {
        "step_id": str(step.get("step_id") or ""),
        "ordinal": int(step.get("ordinal") or 0),
        "status": str(step.get("status") or ""),
        "target_type": str(step.get("target_type") or ""),
        "target_id": str(step.get("target_id") or ""),
        "action_id": str(step.get("action_id") or ""),
        "summary": str(step.get("summary") or ""),
        "verification_status": str(step.get("verification_status") or ""),
        "started_at": str(step.get("started_at") or ""),
        "completed_at": str(step.get("completed_at") or ""),
        "due_at": payload.get("due_at"),
        "result_status": payload.get("result_status"),
    }


def _run_is_active(run: dict[str, Any]) -> bool:
    return str(run.get("status") or "") in {"running", "waiting"}
