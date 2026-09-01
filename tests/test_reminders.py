from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapi import HTTPException

from oracle_app import alerts, application_ui
from oracle_app import reminders as reminders_module
from oracle_app.alert_lifecycle import acknowledge_alert_occurrence, reconcile_alert_lifecycle
from oracle_app.memory.alert_lifecycle import list_alert_occurrences, list_alert_schedules
from oracle_app.memory.alerts import list_alert_records
from oracle_app.memory.sources import seed_sources
from oracle_app.reminders import build_reminder_state, execute_reminder_command, manage_reminder
from oracle_app.schemas import UiReminderActionRequest
from oracle_app.routing_helpers import detect_alert_query


NOW = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)


class Household:
    household = SimpleNamespace(id="home", timezone="America/New_York")
    default_user_id = "phil"

    def __init__(self, *, revision="reminder-config-1", associations=None):
        self.config_revision = revision
        self.users = {
            "phil": SimpleNamespace(id="phil", display_name="Phil", aliases=(), enabled=True),
            "sarah": SimpleNamespace(id="sarah", display_name="Sarah", aliases=(), enabled=True),
            "alex": SimpleNamespace(id="alex", display_name="Alex", aliases=(), enabled=True),
        }
        self.associations = associations or {
            "office": "phil", "office-display": "phil", "bedroom": "sarah", "common": None,
        }

    def user(self, user_id, *, enabled_only=True):
        item = self.users.get(str(user_id or ""))
        return item if item and (item.enabled or not enabled_only) else None

    def source(self, source_id, *, enabled_only=True):
        return SimpleNamespace(id=source_id, enabled=True) if source_id in self.associations else None

    def configured_associated_user_id(self, source_id):
        return self.associations.get(source_id)

    def resolve_user_id(self, value):
        clean = str(value or "").casefold().strip()
        matches = [item.id for item in self.users.values() if clean in {item.id, item.display_name.casefold()}]
        return matches[0] if len(matches) == 1 else None


class Fleet:
    def __init__(self, source_ids=("office", "office-display", "bedroom", "common")):
        self.enabled_satellite_ids_by_source = {item: item for item in source_ids}

    def satellite_for_source(self, source_id):
        return SimpleNamespace(alert_capable=True) if source_id in self.enabled_satellite_ids_by_source else None


@pytest.fixture
def reminder_env(tmp_path: Path):
    db_path = tmp_path / "reminders.sqlite3"
    household, fleet = Household(), Fleet()
    seed_sources([
        {"source_id": source, "source_type": "satellite", "display_name": source, "payload": {}}
        for source in fleet.enabled_satellite_ids_by_source
    ], db_path=db_path)
    return household, fleet, db_path


def run(text, env, *, source="office", now=NOW, session="session", context=None):
    household, fleet, db_path = env
    return execute_reminder_command(
        text, source_id=source, session_id=session, household=household,
        satellites=fleet, context=context, now=now, db_path=db_path,
    )


@pytest.mark.parametrize("utterance", [
    "remind me in 20 minutes to stretch",
    "remind me at 4:30 pm to call the dentist",
    "remind me friday at 3 pm to submit the form",
    "remind me on september 15 at 9 am to renew this",
    "remind me two weeks from today at 10 am to check the filter",
])
def test_one_time_natural_forms(utterance, reminder_env):
    _speech, details = run(utterance, reminder_env)
    assert details["operation"] == "create"
    assert list_alert_schedules(db_path=reminder_env[2])[0].kind == "reminder"


def test_date_without_exact_time_clarifies(reminder_env):
    _speech, details = run("remind me on september 15 to renew this", reminder_env)
    assert details["status"] == "clarification_required"
    assert details["clarification_kind"] == "reminder_time"
    assert list_alert_schedules(db_path=reminder_env[2]) == []


@pytest.mark.parametrize("utterance,frequency", [
    ("remind me every day at 7 pm to take vitamins", "daily"),
    ("remind me every weekday at 7 pm to pack lunch", "weekly"),
    ("remind me every monday and friday at 7 pm to put out bins", "weekly"),
    ("remind me every other week at 7 pm to clean the filter", "weekly"),
    ("remind me on the 15th of every month at 7 pm to pay the bill", "monthly"),
])
def test_recurrence_reuses_alarm_calendar_language(utterance, frequency, reminder_env):
    run(utterance, reminder_env)
    assert list_alert_schedules(db_path=reminder_env[2])[0].recurrence["frequency"] == frequency


