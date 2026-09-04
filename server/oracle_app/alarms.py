from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from .alert_lifecycle import (
    acknowledge_alert_occurrence,
    change_alert_schedule_status,
    create_semantic_alert_schedule,
    materialize_schedule_occurrences,
    override_alert_occurrence,
    skip_alert_occurrence,
    snooze_alert_occurrence,
)
from .alert_recurrence import RecurrenceRule
from .alert_targeting import resolve_alert_targets
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from .deterministic_values import parse_duration, parse_number, parse_ordinal
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
from .temporal import parse_clock_candidates, parse_relative_date, resolve_local_wall_time


ACTIVE_ALARM_STATUSES = frozenset({"scheduled", "due", "ringing", "snoozed"})
VISIBLE_ALARM_SCHEDULE_STATUSES = ("active", "disabled", "completed")
DEFAULT_SNOOZE_MINUTES = 10
MAX_SNOOZE_MINUTES = 120

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}


def parse_calendar_recurrence(text: str) -> tuple[RecurrenceRule | None, str]:
    """Parse the shared Alarm/Reminder recurrence language."""

    return _parse_recurrence(_normalize(text))


def parse_scheduled_alert_due(
    text: str,
    *,
    local_now: datetime,
    recurrence: RecurrenceRule | None,
) -> tuple[datetime, time]:
    """Resolve shared clock/date language through the canonical temporal owner."""

    return _parse_alarm_due(_normalize(text), local_now=local_now, recurrence=recurrence)


@dataclass(frozen=True)
class AlarmSubject:
    schedule: AlertScheduleRecord
    occurrence: AlertOccurrenceRecord | None
    delivery: AlertRecord | None = None

    @property
    def name(self) -> str | None:
        value = str(self.schedule.metadata.get("name") or "").strip()
        return value or None


