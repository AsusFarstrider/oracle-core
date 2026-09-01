from __future__ import annotations

from types import SimpleNamespace

from oracle_app.dispatch import build_dispatch_plan
from oracle_app.handlers.system import SystemHandler
from oracle_app.schemas import CommandRequest, DispatchPlan, RouteResponse
from oracle_app.session_state import clear_session_state, get_pending_state, get_utility_context


def _handler() -> SystemHandler:
    return SystemHandler(
        household_settings=SimpleNamespace(household=SimpleNamespace(timezone="America/New_York")),  # type: ignore[arg-type]
    )


def _dispatch(text: str, *, source: str = "test-source", session_id: str = "temporal-session") -> DispatchPlan:
    return DispatchPlan(
        target="system",
        hook="system.temporal",
        payload={"action": "temporal", "text": text, "source": source, "session_id": session_id},
        status="planned",
    )


def test_extended_temporal_route_builds_owner_specific_dispatch() -> None:
    request = CommandRequest(text="what time is it in London", source="test-source", session_id="route-session")
    route = RouteResponse(
        target="system",
        confidence=0.94,
        reason="Matched deterministic time/date query",
        normalized_text="what time is it in london",
    )
    dispatch = build_dispatch_plan(request, route)
    assert dispatch.hook == "system.temporal"
    assert dispatch.payload["action"] == "temporal"
    assert dispatch.payload["text"] == "what time is it in london"


def test_handler_records_bounded_typed_temporal_context() -> None:
    source, session_id = "test-source", "typed-temporal"
    clear_session_state(source, session_id)
    result = _handler().handle(_dispatch("what time is it in london", source=source, session_id=session_id), object())
    assert result.status == "executed"
    assert result.result["temporal"]["timezone"] == "Europe/London"
    context = get_utility_context(source, session_id, kind="temporal")
    assert context is not None
    assert context["payload"]["subject_type"] == "world_time"
    assert context["payload"]["timezone"] == "Europe/London"


def test_typed_temporal_context_resolves_what_about_followup() -> None:
    source, session_id = "test-source", "temporal-followup"
    clear_session_state(source, session_id)
    handler = _handler()
    first = handler.handle(_dispatch("what time is it in london", source=source, session_id=session_id), object())
    second = handler.handle(_dispatch("what about tokyo", source=source, session_id=session_id), object())
    assert first.status == "executed"
    assert second.status == "executed"
    assert second.result["temporal"]["timezone"] == "Asia/Tokyo"


def test_ambiguous_location_uses_canonical_pending_utility_state() -> None:
    source, session_id = "test-source", "temporal-clarification"
    clear_session_state(source, session_id)
    handler = _handler()
    first = handler.handle(_dispatch("what time is it in washington", source=source, session_id=session_id), object())
    assert first.status == "pending_clarification"
    pending = get_pending_state(source, session_id, domain="utilities")
    assert pending is not None
    assert pending["context_kind"] == "temporal"
    assert pending["options"] == ["Washington DC", "Washington state"]

    resolved = handler.handle(_dispatch("washington dc", source=source, session_id=session_id), object())
    assert resolved.status == "executed"
    assert resolved.result["temporal"]["timezone"] == "America/New_York"
    assert get_pending_state(source, session_id, domain="utilities") is None


def test_invalid_clarification_reply_preserves_pending_state() -> None:
    source, session_id = "test-source", "temporal-clarification-retry"
    clear_session_state(source, session_id)
    handler = _handler()
    handler.handle(_dispatch("what time is it in georgia", source=source, session_id=session_id), object())
    retried = handler.handle(_dispatch("the other one", source=source, session_id=session_id), object())
    assert retried.status == "pending_clarification"
    assert "Georgia the country" in retried.result["speech"]
    assert get_pending_state(source, session_id, domain="utilities") is not None


def test_partially_repeated_ambiguous_name_does_not_guess() -> None:
    source, session_id = "test-source", "temporal-clarification-still-ambiguous"
    clear_session_state(source, session_id)
    handler = _handler()
    handler.handle(_dispatch("what time is it in washington", source=source, session_id=session_id), object())
    retried = handler.handle(_dispatch("washington", source=source, session_id=session_id), object())
    assert retried.status == "pending_clarification"
    assert get_pending_state(source, session_id, domain="utilities") is not None
