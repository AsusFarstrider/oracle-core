from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from oracle_app.alert_lifecycle import (
    acknowledge_alert_occurrence,
    change_alert_schedule_status,
    create_semantic_alert_schedule,
    override_alert_occurrence,
    reconcile_alert_lifecycle,
    skip_alert_occurrence,
    snooze_alert_occurrence,
)
from oracle_app.alert_recurrence import RecurrenceRule
from oracle_app.memory.alert_lifecycle import (
    list_alert_occurrences,
    list_alert_schedules,
    record_alert_acknowledgement,
    transition_alert_occurrence,
)
from oracle_app.memory.alerts import acknowledge_alert, claim_due_alerts, create_alert_records
from oracle_app.memory.identity_reconciliation import reconcile_identities
from oracle_app.memory.schema import SCHEMA_VERSION, ensure_schema
from oracle_app.configuration.runtime_models import MemoryRetentionConfiguration
from oracle_app.memory.retention import retention_policy_from_configuration
from oracle_app.memory.retention_executor import run_retention
from oracle_app.memory.sources import seed_sources
from oracle_app.memory.store import transaction


NOW = datetime(2026, 8, 28, 16, tzinfo=UTC)


class _Household:
    household = SimpleNamespace(id="home")

    def __init__(self, revision: str = "revision-1", associations=None) -> None:
        self.config_revision = revision
        self.associations = associations or {
            "source-a": ("kitchen", "phil"),
            "source-b": ("kitchen", "phil"),
            "common": ("kitchen", None),
        }
        self.users = {"phil": object(), "molly": object()}
        self.rooms = {"kitchen": object()}

    def source(self, source_id):
        return SimpleNamespace(id=source_id) if source_id in self.associations else None

    def room(self, room_id):
        return self.rooms.get(room_id)

    def user(self, user_id):
        return self.users.get(user_id)

    def configured_associated_room_id(self, source_id):
        return self.associations[source_id][0]

    def configured_associated_user_id(self, source_id):
        return self.associations[source_id][1]


class _Fleet:
    def __init__(self, sources=("source-a", "source-b", "common")) -> None:
        self.enabled_satellite_ids_by_source = {source: source for source in sources}

    def satellite_for_source(self, source_id):
        return SimpleNamespace(alert_capable=True) if source_id in self.enabled_satellite_ids_by_source else None


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "memory.sqlite3"
    seed_sources(
        [
            {"source_id": source, "source_type": "satellite", "display_name": source}
            for source in ("source-a", "source-b", "common")
        ],
        db_path=path,
    )
    return path


def _schedule(db_path: Path, **overrides):
    values = dict(
        kind="timer",
        start_at=NOW - timedelta(minutes=1),
        timezone_name="UTC",
        creator_source_id="source-a",
        session_id="session-a",
        message="Timer finished.",
        target_scope="local",
        idempotency_key="schedule-one",
        db_path=db_path,
    )
    values.update(overrides)
    return create_semantic_alert_schedule(**values)


def test_restart_reconciliation_materializes_one_occurrence_and_reuses_one_delivery(db_path: Path) -> None:
    schedule, created = _schedule(db_path)
    duplicate, duplicate_created = _schedule(db_path)
    assert created is True and duplicate_created is False
    assert duplicate.schedule_id == schedule.schedule_id

    first = reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path
    )
    second = reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path
    )
    assert (first.occurrences_created, first.deliveries_created) == (1, 1)
    assert (second.occurrences_created, second.deliveries_created, second.deliveries_reused) == (0, 0, 1)
    with transaction(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_alert_occurrences").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memory_alerts").fetchone()[0] == 1


def test_reconciliation_preserves_runtime_acknowledged_ringing_occurrence(db_path: Path) -> None:
    _schedule(db_path)
    first = reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path
    )
    occurrence = list_alert_occurrences(db_path=db_path)[0]
    leased = claim_due_alerts(source_id="source-a", now=NOW, db_path=db_path)[0]
    acknowledge_alert(
        alert_id=leased.alert_id,
        source_id="source-a",
        lease_id=str(leased.lease_id),
        now=NOW + timedelta(seconds=1),
        db_path=db_path,
    )
    transition_alert_occurrence(
        occurrence.occurrence_id,
        status="ringing",
        actor_type="runtime",
        actor_id="source-a",
        reason="timer_delivery_accepted",
        now=NOW + timedelta(seconds=1),
        db_path=db_path,
    )

    second = reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW + timedelta(seconds=2), db_path=db_path
    )

    assert first.deliveries_created == 1
    assert second.occurrences_transitioned == 0
    assert second.deliveries_created == 0
    assert second.deliveries_reused == 1
    assert list_alert_occurrences(db_path=db_path)[0].status == "ringing"


