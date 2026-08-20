from __future__ import annotations

from oracle_app.conversation_results import (
    GENERIC_SAFETY_REPLY,
    build_conversation_result,
    decode_deferred_satellite_playback,
)
from oracle_app.schemas import CommandRequest, CommandResponse, DispatchPlan, RouteResponse


def _response(*, status: str = "executed", result: dict | None = None, reply_text: str = "Done.") -> CommandResponse:
    return CommandResponse(
        route=RouteResponse(
            target="system",
            confidence=1.0,
            reason="test",
            normalized_text="test",
        ),
        dispatch=DispatchPlan(
            target="system",
            hook="system.test",
            payload={"source": "satellite-one", "session_id": "effective-one"},
            status=status,
            result=result or {"action": "test"},
        ),
        reply_text=reply_text,
        session_id="requested-one",
        effective_session_id="effective-one",
    )


def test_public_conversation_result_contains_only_finite_contract_fields() -> None:
    result = build_conversation_result(
        request=CommandRequest(text="test", source="satellite-one", session_id="requested-one"),
        response=_response(),
        trace_id="trace-one",
    )

    assert result.model_dump(mode="json") == {
        "reply_text": "Done.",
        "session_id": "effective-one",
        "source_id": "satellite-one",
        "status": "executed",
        "failure_code": None,
        "trace_id": "trace-one",
        "effects": {
            "follow_up": None,
            "satellite_playback": None,
            "deferred_satellite_playback": None,
            "ui_presentation": None,
        },
    }


def test_pending_result_exposes_typed_follow_up() -> None:
    result = build_conversation_result(
        request=CommandRequest(text="confirm", source="satellite-one", session_id="requested-one"),
        response=_response(
            status="pending_confirmation",
            result={"prompt": "Continue?"},
            reply_text="Continue?",
        ),
        trace_id="trace-two",
    )

    assert result.status == "pending_confirmation"
    assert result.effects.follow_up is not None
    assert result.effects.follow_up.kind == "confirmation"


def test_malformed_nonignored_result_uses_only_generic_safety_fallback() -> None:
    result = build_conversation_result(
        request=CommandRequest(text="test", source="satellite-one", session_id="requested-one"),
        response=_response(reply_text=""),
        trace_id="trace-three",
    )

    assert result.status == "failed"
    assert result.failure_code == "malformed_internal_result"
    assert result.reply_text == GENERIC_SAFETY_REPLY


def test_ignored_result_is_the_only_status_that_may_be_silent() -> None:
    result = build_conversation_result(
        request=CommandRequest(text="test", source="satellite-one", session_id="requested-one"),
        response=_response(result={"action": "ignore", "ignored": True}, reply_text=""),
        trace_id="trace-four",
    )

    assert result.status == "ignored"
    assert result.reply_text == ""


def test_alert_delivery_target_is_distinct_from_request_and_playback_sources() -> None:
    request = CommandRequest(
        text="set a timer",
        source="browser-client",
        playback_target_source_id="living-room",
        alert_delivery_target_source_id="office",
    )

    assert request.source == "browser-client"
    assert request.playback_target_source_id == "living-room"
    assert request.alert_delivery_target_source_id == "office"


def test_deferred_satellite_continuation_is_opaque_and_round_trips() -> None:
    deferred_session = {
        "resume_action": "play_media",
        "resume_args": {"uri": "plex://track/one"},
    }
    response = _response(result={"action": "routine_start", "deferred_session": deferred_session})
    result = build_conversation_result(
        request=CommandRequest(text="start bedtime", source="satellite-one", session_id="requested-one"),
        response=response,
        trace_id="trace-five",
    )
    effect = result.effects.deferred_satellite_playback
    assert effect is not None
    assert "resume_action" not in effect.continuation_token
    assert decode_deferred_satellite_playback(effect.continuation_token) == deferred_session
