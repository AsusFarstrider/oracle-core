from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from oracle_app.alert_lifecycle import reconcile_alert_lifecycle
from oracle_app import alerts, application_ui, state
from oracle_app.handlers import HandlerRegistry, SystemHandler
from oracle_app.memory.alert_lifecycle import list_alert_occurrences, list_alert_schedules
from oracle_app.memory.sources import seed_sources
from oracle_app.timers import build_timer_state, dismiss_timer, execute_timer_command
from oracle_app.schemas import SatelliteAlertActionRequest, UiTimerActionRequest
from oracle_app import satellite_alert_routes
from oracle_app.dispatch import build_dispatch_plan
from oracle_app.schemas import CommandRequest, DispatchPlan, RouteResponse
from oracle_app.session_state import clear_session_state, set_utility_context


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


class Household:
    household = SimpleNamespace(id="home", timezone="America/New_York")
    config_revision = "config-7"

    def __init__(self) -> None:
        self.rooms = {
            "kitchen": SimpleNamespace(id="kitchen", display_name="Kitchen"),
            "bedroom": SimpleNamespace(id="bedroom", display_name="Bedroom"),
        }
        self.sources = {
            "kitchen-source": ("kitchen", "phil"),
            "kitchen-display": ("kitchen", None),
            "bedroom-source": ("bedroom", "resident"),
        }

    def room(self, room_id):
        return self.rooms.get(room_id)

    def source(self, source_id):
        return SimpleNamespace(id=source_id) if source_id in self.sources else None

    def resolve_room_id(self, value):
        normalized = str(value or "").casefold().replace("'s", "").replace(" room", "").strip()
        return {"kitchen": "kitchen", "bedroom": "bedroom"}.get(normalized)

    def configured_associated_room_id(self, source_id):
        return self.sources[source_id][0]

    def configured_associated_user_id(self, source_id):
        return self.sources[source_id][1]


class Fleet:
    enabled_satellite_ids_by_source = {
        "kitchen-source": "one", "kitchen-display": "two", "bedroom-source": "three"
    }

    def satellite_for_source(self, source_id):
        return SimpleNamespace(alert_capable=True) if source_id in self.enabled_satellite_ids_by_source else None


@pytest.fixture
def timer_env(tmp_path: Path):
    db_path = tmp_path / "timers.sqlite3"
    seed_sources(
        [
            {"source_id": source, "source_type": "satellite", "display_name": source, "payload": {}}
            for source in Fleet.enabled_satellite_ids_by_source
        ],
        db_path=db_path,
    )
    return Household(), Fleet(), db_path


def run(text, timer_env, *, source="kitchen-source", context=None, confirmed=False, now=NOW):
    household, fleet, db_path = timer_env
    return execute_timer_command(
        text,
        source_id=source,
        session_id="session-1",
        household=household,
        satellites=fleet,
        context=context,
        confirmed=confirmed,
        now=now,
        db_path=db_path,
    )


def test_named_compound_and_fractional_timer_creation_uses_semantic_lifecycle(timer_env) -> None:
    speech, details = run("set a pasta timer for one hour and a half", timer_env)
    assert speech == "Pasta timer set for 1 hour, 30 minutes."
    assert details["name"] == "pasta"
    assert details["duration_seconds"] == 5400
    schedules = list_alert_schedules(db_path=timer_env[2])
    occurrences = list_alert_occurrences(db_path=timer_env[2])
    assert [(item.kind, item.target_scope, item.metadata["name"]) for item in schedules] == [
        ("timer", "local", "pasta")
    ]
    assert occurrences[0].occurrence_id == details["occurrence_id"]


@pytest.mark.parametrize(
    ("utterance", "seconds"),
    [
        ("start a 30-second timer", 30),
        ("give me a timer for an hour and 15 minutes", 4500),
        ("set a timer for 1.5 hours", 5400),
        ("start a countdown for five minutes", 300),
    ],
)
def test_natural_timer_creation_forms(utterance, seconds, timer_env) -> None:
    speech, details = run(utterance, timer_env)
    assert details["duration_seconds"] == seconds
    assert speech.endswith(".")


