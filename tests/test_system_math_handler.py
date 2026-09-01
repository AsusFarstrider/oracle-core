from __future__ import annotations

from types import SimpleNamespace

from oracle_app.dispatch import build_dispatch_plan
from oracle_app.handlers.system import SystemHandler
from oracle_app.schemas import CommandRequest, DispatchPlan, RouteResponse
from oracle_app.session_state import clear_session_state, get_utility_context


def _handler() -> SystemHandler:
    return SystemHandler(
        household_settings=SimpleNamespace(household=SimpleNamespace(timezone="America/New_York")),  # type: ignore[arg-type]
    )


def _dispatch(text: str, *, source: str = "test-source", session_id: str = "math-session") -> DispatchPlan:
    return DispatchPlan(
        target="system",
        hook="system.calculation",
        payload={"action": "calculation", "text": text, "source": source, "session_id": session_id},
        status="planned",
    )


def test_math_route_builds_owner_specific_dispatch() -> None:
    request = CommandRequest(text="what is three quarters of 80", source="test-source", session_id="route-session")
    route = RouteResponse(
        target="system",
        confidence=0.9,
        reason="Matched math query",
        normalized_text="what is three quarters of 80",
    )
    dispatch = build_dispatch_plan(request, route)
    assert dispatch.hook == "system.calculation"
    assert dispatch.payload["action"] == "calculation"


def test_handler_records_exact_typed_math_context_and_uses_it_for_followup() -> None:
    source, session_id = "test-source", "typed-math"
    clear_session_state(source, session_id)
    handler = _handler()

    first = handler.handle(_dispatch("what is 18% of 240", source=source, session_id=session_id), object())
    assert first.status == "executed"
    assert first.result["speech"] == "18% of 240 is 43.2."
    context = get_utility_context(source, session_id, kind="calculation")
    assert context is not None
    assert context["payload"] == {"value": "216/5", "display_text": "43.2"}

    second = handler.handle(_dispatch("add that to 240", source=source, session_id=session_id), object())
    assert second.status == "executed"
    assert second.result["speech"] == "283.2."
    context = get_utility_context(source, session_id, kind="calculation")
    assert context is not None
    assert context["payload"]["value"] == "1416/5"

    third = handler.handle(_dispatch("divide that by four", source=source, session_id=session_id), object())
    assert third.status == "executed"
    assert third.result["speech"] == "70.8."


def test_handler_rejects_contextual_reference_outside_session() -> None:
    source, session_id = "test-source", "missing-math-context"
    clear_session_state(source, session_id)
    result = _handler().handle(_dispatch("divide that by four", source=source, session_id=session_id), object())
    assert result.status == "failed"
    assert result.result["error"] == "calculation_unavailable"
    assert "no longer available" in result.result["detail"]


def test_handler_does_not_replace_prior_context_after_rejected_input() -> None:
    source, session_id = "test-source", "rejected-math"
    clear_session_state(source, session_id)
    handler = _handler()
    handler.handle(_dispatch("what is 2 plus 2", source=source, session_id=session_id), object())
    before = get_utility_context(source, session_id, kind="calculation")

    rejected = handler.handle(_dispatch("what is 2 plus bananas 3", source=source, session_id=session_id), object())
    assert rejected.status == "failed"
    assert get_utility_context(source, session_id, kind="calculation") == before
