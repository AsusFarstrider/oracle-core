from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .alert_recurrence import RecurrenceRule, expand_recurrence
from .alert_targeting import AlertDestination, resolve_alert_targets
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from .memory.alert_lifecycle import (
    AlertOccurrenceRecord,
    AlertScheduleRecord,
    create_alert_occurrence,
    create_alert_schedule,
    close_alert_delivery,
    close_occurrence_deliveries,
    get_alert_schedule,
    list_alert_occurrences,
    list_alert_schedules,
    occurrence_delivery_exists,
    record_alert_acknowledgement,
    reschedule_alert_occurrence,
    set_alert_schedule_status,
    transition_alert_occurrence,
)
from .memory.alerts import create_alert_record


_LATE_GRACE = {
    "timer": timedelta(minutes=10),
    "alarm": timedelta(minutes=20),
    "reminder": timedelta(minutes=20),
}


@dataclass(frozen=True)
class AlertReconciliationResult:
    schedules_seen: int
    occurrences_created: int
    occurrences_transitioned: int
    deliveries_created: int
    deliveries_reused: int


def create_semantic_alert_schedule(
    *,
    kind: str,
    start_at: datetime,
    timezone_name: str,
    creator_source_id: str,
    session_id: str | None,
    message: str,
    target_scope: str,
    target_id: str | None = None,
    recipient_user_ids: tuple[str, ...] = (),
    recurrence: RecurrenceRule | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    db_path: Path | None = None,
) -> tuple[AlertScheduleRecord, bool]:
    local = start_at.astimezone(_zone(timezone_name))
    return create_alert_schedule(
        kind=kind,
        schedule_type="recurring" if recurrence is not None else "one_time",
        timezone_name=timezone_name,
        start_at=start_at,
        local_time=local.timetz().replace(tzinfo=None).isoformat(timespec="seconds"),
        recurrence=None if recurrence is None else recurrence.as_dict(),
        creator_source_id=creator_source_id,
        session_id=session_id,
        message=message,
        target_scope=target_scope,
        target_id=target_id,
        recipient_user_ids=recipient_user_ids,
        metadata=metadata,
        idempotency_key=idempotency_key,
        db_path=db_path,
    )


def materialize_schedule_occurrences(
    schedule: AlertScheduleRecord,
    *,
    through: datetime,
    limit: int = 128,
    db_path: Path | None = None,
) -> tuple[AlertOccurrenceRecord, ...]:
    existing = list_alert_occurrences(schedule_id=schedule.schedule_id, db_path=db_path)
    if schedule.schedule_type == "one_time" and existing:
        return ()
    rule = RecurrenceRule.from_dict(schedule.recurrence) if schedule.recurrence else None
    created: list[AlertOccurrenceRecord] = []
    for instant in expand_recurrence(
        anchor=schedule.start_at,
        timezone_name=schedule.timezone,
        rule=rule,
        through=through,
        limit=limit,
    ):
        subjects: tuple[tuple[str | None, bool], ...] = ((None, False),)
        if schedule.target_scope == "recipient":
            subjects = tuple((user_id, False) for user_id in schedule.recipient_user_ids)
            if bool(schedule.metadata.get("include_common_copy")):
                if len(schedule.recipient_user_ids) < 2:
                    raise ValueError("A common reminder copy requires an everyone schedule")
                subjects += ((None, True),)
        for recipient_user_id, common_copy in subjects:
            subject_key = (
                f"{instant.key}|recipient:{recipient_user_id}"
                if recipient_user_id is not None
                else f"{instant.key}|common"
                if common_copy
                else instant.key
            )
            occurrence, was_created = create_alert_occurrence(
                schedule_id=schedule.schedule_id,
                occurrence_key=subject_key,
                due_at=instant.due_at,
                intended_local=instant.intended_local,
                recipient_user_id=recipient_user_id,
                metadata={"common_copy": True} if common_copy else None,
                db_path=db_path,
            )
            if was_created:
                created.append(occurrence)
    return tuple(created)