def test_room_and_house_timer_are_one_occurrence_with_fanout(timer_env) -> None:
    _, room = run("set a timer in the kitchen for 20 minutes", timer_env)
    _, house = run("set a house timer for 30 minutes", timer_env)
    household, fleet, db_path = timer_env
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(minutes=21), db_path=db_path)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(minutes=31), db_path=db_path)
    room_occurrence = next(
        item
        for item in list_alert_occurrences(db_path=db_path)
        if item.occurrence_id == room["occurrence_id"]
    )
    house_occurrence = next(
        item
        for item in list_alert_occurrences(db_path=db_path)
        if item.occurrence_id == house["occurrence_id"]
    )
    assert len(room_occurrence.metadata["destinations"]) == 2
    assert len(house_occurrence.metadata["destinations"]) == 3


def test_multiple_timers_require_selection_and_named_adjustment_is_contextual(timer_env) -> None:
    _, pasta = run("set a pasta timer for 12 minutes", timer_env)
    run("set a laundry timer for 45 minutes", timer_env)
    speech, details = run("how much time is left on my timer", timer_env)
    assert speech.startswith("Which timer did you mean")
    assert details["status"] == "clarification_required"
    context = {"payload": {"occurrence_id": pasta["occurrence_id"], "duration_unit": "minute"}}
    speech, details = run("add five minutes to it", timer_env, context=context)
    assert speech == "Your pasta timer now has 17 minutes remaining."
    speech, details = run("actually make it thirty", timer_env, context=context)
    assert speech == "Your pasta timer now has 30 minutes remaining."


def test_subtraction_rejects_zero_or_past_and_cancel_all_requires_confirmation(timer_env) -> None:
    run("set a timer for 5 minutes", timer_env)
    with pytest.raises(ValueError, match="now or in the past"):
        run("take six minutes off the timer", timer_env)
    speech, details = run("cancel all timers", timer_env)
    assert details["status"] == "confirmation_required"
    speech, details = run("cancel all timers", timer_env, confirmed=True)
    assert speech == "Canceled 1 active timer."
    assert build_timer_state(
        source_id="kitchen-source", household=timer_env[0], satellites=timer_env[1], now=NOW, db_path=timer_env[2]
    )["count"] == 0


def test_named_and_duration_selection_support_status_restart_and_targeted_cancel(timer_env) -> None:
    run("set a pasta timer for 12 minutes", timer_env)
    run("set a timer for 10 minutes", timer_env)
    speech, details = run("when does my pasta timer end", timer_env)
    assert "pasta timer ends in 12 minutes" in speech
    speech, details = run("restart the pasta timer", timer_env, now=NOW + timedelta(minutes=2))
    assert speech == "Your pasta timer now has 12 minutes remaining."
    speech, details = run("cancel the 10-minute timer", timer_env)
    assert speech == "Canceled your timer."
    speech, details = run("what timers do i have", timer_env)
    assert details["count"] == 1
    assert "pasta timer" in speech


def test_cancel_all_confirmation_uses_existing_session_confirmation_authority(
    timer_env, monkeypatch
) -> None:
    household, fleet, db_path = timer_env
    run("set a pasta timer for 12 minutes", timer_env)
    run("set a laundry timer for 45 minutes", timer_env)
    monkeypatch.setattr(alerts, "ALERT_DB_PATH", db_path)
    handler = SystemHandler(household, satellite_settings=fleet)
    registry = HandlerRegistry()
    registry.register(handler)
    pending = registry.execute(
        DispatchPlan(
            target="system",
            hook="system.alerts",
            payload={
                "action": "alerts",
                "text": "cancel all timers",
                "source": "kitchen-source",
                "session_id": "confirm-all",
            },
            status="planned",
        )
    )
    assert pending.status == "pending_confirmation"
    assert state.load_pending_confirmation("kitchen-source", "confirm-all") is not None
    confirmed = registry.execute(
        DispatchPlan(
            target="system",
            hook="system.confirm_pending",
            payload={
                "action": "confirm_pending",
                "source": "kitchen-source",
                "session_id": "confirm-all",
            },
            status="planned",
        )
    )
    assert confirmed.status == "executed"
    assert build_timer_state(
        source_id="kitchen-source",
        household=household,
        satellites=fleet,
        now=NOW,
        db_path=db_path,
    )["count"] == 0


