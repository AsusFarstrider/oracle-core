from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request

from canonical_test_support import neutral_brain_runtime_settings
from oracle_app.application_command import record_repeat_eligible_output
from oracle_app.handlers.system import SystemHandler
from oracle_app.schemas import DispatchPlan
from oracle_app.schemas import SatelliteAlertAcknowledgeRequest
from oracle_app.satellite_alert_routes import satellite_alert_acknowledge
from oracle_app.session_state import clear_all_sessions, get_utility_context, resolve_request_session, set_utility_context
from oracle_app.system_help import classify_help_request, render_help
from oracle_app.system_intents import classify_system_intent


class _Registry:
    def __init__(self, handlers=None):
        self.handlers = handlers or {}

    def get(self, target):
        return self.handlers.get(target)


def _dispatch(action: str, *, text: str = "", source: str = "office", session_id: str = "session-1") -> DispatchPlan:
    return DispatchPlan(
        target="system",
        hook=f"system.{action}",
        payload={"action": action, "text": text, "source": source, "session_id": session_id},
        status="pending_integration",
    )


def _handler() -> SystemHandler:
    return SystemHandler(neutral_brain_runtime_settings().household)


def setup_function() -> None:
    clear_all_sessions()


def teardown_function() -> None:
    clear_all_sessions()


def test_repeat_phrasings_and_help_phrasings_are_deterministic() -> None:
    for text in ("repeat that", "say that again", "what did you say", "can you repeat that"):
        assert classify_system_intent(text).action == "repeat"
    for text in ("help", "what can you do", "what can you do with alarms", "can you do conversions"):
        assert classify_system_intent(text).action == "help"


def test_repeat_reads_only_current_source_and_session_and_does_not_replace_itself() -> None:
    resolve_request_session("office", "session-1")
    set_utility_context(
        "office", "session-1", kind="repeat_output",
        payload={"reply_text": "The answer is 12.", "route_target": "system", "action": "calculation"},
    )
    set_utility_context(
        "kitchen", "session-1", kind="repeat_output",
        payload={"reply_text": "Private kitchen reply.", "route_target": "system", "action": "help"},
    )

    result = _handler().handle(_dispatch("repeat"), _Registry())

    assert result.status == "executed"
    assert result.result["speech"] == "The answer is 12."
    assert get_utility_context("office", "session-1", kind="repeat_output")["payload"]["reply_text"] == "The answer is 12."


def test_repeat_has_clean_no_prior_reply_and_expires_with_session() -> None:
    with patch("oracle_app.session_state.time.monotonic", return_value=100.0):
        resolve_request_session("office", "session-1")
    with patch("oracle_app.session_state.time.monotonic", return_value=191.0):
        result = _handler().handle(_dispatch("repeat"), _Registry())
    assert result.result["repeated"] is False
    assert "don't have a recent reply" in result.result["speech"]


def test_repeat_eligibility_excludes_provider_text_secrets_and_mutations() -> None:
    resolve_request_session("office", "session-1")
    calculation = _dispatch("calculation")
    calculation.status = "executed"
    calculation.result = {"action": "calculation"}
    assert record_repeat_eligible_output(
        source="office", session_id="session-1", route_target="system",
        dispatch=calculation, reply_text="The answer is 12.",
    )

    facts = DispatchPlan(target="facts", hook="facts.lookup", payload={}, status="executed", result={"action": "lookup"})
    assert not record_repeat_eligible_output(
        source="office", session_id="session-1", route_target="facts",
        dispatch=facts, reply_text="Arbitrary provider answer.",
    )
    assert not record_repeat_eligible_output(
        source="office", session_id="session-1", route_target="system",
        dispatch=calculation, reply_text="api key: do-not-repeat",
    )
    mutation = _dispatch("alerts")
    mutation.status = "executed"
    mutation.result = {"action": "alerts", "alerts": {"operation": "create"}}
    assert not record_repeat_eligible_output(
        source="office", session_id="session-1", route_target="system",
        dispatch=mutation, reply_text="Timer set.",
    )
    assert get_utility_context("office", "session-1", kind="repeat_output")["payload"]["reply_text"] == "The answer is 12."


def test_help_catalog_reports_enabled_disabled_deferred_and_task_guidance() -> None:
    enabled_calendar = SimpleNamespace(canonical_execution=object())
    registry = _Registry({"calendar": enabled_calendar, "music": SimpleNamespace(canonical_execution=None)})
    assert "read the configured calendar" in render_help("capability", "calendar", registry=registry)["speech"]
    assert "not enabled" in render_help("capability", "music", registry=registry)["speech"]
    assert "after V2" in render_help("capability", "stopwatch", registry=registry)["speech"]
    assert "only tomorrow's occurrence" in render_help("task", "change_tomorrow_alarm", registry=registry)["speech"]


def test_help_parser_covers_task_and_adversarial_can_you_forms() -> None:
    assert classify_help_request("how do i cancel my timer") == ("task", "cancel_timer")
    assert classify_help_request("how do i change only tomorrow's alarm") == ("task", "change_tomorrow_alarm")
    assert classify_help_request("can you set timers") == ("capability", "timers")
    assert classify_help_request("can you start a stopwatch") == ("capability", "stopwatch")
    assert classify_help_request("can you flip a coin") == ("capability", "randomizer")
    assert classify_help_request("can you do teleportation") == ("unsupported", "teleportation")
    assert classify_help_request("can you set a timer for five minutes") is None
    assert classify_help_request("can you tell me the weather tomorrow") is None


def test_bounded_courtesy_and_deferred_utility_fail_honestly() -> None:
    greeting = _handler().handle(_dispatch("courtesy", text="hello"), _Registry())
    thanks = _handler().handle(_dispatch("courtesy", text="thank you"), _Registry())
    stopwatch = _handler().handle(_dispatch("unsupported_utility", text="start a stopwatch"), _Registry())
    assert greeting.result["speech"] == "Hello. How can I help?"
    assert thanks.result["speech"] == "You're welcome."
    assert stopwatch.status == "failed"
    assert "not supported yet" in stopwatch.result["speech"]


def test_failure_help_uses_typed_prior_failure_context() -> None:
    resolve_request_session("office", "session-1")
    set_utility_context(
        "office", "session-1", kind="repeat_output",
        payload={
            "reply_text": "I couldn't complete that request.", "route_target": "system",
            "action": "calculation", "status": "failed", "error": "calculation_unavailable",
        },
    )
    result = _handler().handle(_dispatch("help", text="help with that"), _Registry())
    assert result.result["help_kind"] == "failure"
    assert "15 percent of 80" in result.result["speech"]


def test_completed_reminder_delivery_seeds_only_authenticated_session_repeat_context() -> None:
    alert = SimpleNamespace(
        alert_id="reminder-1", kind="reminder", message="Reminder: stretch.", occurrence_id=None,
    )
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    with patch(
        "oracle_app.satellite_alert_routes._authenticated_alert_source",
        return_value=(SimpleNamespace(), "office"),
    ), patch("oracle_app.satellite_alert_routes.acknowledge_alert", return_value=alert):
        satellite_alert_acknowledge(
            "reminder-1",
            SatelliteAlertAcknowledgeRequest(
                source_id="office", lease_id="lease-1", status="completed", session_id="alert-session"
            ),
            request,
        )
    stored = get_utility_context("office", "alert-session", kind="repeat_output")
    assert stored["payload"]["reply_text"] == "Reminder: stretch."
    assert get_utility_context("kitchen", "alert-session", kind="repeat_output") is None
