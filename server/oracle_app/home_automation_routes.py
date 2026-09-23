from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request, status

from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .home_automation import handle_home_assistant_event, normalize_home_assistant_trigger_evidence
from .home_automation.state import observe_canonical_state
from .memory.runtime import safe_record_event
from .memory.orchestration_trigger_evidence import (
    complete_trigger_transition,
    observe_trigger_transition,
    pending_trigger_transition,
)
from .orchestration_routine_canonical import CanonicalRoutineExecution
from .schemas import HomeAssistantEventIngressRequest, HomeAssistantEventIngressResponse


def receive_home_assistant_event(
    payload: HomeAssistantEventIngressRequest,
    request: Request,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings,
    routine_execution: CanonicalRoutineExecution | None = None,
) -> HomeAssistantEventIngressResponse:
    configured_token = home_assistant_settings.event_ingress_credential
    if not configured_token:
        raise HTTPException(status_code=503, detail="Home Assistant event ingress is not configured.")
    provided_token = _bearer_token(request.headers.get("Authorization"))
    if not provided_token or not secrets.compare_digest(provided_token, configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Home Assistant event ingress credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = handle_home_assistant_event(
            entity_id=payload.entity_id,
            state=payload.state,
            event_id=payload.event_id,
            occurred_at=payload.occurred_at,
            home_assistant_settings=home_assistant_settings,
        )
    if routine_execution is not None:
        for kind, evidence_id, canonical_state in normalize_home_assistant_trigger_evidence(
            entity_id=payload.entity_id,
            state=payload.state,
            home_assistant_settings=home_assistant_settings,
        ):
            accepted = result.get("reason") != "stale_or_duplicate_event" if kind == "home_event" else observe_canonical_state(
                subject=f"presence:{evidence_id}", event_id=payload.event_id,
                state=canonical_state, observed_at=payload.occurred_at or datetime.now(UTC),
            )
            occurred = (
                observe_trigger_transition(
                    kind=kind,
                    evidence_id=evidence_id,
                    state=canonical_state,
                    observed_at=(payload.occurred_at or datetime.now(UTC)).isoformat(),
                    occurrence_id=payload.event_id,
                    emit_initial=True,
                )
                if accepted
                else pending_trigger_transition(kind=kind, evidence_id=evidence_id)
            )
            if occurred is None:
                continue
            try:
                routine_execution.activate_evidence(
                    kind=kind,
                    evidence_id=evidence_id,
                    state=occurred["state"],
                    occurrence_id=occurred["occurrence_id"],
                )
                complete_trigger_transition(
                    occurred["occurrence_id"], kind=kind, evidence_id=evidence_id
                )
            except Exception as exc:
                safe_record_event(
                    "orchestration_automatic_trigger_failed",
                    severity="error",
                    source_id="brain",
                    provider="home_assistant",
                    domain="orchestration",
                    status="failed",
                    correlation_id=occurred["occurrence_id"],
                    payload={
                        "kind": kind,
                        "evidence_id": evidence_id,
                        "error_class": type(exc).__name__,
                    },
                )
    return HomeAssistantEventIngressResponse(**result)


def receive_home_assistant_event_http(
    payload: HomeAssistantEventIngressRequest,
    request: Request,
) -> HomeAssistantEventIngressResponse:
    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical configuration is unavailable.")
    return receive_home_assistant_event(
        payload,
        request,
        home_assistant_settings=composition.runtime.home_assistant,
        routine_execution=composition.routine_execution,
    )


def register_home_automation_routes(app: FastAPI) -> None:
    app.post(
        "/api/integrations/home-assistant/events",
        response_model=HomeAssistantEventIngressResponse,
    )(receive_home_assistant_event_http)


def _bearer_token(value: str | None) -> str:
    scheme, separator, token = str(value or "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()