def reconcile_alert_lifecycle(
    *,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    now: datetime,
    horizon: timedelta = timedelta(days=35),
    db_path: Path | None = None,
) -> AlertReconciliationResult:
    clock = _utc(now)
    if not timedelta(0) < horizon <= timedelta(days=366):
        raise ValueError("Alert materialization horizon must be positive and at most 366 days")
    schedules = list_alert_schedules(statuses=("active",), db_path=db_path)
    created_count = 0
    transitioned = 0
    deliveries_created = 0
    deliveries_reused = 0
    by_schedule = {schedule.schedule_id: schedule for schedule in schedules}
    for schedule in schedules:
        created_count += len(
            materialize_schedule_occurrences(
                schedule, through=clock + horizon, db_path=db_path
            )
        )

    due = list_alert_occurrences(
        statuses=("scheduled", "snoozed", "due", "ringing", "outstanding"),
        due_before=clock,
        db_path=db_path,
    )
    for occurrence in due:
        if occurrence.status == "snoozed" and any(
            item.parent_occurrence_id == occurrence.occurrence_id
            for item in list_alert_occurrences(
                schedule_id=occurrence.schedule_id, db_path=db_path
            )
        ):
            # The snooze child owns the new deadline. The parent remains durable
            # lineage and must never re-enter delivery at its original due time.
            continue
        schedule = by_schedule.get(occurrence.schedule_id)
        if schedule is None:
            schedule = get_alert_schedule(occurrence.schedule_id, db_path=db_path)
        if occurrence.metadata.get("common_copy"):
            siblings = [
                item
                for item in list_alert_occurrences(
                    schedule_id=occurrence.schedule_id, db_path=db_path
                )
                if item.intended_local == occurrence.intended_local
                and item.recipient_user_id is not None
            ]
            if siblings and all(
                item.status in {"completed", "canceled", "skipped", "missed"}
                for item in siblings
            ):
                transition_alert_occurrence(
                    occurrence.occurrence_id,
                    status="completed",
                    actor_type="system",
                    actor_id=None,
                    reason="all_recipient_occurrences_resolved",
                    now=clock,
                    db_path=db_path,
                )
                close_occurrence_deliveries(
                    occurrence.occurrence_id,
                    status="completed",
                    now=clock,
                    reason="household_copy_resolved",
                    db_path=db_path,
                )
                transitioned += 1
                continue
        if schedule.status != "active":
            continue
        late_by = clock - occurrence.due_at
        grace = _LATE_GRACE[schedule.kind]
        if late_by > grace:
            terminal = "overdue" if schedule.kind == "reminder" else "missed"
            if occurrence.status != terminal:
                transition_alert_occurrence(
                    occurrence.occurrence_id,
                    status=terminal,
                    actor_type="system",
                    actor_id=None,
                    reason="recovery_grace_elapsed",
                    now=clock,
                    db_path=db_path,
                )
                transitioned += 1
            continue

        frozen_destinations = occurrence.metadata.get("destinations")
        if occurrence.config_revision and isinstance(frozen_destinations, list):
            destinations = tuple(
                AlertDestination(
                    source_id=str(item["source_id"]),
                    role=str(item.get("role") or "destination"),
                    recipient_user_id=str(item["recipient_user_id"]) if item.get("recipient_user_id") else None,
                )
                for item in frozen_destinations
                if isinstance(item, dict) and item.get("source_id")
            )
            config_revision = occurrence.config_revision
        else:
            target = resolve_alert_targets(
                household=household,
                satellites=satellites,
                scope=schedule.target_scope,
                requesting_source_id=schedule.creator_source_id,
                target_id=schedule.target_id,
                recipient_user_ids=(occurrence.recipient_user_id,)
                if occurrence.recipient_user_id is not None
                else schedule.recipient_user_ids,
                include_common_copy=bool(occurrence.metadata.get("common_copy")),
            )
            destinations = (
                tuple(item for item in target.destinations if item.role == "common")
                if occurrence.metadata.get("common_copy")
                else tuple(item for item in target.destinations if item.role != "common")
            )
            config_revision = target.config_revision
        desired_status = "outstanding" if schedule.kind == "reminder" else "due"
        # Runtime delivery acceptance advances timers and alarms from due to
        # ringing. Reconciliation may refresh their frozen projection, but it
        # must never move that logical occurrence backward to due.
        reconciled_status = (
            "ringing"
            if schedule.kind in {"timer", "alarm"} and occurrence.status == "ringing"
            else desired_status
        )
        if occurrence.status != reconciled_status or occurrence.config_revision is None:
            occurrence = transition_alert_occurrence(
                occurrence.occurrence_id,
                status=reconciled_status,
                actor_type="system",
                actor_id=None,
                reason="became_due",
                now=clock,
                config_revision=config_revision,
                metadata_update={
                    "destinations": [
                        {
                            "source_id": item.source_id,
                            "role": item.role,
                            "recipient_user_id": item.recipient_user_id,
                        }
                        for item in destinations
                    ]
                },
                db_path=db_path,
            )
            transitioned += 1
        for destination in destinations:
            created = _project_delivery(
                schedule=schedule,
                occurrence=occurrence,
                destination=destination,
                config_revision=config_revision,
                late_seconds=max(0, int(late_by.total_seconds())),
                created_at=clock,
                db_path=db_path,
            )
            deliveries_created += int(created)
            deliveries_reused += int(not created)
    return AlertReconciliationResult(
        schedules_seen=len(schedules),
        occurrences_created=created_count,
        occurrences_transitioned=transitioned,
        deliveries_created=deliveries_created,
        deliveries_reused=deliveries_reused,
    )


