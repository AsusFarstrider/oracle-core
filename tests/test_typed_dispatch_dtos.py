from __future__ import annotations

import pytest
from pydantic import ValidationError

from oracle_app.dispatch import build_dispatch_plan, execute_dispatch
from oracle_app.dispatch_dtos import (
    TARGET_PAYLOAD_MODELS,
    DispatchContext,
    target_outcome,
    validate_target_payload,
)
from oracle_app.handlers.registry import HandlerRegistry
from oracle_app.schemas import CommandRequest, DispatchPlan, RouteResponse


def test_every_route_target_has_one_owned_payload_schema() -> None:
    assert set(TARGET_PAYLOAD_MODELS) == {
        "audiobook", "calendar", "facts", "fallback_router", "home_assistant",
        "music", "network", "news", "system", "weather",
    }
    assert len(set(TARGET_PAYLOAD_MODELS.values())) == 10


def test_target_payload_rejects_arbitrary_extension_fields() -> None:
    dispatch = DispatchPlan(
        target="facts",
        hook="facts.lookup",
        payload={"query": "who", "arbitrary_extension": True},
        status="planned",
    )
    with pytest.raises(ValidationError):
        validate_target_payload(dispatch)


def test_context_keeps_request_playback_and_alert_identities_distinct() -> None:
    payload = TARGET_PAYLOAD_MODELS["music"].model_validate({
        "source": "browser",
        "session_id": "session",
        "playback_target_source_id": "living-room",
        "alert_delivery_target_source_id": "office",
    })
    assert payload.context() == DispatchContext(
        source_id="browser",
        session_id="session",
        playback_target_source_id="living-room",
        alert_delivery_target_source_id="office",
    )


def test_unknown_system_operation_is_explicit_and_never_refreshes_cache() -> None:
    route = RouteResponse(
        target="system",
        confidence=0.5,
        reason="fallback proposed system",
        normalized_text="unrecognized system operation",
    )
    dispatch = build_dispatch_plan(CommandRequest(text=route.normalized_text), route)
    assert dispatch.hook == "system.unknown_operation"
    assert dispatch.payload["action"] == "unknown_system_operation"

    executed = execute_dispatch(dispatch, registry=HandlerRegistry())
    outcome = target_outcome(executed)
    assert outcome.status == "failed"
    assert outcome.failure is not None
    assert outcome.failure.code == "unknown_dispatch_target"


def test_failed_outcome_has_typed_failure_primitive() -> None:
    outcome = target_outcome(DispatchPlan(
        target="system",
        hook="system.unknown_operation",
        payload={"action": "unknown_system_operation"},
        status="failed",
        result={
            "error": "unknown_system_action",
            "detail": "unknown_system_operation",
            "failure_class": "domain_failure",
            "owning_component": "brain.system",
        },
    ))
    assert outcome.failure is not None
    assert outcome.failure.model_dump() == {
        "code": "unknown_system_action",
        "detail": "unknown_system_operation",
        "failure_class": "domain_failure",
        "owning_component": "brain.system",
    }
