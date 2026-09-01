from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .schema import ensure_schema
from .store import DB_PATH, transaction


SCHEDULE_STATUSES = frozenset({"active", "disabled", "completed", "canceled", "deleted"})
OCCURRENCE_STATUSES = frozenset({
    "scheduled", "due", "ringing", "outstanding", "snoozed", "completed",
    "missed", "overdue", "canceled", "skipped",
})
TERMINAL_OCCURRENCE_STATUSES = frozenset({"completed", "missed", "canceled", "skipped"})

_OCCURRENCE_TRANSITIONS = {
    "scheduled": frozenset({"due", "outstanding", "snoozed", "completed", "missed", "overdue", "canceled", "skipped"}),
    "due": frozenset({"ringing", "completed", "missed", "canceled", "snoozed"}),
    "ringing": frozenset({"completed", "missed", "canceled", "snoozed"}),
    "outstanding": frozenset({"completed", "overdue", "canceled", "snoozed"}),
    "snoozed": frozenset({"due", "outstanding", "completed", "missed", "overdue", "canceled"}),
    "overdue": frozenset({"completed", "canceled", "snoozed"}),
}


@dataclass(frozen=True)
class AlertScheduleRecord:
    schedule_id: str
    kind: str
    schedule_type: str
    status: str
    timezone: str
    start_at: datetime
    local_time: str | None
    recurrence: dict[str, Any]
    creator_source_id: str
    session_id: str | None
    message: str
    target_scope: str
    target_id: str | None
    recipient_user_ids: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None


@dataclass(frozen=True)
class AlertOccurrenceRecord:
    occurrence_id: str
    schedule_id: str
    occurrence_key: str
    due_at: datetime
    intended_local: str
    status: str
    parent_occurrence_id: str | None = None
    recipient_user_id: str | None = None
    config_revision: str | None = None
    completed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertAcknowledgementRecord:
    acknowledgement_id: str
    occurrence_id: str
    alert_id: str | None
    created_at: datetime
    actor_type: str
    actor_id: str
    action: str
    idempotency_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