def test_ringing_occurrence_does_not_block_reconciliation_of_later_due_work(db_path: Path) -> None:
    _schedule(db_path, idempotency_key="already-ringing")
    reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path
    )
    first_occurrence = list_alert_occurrences(db_path=db_path)[0]
    transition_alert_occurrence(
        first_occurrence.occurrence_id,
        status="ringing",
        actor_type="runtime",
        actor_id="source-a",
        reason="timer_delivery_accepted",
        now=NOW + timedelta(seconds=1),
        db_path=db_path,
    )
    _schedule(
        db_path,
        idempotency_key="later-due-work",
        start_at=NOW - timedelta(seconds=30),
    )

    result = reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW + timedelta(seconds=2), db_path=db_path
    )

    statuses = {item.occurrence_id: item.status for item in list_alert_occurrences(db_path=db_path)}
    assert statuses[first_occurrence.occurrence_id] == "ringing"
    assert sorted(statuses.values()) == ["due", "ringing"]
    assert result.occurrences_created == 1
    assert result.occurrences_transitioned == 1
    assert result.deliveries_created == 1
    assert result.deliveries_reused == 1


def test_household_fanout_is_one_occurrence_with_multiple_delivery_projections(db_path: Path) -> None:
    _schedule(db_path, target_scope="household")
    result = reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path
    )
    assert result.deliveries_created == 3
    with transaction(db_path) as conn:
        occurrence_ids = {
            str(row[0]) for row in conn.execute("SELECT occurrence_id FROM memory_alerts")
        }
        assert len(occurrence_ids) == 1


def test_compatibility_batch_api_creates_one_logical_occurrence(db_path: Path) -> None:
    alerts, duplicate = create_alert_records(
        kind="timer",
        due_at=NOW + timedelta(minutes=5),
        message="Routine timer finished.",
        source_ids=("source-a", "source-b"),
        session_id="routine",
        idempotency_key="routine-occurrence",
        db_path=db_path,
    )
    assert duplicate is False
    assert len(alerts) == 2
    assert len({item.occurrence_id for item in alerts}) == 1
    with transaction(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_alert_schedules").fetchone()[0] == 1
        assert conn.execute("SELECT target_scope FROM memory_alert_schedules").fetchone()[0] == "household"


def test_runtime_acceptance_is_not_logical_acknowledgement(db_path: Path) -> None:
    _schedule(db_path)
    reconcile_alert_lifecycle(household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path)
    occurrence = list_alert_occurrences(db_path=db_path)[0]
    leased = claim_due_alerts(source_id="source-a", now=NOW, db_path=db_path)[0]
    acknowledge_alert(
        alert_id=leased.alert_id, source_id="source-a", lease_id=str(leased.lease_id),
        now=NOW + timedelta(seconds=1), db_path=db_path,
    )
    assert list_alert_occurrences(db_path=db_path)[0].status == "due"
    with transaction(db_path) as conn:
        row = conn.execute(
            "SELECT actor_type, action FROM memory_alert_acknowledgements"
        ).fetchone()
        assert tuple(row) == ("runtime", "delivery_accepted")

    completed = acknowledge_alert_occurrence(
        occurrence_id=occurrence.occurrence_id,
        actor_type="system",
        actor_id="brain",
        action="completed",
        idempotency_key="logical-complete",
        now=NOW + timedelta(seconds=2),
        db_path=db_path,
    )
    assert completed.status == "completed"


def test_recipient_resolution_uses_current_config_for_each_future_occurrence(db_path: Path) -> None:
    _schedule(
        db_path,
        kind="reminder",
        start_at=NOW,
        message="Take medicine.",
        target_scope="recipient",
        recipient_user_ids=("phil",),
        recurrence=RecurrenceRule("daily"),
    )
    first_household = _Household(associations={
        "source-a": ("kitchen", "phil"), "source-b": ("kitchen", None), "common": ("kitchen", None),
    })
    reconcile_alert_lifecycle(
        household=first_household, satellites=_Fleet(), now=NOW, db_path=db_path
    )
    second_household = _Household(
        revision="revision-2",
        associations={
            "source-a": ("kitchen", None), "source-b": ("kitchen", "phil"), "common": ("kitchen", None),
        },
    )
    reconcile_alert_lifecycle(
        household=second_household,
        satellites=_Fleet(),
        now=NOW + timedelta(days=1),
        db_path=db_path,
    )
    with transaction(db_path) as conn:
        rows = conn.execute(
            "SELECT source_id, config_revision FROM memory_alerts ORDER BY due_at"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("source-a", "revision-1"),
        ("source-b", "revision-2"),
    ]


def test_late_state_policy_and_zero_destination_reminder_are_truthful(db_path: Path) -> None:
    _schedule(db_path, start_at=NOW - timedelta(minutes=11))
    timer = reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path
    )
    assert timer.deliveries_created == 0
    assert list_alert_occurrences(db_path=db_path)[0].status == "missed"

    _schedule(
        db_path,
        kind="reminder",
        start_at=NOW,
        target_scope="recipient",
        recipient_user_ids=("phil",),
        idempotency_key="undelivered-reminder",
    )
    empty_household = _Household(associations={
        "source-a": ("kitchen", None), "source-b": ("kitchen", None), "common": ("kitchen", None),
    })
    result = reconcile_alert_lifecycle(
        household=empty_household, satellites=_Fleet(), now=NOW, db_path=db_path
    )
    assert result.deliveries_created == 0
    reminder = [item for item in list_alert_occurrences(db_path=db_path) if item.status == "outstanding"]
    assert len(reminder) == 1


