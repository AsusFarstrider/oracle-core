from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from .alert_recurrence import RecurrenceRule
from .alarms import parse_calendar_recurrence, parse_scheduled_alert_due
from .alert_lifecycle import (
    acknowledge_alert_occurrence,
    change_alert_schedule_status,
    create_semantic_alert_schedule,
    materialize_schedule_occurrences,
    override_alert_occurrence,
    skip_alert_occurrence,
    snooze_alert_occurrence,
)
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from .deterministic_values import parse_duration, parse_number
from .memory.alert_lifecycle import (
    AlertOccurrenceRecord,
    AlertScheduleRecord,
    list_alert_occurrences,
    list_alert_schedules,
    transition_alert_occurrence,
    update_alert_schedule,
)
from .memory.alerts import ALERT_STATUSES, AlertRecord, list_alert_records
from .memory.store import DB_PATH
from .session_state import get_active_user_id
from .temporal import parse_clock_candidates, resolve_local_wall_time


ACTIVE_REMINDER_STATUSES = frozenset({"scheduled", "outstanding", "overdue", "snoozed"})
VISIBLE_REMINDER_SCHEDULE_STATUSES = ("active", "disabled", "completed")
DEFAULT_SNOOZE_MINUTES = 10
MAX_SNOOZE_MINUTES = 24 * 60


@dataclass(frozen=True)
class ReminderSubject:
    schedule: AlertScheduleRecord
    occurrence: AlertOccurrenceRecord | None
    delivery: AlertRecord | None = None

    @property
    def text(self) -> str:
        return str(self.schedule.metadata.get("reminder_text") or self.schedule.message).strip()