def create_alert_schedule(
    *,
    kind: str,
    schedule_type: str,
    timezone_name: str,
    start_at: datetime,
    local_time: str | None,
    recurrence: dict[str, Any] | None,
    creator_source_id: str,
    session_id: str | None,
    message: str,
    target_scope: str,
    target_id: str | None = None,
    recipient_user_ids: Iterable[str] = (),
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    schedule_id: str | None = None,
    created_at: datetime | None = None,
    db_path: Path | None = None,
) -> tuple[AlertScheduleRecord, bool]:
    path = db_path or DB_PATH
    ensure_schema(path)
    clean_kind = _choice(kind, {"timer", "alarm", "reminder"}, "kind")
    clean_type = _choice(schedule_type, {"one_time", "recurring"}, "schedule_type")
    clean_timezone = _timezone(timezone_name)
    clean_start = _timestamp(start_at, "start_at")
    clean_created = _timestamp(created_at or _utc_now(), "created_at")
    clean_source = _required(creator_source_id, "creator_source_id")
    clean_message = _required(message, "message")
    clean_scope = _choice(target_scope, {"local", "room", "household", "recipient"}, "target_scope")
    recipients = tuple(dict.fromkeys(_required(item, "recipient_user_id") for item in recipient_user_ids))
    if clean_scope == "recipient" and not recipients:
        raise ValueError("Recipient alert schedules require semantic recipients")
    if clean_scope != "recipient" and recipients:
        raise ValueError("Only recipient alert schedules may retain semantic recipients")
    recurrence_value = dict(recurrence or {})
    if clean_type == "recurring" and not recurrence_value:
        raise ValueError("Recurring alert schedules require a recurrence rule")
    if clean_type == "one_time" and recurrence_value:
        raise ValueError("One-time alert schedules cannot carry recurrence")
    clean_key = str(idempotency_key or "").strip() or None
    clean_id = str(schedule_id or f"alert-schedule-{uuid.uuid4().hex}").strip()
    with transaction(path) as conn:
        _require_active_source(conn, clean_source)
        if clean_key:
            existing = conn.execute(
                "SELECT * FROM memory_alert_schedules WHERE creator_source_id=? AND idempotency_key=?",
                (clean_source, clean_key),
            ).fetchone()
            if existing is not None:
                record = _schedule_row(existing)
                expected = (
                    clean_kind, clean_type, clean_timezone, clean_start, clean_source,
                    clean_message, clean_scope, str(target_id or "").strip() or None,
                    recipients, recurrence_value,
                )
                actual = (
                    record.kind, record.schedule_type, record.timezone, record.start_at,
                    record.creator_source_id, record.message, record.target_scope,
                    record.target_id, record.recipient_user_ids, record.recurrence,
                )
                if actual != expected:
                    raise ValueError("Alert schedule idempotency key conflicts with different intent")
                return record, False
        conn.execute(
            """INSERT INTO memory_alert_schedules (
                   schedule_id, created_at, updated_at, kind, schedule_type, status,
                   timezone, start_at, local_time, recurrence_json, creator_source_id,
                   session_id, message, target_scope, target_id, recipients_json,
                   metadata_json, idempotency_key
               ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                clean_id, clean_created.isoformat(), clean_created.isoformat(), clean_kind,
                clean_type, clean_timezone, clean_start.isoformat(), str(local_time or "").strip() or None,
                _json(recurrence_value), clean_source, str(session_id or "").strip() or None,
                clean_message, clean_scope, str(target_id or "").strip() or None,
                _json(list(recipients)), _json(dict(metadata or {})), clean_key,
            ),
        )
        row = conn.execute("SELECT * FROM memory_alert_schedules WHERE schedule_id=?", (clean_id,)).fetchone()
    if row is None:
        raise RuntimeError("Created alert schedule could not be reloaded")
    return _schedule_row(row), True


def get_alert_schedule(schedule_id: str, *, db_path: Path | None = None) -> AlertScheduleRecord:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM memory_alert_schedules WHERE schedule_id=?",
            (_required(schedule_id, "schedule_id"),),
        ).fetchone()
    if row is None:
        raise KeyError(f"Unknown alert schedule {schedule_id}")
    return _schedule_row(row)


def list_alert_schedules(
    *, statuses: Iterable[str] | None = None, db_path: Path | None = None
) -> list[AlertScheduleRecord]:
    path = db_path or DB_PATH
    ensure_schema(path)
    args: list[str] = []
    clause = ""
    if statuses is not None:
        clean = tuple(dict.fromkeys(_choice(item, SCHEDULE_STATUSES, "status") for item in statuses))
        if not clean:
            return []
        clause = " WHERE status IN (" + ",".join("?" for _ in clean) + ")"
        args.extend(clean)
    with transaction(path) as conn:
        rows = conn.execute(
            "SELECT * FROM memory_alert_schedules" + clause + " ORDER BY start_at, schedule_id",
            args,
        ).fetchall()
    return [_schedule_row(row) for row in rows]


def set_alert_schedule_status(
    schedule_id: str,
    *,
    status: str,
    now: datetime,
    db_path: Path | None = None,
) -> AlertScheduleRecord:
    path = db_path or DB_PATH
    ensure_schema(path)
    clean_id = _required(schedule_id, "schedule_id")
    clean_status = _choice(status, SCHEDULE_STATUSES, "status")
    clock = _timestamp(now, "now")
    allowed = {
        "active": {"disabled", "completed", "canceled", "deleted"},
        "disabled": {"active", "canceled", "deleted"},
    }
    with transaction(path) as conn:
        current = conn.execute(
            "SELECT * FROM memory_alert_schedules WHERE schedule_id=?", (clean_id,)
        ).fetchone()
        if current is None:
            raise KeyError(f"Unknown alert schedule {clean_id}")
        previous = str(current["status"])
        if previous == clean_status:
            return _schedule_row(current)
        if clean_status not in allowed.get(previous, set()):
            raise ValueError(f"Invalid alert schedule transition {previous} -> {clean_status}")
        conn.execute(
            "UPDATE memory_alert_schedules SET status=?, updated_at=? WHERE schedule_id=?",
            (clean_status, clock.isoformat(), clean_id),
        )
        row = conn.execute(
            "SELECT * FROM memory_alert_schedules WHERE schedule_id=?", (clean_id,)
        ).fetchone()
    assert row is not None
    return _schedule_row(row)


def update_alert_schedule(
    schedule_id: str,
    *,
    start_at: datetime,
    local_time: str,
    recurrence: dict[str, Any],
    message: str,
    metadata: dict[str, Any],
    now: datetime,
    db_path: Path | None = None,
) -> AlertScheduleRecord:
    """Replace the mutable semantic fields of an active alarm or reminder schedule."""

    path = db_path or DB_PATH
    ensure_schema(path)
    clean_id = _required(schedule_id, "schedule_id")
    clean_start = _timestamp(start_at, "start_at")
    clean_local_time = _required(local_time, "local_time")
    clean_message = _required(message, "message")
    clock = _timestamp(now, "now")
    with transaction(path) as conn:
        current = conn.execute(
            "SELECT * FROM memory_alert_schedules WHERE schedule_id=?", (clean_id,)
        ).fetchone()
        if current is None:
            raise KeyError(f"Unknown alert schedule {clean_id}")
        if str(current["kind"]) not in {"alarm", "reminder"} or str(current["status"]) != "active":
            raise ValueError("Only an active alarm or reminder schedule may be edited")
        recurrence_value = dict(recurrence)
        schedule_type = "recurring" if recurrence_value else "one_time"
        conn.execute(
            """UPDATE memory_alert_schedules
               SET updated_at=?, schedule_type=?, start_at=?, local_time=?,
                   recurrence_json=?, message=?, metadata_json=?
               WHERE schedule_id=?""",
            (
                clock.isoformat(), schedule_type, clean_start.isoformat(),
                clean_local_time, _json(recurrence_value), clean_message,
                _json(dict(metadata)), clean_id,
            ),
        )
        row = conn.execute(
            "SELECT * FROM memory_alert_schedules WHERE schedule_id=?", (clean_id,)
        ).fetchone()
    assert row is not None
    return _schedule_row(row)


def create_alert_occurrence(
    *,
    schedule_id: str,
    occurrence_key: str,
    due_at: datetime,
    intended_local: str,
    metadata: dict[str, Any] | None = None,
    occurrence_id: str | None = None,
    parent_occurrence_id: str | None = None,
    recipient_user_id: str | None = None,
    created_at: datetime | None = None,
    db_path: Path | None = None,
) -> tuple[AlertOccurrenceRecord, bool]:
    path = db_path or DB_PATH
    ensure_schema(path)
    clean_schedule = _required(schedule_id, "schedule_id")
    clean_key = _required(occurrence_key, "occurrence_key")
    clean_due = _timestamp(due_at, "due_at")
    clean_created = _timestamp(created_at or _utc_now(), "created_at")
    clean_id = str(occurrence_id or f"alert-occurrence-{uuid.uuid4().hex}").strip()
    with transaction(path) as conn:
        schedule = conn.execute(
            "SELECT status FROM memory_alert_schedules WHERE schedule_id=?", (clean_schedule,)
        ).fetchone()
        if schedule is None:
            raise KeyError(f"Unknown alert schedule {clean_schedule}")
        if str(schedule["status"]) != "active":
            raise ValueError("Occurrences may be materialized only for active schedules")
        existing = conn.execute(
            "SELECT * FROM memory_alert_occurrences WHERE schedule_id=? AND occurrence_key=?",
            (clean_schedule, clean_key),
        ).fetchone()
        if existing is not None:
            return _occurrence_row(existing), False
        conn.execute(
            """INSERT INTO memory_alert_occurrences (
                   occurrence_id, schedule_id, occurrence_key, created_at, updated_at,
                   due_at, intended_local, status, parent_occurrence_id,
                   recipient_user_id, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)""",
            (
                clean_id, clean_schedule, clean_key, clean_created.isoformat(),
                clean_created.isoformat(), clean_due.isoformat(),
                _required(intended_local, "intended_local"),
                str(parent_occurrence_id or "").strip() or None,
                str(recipient_user_id or "").strip() or None,
                _json(dict(metadata or {})),
            ),
        )
        _occurrence_transition_row(
            conn, clean_id, None, "scheduled", "system", None, "materialized", clean_created
        )
        row = conn.execute(
            "SELECT * FROM memory_alert_occurrences WHERE occurrence_id=?", (clean_id,)
        ).fetchone()
    if row is None:
        raise RuntimeError("Created alert occurrence could not be reloaded")
    return _occurrence_row(row), True


def list_alert_occurrences(
    *,
    schedule_id: str | None = None,
    statuses: Iterable[str] | None = None,
    due_before: datetime | None = None,
    db_path: Path | None = None,
) -> list[AlertOccurrenceRecord]:
    path = db_path or DB_PATH
    ensure_schema(path)
    clauses: list[str] = []
    args: list[str] = []
    if schedule_id is not None:
        clauses.append("schedule_id=?")
        args.append(_required(schedule_id, "schedule_id"))
    if statuses is not None:
        clean = tuple(dict.fromkeys(_choice(item, OCCURRENCE_STATUSES, "status") for item in statuses))
        if not clean:
            return []
        clauses.append("status IN (" + ",".join("?" for _ in clean) + ")")
        args.extend(clean)
    if due_before is not None:
        clauses.append("due_at<=?")
        args.append(_timestamp(due_before, "due_before").isoformat())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with transaction(path) as conn:
        rows = conn.execute(
            "SELECT * FROM memory_alert_occurrences" + where + " ORDER BY due_at, occurrence_id",
            args,
        ).fetchall()
    return [_occurrence_row(row) for row in rows]


def transition_alert_occurrence(
    occurrence_id: str,
    *,
    status: str,
    actor_type: str,
    actor_id: str | None,
    reason: str,
    now: datetime,
    config_revision: str | None = None,
    metadata_update: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> AlertOccurrenceRecord:
    path = db_path or DB_PATH
    ensure_schema(path)
    clean_id = _required(occurrence_id, "occurrence_id")
    clean_status = _choice(status, OCCURRENCE_STATUSES, "status")
    clean_actor = _choice(actor_type, {"runtime", "destination", "person", "system"}, "actor_type")
    clock = _timestamp(now, "now")
    with transaction(path) as conn:
        current = conn.execute(
            "SELECT * FROM memory_alert_occurrences WHERE occurrence_id=?", (clean_id,)
        ).fetchone()
        if current is None:
            raise KeyError(f"Unknown alert occurrence {clean_id}")
        previous = str(current["status"])
        current_metadata = _object(current["metadata_json"])
        updated_metadata = {**current_metadata, **dict(metadata_update or {})}
        if previous == clean_status:
            if config_revision is None and updated_metadata == current_metadata:
                return _occurrence_row(current)
            conn.execute(
                """UPDATE memory_alert_occurrences
                   SET updated_at=?, config_revision=COALESCE(?, config_revision), metadata_json=?
                   WHERE occurrence_id=?""",
                (clock.isoformat(), config_revision, _json(updated_metadata), clean_id),
            )
            row = conn.execute(
                "SELECT * FROM memory_alert_occurrences WHERE occurrence_id=?", (clean_id,)
            ).fetchone()
            assert row is not None
            return _occurrence_row(row)
        if clean_status not in _OCCURRENCE_TRANSITIONS.get(previous, frozenset()):
            raise ValueError(f"Invalid alert occurrence transition {previous} -> {clean_status}")
        completed_at = clock.isoformat() if clean_status in TERMINAL_OCCURRENCE_STATUSES else None
        conn.execute(
            """UPDATE memory_alert_occurrences
               SET status=?, updated_at=?, completed_at=?,
                   config_revision=COALESCE(?, config_revision), metadata_json=?
               WHERE occurrence_id=?""",
            (
                clean_status, clock.isoformat(), completed_at, config_revision,
                _json(updated_metadata), clean_id,
            ),
        )
        _occurrence_transition_row(
            conn, clean_id, previous, clean_status, clean_actor,
            str(actor_id or "").strip() or None, reason, clock,
        )
        if clean_status in TERMINAL_OCCURRENCE_STATUSES:
            schedule_status = "canceled" if clean_status == "canceled" else "completed"
            conn.execute(
                """UPDATE memory_alert_schedules
                   SET status=?, updated_at=?
                   WHERE schedule_id=? AND schedule_type='one_time'
                     AND status IN ('active','disabled')""",
                (schedule_status, clock.isoformat(), current["schedule_id"]),
            )
        row = conn.execute(
            "SELECT * FROM memory_alert_occurrences WHERE occurrence_id=?", (clean_id,)
        ).fetchone()
    assert row is not None
    return _occurrence_row(row)


def reschedule_alert_occurrence(
    occurrence_id: str,
    *,
    due_at: datetime,
    intended_local: str,
    actor_type: str,
    actor_id: str,
    now: datetime,
    db_path: Path | None = None,
) -> AlertOccurrenceRecord:
    path = db_path or DB_PATH
    ensure_schema(path)
    clean_id = _required(occurrence_id, "occurrence_id")
    clean_actor = _choice(actor_type, {"person", "system"}, "actor_type")
    clock = _timestamp(now, "now")
    clean_due = _timestamp(due_at, "due_at")
    if clean_due <= clock:
        raise ValueError("Occurrence override must remain in the future")
    with transaction(path) as conn:
        current = conn.execute(
            "SELECT * FROM memory_alert_occurrences WHERE occurrence_id=?", (clean_id,)
        ).fetchone()
        if current is None:
            raise KeyError(f"Unknown alert occurrence {clean_id}")
        if str(current["status"]) != "scheduled":
            raise ValueError("Only a scheduled occurrence may be rescheduled")
        metadata = _object(current["metadata_json"])
        metadata.setdefault("original_due_at", str(current["due_at"]))
        conn.execute(
            """UPDATE memory_alert_occurrences
               SET due_at=?, intended_local=?, updated_at=?, metadata_json=?
               WHERE occurrence_id=?""",
            (
                clean_due.isoformat(), _required(intended_local, "intended_local"),
                clock.isoformat(), _json(metadata), clean_id,
            ),
        )
        _occurrence_transition_row(
            conn, clean_id, "scheduled", "scheduled", clean_actor, actor_id,
            "occurrence_rescheduled", clock,
        )
        row = conn.execute(
            "SELECT * FROM memory_alert_occurrences WHERE occurrence_id=?", (clean_id,)
        ).fetchone()
    assert row is not None
    return _occurrence_row(row)


def record_alert_acknowledgement(
    *,
    occurrence_id: str,
    actor_type: str,
    actor_id: str,
    action: str,
    idempotency_key: str,
    now: datetime,
    alert_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> tuple[AlertAcknowledgementRecord, bool]:
    path = db_path or DB_PATH
    ensure_schema(path)
    clean_occurrence = _required(occurrence_id, "occurrence_id")
    clean_actor = _choice(actor_type, {"runtime", "destination", "person", "system"}, "actor_type")
    clean_action = _choice(action, {"delivery_accepted", "copy_dismissed", "acknowledged", "dismissed", "snoozed", "completed"}, "action")
    _validate_acknowledgement(clean_actor, clean_action)
    clean_actor_id = _required(actor_id, "actor_id")
    clean_key = _required(idempotency_key, "idempotency_key")
    clock = _timestamp(now, "now")
    with transaction(path) as conn:
        occurrence_row = conn.execute(
            """SELECT occurrence.status, occurrence.recipient_user_id, schedule.kind
               FROM memory_alert_occurrences AS occurrence
               JOIN memory_alert_schedules AS schedule
                 ON schedule.schedule_id=occurrence.schedule_id
               WHERE occurrence.occurrence_id=?""",
            (clean_occurrence,),
        ).fetchone()
        if occurrence_row is None:
            raise KeyError(f"Unknown alert occurrence {clean_occurrence}")
        delivery = None
        if alert_id is not None:
            delivery = conn.execute(
                """SELECT occurrence_id, source_id, status, delivery_role
                   FROM memory_alerts WHERE alert_id=?""",
                (alert_id,),
            ).fetchone()
            if delivery is None or str(delivery["occurrence_id"] or "") != clean_occurrence:
                raise ValueError("Acknowledgement delivery does not belong to the occurrence")
        _validate_acknowledgement_context(
            actor_type=clean_actor,
            actor_id=clean_actor_id,
            action=clean_action,
            occurrence_status=str(occurrence_row["status"]),
            occurrence_kind=str(occurrence_row["kind"]),
            recipient_user_id=str(occurrence_row["recipient_user_id"] or "") or None,
            delivery=delivery,
        )
        existing = conn.execute(
            """SELECT * FROM memory_alert_acknowledgements
               WHERE occurrence_id=? AND idempotency_key=?""",
            (clean_occurrence, clean_key),
        ).fetchone()
        if existing is not None:
            record = _acknowledgement_row(existing)
            if (
                record.actor_type != clean_actor
                or record.actor_id != clean_actor_id
                or record.action != clean_action
                or record.alert_id != alert_id
            ):
                raise ValueError("Alert acknowledgement idempotency key conflicts with another transition")
            return record, False
        acknowledgement_id = f"alert-ack-{uuid.uuid4().hex}"
        conn.execute(
            """INSERT INTO memory_alert_acknowledgements (
                   acknowledgement_id, occurrence_id, alert_id, created_at,
                   actor_type, actor_id, action, idempotency_key, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                acknowledgement_id, clean_occurrence, alert_id, clock.isoformat(),
                clean_actor, clean_actor_id, clean_action, clean_key,
                _json(dict(metadata or {})),
            ),
        )
        row = conn.execute(
            "SELECT * FROM memory_alert_acknowledgements WHERE acknowledgement_id=?",
            (acknowledgement_id,),
        ).fetchone()
    assert row is not None
    return _acknowledgement_row(row), True


