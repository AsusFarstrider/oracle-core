from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from oracle_app import alerts, application_ui
from oracle_app.alert_lifecycle import reconcile_alert_lifecycle
from oracle_app.alarms import build_alarm_state, execute_alarm_command, manage_alarm
from oracle_app.memory.alert_lifecycle import list_alert_occurrences, list_alert_schedules
from oracle_app.memory.sources import seed_sources
from oracle_app.schemas import UiAlarmActionRequest


NOW = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)  # noon household time


class Household:
    household = SimpleNamespace(id="home", timezone="America/New_York")
    config_revision = "alarm-config-1"

    def __init__(self) -> None:
        self.rooms = {
            "kitchen": SimpleNamespace(id="kitchen", display_name="Kitchen"),
            "bedroom": SimpleNamespace(id="bedroom", display_name="Bedroom"),
        }
        self.sources = {
            "kitchen-source": "kitchen", "kitchen-display": "kitchen",
            "bedroom-source": "bedroom",
        }

    def room(self, room_id): return self.rooms.get(room_id)
    def source(self, source_id): return SimpleNamespace(id=source_id) if source_id in self.sources else None
    def configured_associated_room_id(self, source_id): return self.sources[source_id]
    def configured_associated_user_id(self, source_id): return None
    def resolve_room_id(self, value):
        clean = str(value or "").casefold().replace("'s", "").replace(" room", "").strip()
        return clean if clean in self.rooms else None


class Fleet:
    enabled_satellite_ids_by_source = {
        "kitchen-source": "one", "kitchen-display": "two", "bedroom-source": "three"
    }
    def satellite_for_source(self, source_id):
        return SimpleNamespace(alert_capable=True) if source_id in self.enabled_satellite_ids_by_source else None


@pytest.fixture
def alarm_env(tmp_path: Path):
    db_path = tmp_path / "alarms.sqlite3"
    seed_sources([
        {"source_id": source, "source_type": "satellite", "display_name": source, "payload": {}}
        for source in Fleet.enabled_satellite_ids_by_source
    ], db_path=db_path)
    return Household(), Fleet(), db_path


def run(text, alarm_env, *, source="kitchen-source", context=None, now=NOW):
    household, fleet, db_path = alarm_env
    return execute_alarm_command(
        text, source_id=source, session_id="alarm-session", household=household,
        satellites=fleet, context=context, now=now, db_path=db_path,
    )


@pytest.mark.parametrize(
    ("utterance", "expected_hour"),
    [
        ("set an alarm for 7", 23),
        ("wake me up at 6:30 tomorrow", 10),
        ("set an alarm for 8 friday morning", 12),
        ("set an alarm for 7 am on september 4", 11),
        ("set an alarm two hours from now", 18),
    ],
)
def test_one_time_alarm_natural_forms(utterance, expected_hour, alarm_env) -> None:
    _speech, details = run(utterance, alarm_env)
    assert datetime.fromisoformat(details["due_at"]).hour == expected_hour
    assert list_alert_schedules(db_path=alarm_env[2])[0].kind == "alarm"


@pytest.mark.parametrize(
    ("utterance", "expected_hour", "expected_minute"),
    [
        ("set an alarm for 9.11 pm", 21, 11),
        ("set an alarm for 9 12 p m", 21, 12),
    ],
)
def test_alarm_accepts_observed_stt_clock_punctuation(
    utterance, expected_hour, expected_minute, alarm_env
) -> None:
    _speech, details = run(
        utterance,
        alarm_env,
        now=datetime(2026, 4, 3, 20, tzinfo=timezone(timedelta(hours=-4))),
    )
    due = datetime.fromisoformat(details["due_at"]).astimezone(timezone(timedelta(hours=-4)))
    assert (due.hour, due.minute) == (expected_hour, expected_minute)


def test_cancel_alarm_deletes_the_selected_future_schedule(alarm_env) -> None:
    _speech, created = run("set an alarm for 9:12 pm", alarm_env)
    speech, details = run("cancel my alarm", alarm_env)

    assert speech.startswith("Deleted")
    assert details == {
        "kind": "alarm",
        "operation": "delete",
        "schedule_id": created["schedule_id"],
        "status": "deleted",
        "terminal": True,
    }
    assert list_alert_schedules(db_path=alarm_env[2])[0].status == "deleted"
    assert list_alert_occurrences(db_path=alarm_env[2])[0].status == "canceled"