def looks_like_reminder_followup(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {
        "dismiss", "dismiss it", "done", "mark it done", "snooze", "snooze it",
        "skip the next one", "turn it off", "turn it on", "delete it",
    } or bool(re.fullmatch(r"snooze(?: it)?(?: for .+)?", normalized))


def execute_reminder_command(
    text: str,
    *,
    source_id: str,
    session_id: str | None,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    clock = _utc(now or datetime.now(UTC))
    normalized = _normalize(text)
    if _looks_like_creation(normalized):
        return _create_reminder(
            normalized, source_id=source_id, session_id=session_id,
            household=household, satellites=satellites, now=clock, db_path=db_path,
        )

    subjects = _reminder_subjects(
        source_id=source_id, household=household, satellites=satellites, db_path=db_path
    )
    operation = _operation(normalized)
    active = [item for item in subjects if item.occurrence and item.occurrence.status in {"outstanding", "overdue"}]
    if operation in {"dismiss", "snooze"}:
        selected = _select_subject(normalized, active, context=context)
        if selected is None:
            if not active:
                return "There are no outstanding reminders here.", _details(operation, count=0)
            return _clarification(normalized, active, operation)
        return _manage_active(
            selected, operation=operation, normalized=normalized, source_id=source_id,
            household=household, now=clock, db_path=db_path,
        )

    if operation in {"status", "count", "next"}:
        manageable = [item for item in subjects if item.schedule.status in {"active", "disabled"}]
        if operation == "count":
            outstanding = sum(bool(item.occurrence and item.occurrence.status in {"outstanding", "overdue"}) for item in manageable)
            return f"You have {len(manageable)} {_plural('reminder', len(manageable))}, {outstanding} outstanding.", _details(
                "count", count=len(manageable), outstanding_count=outstanding
            )
        if not manageable:
            return "You have no reminders here.", _details(operation, count=0)
        if operation == "next":
            upcoming = [item for item in manageable if item.schedule.status == "active" and item.occurrence]
            if not upcoming:
                return "You have no upcoming reminders here.", _details("next", count=0)
            selected = min(upcoming, key=lambda item: item.occurrence.due_at)  # type: ignore[union-attr]
            return _next_reply(selected, clock, household)
        return _list_reply(manageable, clock, household)

    selected = _select_subject(normalized, subjects, context=context)
    if selected is None:
        candidates = [item for item in subjects if item.schedule.status in {"active", "disabled"}]
        if not candidates:
            return "You have no matching reminders here.", _details(operation, count=0)
        return _clarification(normalized, candidates, operation)

    if operation in {"disable", "enable", "delete"}:
        updated = _change_schedule_state(
            selected, operation=operation, actor_id=_actor_user(source_id, household) or source_id,
            now=clock, db_path=db_path,
        )
        verb = {"disable": "Turned off", "enable": "Turned on", "delete": "Deleted"}[operation]
        return f"{verb} the reminder to {selected.text}.", _details(
            operation, schedule_id=updated.schedule_id, status=updated.status,
            terminal=operation == "delete",
        )

    if operation == "skip":
        occurrence = _next_occurrence(selected)
        if occurrence is None or selected.schedule.schedule_type != "recurring":
            raise ValueError("Skip-next requires an enabled recurring reminder with an upcoming occurrence.")
        actor = _actor_user(source_id, household)
        if occurrence.recipient_user_id and actor != occurrence.recipient_user_id:
            raise ValueError("Only the reminder recipient may skip that occurrence.")
        skipped = skip_alert_occurrence(
            occurrence.occurrence_id, actor_id=actor or source_id, now=clock, db_path=db_path
        )
        return f"Skipped the next reminder to {selected.text}.", _details(
            "skip", schedule_id=selected.schedule.schedule_id,
            occurrence_id=skipped.occurrence_id,
        )

    if operation == "edit":
        scope = _edit_scope(normalized, selected)
        if scope == "ambiguous":
            return (
                "Should I change the recurring reminder schedule or only its next occurrence?",
                _details(
                    "edit", status="clarification_required",
                    options=["the schedule", "the next occurrence"],
                    original_text=normalized, subject_text="reminder",
                    clarification_kind="reminder_edit_scope",
                ),
            )
        new_due, new_local_time = _edited_time(
            normalized, selected, now=clock, timezone_name=selected.schedule.timezone
        )
        actor = _actor_user(source_id, household) or source_id
        if scope == "occurrence":
            occurrence = _next_occurrence(selected)
            if occurrence is None:
                raise ValueError("That reminder has no upcoming occurrence to change.")
            if occurrence.recipient_user_id and actor != occurrence.recipient_user_id:
                raise ValueError("Only the reminder recipient may change that occurrence.")
            updated_occurrence = override_alert_occurrence(
                occurrence.occurrence_id, due_at=new_due,
                intended_local=new_due.astimezone(ZoneInfo(selected.schedule.timezone)).replace(tzinfo=None).isoformat(timespec="seconds"),
                actor_id=actor, now=clock, db_path=db_path,
            )
            return f"Changed only the next reminder to {selected.text} to {_format_due(new_due, selected.schedule.timezone)}.", _selected_details(
                "edit_occurrence", ReminderSubject(selected.schedule, updated_occurrence), clock, household
            )
        updated_schedule = update_alert_schedule(
            selected.schedule.schedule_id, start_at=new_due,
            local_time=new_local_time.isoformat(timespec="seconds"),
            recurrence=selected.schedule.recurrence,
            message=_reminder_message(selected.text), metadata=selected.schedule.metadata,
            now=clock, db_path=db_path,
        )
        for existing in list_alert_occurrences(
            schedule_id=updated_schedule.schedule_id, db_path=db_path or DB_PATH
        ):
            if existing.status == "scheduled" and existing.due_at > clock:
                transition_alert_occurrence(
                    existing.occurrence_id, status="canceled", actor_type="system",
                    actor_id=actor, reason="reminder_series_schedule_replaced", now=clock,
                    db_path=db_path,
                )
        replacements = materialize_schedule_occurrences(
            updated_schedule, through=clock + timedelta(days=366), db_path=db_path
        )
        occurrence = next(
            (item for item in replacements if _occurrence_visible(item, source_id, household, satellites)),
            None,
        )
        return f"Changed the reminder to {selected.text} to {_format_clock(new_local_time)}.", _selected_details(
            "edit_schedule", ReminderSubject(updated_schedule, occurrence), clock, household
        )

    raise ValueError("I need a reminder time or a reminder management request.")


def build_reminder_state(
    *,
    source_id: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clock = _utc(now or datetime.now(UTC))
    subjects = _reminder_subjects(
        source_id=source_id, household=household, satellites=satellites, db_path=db_path
    )
    reminders = [_serialize(item, clock, household) for item in subjects if item.schedule.status != "deleted"]
    outstanding = [item for item in reminders if item.get("occurrence_status") in {"outstanding", "overdue"}]
    upcoming = [item for item in reminders if item.get("occurrence_status") in {"scheduled", "snoozed"}]
    return {
        "source_id": source_id, "generated_at": clock.isoformat(),
        "count": len(reminders), "reminders": reminders,
        "outstanding": outstanding, "overdue": [item for item in outstanding if item.get("overdue")],
        "upcoming": upcoming,
        "next": min(upcoming, key=lambda item: str(item.get("due_at") or ""), default=None),
        "display_attention_required": bool(outstanding),
    }


def manage_reminder(
    *,
    source_id: str,
    action: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    occurrence_id: str,
    snooze_minutes: int = DEFAULT_SNOOZE_MINUTES,
    idempotency_key: str,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clock = _utc(now or datetime.now(UTC))
    selected = next(
        (
            item for item in _reminder_subjects(
                source_id=source_id, household=household, satellites=satellites, db_path=db_path
            )
            if item.occurrence and item.occurrence.occurrence_id == occurrence_id
        ),
        None,
    )
    if selected is None or selected.occurrence is None:
        raise KeyError("Unknown visible reminder occurrence")
    if selected.occurrence.status not in {"outstanding", "overdue"}:
        raise ValueError("Only an outstanding reminder may be managed")
    speech, details = _manage_active(
        selected, operation=action, normalized=f"{action} for {snooze_minutes} minutes",
        source_id=source_id, household=household, now=clock,
        idempotency_key=idempotency_key, db_path=db_path,
    )
    return {
        "ok": True, "action": action,
        "schedule_id": selected.schedule.schedule_id,
        "occurrence_id": str(details.get("occurrence_id") or occurrence_id),
        "speech": speech,
    }


def _create_reminder(
    normalized: str,
    *,
    source_id: str,
    session_id: str | None,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    now: datetime,
    db_path: Path | None,
) -> tuple[str, dict[str, Any]]:
    recipient_phrase, when_text, reminder_text = _parse_creation_parts(normalized)
    recipients, include_common = _resolve_recipients(
        recipient_phrase, source_id=source_id, session_id=session_id, household=household
    )
    if recipients is None:
        options = [item.display_name for item in household.users.values() if item.enabled]
        if not recipient_phrase:
            options.insert(0, "everyone")
        return (
            "Who should I remind: " + ", ".join(options) + "?",
            _details(
                "create", status="clarification_required", options=options,
                original_text=normalized, subject_text=recipient_phrase or "recipient",
                clarification_kind="reminder_recipient",
            ),
        )
    rule, recurrence_text = parse_calendar_recurrence(when_text)
    due_at, local_time = _parse_due(
        when_text, now=now, timezone_name=household.household.timezone, recurrence=rule
    )
    if due_at is None:
        resume_text = f"remind {recipient_phrase} {when_text} at __TIME__ to {reminder_text}"
        return (
            "What exact time should I use for that reminder?",
            _details(
                "create", status="clarification_required", options=[],
                original_text=resume_text, subject_text="__TIME__",
                clarification_kind="reminder_time",
            ),
        )
    schedule, _created = create_semantic_alert_schedule(
        kind="reminder", start_at=due_at,
        timezone_name=household.household.timezone,
        creator_source_id=source_id, session_id=session_id,
        message=_reminder_message(reminder_text), target_scope="recipient",
        recipient_user_ids=recipients, recurrence=rule,
        metadata={
            "reminder_text": reminder_text,
            "recurrence_text": recurrence_text,
            "include_common_copy": include_common,
        },
        db_path=db_path,
    )
    occurrences = materialize_schedule_occurrences(
        schedule, through=now + timedelta(days=366) if rule else due_at, db_path=db_path
    )
    occurrence = next(
        (item for item in occurrences if _occurrence_visible(item, source_id, household, satellites)),
        occurrences[0] if len(occurrences) == 1 else None,
    )
    recipient_text = "everyone" if include_common else household.user(recipients[0]).display_name
    recurrence_words = f" {recurrence_text}" if recurrence_text else ""
    return (
        f"Reminder set for {recipient_text} {_format_due(due_at, household.household.timezone)}{recurrence_words}: {reminder_text}.",
        _selected_details("create", ReminderSubject(schedule, occurrence), now, household),
    )


def _parse_creation_parts(text: str) -> tuple[str, str, str]:
    before_message = re.fullmatch(
        r"remind\s+(?P<recipient>.+?)\s+(?P<when>(?:in|at|on|tomorrow|next|this|every|daily|weekdays|weekends|monday|tuesday|wednesday|thursday|friday|saturday|sunday|one|two|three|four|five|six|seven|eight|nine|ten|the first|the second|the third|the fourth|the last).+?)\s+to\s+(?P<message>.+)",
        text,
    )
    if before_message:
        return (
            before_message.group("recipient").strip(),
            before_message.group("when").strip(),
            before_message.group("message").strip(),
        )
    after_message = re.fullmatch(
        r"remind\s+(?P<recipient>.+?)\s+to\s+(?P<message>.+?)\s+(?P<when>(?:in|at|on|tomorrow|next|this|every|daily|weekdays|weekends).+)",
        text,
    )
    if after_message:
        return (
            after_message.group("recipient").strip(),
            after_message.group("when").strip(),
            after_message.group("message").strip(),
        )
    raise ValueError("I need a reminder message and when it should be due.")


def _resolve_recipients(
    phrase: str,
    *,
    source_id: str,
    session_id: str | None,
    household: HouseholdRuntimeSettings,
) -> tuple[tuple[str, ...] | None, bool]:
    clean = _normalize(phrase)
    if clean in {"everyone", "everybody", "the family", "all of us"}:
        recipients = tuple(sorted(item.id for item in household.users.values() if item.enabled))
        if not recipients:
            raise ValueError("There are no enabled household users to remind.")
        return recipients, True
    if clean in {"me", "myself"}:
        session_user = get_active_user_id(source_id, session_id)
        session_identity = household.user(session_user) if session_user else None
        if session_identity is not None and session_identity.enabled:
            return (session_user,), False
        associated = household.configured_associated_user_id(source_id)
        return ((associated,), False) if associated else (None, False)
    resolved = household.resolve_user_id(clean)
    if resolved is None:
        return None, False
    identity = household.user(resolved)
    if identity is None or not identity.enabled:
        return None, False
    return (resolved,), False


def _parse_due(
    when_text: str,
    *,
    now: datetime,
    timezone_name: str,
    recurrence: RecurrenceRule | None,
) -> tuple[datetime | None, time | None]:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    relative = re.fullmatch(r"in (.+)", when_text)
    if relative:
        duration = parse_duration(relative.group(1), max_seconds=366 * 86400)
        if duration is None:
            raise ValueError("I need a valid reminder duration.")
        due = local_now + timedelta(seconds=duration.seconds)
        return due.astimezone(UTC), due.timetz().replace(tzinfo=None)
    weeks = re.search(r"(.+?) weeks? from today", when_text)
    if weeks:
        count = parse_number(weeks.group(1))
        clocks = parse_clock_candidates(_clock_text(when_text))
        if count is None or count != count.to_integral_value() or int(count) < 1:
            raise ValueError("I need a valid number of weeks for that reminder.")
        if not clocks:
            return None, None
        target_date = local_now.date() + timedelta(weeks=int(count))
        due = resolve_local_wall_time(target_date, clocks[0], timezone_name)
        return due.astimezone(UTC), clocks[0]
    if not parse_clock_candidates(_clock_text(when_text)):
        return None, None
    due, clock_value = parse_scheduled_alert_due(
        f"set an alarm {when_text}", local_now=local_now, recurrence=recurrence
    )
    return due, clock_value


def _clock_text(text: str) -> str:
    match = re.search(r"\b(\d{1,2}(?::[0-5]\d)?\s*(?:am|pm))\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(?:at|for|to)\s+(\d{1,2}(?::[0-5]\d)?)\b", text)
    return match.group(1) if match else ""


def _reminder_subjects(
    *,
    source_id: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    db_path: Path | None,
) -> list[ReminderSubject]:
    schedules = {
        item.schedule_id: item for item in list_alert_schedules(
            statuses=VISIBLE_REMINDER_SCHEDULE_STATUSES, db_path=db_path or DB_PATH
        ) if item.kind == "reminder"
    }
    occurrences = list_alert_occurrences(db_path=db_path or DB_PATH)
    deliveries = list_alert_records(
        source_id=source_id, kind="reminder", statuses=ALERT_STATUSES, db_path=db_path or DB_PATH
    )
    by_occurrence = {item.occurrence_id: item for item in deliveries if item.occurrence_id}
    result: list[ReminderSubject] = []
    for schedule in schedules.values():
        visible = [
            item for item in occurrences
            if item.schedule_id == schedule.schedule_id
            and _occurrence_visible(item, source_id, household, satellites)
        ]
        visible.sort(key=lambda item: item.due_at)
        parent_ids = {item.parent_occurrence_id for item in visible if item.parent_occurrence_id}
        occurrence = next((item for item in visible if item.status in {"outstanding", "overdue"}), None)
        if occurrence is None:
            occurrence = next(
                (
                    item for item in visible
                    if item.status in ACTIVE_REMINDER_STATUSES
                    and not (item.status == "snoozed" and item.occurrence_id in parent_ids)
                ),
                None,
            )
        if occurrence is not None:
            delivery = by_occurrence.get(occurrence.occurrence_id)
            if occurrence.metadata.get("common_copy") and delivery is not None and delivery.status == "canceled":
                continue
            result.append(ReminderSubject(schedule, occurrence, delivery))
    return sorted(result, key=lambda item: (item.occurrence.due_at if item.occurrence else datetime.max.replace(tzinfo=UTC), item.schedule.schedule_id))


def _occurrence_visible(
    occurrence: AlertOccurrenceRecord,
    source_id: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
) -> bool:
    if source_id not in satellites.enabled_satellite_ids_by_source:
        return False
    associated = household.configured_associated_user_id(source_id)
    if occurrence.metadata.get("common_copy"):
        if associated is not None:
            return False
    elif occurrence.recipient_user_id != associated or associated is None:
        return False
    frozen = occurrence.metadata.get("destinations")
    if occurrence.config_revision and isinstance(frozen, list):
        return any(isinstance(item, dict) and item.get("source_id") == source_id for item in frozen)
    return True


def _manage_active(
    subject: ReminderSubject,
    *,
    operation: str,
    normalized: str,
    source_id: str,
    household: HouseholdRuntimeSettings,
    now: datetime,
    idempotency_key: str | None = None,
    db_path: Path | None,
) -> tuple[str, dict[str, Any]]:
    occurrence = subject.occurrence
    if occurrence is None:
        raise ValueError("That reminder is not outstanding.")
    if occurrence.metadata.get("common_copy"):
        if operation != "dismiss":
            raise ValueError("A common reminder copy may only be dismissed locally.")
        if subject.delivery is None:
            raise ValueError("The common reminder copy has no delivery on this destination.")
        acknowledge_alert_occurrence(
            occurrence_id=occurrence.occurrence_id, alert_id=subject.delivery.alert_id,
            actor_type="destination", actor_id=source_id, action="copy_dismissed",
            idempotency_key=idempotency_key or f"reminder-copy-dismiss:{occurrence.occurrence_id}:{source_id}",
            now=now, db_path=db_path,
        )
        return "Dismissed the household reminder from this display.", _selected_details(
            "dismiss", subject, now, household, terminal=True, copy_only=True
        )
    actor = _actor_user(source_id, household)
    if not actor or actor != occurrence.recipient_user_id:
        raise ValueError("Only the reminder recipient may manage that reminder.")
    if operation == "dismiss":
        completed = acknowledge_alert_occurrence(
            occurrence_id=occurrence.occurrence_id, actor_type="person", actor_id=actor,
            action="dismissed",
            idempotency_key=idempotency_key or f"reminder-dismiss:{occurrence.occurrence_id}:{actor}",
            now=now, alert_id=subject.delivery.alert_id if subject.delivery else None,
            db_path=db_path,
        )
        return f"Marked the reminder to {subject.text} done.", _selected_details(
            "dismiss", ReminderSubject(subject.schedule, completed), now, household, terminal=True
        )
    if operation != "snooze":
        raise ValueError(f"Unsupported reminder action {operation!r}")
    minutes = _snooze_minutes(normalized)
    snoozed = snooze_alert_occurrence(
        occurrence.occurrence_id, until=now + timedelta(minutes=minutes),
        actor_type="person", actor_id=actor, now=now, db_path=db_path,
    )
    return f"Snoozed the reminder to {subject.text} for {minutes} {_plural('minute', minutes)}.", _selected_details(
        "snooze", ReminderSubject(subject.schedule, snoozed), now, household
    )


def _change_schedule_state(
    subject: ReminderSubject,
    *,
    operation: str,
    actor_id: str,
    now: datetime,
    db_path: Path | None,
) -> AlertScheduleRecord:
    schedule = subject.schedule
    if operation == "enable":
        if schedule.schedule_type == "one_time" and schedule.start_at <= now:
            raise ValueError("That one-time reminder has already passed. Set a new reminder instead.")
        return change_alert_schedule_status(schedule.schedule_id, status="active", now=now, db_path=db_path)
    desired = "disabled" if operation == "disable" else "deleted"
    updated = change_alert_schedule_status(schedule.schedule_id, status=desired, now=now, db_path=db_path)
    if operation == "delete":
        for occurrence in list_alert_occurrences(schedule_id=schedule.schedule_id, db_path=db_path or DB_PATH):
            if occurrence.status in ACTIVE_REMINDER_STATUSES:
                transition_alert_occurrence(
                    occurrence.occurrence_id, status="canceled", actor_type="system",
                    actor_id=actor_id, reason="reminder_schedule_deleted", now=now, db_path=db_path,
                )
    return updated


def _select_subject(
    text: str,
    subjects: list[ReminderSubject],
    *,
    context: dict[str, Any] | None,
) -> ReminderSubject | None:
    payload = context.get("payload") if isinstance(context, dict) else None
    if isinstance(payload, dict) and payload.get("alert_kind") == "reminder":
        occurrence_id = str(payload.get("occurrence_id") or "")
        schedule_id = str(payload.get("schedule_id") or "")
        matches = [
            item for item in subjects
            if (occurrence_id and item.occurrence and item.occurrence.occurrence_id == occurrence_id)
            or (schedule_id and item.schedule.schedule_id == schedule_id)
        ]
        if len(matches) == 1:
            return matches[0]
    selector = _selector(text)
    if selector:
        matches = [item for item in subjects if selector in _normalize(item.text)]
        if len(matches) == 1:
            return matches[0]
    return subjects[0] if len(subjects) == 1 else None


def _selector(text: str) -> str:
    match = re.search(r"(?:my|the) (.+?) reminder", text)
    if not match:
        return ""
    value = _normalize(match.group(1))
    return "" if value in {"next", "current", "outstanding", "recurring"} else value


def _edit_scope(text: str, subject: ReminderSubject) -> str:
    if re.search(r"\b(?:tomorrow|next occurrence|next one|this occurrence|today|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)'s)\b", text):
        return "occurrence"
    if subject.schedule.schedule_type == "one_time":
        return "occurrence"
    if re.search(r"\b(?:weekday|weekend|daily|schedule|every|series)\b", text) or _selector(text):
        return "schedule"
    return "ambiguous"


def _edited_time(
    text: str,
    subject: ReminderSubject,
    *,
    now: datetime,
    timezone_name: str,
) -> tuple[datetime, time]:
    clocks = parse_clock_candidates(_clock_text(text))
    if not clocks:
        raise ValueError("I need a valid new reminder time.")
    zone = ZoneInfo(timezone_name)
    base = (subject.occurrence.due_at if subject.occurrence else subject.schedule.start_at).astimezone(zone)
    target_date = base.date()
    if "tomorrow" in text:
        target_date = now.astimezone(zone).date() + timedelta(days=1)
    candidates = [resolve_local_wall_time(target_date, item, timezone_name) for item in clocks]
    future = [item for item in candidates if item.astimezone(UTC) > now]
    if not future:
        raise ValueError("The new reminder time must be in the future.")
    selected = min(future)
    return selected.astimezone(UTC), selected.timetz().replace(tzinfo=None)


def _operation(text: str) -> str:
    if "snooze" in text or "more minutes" in text:
        return "snooze"
    if "skip" in text:
        return "skip"
    if re.search(r"\b(?:dismiss|done|complete|acknowledge|mark it done)\b", text):
        return "dismiss"
    if re.search(r"\b(?:delete|remove|cancel)\b", text):
        return "delete"
    if any(item in text for item in ("turn off", "disable")):
        return "disable"
    if any(item in text for item in ("turn on", "back on", "enable", "re-enable")):
        return "enable"
    if re.search(r"\b(?:change|move|make)\b", text):
        return "edit"
    if "how many" in text:
        return "count"
    if "next reminder" in text or "reminder is next" in text:
        return "next"
    return "status"


def _looks_like_creation(text: str) -> bool:
    return text.startswith("remind ") and not re.search(r"\b(?:change|move|turn|delete|remove|cancel|skip)\b", text)


def _snooze_minutes(text: str) -> int:
    match = re.search(r"snooze(?: it)?(?: for)? (.+?) minutes?", text)
    if not match:
        return DEFAULT_SNOOZE_MINUTES
    value = parse_number(match.group(1))
    if value is None or value != value.to_integral_value() or not 1 <= int(value) <= MAX_SNOOZE_MINUTES:
        raise ValueError("Reminder snooze must be between 1 minute and 24 hours.")
    return int(value)


def _next_occurrence(subject: ReminderSubject) -> AlertOccurrenceRecord | None:
    return subject.occurrence if subject.occurrence and subject.occurrence.status in ACTIVE_REMINDER_STATUSES else None


def _actor_user(source_id: str, household: HouseholdRuntimeSettings) -> str | None:
    return household.configured_associated_user_id(source_id)


def _serialize(
    subject: ReminderSubject,
    now: datetime,
    household: HouseholdRuntimeSettings,
) -> dict[str, Any]:
    occurrence = subject.occurrence
    common = bool(occurrence and occurrence.metadata.get("common_copy"))
    recipient = household.user(occurrence.recipient_user_id) if occurrence and occurrence.recipient_user_id else None
    return {
        "kind": "reminder", "schedule_id": subject.schedule.schedule_id,
        "occurrence_id": occurrence.occurrence_id if occurrence else None,
        "alert_id": subject.delivery.alert_id if subject.delivery else None,
        "message": subject.text,
        "label": "Household reminder" if common else f"Reminder for {recipient.display_name}" if recipient else "Reminder",
        "recipient_user_id": occurrence.recipient_user_id if occurrence else None,
        "common_copy": common,
        "schedule_status": subject.schedule.status,
        "schedule_type": subject.schedule.schedule_type,
        "occurrence_status": occurrence.status if occurrence else None,
        "status": occurrence.status if occurrence else subject.schedule.status,
        "due_at": occurrence.due_at.isoformat() if occurrence else None,
        "local_time": subject.schedule.local_time,
        "recurrence": dict(subject.schedule.recurrence),
        "recurrence_text": str(subject.schedule.metadata.get("recurrence_text") or ""),
        "late_seconds": max(0, int((now - occurrence.due_at).total_seconds())) if occurrence else 0,
        "outstanding": bool(occurrence and occurrence.status in {"outstanding", "overdue"}),
        "overdue": bool(occurrence and occurrence.status == "overdue"),
    }


def _selected_details(
    operation: str,
    subject: ReminderSubject,
    now: datetime,
    household: HouseholdRuntimeSettings,
    **extra: Any,
) -> dict[str, Any]:
    return _details(operation, **_serialize(subject, now, household), **extra)


def _list_reply(
    subjects: list[ReminderSubject],
    now: datetime,
    household: HouseholdRuntimeSettings,
) -> tuple[str, dict[str, Any]]:
    parts = [
        f"{item.text} ({item.occurrence.status if item.occurrence else item.schedule.status}; {_format_due(item.occurrence.due_at, item.schedule.timezone) if item.occurrence else 'no upcoming occurrence'})"
        for item in subjects[:6]
    ]
    return f"You have {len(subjects)} {_plural('reminder', len(subjects))}: " + "; ".join(parts) + ".", _details(
        "status", count=len(subjects), reminders=[_serialize(item, now, household) for item in subjects]
    )


def _next_reply(
    subject: ReminderSubject,
    now: datetime,
    household: HouseholdRuntimeSettings,
) -> tuple[str, dict[str, Any]]:
    assert subject.occurrence is not None
    return f"Your next reminder is {_format_due(subject.occurrence.due_at, subject.schedule.timezone)}: {subject.text}.", _selected_details(
        "next", subject, now, household
    )


def _clarification(
    text: str,
    subjects: list[ReminderSubject],
    operation: str,
) -> tuple[str, dict[str, Any]]:
    options = [item.text for item in subjects[:6]]
    return "Which reminder did you mean: " + ", ".join(options) + "?", _details(
        operation, status="clarification_required", options=options,
        original_text=text, subject_text="reminder",
        clarification_kind="reminder_subject",
    )


def _reminder_message(text: str) -> str:
    clean = text.strip().rstrip(".")
    return f"Reminder: {clean}."


def _format_due(value: datetime, timezone_name: str) -> str:
    local = value.astimezone(ZoneInfo(timezone_name))
    return f"{local.strftime('%A')} at {_format_clock(local.timetz().replace(tzinfo=None))}"


def _format_clock(value: time) -> str:
    hour = value.strftime("%I").lstrip("0") or "0"
    return f"{hour}:{value.strftime('%M %p')}"


def _details(operation: str, **values: Any) -> dict[str, Any]:
    return {"kind": "reminder", "operation": operation, **values}


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("’", "'").strip(" .?!").split())


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Reminder timestamps must be timezone-aware")
    return value.astimezone(UTC)