def attach_delivery_lifecycle(
    *,
    alert_id: str,
    occurrence_id: str,
    delivery_role: str,
    recipient_user_id: str | None,
    config_revision: str,
    db_path: Path | None = None,
) -> None:
    path = db_path or DB_PATH
    ensure_schema(path)
    clean_role = _choice(delivery_role, {"destination", "common"}, "delivery_role")
    with transaction(path) as conn:
        occurrence = conn.execute(
            "SELECT 1 FROM memory_alert_occurrences WHERE occurrence_id=?",
            (_required(occurrence_id, "occurrence_id"),),
        ).fetchone()
        if occurrence is None:
            raise KeyError(f"Unknown alert occurrence {occurrence_id}")
        updated = conn.execute(
            """UPDATE memory_alerts
               SET occurrence_id=?, delivery_role=?, recipient_user_id=?, config_revision=?
               WHERE alert_id=?""",
            (
                occurrence_id, clean_role, str(recipient_user_id or "").strip() or None,
                _required(config_revision, "config_revision"), _required(alert_id, "alert_id"),
            ),
        )
        if updated.rowcount != 1:
            raise KeyError(f"Unknown alert delivery {alert_id}")


def close_occurrence_deliveries(
    occurrence_id: str,
    *,
    status: str,
    now: datetime,
    reason: str,
    db_path: Path | None = None,
) -> int:
    path = db_path or DB_PATH
    ensure_schema(path)
    clean_status = _choice(status, {"completed", "canceled"}, "delivery status")
    clock = _timestamp(now, "now")
    changed = 0
    with transaction(path) as conn:
        rows = conn.execute(
            """SELECT alert_id, source_id, status FROM memory_alerts
               WHERE occurrence_id=? AND status IN ('pending','leased')""",
            (_required(occurrence_id, "occurrence_id"),),
        ).fetchall()
        for row in rows:
            previous = str(row["status"])
            alert_id = str(row["alert_id"])
            conn.execute(
                """UPDATE memory_alerts
                   SET status=?, updated_at=?, lease_id=NULL, leased_at=NULL,
                       lease_expires_at=NULL,
                       completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END,
                       canceled_at=CASE WHEN ?='canceled' THEN ? ELSE canceled_at END
                   WHERE alert_id=? AND status=?""",
                (
                    clean_status, clock.isoformat(), clean_status, clock.isoformat(),
                    clean_status, clock.isoformat(), alert_id, previous,
                ),
            )
            conn.execute(
                """INSERT INTO memory_alert_transitions (
                       transition_id, alert_id, created_at, from_status, to_status,
                       source_id, lease_id, reason
                   ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    f"alert-transition-{uuid.uuid4().hex}", alert_id, clock.isoformat(),
                    previous, clean_status, row["source_id"], str(reason or "")[:160],
                ),
            )
            changed += 1
    return changed


def close_alert_delivery(
    alert_id: str,
    *,
    now: datetime,
    reason: str,
    db_path: Path | None = None,
) -> None:
    path = db_path or DB_PATH
    ensure_schema(path)
    clock = _timestamp(now, "now")
    clean_id = _required(alert_id, "alert_id")
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT source_id, status FROM memory_alerts WHERE alert_id=?", (clean_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown alert delivery {clean_id}")
        previous = str(row["status"])
        if previous == "canceled":
            return
        if previous not in {"pending", "leased", "acknowledged"}:
            raise ValueError("Only an active alert delivery may be dismissed")
        conn.execute(
            """UPDATE memory_alerts SET status='canceled', updated_at=?, canceled_at=?,
                   lease_id=NULL, leased_at=NULL, lease_expires_at=NULL
               WHERE alert_id=?""",
            (clock.isoformat(), clock.isoformat(), clean_id),
        )
        conn.execute(
            """INSERT INTO memory_alert_transitions (
                   transition_id, alert_id, created_at, from_status, to_status,
                   source_id, lease_id, reason
               ) VALUES (?, ?, ?, ?, 'canceled', ?, NULL, ?)""",
            (
                f"alert-transition-{uuid.uuid4().hex}", clean_id, clock.isoformat(),
                previous, row["source_id"], str(reason or "")[:160],
            ),
        )


def occurrence_delivery_exists(
    *,
    occurrence_id: str,
    source_id: str,
    delivery_role: str,
    recipient_user_id: str | None,
    db_path: Path | None = None,
) -> bool:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        row = conn.execute(
            """SELECT 1 FROM memory_alerts
               WHERE occurrence_id=? AND source_id=? AND delivery_role=?
                 AND COALESCE(recipient_user_id, '')=? LIMIT 1""",
            (
                _required(occurrence_id, "occurrence_id"),
                _required(source_id, "source_id"),
                _choice(delivery_role, {"destination", "common"}, "delivery_role"),
                str(recipient_user_id or "").strip(),
            ),
        ).fetchone()
    return row is not None


def _validate_acknowledgement(actor_type: str, action: str) -> None:
    allowed = {
        "runtime": {"delivery_accepted"},
        "destination": {"copy_dismissed", "dismissed"},
        "person": {"acknowledged", "dismissed", "snoozed"},
        "system": {"completed", "snoozed"},
    }
    if action not in allowed[actor_type]:
        raise ValueError(f"Actor {actor_type!r} cannot record acknowledgement action {action!r}")


def _validate_acknowledgement_context(
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    occurrence_status: str,
    occurrence_kind: str,
    recipient_user_id: str | None,
    delivery: Any | None,
) -> None:
    if actor_type == "person" and occurrence_kind == "reminder" and actor_id != recipient_user_id:
        raise ValueError("Reminder acknowledgement actor must be the occurrence recipient")
    if actor_type == "runtime":
        if (
            delivery is None
            or str(delivery["source_id"]) != actor_id
            or str(delivery["status"]) not in {"acknowledged", "completed"}
        ):
            raise ValueError("Runtime acceptance requires its accepted delivery")
    elif actor_type == "destination":
        if delivery is None or str(delivery["source_id"]) != actor_id:
            raise ValueError("Destination dismissal requires that destination's delivery")
        if action == "copy_dismissed" and str(delivery["delivery_role"]) != "common":
            raise ValueError("Common-copy dismissal requires that destination's common delivery")
        if action == "dismissed" and (
            str(delivery["delivery_role"]) == "common"
            or occurrence_kind not in {"timer", "alarm"}
            or occurrence_status not in {"due", "ringing"}
        ):
            raise ValueError("Logical destination dismissal requires an active timer or alarm delivery")
    elif actor_type == "person" and occurrence_status not in {
        "due", "ringing", "outstanding", "overdue"
    }:
        raise ValueError("Person acknowledgement requires an active due occurrence")


def _occurrence_transition_row(
    conn: Any,
    occurrence_id: str,
    from_status: str | None,
    to_status: str,
    actor_type: str,
    actor_id: str | None,
    reason: str,
    at: datetime,
) -> None:
    conn.execute(
        """INSERT INTO memory_alert_occurrence_transitions (
               transition_id, occurrence_id, created_at, from_status, to_status,
               actor_type, actor_id, reason
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"alert-occurrence-transition-{uuid.uuid4().hex}", occurrence_id,
            at.isoformat(), from_status, to_status, actor_type, actor_id,
            str(reason or "").strip()[:160],
        ),
    )