def test_due_timer_is_late_bounded_and_destination_dismissal_converges_house(timer_env) -> None:
    _, details = run("set a house timer for 30 seconds", timer_env)
    household, fleet, db_path = timer_env
    due = NOW + timedelta(seconds=31)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=due, db_path=db_path)
    state = build_timer_state(
        source_id="bedroom-source", household=household, satellites=fleet, now=due, db_path=db_path
    )
    assert state["ringing"][0]["late_seconds"] == 1
    dismissed = dismiss_timer(
        details["occurrence_id"], source_id="bedroom-source", household=household,
        satellites=fleet, now=due, db_path=db_path,
    )
    assert dismissed["action"] == "dismiss"
    assert build_timer_state(
        source_id="kitchen-source", household=household, satellites=fleet, now=due, db_path=db_path
    )["count"] == 0


def test_timer_past_grace_becomes_missed_without_delivery(timer_env) -> None:
    _, details = run("set a timer for 30 seconds", timer_env)
    household, fleet, db_path = timer_env
    reconcile_alert_lifecycle(
        household=household, satellites=fleet, now=NOW + timedelta(minutes=11), db_path=db_path
    )
    occurrence = next(
        item
        for item in list_alert_occurrences(db_path=db_path)
        if item.occurrence_id == details["occurrence_id"]
    )
    assert occurrence.status == "missed"
    assert build_timer_state(
        source_id="kitchen-source", household=household, satellites=fleet,
        now=NOW + timedelta(minutes=11), db_path=db_path,
    )["count"] == 0


def test_authenticated_satellite_timer_state_and_action_are_typed(monkeypatch) -> None:
    composition = SimpleNamespace(
        runtime=SimpleNamespace(household=object(), satellites=object())
    )
    monkeypatch.setattr(
        satellite_alert_routes,
        "_authenticated_alert_source",
        lambda request, claimed: (composition, "kitchen-source"),
    )
    monkeypatch.setattr(
        satellite_alert_routes,
        "build_timer_state",
        lambda **kwargs: {
            "source_id": "kitchen-source", "generated_at": NOW.isoformat(),
            "count": 1, "timers": [{"occurrence_id": "occurrence-1"}],
            "ringing": [{"occurrence_id": "occurrence-1"}],
        },
    )
    monkeypatch.setattr(
        satellite_alert_routes,
        "dismiss_timer",
        lambda occurrence_id, **kwargs: {
            "ok": True, "action": "dismiss", "occurrence_id": occurrence_id
        },
    )
    state = satellite_alert_routes.satellite_alert_state("kitchen-source", object())
    assert state.count == 1
    action = satellite_alert_routes.satellite_alert_action(
        "occurrence-1",
        SatelliteAlertActionRequest(
            source_id="kitchen-source", action="dismiss", idempotency_key="runtime-dismiss-1"
        ),
        object(),
    )
    assert action.model_dump() == {
        "ok": True, "action": "dismiss", "occurrence_id": "occurrence-1"
    }


def test_ui_timer_action_rejects_a_different_authenticated_source(monkeypatch) -> None:
    monkeypatch.setattr(
        application_ui,
        "_require_stable_alert_ui_request",
        lambda source_id, request: SimpleNamespace(request_source_id="bedroom-source"),
    )
    with pytest.raises(HTTPException, match="does not match") as exc:
        application_ui._ui_timer_action_impl(
            UiTimerActionRequest(
                client_id="kitchen-ui",
                source_id="kitchen-source",
                occurrence_id="occurrence-1",
                action="dismiss",
            ),
            object(),
        )
    assert exc.value.status_code == 403


def test_typed_timer_followup_builds_alert_dispatch_without_reparsing_keywords() -> None:
    source, session_id = "kitchen-source", "timer-followup"
    clear_session_state(source, session_id)
    assert set_utility_context(
        source,
        session_id,
        kind="alert_subject",
        payload={
            "schedule_id": "schedule-1", "occurrence_id": "occurrence-1",
            "alert_kind": "timer", "duration_seconds": 600, "duration_unit": "minute",
        },
    )
    dispatch = build_dispatch_plan(
        CommandRequest(text="add five minutes to it", source=source, session_id=session_id),
        RouteResponse(
            target="system", confidence=0.99, reason="typed alert context",
            normalized_text="add five minutes to it",
        ),
    )
    assert dispatch.hook == "system.alerts"
    assert dispatch.payload["action"] == "alerts"
    unrelated = build_dispatch_plan(
        CommandRequest(text="add 5 to 10", source=source, session_id=session_id),
        RouteResponse(
            target="system", confidence=0.9, reason="deterministic calculation",
            normalized_text="add 5 to 10",
        ),
    )
    assert unrelated.hook == "system.calculation"