def test_skip_and_snooze_are_occurrence_scoped_and_preserve_lineage(db_path: Path) -> None:
    schedule, _ = _schedule(db_path, recurrence=RecurrenceRule("daily"), start_at=NOW)
    reconcile_alert_lifecycle(household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path)
    occurrences = list_alert_occurrences(schedule_id=schedule.schedule_id, db_path=db_path)
    skipped = skip_alert_occurrence(
        occurrences[1].occurrence_id, actor_id="phil", now=NOW, db_path=db_path
    )
    assert skipped.status == "skipped"
    snoozed = snooze_alert_occurrence(
        occurrences[0].occurrence_id,
        until=NOW + timedelta(minutes=5),
        actor_type="system",
        actor_id="brain",
        now=NOW + timedelta(seconds=1),
        db_path=db_path,
    )
    assert snoozed.parent_occurrence_id == occurrences[0].occurrence_id
    assert get_status(db_path, occurrences[0].occurrence_id) == "snoozed"
    assert list_alert_schedules(db_path=db_path)[0].status == "active"


def test_schedule_state_and_occurrence_override_do_not_rewrite_the_series(db_path: Path) -> None:
    schedule, _ = _schedule(db_path, recurrence=RecurrenceRule("daily"), start_at=NOW)
    reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=NOW - timedelta(seconds=1), db_path=db_path
    )
    occurrences = list_alert_occurrences(schedule_id=schedule.schedule_id, db_path=db_path)
    original_second = occurrences[1].due_at
    overridden = override_alert_occurrence(
        occurrences[0].occurrence_id,
        due_at=NOW + timedelta(hours=2),
        intended_local="2026-08-28T18:00:00",
        actor_id="phil",
        now=NOW - timedelta(seconds=1),
        db_path=db_path,
    )
    assert overridden.due_at == NOW + timedelta(hours=2)
    assert overridden.metadata["original_due_at"] == occurrences[0].due_at.isoformat()
    assert list_alert_occurrences(schedule_id=schedule.schedule_id, db_path=db_path)[1].due_at == original_second

    disabled = change_alert_schedule_status(
        schedule.schedule_id, status="disabled", now=NOW, db_path=db_path
    )
    assert disabled.status == "disabled"
    assert change_alert_schedule_status(
        schedule.schedule_id, status="active", now=NOW, db_path=db_path
    ).status == "active"