@pytest.mark.parametrize(
    ("utterance", "frequency", "weekdays", "interval", "month_days", "ordinals"),
    [
        ("set an alarm for 7 every day", "daily", [], 1, [], []),
        ("set an alarm for 7 every weekday", "weekly", [0, 1, 2, 3, 4], 1, [], []),
        ("set an alarm for 7 weekends", "weekly", [5, 6], 1, [], []),
        ("set an alarm for 7 every monday and friday", "weekly", [0, 4], 1, [], []),
        ("set an alarm for 7 every other week", "weekly", [5], 2, [], []),
        ("set an alarm for 7 the first and third mondays of every month", "monthly", [0], 1, [], [1, 3]),
        ("set an alarm for 7 on the 15th of every month", "monthly", [], 1, [15], []),
    ],
)
def test_required_recurrence_forms(utterance, frequency, weekdays, interval, month_days, ordinals, alarm_env) -> None:
    run(utterance, alarm_env)
    recurrence = list_alert_schedules(db_path=alarm_env[2])[0].recurrence
    assert recurrence == {
        "frequency": frequency, "interval": interval, "weekdays": weekdays,
        "month_days": month_days, "ordinals": ordinals,
    }


def test_daily_alarm_preserves_wall_clock_policy_across_dst_gap(alarm_env) -> None:
    run(
        "set an alarm for 2:30 am every day",
        alarm_env,
        now=datetime(2026, 3, 7, 0, tzinfo=UTC),
    )
    occurrences = list_alert_occurrences(db_path=alarm_env[2])
    due_by_day = {
        datetime.fromisoformat(item.intended_local).day: item.due_at
        for item in occurrences
        if datetime.fromisoformat(item.intended_local).month == 3
        and datetime.fromisoformat(item.intended_local).day in {7, 8, 9}
    }
    assert due_by_day[7] == datetime(2026, 3, 7, 7, 30, tzinfo=UTC)
    assert due_by_day[8] == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    assert due_by_day[9] == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


def test_named_alarm_management_and_next_query(alarm_env) -> None:
    run("set a work alarm for 6:30 every weekday", alarm_env)
    speech, details = run("what alarms do i have", alarm_env)
    assert details["count"] == 1 and "work alarm" in speech
    assert run("turn off my work alarm", alarm_env)[1]["status"] == "disabled"
    assert run("turn my work alarm back on", alarm_env)[1]["status"] == "active"
    assert run("what is my next alarm", alarm_env)[1]["operation"] == "next"
    assert run("delete my work alarm", alarm_env)[1]["status"] == "deleted"


def test_disabled_alarm_does_not_ring_and_reenable_preserves_future_series(alarm_env) -> None:
    run("set a work alarm for 12:01 pm every day", alarm_env)
    run("turn off my work alarm", alarm_env)
    reconcile_alert_lifecycle(
        household=alarm_env[0], satellites=alarm_env[1],
        now=NOW + timedelta(minutes=2), db_path=alarm_env[2],
    )
    assert list_alert_occurrences(db_path=alarm_env[2])[0].status == "scheduled"
    run("turn my work alarm back on", alarm_env, now=NOW + timedelta(minutes=2))
    occurrences = list_alert_occurrences(db_path=alarm_env[2])
    assert occurrences[0].status == "skipped"
    assert any(item.status == "scheduled" for item in occurrences[1:])


def test_occurrence_override_and_skip_do_not_rewrite_series(alarm_env) -> None:
    _, created = run("set a work alarm for 6:30 every weekday", alarm_env)
    schedule_before = list_alert_schedules(db_path=alarm_env[2])[0]
    context = {"payload": {"alert_kind": "alarm", "schedule_id": created["schedule_id"], "occurrence_id": created["occurrence_id"]}}
    _speech, changed = run("tomorrow make my work alarm 7:15", alarm_env, context=context)
    assert changed["operation"] == "edit_occurrence"
    assert list_alert_schedules(db_path=alarm_env[2])[0].local_time == schedule_before.local_time
    _speech, skipped = run("skip the next one", alarm_env, context=context)
    assert skipped["operation"] == "skip"
    assert list_alert_schedules(db_path=alarm_env[2])[0].status == "active"


def test_material_edit_scope_ambiguity_clarifies(alarm_env) -> None:
    _, created = run("set an alarm for 7 every day", alarm_env)
    context = {"payload": {"alert_kind": "alarm", "schedule_id": created["schedule_id"]}}
    speech, details = run("change my alarm to 8", alarm_env, context=context)
    assert speech.startswith("Should I change")
    assert details["status"] == "clarification_required"