def test_me_uses_source_association_and_never_household_default(reminder_env):
    household, fleet, db_path = reminder_env
    _speech, details = run("remind me in 10 minutes to check the oven", reminder_env, source="bedroom")
    occurrence = next(item for item in list_alert_occurrences(db_path=db_path) if item.occurrence_id == details["occurrence_id"])
    assert occurrence.recipient_user_id == "sarah"
    _speech, common = run("remind me in 10 minutes to check the door", reminder_env, source="common")
    assert common["status"] == "clarification_required"


def test_me_prefers_explicit_session_user_over_source_association(monkeypatch, reminder_env):
    monkeypatch.setattr(reminders_module, "get_active_user_id", lambda source, session: "sarah")
    _, details = run("remind me in 10 minutes to check the mail", reminder_env, source="office")
    assert details["recipient_user_id"] == "sarah"


def test_named_unknown_user_clarifies_without_creation(reminder_env):
    _speech, details = run("remind nobody in 10 minutes to wave", reminder_env)
    assert details["clarification_kind"] == "reminder_recipient"
    assert list_alert_schedules(db_path=reminder_env[2]) == []


def test_named_recipient_creation_routes_to_alert_owner():
    assert detect_alert_query("Remind Sarah tomorrow at 9 to call home") is True


def test_person_occurrence_projects_to_multiple_destinations_and_ack_converges(reminder_env):
    household, fleet, db_path = reminder_env
    _, created = run("remind phil in 1 minute to hydrate", reminder_env)
    due = NOW + timedelta(minutes=1)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=due, db_path=db_path)
    deliveries = list_alert_records(kind="reminder", db_path=db_path)
    assert {item.source_id for item in deliveries} == {"office", "office-display"}
    manage_reminder(
        source_id="office", occurrence_id=created["occurrence_id"], action="dismiss",
        household=household, satellites=fleet, idempotency_key="person-dismiss",
        now=due, db_path=db_path,
    )
    assert build_reminder_state(source_id="office-display", household=household, satellites=fleet, now=due, db_path=db_path)["outstanding"] == []


def test_everyone_has_independent_people_and_common_copy(reminder_env):
    household, fleet, db_path = reminder_env
    run("remind everyone in 1 minute to come downstairs", reminder_env)
    occurrences = list_alert_occurrences(db_path=db_path)
    assert {item.recipient_user_id for item in occurrences} == {"phil", "sarah", "alex", None}
    due = NOW + timedelta(minutes=1)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=due, db_path=db_path)
    phil = build_reminder_state(source_id="office", household=household, satellites=fleet, now=due, db_path=db_path)["outstanding"][0]
    manage_reminder(source_id="office", occurrence_id=phil["occurrence_id"], action="dismiss", household=household, satellites=fleet, idempotency_key="phil-done", now=due, db_path=db_path)
    assert build_reminder_state(source_id="bedroom", household=household, satellites=fleet, now=due, db_path=db_path)["outstanding"]
    common = build_reminder_state(source_id="common", household=household, satellites=fleet, now=due, db_path=db_path)["outstanding"][0]
    manage_reminder(source_id="common", occurrence_id=common["occurrence_id"], action="dismiss", household=household, satellites=fleet, idempotency_key="common-copy", now=due, db_path=db_path)
    assert build_reminder_state(source_id="common", household=household, satellites=fleet, now=due, db_path=db_path)["outstanding"] == []
    assert build_reminder_state(source_id="bedroom", household=household, satellites=fleet, now=due, db_path=db_path)["outstanding"]


def test_zero_destination_recipient_remains_outstanding_without_delivery(reminder_env):
    household, fleet, db_path = reminder_env
    _, created = run("remind alex in 1 minute to practice", reminder_env)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(minutes=1), db_path=db_path)
    occurrence = next(item for item in list_alert_occurrences(db_path=db_path) if item.occurrence_id == created["occurrence_id"])
    assert occurrence.status == "outstanding"
    assert occurrence.metadata["destinations"] == []
    assert list_alert_records(kind="reminder", db_path=db_path) == []