def test_acknowledgement_actor_semantics_and_idempotency_conflicts_fail_closed(db_path: Path) -> None:
    _schedule(db_path)
    reconcile_alert_lifecycle(household=_Household(), satellites=_Fleet(), now=NOW, db_path=db_path)
    occurrence = list_alert_occurrences(db_path=db_path)[0]
    with pytest.raises(ValueError, match="cannot record"):
        record_alert_acknowledgement(
            occurrence_id=occurrence.occurrence_id,
            actor_type="destination",
            actor_id="common",
            action="acknowledged",
            idempotency_key="wrong-actor",
            now=NOW,
            db_path=db_path,
        )
    record, created = record_alert_acknowledgement(
        occurrence_id=occurrence.occurrence_id,
        actor_type="person",
        actor_id="phil",
        action="acknowledged",
        idempotency_key="person-only",
        now=NOW,
        db_path=db_path,
    )
    assert created is True and record.action == "acknowledged"
    _same, created = record_alert_acknowledgement(
        occurrence_id=occurrence.occurrence_id,
        actor_type="person",
        actor_id="phil",
        action="acknowledged",
        idempotency_key="person-only",
        now=NOW + timedelta(seconds=1),
        db_path=db_path,
    )
    assert created is False
    with pytest.raises(ValueError, match="conflicts"):
        record_alert_acknowledgement(
            occurrence_id=occurrence.occurrence_id,
            actor_type="person",
            actor_id="another-person",
            action="acknowledged",
            idempotency_key="person-only",
            now=NOW + timedelta(seconds=2),
            db_path=db_path,
        )


def test_common_copy_dismissal_cannot_acknowledge_recipient_occurrence(db_path: Path) -> None:
    _schedule(
        db_path,
        kind="reminder",
        target_scope="recipient",
        recipient_user_ids=("phil", "molly"),
        metadata={"include_common_copy": True},
    )
    household = _Household(associations={
        "source-a": ("kitchen", "phil"),
        "source-b": ("kitchen", "molly"),
        "common": ("kitchen", None),
    })
    reconcile_alert_lifecycle(
        household=household, satellites=_Fleet(), now=NOW, db_path=db_path
    )
    occurrence = next(
        item for item in list_alert_occurrences(db_path=db_path)
        if item.metadata.get("common_copy")
    )
    with transaction(db_path) as conn:
        common_alert_id = str(conn.execute(
            "SELECT alert_id FROM memory_alerts WHERE delivery_role='common'"
        ).fetchone()[0])
    acknowledged = acknowledge_alert_occurrence(
        occurrence_id=occurrence.occurrence_id,
        alert_id=common_alert_id,
        actor_type="destination",
        actor_id="common",
        action="copy_dismissed",
        idempotency_key="common-dismiss",
        now=NOW,
        db_path=db_path,
    )
    assert acknowledged.status == "outstanding"
    with transaction(db_path) as conn:
        assert conn.execute(
            "SELECT status FROM memory_alerts WHERE alert_id=?", (common_alert_id,)
        ).fetchone()[0] == "canceled"


def test_everyone_reminder_has_independent_person_occurrences_and_one_common_copy(db_path: Path) -> None:
    _schedule(
        db_path,
        kind="reminder",
        target_scope="recipient",
        recipient_user_ids=("phil", "molly"),
        metadata={"include_common_copy": True},
    )
    household = _Household(associations={
        "source-a": ("kitchen", "phil"),
        "source-b": ("kitchen", "molly"),
        "common": ("kitchen", None),
    })
    reconcile_alert_lifecycle(
        household=household, satellites=_Fleet(), now=NOW, db_path=db_path
    )
    occurrences = list_alert_occurrences(db_path=db_path)
    assert sorted(item.recipient_user_id or "common" for item in occurrences) == [
        "common", "molly", "phil"
    ]
    with transaction(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_alerts").fetchone()[0] == 3
    phil = next(item for item in occurrences if item.recipient_user_id == "phil")
    molly = next(item for item in occurrences if item.recipient_user_id == "molly")
    common = next(item for item in occurrences if item.metadata.get("common_copy"))
    acknowledge_alert_occurrence(
        occurrence_id=phil.occurrence_id,
        actor_type="person", actor_id="phil", action="acknowledged",
        idempotency_key="phil-ack", now=NOW, db_path=db_path,
    )
    assert get_status(db_path, molly.occurrence_id) == "outstanding"
    assert get_status(db_path, common.occurrence_id) == "outstanding"
    acknowledge_alert_occurrence(
        occurrence_id=molly.occurrence_id,
        actor_type="person", actor_id="molly", action="acknowledged",
        idempotency_key="molly-ack", now=NOW, db_path=db_path,
    )
    reconcile_alert_lifecycle(
        household=household, satellites=_Fleet(), now=NOW, db_path=db_path
    )
    assert get_status(db_path, common.occurrence_id) == "completed"


def test_retention_prunes_terminal_delivery_occurrence_and_one_time_schedule(db_path: Path) -> None:
    old = NOW - timedelta(days=100)
    _schedule(db_path, start_at=old)
    reconcile_alert_lifecycle(
        household=_Household(), satellites=_Fleet(), now=old, db_path=db_path
    )
    occurrence = list_alert_occurrences(db_path=db_path)[0]
    acknowledge_alert_occurrence(
        occurrence_id=occurrence.occurrence_id,
        actor_type="system",
        actor_id="brain",
        action="completed",
        idempotency_key="completed-old",
        now=old + timedelta(seconds=1),
        db_path=db_path,
    )
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE memory_alert_schedules SET updated_at=?",
            (old.isoformat(),),
        )
        conn.execute(
            "UPDATE memory_alert_occurrences SET updated_at=?, completed_at=?",
            (old.isoformat(), old.isoformat()),
        )
        conn.execute(
            "UPDATE memory_alerts SET updated_at=?, completed_at=?",
            (old.isoformat(), old.isoformat()),
        )
    policy = retention_policy_from_configuration(MemoryRetentionConfiguration())
    report = run_retention(policy, db_path=db_path, now=NOW, dry_run=True)
    by_name = {item.class_name: item for item in report.classes}
    assert len(by_name["terminal_alerts"].candidate_ids) == 1
    assert len(by_name["terminal_alert_occurrences"].candidate_ids) == 1
    assert len(by_name["terminal_alert_schedules"].candidate_ids) == 1
    run_retention(policy, db_path=db_path, now=NOW, dry_run=False)
    with transaction(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_alerts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_alert_occurrences").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_alert_schedules").fetchone()[0] == 0