def _schedule_row(row: Any) -> AlertScheduleRecord:
    return AlertScheduleRecord(
        schedule_id=str(row["schedule_id"]), kind=str(row["kind"]),
        schedule_type=str(row["schedule_type"]), status=str(row["status"]),
        timezone=str(row["timezone"]), start_at=_timestamp(row["start_at"], "start_at"),
        local_time=str(row["local_time"]) if row["local_time"] is not None else None,
        recurrence=_object(row["recurrence_json"]), creator_source_id=str(row["creator_source_id"]),
        session_id=str(row["session_id"]) if row["session_id"] is not None else None,
        message=str(row["message"]), target_scope=str(row["target_scope"]),
        target_id=str(row["target_id"]) if row["target_id"] is not None else None,
        recipient_user_ids=tuple(str(item) for item in _array(row["recipients_json"])),
        metadata=_object(row["metadata_json"]),
        idempotency_key=str(row["idempotency_key"]) if row["idempotency_key"] is not None else None,
    )


def _occurrence_row(row: Any) -> AlertOccurrenceRecord:
    return AlertOccurrenceRecord(
        occurrence_id=str(row["occurrence_id"]), schedule_id=str(row["schedule_id"]),
        occurrence_key=str(row["occurrence_key"]), due_at=_timestamp(row["due_at"], "due_at"),
        intended_local=str(row["intended_local"]), status=str(row["status"]),
        parent_occurrence_id=str(row["parent_occurrence_id"]) if row["parent_occurrence_id"] is not None else None,
        recipient_user_id=str(row["recipient_user_id"]) if row["recipient_user_id"] is not None else None,
        config_revision=str(row["config_revision"]) if row["config_revision"] is not None else None,
        completed_at=_optional_timestamp(row["completed_at"]), metadata=_object(row["metadata_json"]),
    )