def looks_like_alarm_followup(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {
        "stop", "stop it", "dismiss", "dismiss it", "snooze", "snooze it",
        "give me ten more minutes", "skip the next one", "turn it off", "turn it on",
    } or bool(re.fullmatch(r"snooze(?: (?:it|the alarm))?(?: for .+)?", normalized))


def execute_alarm_command(
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
    subjects = _alarm_subjects(
        source_id=source_id, household=household, satellites=satellites, db_path=db_path
    )
    ringing = [item for item in subjects if item.occurrence and item.occurrence.status in {"due", "ringing"}]

    if _looks_like_creation(normalized):
        return _create_alarm(
            normalized, source_id=source_id, session_id=session_id,
            household=household, satellites=satellites, now=clock, db_path=db_path,
        )

    operation = _operation(normalized)
    if operation in {"dismiss", "snooze"}:
        selected = _select_subject(normalized, ringing, context=context, prefer_occurrence=True)
        if selected is None:
            if not ringing:
                return "No alarm is ringing here.", _details(operation, count=0)
            return _clarification(normalized, ringing, operation)
        assert selected.occurrence is not None
        if operation == "dismiss":
            _dismiss(selected, source_id=source_id, now=clock, db_path=db_path)
            return f"Dismissed {_possessive_label(selected)}.", _selected_details(operation, selected, clock, terminal=True)
        minutes = _snooze_minutes(normalized)
        snoozed = snooze_alert_occurrence(
            selected.occurrence.occurrence_id,
            until=clock + timedelta(minutes=minutes),
            actor_type="system", actor_id=source_id, now=clock, db_path=db_path,
        )
        updated = AlarmSubject(selected.schedule, snoozed)
        return (
            f"Snoozed {_possessive_label(selected)} for {minutes} {_plural('minute', minutes)}.",
            _selected_details("snooze", updated, clock),
        )

    if operation in {"status", "count", "next"}:
        manageable = [item for item in subjects if item.schedule.status in {"active", "disabled"}]
        if operation == "count":
            enabled = sum(item.schedule.status == "active" for item in manageable)
            return f"You have {len(manageable)} {_plural('alarm', len(manageable))}, {enabled} enabled.", _details(
                "count", count=len(manageable), enabled_count=enabled
            )
        if not manageable:
            return "You have no alarms.", _details(operation, count=0)
        if operation == "next":
            upcoming = [item for item in manageable if item.schedule.status == "active" and item.occurrence]
            if not upcoming:
                return "You have no enabled upcoming alarms.", _details("next", count=0)
            selected = min(upcoming, key=lambda item: item.occurrence.due_at)  # type: ignore[union-attr]
            return _next_reply(selected, clock)
        return _list_reply(manageable, clock)

    selected = _select_subject(normalized, subjects, context=context, prefer_occurrence=False)
    if selected is None:
        candidates = [item for item in subjects if item.schedule.status in {"active", "disabled"}]
        if not candidates:
            return "You have no matching alarms.", _details(operation, count=0)
        return _clarification(normalized, candidates, operation)

    if operation in {"disable", "enable", "delete"}:
        updated = _change_schedule_state(selected, operation=operation, actor_id=source_id, now=clock, db_path=db_path)
        verb = {"disable": "Turned off", "enable": "Turned on", "delete": "Deleted"}[operation]
        return f"{verb} {_possessive_label(selected)}.", _details(
            operation, schedule_id=updated.schedule_id, status=updated.status, terminal=operation == "delete"
        )

    if operation == "skip":
        occurrence = _next_occurrence(selected)
        if occurrence is None or selected.schedule.schedule_type != "recurring":
            raise ValueError("Skip-next requires an enabled recurring alarm with an upcoming occurrence.")
        skipped = skip_alert_occurrence(occurrence.occurrence_id, actor_id=source_id, now=clock, db_path=db_path)
        return f"Skipped the next occurrence of {_possessive_label(selected)}.", _details(
            "skip", schedule_id=selected.schedule.schedule_id,
            occurrence_id=skipped.occurrence_id, terminal=False,
        )

    if operation == "edit":
        scope = _edit_scope(normalized, selected)
        if scope == "ambiguous":
            return (
                "Should I change the recurring alarm schedule or only its next occurrence?",
                _details("edit", status="clarification_required", options=["the schedule", "the next occurrence"],
                         original_text=normalized, subject_text="alarm"),
            )
        new_due, new_local_time = _edited_time(
            normalized, selected, now=clock, timezone_name=selected.schedule.timezone
        )
        if scope == "occurrence":
            occurrence = _next_occurrence(selected)
            if occurrence is None:
                raise ValueError("That alarm has no upcoming occurrence to change.")
            updated_occurrence = override_alert_occurrence(
                occurrence.occurrence_id, due_at=new_due,
                intended_local=new_due.astimezone(ZoneInfo(selected.schedule.timezone)).replace(tzinfo=None).isoformat(timespec="seconds"),
                actor_id=source_id, now=clock, db_path=db_path,
            )
            return f"Changed only the next occurrence of {_possessive_label(selected)} to {_format_due(new_due, selected.schedule.timezone)}.", _selected_details(
                "edit_occurrence", AlarmSubject(selected.schedule, updated_occurrence), clock
            )
        local = new_due.astimezone(ZoneInfo(selected.schedule.timezone))
        updated_schedule = update_alert_schedule(
            selected.schedule.schedule_id, start_at=new_due,
            local_time=new_local_time.isoformat(timespec="seconds"),
            recurrence=selected.schedule.recurrence,
            message=_alarm_message(selected.name, local), metadata=selected.schedule.metadata,
            now=clock, db_path=db_path,
        )
        for existing in list_alert_occurrences(
            schedule_id=updated_schedule.schedule_id, db_path=db_path or DB_PATH
        ):
            if existing.status == "scheduled" and existing.due_at > clock:
                transition_alert_occurrence(
                    existing.occurrence_id, status="canceled", actor_type="system",
                    actor_id=source_id, reason="alarm_series_schedule_replaced", now=clock,
                    db_path=db_path,
                )
        replacements = materialize_schedule_occurrences(
            updated_schedule, through=clock + timedelta(days=366), db_path=db_path
        )
        occurrence = replacements[0] if replacements else _next_for_schedule(
            updated_schedule.schedule_id, db_path
        )
        return f"Changed {_possessive_label(selected)} to {_format_clock(local.timetz().replace(tzinfo=None))}.", _selected_details(
            "edit_schedule", AlarmSubject(updated_schedule, occurrence), clock
        )

    raise ValueError("I need an alarm time or an alarm management request.")


def build_alarm_state(
    *,
    source_id: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clock = _utc(now or datetime.now(UTC))
    subjects = _alarm_subjects(
        source_id=source_id, household=household, satellites=satellites, db_path=db_path
    )
    alarms = [_serialize(item, clock) for item in subjects if item.schedule.status != "deleted"]
    ringing = [item for item in alarms if item.get("occurrence_status") in {"due", "ringing"}]
    return {
        "source_id": source_id, "generated_at": clock.isoformat(),
        "count": len(alarms), "alarms": alarms, "ringing": ringing,
        "next": next((item for item in alarms if item.get("schedule_status") == "active" and item.get("due_at")), None),
        "display_attention_required": bool(ringing),
    }


def manage_alarm(
    *,
    source_id: str,
    action: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    schedule_id: str | None = None,
    occurrence_id: str | None = None,
    snooze_minutes: int = DEFAULT_SNOOZE_MINUTES,
    idempotency_key: str,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clock = _utc(now or datetime.now(UTC))
    subjects = _alarm_subjects(source_id=source_id, household=household, satellites=satellites, db_path=db_path)
    selected = next((item for item in subjects if occurrence_id and item.occurrence and item.occurrence.occurrence_id == occurrence_id), None)
    if selected is None and occurrence_id:
        occurrence = next((item for item in list_alert_occurrences(db_path=db_path or DB_PATH) if item.occurrence_id == occurrence_id), None)
        schedule = next((item for item in list_alert_schedules(db_path=db_path or DB_PATH) if occurrence and item.schedule_id == occurrence.schedule_id and item.kind == "alarm"), None)
        delivery = next((item for item in list_alert_records(source_id=source_id, kind="alarm", statuses=ALERT_STATUSES, db_path=db_path or DB_PATH) if item.occurrence_id == occurrence_id), None)
        if schedule is not None and occurrence is not None and _schedule_visible(schedule, source_id, household, satellites):
            selected = AlarmSubject(schedule, occurrence, delivery)
    if selected is None:
        selected = next((item for item in subjects if schedule_id and item.schedule.schedule_id == schedule_id), None)
    if selected is None:
        raise KeyError("Unknown alarm schedule or occurrence")
    if action == "dismiss":
        if selected.occurrence is None or selected.occurrence.status not in {"due", "ringing"}:
            raise ValueError("Only a ringing alarm may be dismissed")
        _dismiss(selected, source_id=source_id, now=clock, idempotency_key=idempotency_key, db_path=db_path)
    elif action == "snooze":
        if selected.occurrence is None or selected.occurrence.status not in {"due", "ringing"}:
            raise ValueError("Only a ringing alarm may be snoozed")
        if not 1 <= snooze_minutes <= MAX_SNOOZE_MINUTES:
            raise ValueError("Alarm snooze must be between 1 and 120 minutes")
        snoozed = snooze_alert_occurrence(
            selected.occurrence.occurrence_id, until=clock + timedelta(minutes=snooze_minutes),
            actor_type="system", actor_id=source_id, now=clock, db_path=db_path,
        )
        occurrence_id = snoozed.occurrence_id
    elif action in {"enable", "disable", "delete"}:
        _change_schedule_state(selected, operation=action, actor_id=source_id, now=clock, db_path=db_path)
    elif action == "skip":
        occurrence = _next_occurrence(selected)
        if occurrence is None or selected.schedule.schedule_type != "recurring":
            raise ValueError("Skip-next requires an upcoming recurring alarm")
        skip_alert_occurrence(occurrence.occurrence_id, actor_id=source_id, now=clock, db_path=db_path)
        occurrence_id = occurrence.occurrence_id
    else:
        raise ValueError(f"Unsupported alarm action {action!r}")
    return {"ok": True, "action": action, "schedule_id": selected.schedule.schedule_id, "occurrence_id": occurrence_id}


def _create_alarm(normalized: str, *, source_id: str, session_id: str | None,
                  household: HouseholdRuntimeSettings, satellites: SatelliteFleetRuntimeSettings,
                  now: datetime, db_path: Path | None) -> tuple[str, dict[str, Any]]:
    timezone_name = household.household.timezone
    local_now = now.astimezone(ZoneInfo(timezone_name))
    rule, recurrence_text = _parse_recurrence(normalized)
    anchor_weekday_recurrence = bool(rule is not None and recurrence_text == "every other week" and not _weekdays_in(normalized))
    due_at, clock_value = _parse_alarm_due(
        normalized, local_now=local_now, recurrence=None if anchor_weekday_recurrence else rule
    )
    if anchor_weekday_recurrence:
        rule = RecurrenceRule("weekly", interval=2, weekdays=(due_at.astimezone(ZoneInfo(timezone_name)).weekday(),))
    scope, target_id = _target_from_text(normalized, household)
    target = resolve_alert_targets(
        household=household, satellites=satellites, scope=scope,
        requesting_source_id=source_id, target_id=target_id,
    )
    if not target.destinations:
        raise ValueError("That alarm target has no enabled alert-capable satellite.")
    name = _alarm_name(normalized)
    local_due = due_at.astimezone(ZoneInfo(timezone_name))
    schedule, _created = create_semantic_alert_schedule(
        kind="alarm", start_at=due_at, timezone_name=timezone_name,
        creator_source_id=source_id, session_id=session_id,
        message=_alarm_message(name, local_due), target_scope=scope, target_id=target_id,
        recurrence=rule, metadata={"name": name, "recurrence_text": recurrence_text}, db_path=db_path,
    )
    occurrences = materialize_schedule_occurrences(
        schedule, through=now + timedelta(days=366) if rule else due_at, db_path=db_path
    )
    occurrence = occurrences[0] if occurrences else _next_for_schedule(schedule.schedule_id, db_path)
    label = f"{name.title()} alarm" if name else "Alarm"
    recurrence_words = f" {recurrence_text}" if recurrence_text else ""
    target_words = " throughout the house" if scope == "household" else (
        f" in {household.room(target_id).display_name}" if scope == "room" and household.room(target_id) else ""
    )
    return (
        f"{label} set for {_format_due(due_at, timezone_name)}{recurrence_words}{target_words}.",
        _selected_details("create", AlarmSubject(schedule, occurrence), now),
    )


def _alarm_subjects(*, source_id: str, household: HouseholdRuntimeSettings,
                    satellites: SatelliteFleetRuntimeSettings, db_path: Path | None) -> list[AlarmSubject]:
    schedules = {
        item.schedule_id: item for item in list_alert_schedules(
            statuses=VISIBLE_ALARM_SCHEDULE_STATUSES, db_path=db_path or DB_PATH
        ) if item.kind == "alarm" and _schedule_visible(item, source_id, household, satellites)
    }
    occurrences = list_alert_occurrences(db_path=db_path or DB_PATH)
    by_schedule: dict[str, list[AlertOccurrenceRecord]] = {}
    for item in occurrences:
        if item.schedule_id in schedules:
            by_schedule.setdefault(item.schedule_id, []).append(item)
    deliveries = list_alert_records(source_id=source_id, kind="alarm", statuses=ALERT_STATUSES, db_path=db_path or DB_PATH)
    by_occurrence = {item.occurrence_id: item for item in deliveries if item.occurrence_id}
    result = []
    for schedule in schedules.values():
        items = sorted(by_schedule.get(schedule.schedule_id, []), key=lambda item: item.due_at)
        occurrence = next((item for item in items if item.status in {"due", "ringing"}), None)
        if occurrence is None:
            parent_ids = {item.parent_occurrence_id for item in items if item.parent_occurrence_id}
            occurrence = next((item for item in items if item.status in ACTIVE_ALARM_STATUSES and not (item.status == "snoozed" and item.occurrence_id in parent_ids)), None)
        if occurrence is None:
            occurrence = next((item for item in reversed(items) if item.status == "missed"), None)
        result.append(AlarmSubject(schedule, occurrence, by_occurrence.get(occurrence.occurrence_id) if occurrence else None))
    return sorted(result, key=lambda item: (item.occurrence.due_at if item.occurrence else datetime.max.replace(tzinfo=UTC), item.schedule.schedule_id))


def _schedule_visible(schedule: AlertScheduleRecord, source_id: str,
                      household: HouseholdRuntimeSettings, satellites: SatelliteFleetRuntimeSettings) -> bool:
    if schedule.target_scope == "local": return schedule.creator_source_id == source_id
    if schedule.target_scope == "room": return household.configured_associated_room_id(source_id) == schedule.target_id
    if schedule.target_scope == "household": return source_id in satellites.enabled_satellite_ids_by_source
    return False


def _parse_recurrence(text: str) -> tuple[RecurrenceRule | None, str]:
    if "every day" in text or "daily" in text:
        return RecurrenceRule("daily"), "every day"
    if "every weekday" in text or "weekdays" in text:
        return RecurrenceRule("weekly", weekdays=(0, 1, 2, 3, 4)), "every weekday"
    if "every weekend" in text or "weekends" in text:
        return RecurrenceRule("weekly", weekdays=(5, 6)), "every weekend"
    month_day = re.search(r"(?:on )?the (.+?) of every month", text)
    if month_day:
        ordinal = parse_ordinal(month_day.group(1))
        if ordinal is not None and 1 <= ordinal <= 31:
            return RecurrenceRule("monthly", month_days=(ordinal,)), f"on the {ordinal}{_ordinal_suffix(ordinal)} of every month"
    monthly = re.search(r"(?:the )?(.+?) (monday|tuesday|wednesday|thursday|friday|saturday|sunday)s? of every month", text)
    if monthly:
        ordinals = tuple(_ORDINALS[item] for item in re.findall(r"first|second|third|fourth|fifth|last", monthly.group(1)))
        if ordinals:
            weekday = _WEEKDAYS[monthly.group(2)]
            return RecurrenceRule("monthly", weekdays=(weekday,), ordinals=ordinals), f"the {monthly.group(1)} {monthly.group(2)} of every month"
    if "every other week" in text:
        weekdays = _weekdays_in(text)
        return RecurrenceRule("weekly", interval=2, weekdays=weekdays or (0,)), "every other week"
    if re.search(r"\bevery\b", text):
        weekdays = _weekdays_in(text)
        if weekdays:
            labels = [name.title() for name, value in _WEEKDAYS.items() if value in weekdays]
            return RecurrenceRule("weekly", weekdays=weekdays), "every " + " and ".join(labels)
    return None, ""


def _weekdays_in(text: str) -> tuple[int, ...]:
    return tuple(value for name, value in _WEEKDAYS.items() if re.search(rf"\b{name}s?\b", text))


def _parse_alarm_due(text: str, *, local_now: datetime, recurrence: RecurrenceRule | None) -> tuple[datetime, time]:
    relative_match = re.search(r"\balarm\s+(.+?) from now\b", text)
    if relative_match:
        parsed = parse_duration(relative_match.group(1), max_seconds=366 * 86400)
        if parsed is not None:
            due = local_now + timedelta(seconds=parsed.seconds)
            return due.astimezone(UTC), due.timetz().replace(tzinfo=None)
    clock_text = _clock_text(text)
    clocks = parse_clock_candidates(clock_text)
    if not clocks:
        raise ValueError("I need a valid time for the alarm.")
    date_value = _alarm_date(text, local_now.date())
    candidates: list[datetime] = []
    for clock_value in clocks:
        candidate_date = date_value or local_now.date()
        candidate = resolve_local_wall_time(candidate_date, clock_value, str(local_now.tzinfo))
        if recurrence is None and date_value is None and candidate <= local_now:
            candidate = resolve_local_wall_time(candidate_date + timedelta(days=1), clock_value, str(local_now.tzinfo))
        candidates.append(candidate)
    if recurrence is not None:
        matching = [item for item in candidates if item > local_now]
        if not matching:
            matching = [
                resolve_local_wall_time(local_now.date() + timedelta(days=1), item, str(local_now.tzinfo))
                for item in clocks
            ]
        due = min(matching)
        while not _recurrence_matches_anchor(due.date(), recurrence):
            due = resolve_local_wall_time(due.date() + timedelta(days=1), due.timetz().replace(tzinfo=None), str(local_now.tzinfo))
    else:
        future = [item for item in candidates if item > local_now]
        if not future:
            raise ValueError("Alarm time must be in the future.")
        due = min(future)
    return due.astimezone(UTC), due.timetz().replace(tzinfo=None)


def _clock_text(text: str) -> str:
    cleaned = re.sub(r"\ba\.m\.\b", "am", text).replace("p.m.", "pm")
    cleaned = re.sub(
        r"\b([01]?\d|2[0-3])[.\s]([0-5]\d)\s*([ap])(?:[.\s])*m\b",
        lambda match: f"{match.group(1)}:{match.group(2)} {match.group(3)}m",
        cleaned,
    )
    matches = list(re.finditer(r"\b(?:at|for|to)\s+([a-z0-9]+(?::[0-5]\d)?(?:\s*(?:am|pm))?)\b", cleaned))
    if matches:
        return matches[-1].group(1)
    match = re.search(r"\b(\d{1,2}(?::[0-5]\d)?\s*(?:am|pm))\b", cleaned)
    if match: return match.group(1)
    match = re.search(r"\balarm\s+(\d{1,2}(?::[0-5]\d)?)\b", cleaned)
    if match: return match.group(1)
    raise ValueError("I need a time for the alarm.")


def _alarm_date(text: str, today: date) -> date | None:
    if "tomorrow" in text: return today + timedelta(days=1)
    explicit = re.search(r"\b(?:on )?((?:january|february|march|april|may|june|july|august|september|october|november|december)\s+[a-z0-9]+(?:\s+\d{4})?)\b", text)
    if explicit:
        return parse_relative_date(explicit.group(1), today)
    for name, weekday in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", text) and not re.search(rf"\bevery\b.*\b{name}\b", text):
            days = (weekday - today.weekday()) % 7
            return today + timedelta(days=days)
    return None


def _target_from_text(text: str, household: HouseholdRuntimeSettings) -> tuple[str, str | None]:
    if any(item in text for item in ("throughout the house", "whole house", "house-wide", "house alarm")):
        return "household", household.household.id
    room_match = re.search(r"\bin (?:the )?(.+?)(?: room)?\s+for\b", text)
    room_phrase = room_match.group(1) if room_match else ""
    if not room_phrase:
        prefix = re.search(r"(?:set|create) (?:an? |the )?(.+?) alarm", text)
        room_phrase = prefix.group(1) if prefix else ""
    if room_phrase:
        clean = room_phrase.replace("'s", "").removesuffix(" room").strip()
        room_id = household.resolve_room_id(clean) or household.resolve_room_id(f"{clean} room")
        if room_id: return "room", room_id
        if room_match: raise ValueError(f"I could not resolve the alarm room {room_phrase}.")
    return "local", None


def _alarm_name(text: str) -> str:
    match = re.search(r"(?:set|create) (?:an? |the )?(.+?) alarm\b", text)
    if not match: return ""
    candidate = match.group(1).strip()
    if candidate in {"", "my", "house", "whole house", "weekday", "weekend"}: return ""
    return candidate[:80]


def _operation(text: str) -> str:
    if "snooze" in text or "more minutes" in text: return "snooze"
    if "skip" in text or "don't run" in text or "do not run" in text: return "skip"
    if re.search(r"\b(?:dismiss|stop)\b", text): return "dismiss"
    if re.search(r"\b(?:cancel|delete|remove)\b", text): return "delete"
    if any(item in text for item in ("turn off", "disable")): return "disable"
    if any(item in text for item in ("turn on", "back on", "enable", "re-enable")): return "enable"
    if re.search(r"\b(?:change|move|make)\b", text): return "edit"
    if "how many" in text: return "count"
    if "next alarm" in text or "alarm is next" in text: return "next"
    return "status"


def _looks_like_creation(text: str) -> bool:
    return bool(re.search(r"\b(?:set|create)\b.*\balarm\b|\bwake me up\b", text)) and not re.search(r"\b(?:change|move|turn|delete|disable|enable)\b", text)


def _select_subject(text: str, subjects: list[AlarmSubject], *, context: dict[str, Any] | None,
                    prefer_occurrence: bool) -> AlarmSubject | None:
    payload = context.get("payload") if isinstance(context, dict) else None
    if isinstance(payload, dict) and payload.get("alert_kind") == "alarm":
        occurrence_id, schedule_id = str(payload.get("occurrence_id") or ""), str(payload.get("schedule_id") or "")
        matches = [item for item in subjects if (occurrence_id and item.occurrence and item.occurrence.occurrence_id == occurrence_id) or (schedule_id and item.schedule.schedule_id == schedule_id)]
        if len(matches) == 1: return matches[0]
    selector = _selector(text)
    if selector:
        matches = [item for item in subjects if item.name and _normalize(item.name) == selector]
        if len(matches) == 1: return matches[0]
        schedule_matches = [item for item in subjects if selector in _normalize(_schedule_label(item.schedule))]
        if len(schedule_matches) == 1: return schedule_matches[0]
    if prefer_occurrence and len(subjects) == 1: return subjects[0]
    if len(subjects) == 1: return subjects[0]
    return None


def _selector(text: str) -> str:
    match = re.search(r"(?:my|the) ([a-z0-9' -]+?) alarm", text)
    if not match: return ""
    value = _normalize(match.group(1))
    return "" if value in {"next", "current", "active", "ringing"} else value


def _edit_scope(text: str, subject: AlarmSubject) -> str:
    if re.search(r"\b(?:tomorrow|next occurrence|next one|this occurrence|today|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)'s)\b", text):
        return "occurrence"
    if subject.schedule.schedule_type == "one_time": return "occurrence"
    if re.search(r"\b(?:weekday|weekend|daily|schedule|every|series)\b", text) or subject.name and re.search(rf"\b{re.escape(_normalize(subject.name))}\s+alarm\b", text):
        return "schedule"
    return "ambiguous"


def _edited_time(text: str, subject: AlarmSubject, *, now: datetime, timezone_name: str) -> tuple[datetime, time]:
    clock_text = _clock_text(text)
    clocks = parse_clock_candidates(clock_text)
    if not clocks: raise ValueError("I need a valid new alarm time.")
    zone = ZoneInfo(timezone_name)
    base = (subject.occurrence.due_at if subject.occurrence else subject.schedule.start_at).astimezone(zone)
    date_value = _alarm_date(text, now.astimezone(zone).date()) or base.date()
    candidates = [resolve_local_wall_time(date_value, item, timezone_name) for item in clocks]
    future = [item for item in candidates if item.astimezone(UTC) > now]
    if not future: raise ValueError("The new alarm time must be in the future.")
    selected = min(future)
    return selected.astimezone(UTC), selected.timetz().replace(tzinfo=None)


def _dismiss(subject: AlarmSubject, *, source_id: str, now: datetime,
             idempotency_key: str | None = None, db_path: Path | None) -> None:
    if subject.occurrence is None or subject.delivery is None:
        raise ValueError("The active alarm has no delivery on this destination.")
    acknowledge_alert_occurrence(
        occurrence_id=subject.occurrence.occurrence_id, actor_type="destination",
        actor_id=source_id, action="dismissed",
        idempotency_key=idempotency_key or f"alarm-dismiss:{subject.occurrence.occurrence_id}:{source_id}",
        now=now, alert_id=subject.delivery.alert_id, db_path=db_path,
    )


def _change_schedule_state(subject: AlarmSubject, *, operation: str, actor_id: str,
                           now: datetime, db_path: Path | None) -> AlertScheduleRecord:
    schedule = subject.schedule
    if operation == "enable":
        if schedule.schedule_type == "one_time" and schedule.start_at <= now:
            raise ValueError("That one-time alarm has already passed. Set a new alarm instead.")
        for occurrence in list_alert_occurrences(schedule_id=schedule.schedule_id, db_path=db_path or DB_PATH):
            if occurrence.status == "scheduled" and occurrence.due_at <= now:
                transition_alert_occurrence(
                    occurrence.occurrence_id, status="skipped", actor_type="system",
                    actor_id=actor_id, reason="alarm_disabled_at_due_time", now=now, db_path=db_path,
                )
        return change_alert_schedule_status(schedule.schedule_id, status="active", now=now, db_path=db_path)
    desired = "disabled" if operation == "disable" else "deleted"
    updated = change_alert_schedule_status(schedule.schedule_id, status=desired, now=now, db_path=db_path)
    if operation == "delete":
        for occurrence in list_alert_occurrences(schedule_id=schedule.schedule_id, db_path=db_path or DB_PATH):
            if occurrence.status in ACTIVE_ALARM_STATUSES:
                transition_alert_occurrence(
                    occurrence.occurrence_id, status="canceled", actor_type="system",
                    actor_id=actor_id, reason="alarm_schedule_deleted", now=now, db_path=db_path,
                )
    return updated


def _snooze_minutes(text: str) -> int:
    match = re.search(r"(?:snooze(?: (?:it|the alarm))? for|give me) (.+?)(?: more)? minutes?", text)
    if not match: return DEFAULT_SNOOZE_MINUTES
    value = parse_number(match.group(1))
    if value is None or value != value.to_integral_value() or not 1 <= int(value) <= MAX_SNOOZE_MINUTES:
        raise ValueError("Alarm snooze must be between 1 and 120 minutes.")
    return int(value)


def _next_occurrence(subject: AlarmSubject) -> AlertOccurrenceRecord | None:
    return subject.occurrence if subject.occurrence and subject.occurrence.status in ACTIVE_ALARM_STATUSES else None


def _next_for_schedule(schedule_id: str, db_path: Path | None) -> AlertOccurrenceRecord | None:
    items = list_alert_occurrences(schedule_id=schedule_id, db_path=db_path or DB_PATH)
    parent_ids = {item.parent_occurrence_id for item in items if item.parent_occurrence_id}
    return next((item for item in items if item.status in ACTIVE_ALARM_STATUSES and not (item.status == "snoozed" and item.occurrence_id in parent_ids)), None)


def _recurrence_matches_anchor(value: date, rule: RecurrenceRule) -> bool:
    if rule.frequency == "daily": return True
    if rule.frequency == "weekly": return value.weekday() in rule.weekdays
    if rule.month_days: return value.day in rule.month_days
    return value.weekday() in rule.weekdays


def _serialize(subject: AlarmSubject, now: datetime) -> dict[str, Any]:
    occurrence = subject.occurrence
    return {
        "kind": "alarm", "schedule_id": subject.schedule.schedule_id,
        "occurrence_id": occurrence.occurrence_id if occurrence else None,
        "alert_id": subject.delivery.alert_id if subject.delivery else None,
        "name": subject.name, "label": _subject_label(subject),
        "schedule_status": subject.schedule.status,
        "schedule_type": subject.schedule.schedule_type,
        "occurrence_status": occurrence.status if occurrence else None,
        "status": occurrence.status if occurrence else subject.schedule.status,
        "due_at": occurrence.due_at.isoformat() if occurrence else None,
        "local_time": subject.schedule.local_time,
        "recurrence": dict(subject.schedule.recurrence),
        "recurrence_text": str(subject.schedule.metadata.get("recurrence_text") or ""),
        "schedule_text": _schedule_label(subject.schedule),
        "target_scope": subject.schedule.target_scope, "target_id": subject.schedule.target_id,
        "message": subject.schedule.message,
        "late_seconds": max(0, int((now - occurrence.due_at).total_seconds())) if occurrence else 0,
        "missed": bool(occurrence and occurrence.status == "missed"),
    }


def _selected_details(operation: str, subject: AlarmSubject, now: datetime, **extra: Any) -> dict[str, Any]:
    return _details(operation, **_serialize(subject, now), **extra)


def _list_reply(subjects: list[AlarmSubject], now: datetime) -> tuple[str, dict[str, Any]]:
    parts = [f"{_subject_label(item)} ({'enabled' if item.schedule.status == 'active' else 'disabled'}; {_schedule_label(item.schedule)})" for item in subjects[:6]]
    return f"You have {len(subjects)} {_plural('alarm', len(subjects))}: " + "; ".join(parts) + ".", _details("status", count=len(subjects), alarms=[_serialize(item, now) for item in subjects])


def _next_reply(subject: AlarmSubject, now: datetime) -> tuple[str, dict[str, Any]]:
    assert subject.occurrence is not None
    return f"Your next alarm is {_subject_label(subject)} at {_format_due(subject.occurrence.due_at, subject.schedule.timezone)}.", _selected_details("next", subject, now)


def _clarification(text: str, subjects: list[AlarmSubject], operation: str) -> tuple[str, dict[str, Any]]:
    options = [_subject_label(item) for item in subjects[:6]]
    return "Which alarm did you mean: " + ", ".join(options) + "?", _details(
        operation, status="clarification_required", options=options,
        original_text=text, subject_text="alarm",
    )


def _subject_label(subject: AlarmSubject) -> str:
    return f"{subject.name} alarm" if subject.name else _schedule_label(subject.schedule)


def _possessive_label(subject: AlarmSubject) -> str:
    return f"your {_subject_label(subject)}"


def _schedule_label(schedule: AlertScheduleRecord) -> str:
    local_time = _format_clock(time.fromisoformat(str(schedule.local_time or "00:00:00")))
    recurrence = str(schedule.metadata.get("recurrence_text") or "").strip()
    return f"{recurrence} alarm at {local_time}" if recurrence else f"alarm for {local_time}"


def _alarm_message(name: str | None, local_due: datetime) -> str:
    return f"Your {name} alarm is going off." if name else f"Your {_format_clock(local_due.timetz().replace(tzinfo=None))} alarm is going off."


def _format_due(value: datetime, timezone_name: str) -> str:
    local = value.astimezone(ZoneInfo(timezone_name))
    return f"{local.strftime('%A')} at {_format_clock(local.timetz().replace(tzinfo=None))}"


def _format_clock(value: time) -> str:
    return datetime.combine(date.today(), value).strftime("%-I:%M %p")


def _details(operation: str, **values: Any) -> dict[str, Any]:
    return {"kind": "alarm", "operation": operation, **values}


def _ordinal_suffix(value: int) -> str:
    if 10 <= value % 100 <= 20: return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("’", "'").strip(" .?!").split())


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Alarm timestamps must be timezone-aware")
    return value.astimezone(UTC)