def test_active_schedule_blocks_creator_source_retirement_without_a_delivery(db_path: Path) -> None:
    _schedule(db_path, creator_source_id="source-b", start_at=NOW + timedelta(days=1))
    household = SimpleNamespace(
        users={},
        sources={"source-a": SimpleNamespace(enabled=True, type="satellite", fixed=True)},
    )
    with pytest.raises(ValueError, match="active alert schedules"):
        reconcile_identities(household, SimpleNamespace(satellites={}), db_path=db_path)
def get_status(db_path: Path, occurrence_id: str) -> str:
    return next(
        item.status for item in list_alert_occurrences(db_path=db_path)
        if item.occurrence_id == occurrence_id
    )


def test_schema_migrates_legacy_one_shot_alerts_without_rewriting_identity(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE memory_sources (
            source_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            source_type TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        INSERT INTO memory_sources VALUES (
            'source-a','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',
            'satellite','Source A','active','{}'
        );
        CREATE TABLE memory_alerts (
            alert_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            kind TEXT NOT NULL, source_id TEXT NOT NULL, session_id TEXT, due_at TEXT NOT NULL,
            expires_at TEXT, message TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL, idempotency_key TEXT, lease_id TEXT, leased_at TEXT,
            lease_expires_at TEXT, acknowledged_at TEXT, completed_at TEXT, canceled_at TEXT
        );
        INSERT INTO memory_alerts VALUES (
            'old-timer','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',
            'timer','source-a','session-a','2026-01-01T00:05:00+00:00',NULL,
            'Timer finished.','{}','pending',NULL,NULL,NULL,NULL,NULL,NULL,NULL
        );
        """
    )
    conn.commit()
    conn.close()

    ensure_schema(path)
    with transaction(path) as migrated:
        alert = migrated.execute(
            "SELECT alert_id, occurrence_id FROM memory_alerts"
        ).fetchone()
        schedule = migrated.execute(
            "SELECT schedule_id, creator_source_id, target_scope, target_id FROM memory_alert_schedules"
        ).fetchone()
        occurrence = migrated.execute(
            "SELECT occurrence_id, schedule_id, status FROM memory_alert_occurrences"
        ).fetchone()
        version = migrated.execute(
            "SELECT COUNT(*) FROM memory_schema_migrations WHERE version=?", (SCHEMA_VERSION,)
        ).fetchone()[0]
    assert tuple(alert) == ("old-timer", "legacy-occurrence-old-timer")
    assert tuple(schedule) == (
        "legacy-schedule-old-timer", "source-a", "local", "source-a"
    )
    assert tuple(occurrence) == (
        "legacy-occurrence-old-timer", "legacy-schedule-old-timer", "scheduled"
    )
    assert version == 1