def _acknowledgement_row(row: Any) -> AlertAcknowledgementRecord:
    return AlertAcknowledgementRecord(
        acknowledgement_id=str(row["acknowledgement_id"]), occurrence_id=str(row["occurrence_id"]),
        alert_id=str(row["alert_id"]) if row["alert_id"] is not None else None,
        created_at=_timestamp(row["created_at"], "created_at"), actor_type=str(row["actor_type"]),
        actor_id=str(row["actor_id"]), action=str(row["action"]),
        idempotency_key=str(row["idempotency_key"]), metadata=_object(row["metadata_json"]),
    )


def _require_active_source(conn: Any, source_id: str) -> None:
    row = conn.execute("SELECT status FROM memory_sources WHERE source_id=?", (source_id,)).fetchone()
    if row is None or str(row["status"]) != "active":
        raise ValueError(f"Alert source {source_id!r} is not an active canonical identity")


def _choice(value: object, allowed: Iterable[str], field: str) -> str:
    clean = _required(value, field)
    if clean not in allowed:
        raise ValueError(f"Unsupported {field} {clean!r}")
    return clean


def _timezone(value: object) -> str:
    clean = _required(value, "timezone")
    try:
        ZoneInfo(clean)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Alert schedule timezone must be an installed IANA identifier") from exc
    return clean


def _required(value: object, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field} is required")
    return clean


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(value: object) -> datetime | None:
    return None if value in (None, "") else _timestamp(value, "timestamp")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _object(value: object) -> dict[str, Any]:
    parsed = json.loads(str(value or "{}"))
    return parsed if isinstance(parsed, dict) else {}


def _array(value: object) -> list[Any]:
    parsed = json.loads(str(value or "[]"))
    return parsed if isinstance(parsed, list) else []


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