def test_destination_revision_is_frozen_for_current_occurrence_and_refreshed_for_future(reminder_env):
    household, fleet, db_path = reminder_env
    run("remind phil every day at 12:01 pm to stretch", reminder_env)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(minutes=1), db_path=db_path)
    first = min(list_alert_occurrences(db_path=db_path), key=lambda item: item.due_at)
    assert first.config_revision == "reminder-config-1"
    household.config_revision = "reminder-config-2"
    household.associations = {"office": None, "office-display": None, "bedroom": "sarah", "common": "phil"}
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(days=1, minutes=1), db_path=db_path)
    occurrences = sorted(list_alert_occurrences(db_path=db_path), key=lambda item: item.due_at)
    assert occurrences[0].config_revision == "reminder-config-1"
    assert occurrences[1].config_revision == "reminder-config-2"
    assert occurrences[1].metadata["destinations"][0]["source_id"] == "common"


def test_stale_reminder_becomes_overdue_without_delivery(reminder_env):
    household, fleet, db_path = reminder_env
    _, created = run("remind phil in 1 minute to check in", reminder_env)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(minutes=22), db_path=db_path)
    occurrence = next(item for item in list_alert_occurrences(db_path=db_path) if item.occurrence_id == created["occurrence_id"])
    assert occurrence.status == "overdue"
    assert list_alert_records(kind="reminder", db_path=db_path) == []


def test_snooze_creates_child_lineage_and_keeps_series(reminder_env):
    household, fleet, db_path = reminder_env
    _, created = run("remind phil every day at 12:01 pm to stretch", reminder_env)
    due = NOW + timedelta(minutes=1)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=due, db_path=db_path)
    result = manage_reminder(
        source_id="office", occurrence_id=created["occurrence_id"], action="snooze", snooze_minutes=15,
        household=household, satellites=fleet, idempotency_key="snooze-15", now=due, db_path=db_path,
    )
    child = next(item for item in list_alert_occurrences(db_path=db_path) if item.occurrence_id == result["occurrence_id"])
    assert child.parent_occurrence_id == created["occurrence_id"]
    assert child.due_at == due + timedelta(minutes=15)
    assert list_alert_schedules(db_path=db_path)[0].status == "active"


def test_recurring_management_skip_disable_enable_delete(reminder_env):
    _, created = run("remind phil every weekday at 7 pm to pack lunch", reminder_env)
    context = {"payload": {"alert_kind": "reminder", "schedule_id": created["schedule_id"], "occurrence_id": created["occurrence_id"]}}
    assert run("skip the next one", reminder_env, context=context)[1]["operation"] == "skip"
    assert run("turn off my pack lunch reminder", reminder_env, context=context)[1]["status"] == "disabled"
    assert run("turn my pack lunch reminder back on", reminder_env, context=context)[1]["status"] == "active"
    assert run("delete my pack lunch reminder", reminder_env, context=context)[1]["status"] == "deleted"


def test_public_alert_owner_uses_semantic_reminder_path(monkeypatch, reminder_env):
    monkeypatch.setattr(alerts, "ALERT_DB_PATH", reminder_env[2])
    _speech, details = alerts.build_alert_response(
        "remind sarah in 10 minutes to call home", "office", "session",
        household_settings=reminder_env[0], satellite_settings=reminder_env[1],
    )
    assert details["kind"] == "reminder" and details["recipient_user_id"] == "sarah"


def test_person_acknowledgement_rejects_wrong_recipient(reminder_env):
    household, fleet, db_path = reminder_env
    _, created = run("remind sarah in 1 minute to call home", reminder_env)
    reconcile_alert_lifecycle(household=household, satellites=fleet, now=NOW + timedelta(minutes=1), db_path=db_path)
    with pytest.raises(ValueError, match="occurrence recipient"):
        acknowledge_alert_occurrence(
            occurrence_id=created["occurrence_id"], actor_type="person", actor_id="phil",
            action="dismissed", idempotency_key="wrong-person", now=NOW + timedelta(minutes=1), db_path=db_path,
        )


def test_ui_reminder_action_is_source_bound(monkeypatch):
    monkeypatch.setattr(
        application_ui, "_require_stable_alert_ui_request",
        lambda source_id, request: SimpleNamespace(request_source_id="bedroom"),
    )
    with pytest.raises(HTTPException) as exc:
        application_ui._ui_reminder_action_impl(
            UiReminderActionRequest(
                client_id="office-ui", source_id="office", occurrence_id="occurrence-1", action="dismiss",
            ),
            object(),
        )
    assert exc.value.status_code == 403