def test_explicit_series_edit_updates_wall_clock_without_duplicate_schedule(alarm_env) -> None:
    _, created = run("set a work alarm for 6:30 every weekday", alarm_env)
    context = {"payload": {"alert_kind": "alarm", "schedule_id": created["schedule_id"]}}
    _speech, details = run("change my weekday alarm to 6:45", alarm_env, context=context)
    assert details["operation"] == "edit_schedule"
    schedules = list_alert_schedules(db_path=alarm_env[2])
    assert len(schedules) == 1 and schedules[0].local_time == "06:45:00"
    active = [item for item in list_alert_occurrences(db_path=alarm_env[2]) if item.status == "scheduled"]
    assert active and all(item.due_at.astimezone(timezone(timedelta(hours=-4))).minute == 45 for item in active)
    assert len({item.occurrence_key for item in active}) == len(active)
    assert not any(item.due_at.minute == 30 for item in active)


def test_ringing_snooze_lineage_and_housewide_dismissal(alarm_env) -> None:
    _, created = run("set a house alarm for 12:01 pm", alarm_env)
    household, fleet, db_path = alarm_env
    due = NOW + timedelta(minutes=1)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=due, db_path=db_path)
    state = build_alarm_state(source_id="bedroom-source", household=household, satellites=fleet, now=due, db_path=db_path)
    assert len(state["ringing"]) == 1 and state["display_attention_required"] is True
    snoozed = manage_alarm(
        source_id="bedroom-source", occurrence_id=created["occurrence_id"], action="snooze",
        snooze_minutes=5, household=household, satellites=fleet,
        idempotency_key="snooze-house", now=due, db_path=db_path,
    )
    child = next(item for item in list_alert_occurrences(db_path=db_path) if item.occurrence_id == snoozed["occurrence_id"])
    assert child.parent_occurrence_id == created["occurrence_id"]
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=due + timedelta(minutes=5), db_path=db_path)
    manage_alarm(
        source_id="kitchen-source", occurrence_id=child.occurrence_id, action="dismiss",
        household=household, satellites=fleet, idempotency_key="dismiss-house",
        now=due + timedelta(minutes=5), db_path=db_path,
    )
    assert build_alarm_state(source_id="bedroom-source", household=household, satellites=fleet, now=due + timedelta(minutes=5), db_path=db_path)["ringing"] == []


def test_twenty_minute_delayed_boundary_and_missed_visibility(alarm_env) -> None:
    _, within = run("set an alarm for 12:01 pm", alarm_env)
    _, old = run("set an alarm for 12:02 pm", alarm_env)
    household, fleet, db_path = alarm_env
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(minutes=21), db_path=db_path)
    occurrences = {item.occurrence_id: item for item in list_alert_occurrences(db_path=db_path)}
    assert occurrences[within["occurrence_id"]].status == "due"
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(minutes=23), db_path=db_path)
    assert {item.occurrence_id: item.status for item in list_alert_occurrences(db_path=db_path)}[old["occurrence_id"]] == "missed"
    state = build_alarm_state(source_id="kitchen-source", household=household, satellites=fleet, now=NOW + timedelta(minutes=23), db_path=db_path)
    assert any(item["missed"] for item in state["alarms"])


def test_public_alert_owner_uses_semantic_alarm_path(monkeypatch, alarm_env) -> None:
    monkeypatch.setattr(alerts, "ALERT_DB_PATH", alarm_env[2])
    speech, details = alerts.build_alert_response(
        "set a work alarm for 7 every weekday", "kitchen-source", "session",
        household_settings=alarm_env[0], satellite_settings=alarm_env[1],
    )
    assert speech.startswith("Work alarm set")
    assert details["schedule_type"] == "recurring"


def test_ui_alarm_action_is_source_bound(monkeypatch) -> None:
    monkeypatch.setattr(
        application_ui, "_require_stable_alert_ui_request",
        lambda source_id, request: SimpleNamespace(request_source_id="bedroom-source"),
    )
    with pytest.raises(HTTPException) as exc:
        application_ui._ui_alarm_action_impl(
            UiAlarmActionRequest(
                client_id="kitchen-ui", source_id="kitchen-source",
                schedule_id="schedule-1", action="disable",
            ),
            object(),
        )
    assert exc.value.status_code == 403
