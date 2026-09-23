from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .alert_lifecycle import acknowledge_alert_occurrence, create_semantic_alert_schedule
from .calendar_runtime.canonical import CanonicalCalendarExecution
from .memory.alert_lifecycle import list_alert_occurrences, list_alert_schedules
from .memory.alerts import list_alert_records


def reconcile_calendar_alerts(
    execution: CanonicalCalendarExecution,
    *,
    now: datetime,
    db_path: Path | None = None,
) -> dict[str, int]:
    clock = now.astimezone(UTC)
    feeds_seen = intents_seen = schedules_created = schedules_reused = 0
    for feed in execution.settings.read.feeds_for_kind("events"):
        if not feed.alert_enabled:
            continue
        feeds_seen += 1
        snapshot = execution.load_calendar(
            scope="personal", calendar_id=feed.id, require_config=True,
            force_refresh=False, allow_stale=False,
        )
        if not snapshot.complete or snapshot.freshness != "fresh":
            continue
        for event in snapshot.events:
            for intent in event.reminder_intents:
                if not clock - timedelta(minutes=20) <= intent.due_at.astimezone(UTC) <= clock + timedelta(days=35):
                    continue
                intents_seen += 1
                _schedule, created = create_semantic_alert_schedule(
                    kind="reminder",
                    start_at=intent.due_at,
                    timezone_name=execution.settings.timezone,
                    creator_source_id="background",
                    session_id=None,
                    message=intent.event_summary,
                    target_scope="recipient" if feed.user_ids else "household",
                    recipient_user_ids=feed.user_ids,
                    metadata={
                        "calendar_alert": True,
                        "calendar_feed_id": feed.id,
                        "calendar_source_label": feed.label,
                        "event_uid": event.uid,
                        "event_occurrence_id": intent.event_occurrence_id,
                        "provider_reminder_id": intent.reminder_id,
                        "event_start": intent.event_start.isoformat(),
                    },
                    idempotency_key=f"calendar:{feed.id}:{intent.event_occurrence_id}:{intent.reminder_id}",
                    db_path=db_path,
                )
                schedules_created += int(created)
                schedules_reused += int(not created)
    return {
        "feeds_seen": feeds_seen,
        "intents_seen": intents_seen,
        "schedules_created": schedules_created,
        "schedules_reused": schedules_reused,
    }


def build_calendar_alert_state(*, source_id: str, db_path: Path | None = None) -> dict[str, Any]:
    schedules = {item.schedule_id: item for item in list_alert_schedules(statuses=("active",), db_path=db_path) if item.kind == "reminder" and item.metadata.get("calendar_alert")}
    occurrences = {item.occurrence_id: item for item in list_alert_occurrences(statuses=("outstanding", "overdue"), db_path=db_path) if item.schedule_id in schedules}
    deliveries = list_alert_records(source_id=source_id, kind="calendar", statuses=("pending", "leased", "acknowledged", "completed"), db_path=db_path)
    rows = []
    seen: set[str] = set()
    for delivery in deliveries:
        occurrence = occurrences.get(str(delivery.occurrence_id or ""))
        if occurrence is None or occurrence.occurrence_id in seen:
            continue
        seen.add(occurrence.occurrence_id)
        schedule = schedules[occurrence.schedule_id]
        rows.append({
            "kind": "calendar", "schedule_id": schedule.schedule_id,
            "occurrence_id": occurrence.occurrence_id, "alert_id": delivery.alert_id,
            "message": schedule.message, "label": "Calendar alert",
            "status": occurrence.status, "due_at": occurrence.due_at.isoformat(),
            "calendar_feed_id": schedule.metadata.get("calendar_feed_id"),
            "event_uid": schedule.metadata.get("event_uid"),
        })
    rows.sort(key=lambda item: (item["due_at"], item["occurrence_id"]))
    return {"calendar_alerts": rows, "count": len(rows)}


def dismiss_calendar_alert(
    occurrence_id: str, *, source_id: str, idempotency_key: str,
    now: datetime, db_path: Path | None = None,
) -> dict[str, Any]:
    visible = next((item for item in list_alert_records(source_id=source_id, kind="calendar", db_path=db_path) if item.occurrence_id == occurrence_id), None)
    if visible is None:
        raise KeyError(occurrence_id)
    occurrence = acknowledge_alert_occurrence(
        occurrence_id=occurrence_id, alert_id=visible.alert_id,
        actor_type="destination", actor_id=source_id, action="dismissed",
        idempotency_key=idempotency_key, now=now, db_path=db_path,
    )
    return {"ok": True, "action": "dismiss", "occurrence_id": occurrence.occurrence_id}