def acknowledge_alert_occurrence(
    *,
    occurrence_id: str,
    actor_type: str,
    actor_id: str,
    action: str,
    idempotency_key: str,
    now: datetime,
    alert_id: str | None = None,
    db_path: Path | None = None,
) -> AlertOccurrenceRecord:
    _record, created = record_alert_acknowledgement(
        occurrence_id=occurrence_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        idempotency_key=idempotency_key,
        now=now,
        alert_id=alert_id,
        db_path=db_path,
    )
    current = next(
        item for item in list_alert_occurrences(db_path=db_path)
        if item.occurrence_id == occurrence_id
    )
    if not created or action == "delivery_accepted":
        return current
    if action == "copy_dismissed":
        if alert_id is None:
            raise ValueError("Common-copy dismissal requires a delivery")
        close_alert_delivery(
            alert_id,
            now=now,
            reason="common_copy_dismissed",
            db_path=db_path,
        )
        return current
    target_status = "snoozed" if action == "snoozed" else "completed"
    transitioned = transition_alert_occurrence(
        occurrence_id,
        status=target_status,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=action,
        now=now,
        db_path=db_path,
    )
    close_occurrence_deliveries(
        occurrence_id,
        status="canceled" if target_status == "snoozed" else "completed",
        now=now,
        reason=f"logical_occurrence_{target_status}",
        db_path=db_path,
    )
    return transitioned


def skip_alert_occurrence(
    occurrence_id: str,
    *,
    actor_id: str,
    now: datetime,
    db_path: Path | None = None,
) -> AlertOccurrenceRecord:
    return transition_alert_occurrence(
        occurrence_id,
        status="skipped",
        actor_type="person",
        actor_id=actor_id,
        reason="skip_next",
        now=now,
        db_path=db_path,
    )


def change_alert_schedule_status(
    schedule_id: str,
    *,
    status: str,
    now: datetime,
    db_path: Path | None = None,
) -> AlertScheduleRecord:
    return set_alert_schedule_status(
        schedule_id, status=status, now=now, db_path=db_path
    )


def override_alert_occurrence(
    occurrence_id: str,
    *,
    due_at: datetime,
    intended_local: str,
    actor_id: str,
    now: datetime,
    db_path: Path | None = None,
) -> AlertOccurrenceRecord:
    return reschedule_alert_occurrence(
        occurrence_id,
        due_at=due_at,
        intended_local=intended_local,
        actor_type="person",
        actor_id=actor_id,
        now=now,
        db_path=db_path,
    )


def snooze_alert_occurrence(
    occurrence_id: str,
    *,
    until: datetime,
    actor_type: str,
    actor_id: str,
    now: datetime,
    db_path: Path | None = None,
) -> AlertOccurrenceRecord:
    current = next(
        item for item in list_alert_occurrences(db_path=db_path)
        if item.occurrence_id == occurrence_id
    )
    if _utc(until) <= _utc(now):
        raise ValueError("Snooze time must be in the future")
    acknowledge_alert_occurrence(
        occurrence_id=occurrence_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="snoozed",
        idempotency_key=f"snooze:{occurrence_id}:{_utc(until).isoformat()}",
        now=now,
        db_path=db_path,
    )
    snoozed, _created = create_alert_occurrence(
        schedule_id=current.schedule_id,
        occurrence_key=f"snooze:{occurrence_id}:{_utc(until).isoformat()}",
        due_at=until,
        intended_local=_utc(until).isoformat(),
        parent_occurrence_id=occurrence_id,
        recipient_user_id=current.recipient_user_id,
        metadata={**current.metadata, "snoozed_from": occurrence_id},
        db_path=db_path,
    )
    return snoozed


def _project_delivery(
    *,
    schedule: AlertScheduleRecord,
    occurrence: AlertOccurrenceRecord,
    destination: AlertDestination,
    config_revision: str,
    late_seconds: int,
    created_at: datetime,
    db_path: Path | None,
) -> bool:
    if occurrence_delivery_exists(
        occurrence_id=occurrence.occurrence_id,
        source_id=destination.source_id,
        delivery_role=destination.role,
        recipient_user_id=destination.recipient_user_id,
        db_path=db_path,
    ):
        return False
    alert, created = create_alert_record(
        kind=schedule.kind,
        due_at=occurrence.due_at,
        message=schedule.message,
        source_id=destination.source_id,
        session_id=schedule.session_id,
        metadata={
            "schedule_id": schedule.schedule_id,
            "occurrence_id": occurrence.occurrence_id,
            "late_seconds": late_seconds,
        },
        expires_at=occurrence.due_at + _LATE_GRACE[schedule.kind] + timedelta(seconds=1),
        idempotency_key=f"occurrence:{occurrence.occurrence_id}:{destination.role}:{destination.recipient_user_id or '-'}",
        occurrence_id=occurrence.occurrence_id,
        delivery_role=destination.role,
        recipient_user_id=destination.recipient_user_id,
        config_revision=config_revision,
        created_at=created_at,
        db_path=db_path,
    )
    if alert.occurrence_id != occurrence.occurrence_id:
        raise RuntimeError("Alert delivery idempotency resolved to another occurrence")
    return created


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Alert lifecycle timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)
