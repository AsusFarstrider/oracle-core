from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from .alert_lifecycle import (
    acknowledge_alert_occurrence,
    create_semantic_alert_schedule,
    materialize_schedule_occurrences,
)
from .alert_targeting import resolve_alert_targets
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from .deterministic_values import parse_duration, parse_number
from .memory.alert_lifecycle import (
    AlertOccurrenceRecord,
    AlertScheduleRecord,
    list_alert_occurrences,
    list_alert_schedules,
    reschedule_alert_occurrence,
    transition_alert_occurrence,
)
from .memory.alerts import ALERT_STATUSES, AlertRecord, list_alert_records
from .memory.store import DB_PATH


MAX_TIMER_SECONDS = 7 * 24 * 60 * 60
ACTIVE_TIMER_STATUSES = frozenset({"scheduled", "due", "ringing"})


@dataclass(frozen=True)
class TimerSubject:
    schedule: AlertScheduleRecord
    occurrence: AlertOccurrenceRecord
    delivery: AlertRecord | None

    @property
    def name(self) -> str | None:
        value = str(self.schedule.metadata.get("name") or "").strip()
        return value or None

    @property
    def duration_seconds(self) -> int:
        value = self.occurrence.metadata.get("duration_seconds")
        if value is None:
            value = self.schedule.metadata.get("duration_seconds")
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0


def looks_like_timer_followup(text: str) -> bool:
    normalized = _normalize(text)
    if normalized in {
        "stop", "stop it", "cancel", "cancel it", "dismiss", "dismiss it",
        "restart", "restart it",
    }:
        return True
    return bool(
        re.search(r"\b(?:add|give)\b.+\bto (?:it|the .+ timer)\b", normalized)
        or re.search(r"\b(?:take|subtract|remove)\b.+\b(?:off|from) (?:it|the .+ timer)\b", normalized)
        or re.search(r"\b(?:change|restart) (?:it|the .+ timer)\b", normalized)
        or normalized.startswith(("make it ", "set it to "))
    )


def execute_timer_command(
    text: str,
    *,
    source_id: str,
    session_id: str | None,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    context: dict[str, Any] | None = None,
    confirmed: bool = False,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    clock = _utc(now or datetime.now(timezone.utc))
    normalized = _normalize(text)
    subjects = _timer_subjects(
        source_id=source_id,
        household=household,
        satellites=satellites,
        db_path=db_path,
    )

    if _looks_like_creation(normalized):
        return _create_timer(
            normalized,
            source_id=source_id,
            session_id=session_id,
            household=household,
            satellites=satellites,
            now=clock,
            db_path=db_path,
        )

    operation = _operation(normalized)
    if operation == "cancel_all":
        if not subjects:
            return "You have no active timers to cancel.", _details("cancel_all", count=0)
        if not confirmed:
            return (
                f"Cancel all {len(subjects)} active timers?",
                _details("cancel_all", status="confirmation_required", count=len(subjects)),
            )
        for subject in subjects:
            _cancel(subject, source_id=source_id, now=clock, db_path=db_path)
        return (
            f"Canceled {len(subjects)} active {_plural('timer', len(subjects))}.",
            _details("cancel_all", count=len(subjects), terminal=True),
        )

    if operation in {"status", "count", "next"}:
        if operation == "count":
            return (
                f"You have {len(subjects)} active {_plural('timer', len(subjects))}.",
                _details("count", count=len(subjects)),
            )
        if not subjects:
            return "You have no active timers.", _details(operation, count=0)
        if operation == "status" and _plural_status(normalized):
            return _format_timer_list(subjects, now=clock), _details(
                "status", count=len(subjects), timers=[_serialize(item, clock) for item in subjects]
            )
        selected = subjects[0] if operation == "next" else _select_subject(
            normalized, subjects, context=context
        )
        if selected is None:
            return _clarification(normalized, subjects, operation)
        remaining = max(0, int((selected.occurrence.due_at - clock).total_seconds()))
        name = f" {selected.name}" if selected.name else ""
        local_due = selected.occurrence.due_at.astimezone(
            ZoneInfo(household.household.timezone)
        )
        speech = (
            f"Your{name} timer ends in {_format_duration(remaining)}, at "
            f"{local_due.strftime('%-I:%M:%S %p')}."
        )
        return speech, _selected_details(operation, selected, clock)

    selected = _select_subject(normalized, subjects, context=context)
    if selected is None:
        if not subjects:
            return "You have no active timers.", _details(operation or "status", count=0)
        return _clarification(normalized, subjects, operation or "select")

    if operation == "dismiss" or (
        operation == "cancel" and selected.occurrence.status in {"due", "ringing"}
    ):
        _dismiss(selected, source_id=source_id, now=clock, db_path=db_path)
        return _terminal_reply("Dismissed", selected, operation="dismiss")
    if operation == "cancel":
        _cancel(selected, source_id=source_id, now=clock, db_path=db_path)
        return _terminal_reply("Canceled", selected, operation="cancel")
    if operation in {"add", "subtract", "set", "restart"}:
        if selected.occurrence.status != "scheduled":
            raise ValueError("A timer that is already ringing can only be dismissed.")
        new_due, effective_duration = _adjusted_deadline(
            normalized, operation, selected, now=clock, context=context
        )
        if new_due <= clock:
            raise ValueError("That adjustment would end the timer now or in the past. Cancel it instead.")
        updated = reschedule_alert_occurrence(
            selected.occurrence.occurrence_id,
            due_at=new_due,
            intended_local=new_due.astimezone().isoformat(timespec="seconds"),
            actor_type="system",
            actor_id=source_id,
            now=clock,
            db_path=db_path,
        )
        updated = transition_alert_occurrence(
            updated.occurrence_id,
            status="scheduled",
            actor_type="system",
            actor_id=source_id,
            reason="timer_adjusted",
            now=clock,
            metadata_update={"duration_seconds": effective_duration},
            db_path=db_path,
        )
        adjusted = TimerSubject(selected.schedule, updated, selected.delivery)
        label = f" {selected.name}" if selected.name else ""
        return (
            f"Your{label} timer now has {_format_duration(effective_duration)} remaining.",
            _selected_details("adjust", adjusted, clock),
        )
    raise ValueError("I need a timer duration or a timer management request.")


def build_timer_state(
    *,
    source_id: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clock = _utc(now or datetime.now(timezone.utc))
    timers = _timer_subjects(
        source_id=source_id,
        household=household,
        satellites=satellites,
        db_path=db_path,
    )
    serialized = [_serialize(item, clock) for item in timers]
    return {
        "source_id": source_id,
        "generated_at": clock.isoformat(),
        "count": len(serialized),
        "timers": serialized,
        "ringing": [item for item in serialized if item["status"] in {"due", "ringing"}],
    }


def dismiss_timer(
    occurrence_id: str,
    *,
    source_id: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    now: datetime | None = None,
    idempotency_key: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clock = _utc(now or datetime.now(timezone.utc))
    subject = next(
        (
            item for item in _timer_subjects(
                source_id=source_id,
                household=household,
                satellites=satellites,
                db_path=db_path,
            )
            if item.occurrence.occurrence_id == occurrence_id
        ),
        None,
    )
    if subject is None:
        raise KeyError(f"Unknown active timer occurrence {occurrence_id}")
    if subject.occurrence.status in {"due", "ringing"}:
        _dismiss(
            subject,
            source_id=source_id,
            now=clock,
            idempotency_key=idempotency_key,
            db_path=db_path,
        )
        action = "dismiss"
    else:
        _cancel(subject, source_id=source_id, now=clock, db_path=db_path)
        action = "cancel"
    return {"ok": True, "action": action, "occurrence_id": occurrence_id}


def _create_timer(
    normalized: str,
    *,
    source_id: str,
    session_id: str | None,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    now: datetime,
    db_path: Path | None,
) -> tuple[str, dict[str, Any]]:
    duration_text, duration_seconds = _duration_from_text(normalized)
    if duration_seconds is None:
        raise ValueError("I need a valid timer duration.")
    scope, target_id, target_phrase = _target_from_text(normalized, household)
    resolved = resolve_alert_targets(
        household=household,
        satellites=satellites,
        scope=scope,
        requesting_source_id=source_id,
        target_id=target_id,
    )
    if not resolved.destinations:
        raise ValueError("That timer target has no enabled alert-capable satellite.")
    name = _timer_name(normalized, duration_text=duration_text, target_phrase=target_phrase)
    due_at = now + timedelta(seconds=duration_seconds)
    label = f"{name.title()} timer" if name else "Timer"
    schedule, _created = create_semantic_alert_schedule(
        kind="timer",
        start_at=due_at,
        timezone_name=household.household.timezone,
        creator_source_id=source_id,
        session_id=session_id,
        message=f"{label} is finished.",
        target_scope=scope,
        target_id=target_id,
        metadata={"name": name, "duration_seconds": duration_seconds},
        db_path=db_path,
    )
    occurrence = materialize_schedule_occurrences(
        schedule, through=due_at, db_path=db_path
    )[0]
    occurrence = transition_alert_occurrence(
        occurrence.occurrence_id,
        status="scheduled",
        actor_type="system",
        actor_id=source_id,
        reason="timer_created",
        now=now,
        metadata_update={"duration_seconds": duration_seconds},
        db_path=db_path,
    )
    target_words = (
        " throughout the house" if scope == "household"
        else f" in {household.room(target_id).display_name}" if scope == "room" and household.room(target_id)
        else ""
    )
    return (
        f"{label} set for {_format_duration(duration_seconds)}{target_words}.",
        _selected_details("create", TimerSubject(schedule, occurrence, None), now),
    )


def _timer_subjects(
    *,
    source_id: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    db_path: Path | None,
) -> list[TimerSubject]:
    schedules = {
        item.schedule_id: item
        for item in list_alert_schedules(statuses=("active",), db_path=db_path or DB_PATH)
        if item.kind == "timer" and _schedule_visible(item, source_id, household, satellites)
    }
    occurrences = [
        item for item in list_alert_occurrences(
            statuses=ACTIVE_TIMER_STATUSES, db_path=db_path or DB_PATH
        )
        if item.schedule_id in schedules
    ]
    deliveries = list_alert_records(
        source_id=source_id,
        kind="timer",
        statuses=ALERT_STATUSES,
        db_path=db_path or DB_PATH,
    )
    by_occurrence: dict[str, AlertRecord] = {}
    for delivery in deliveries:
        if delivery.occurrence_id:
            by_occurrence[delivery.occurrence_id] = delivery
    return sorted(
        [
            TimerSubject(schedules[item.schedule_id], item, by_occurrence.get(item.occurrence_id))
            for item in occurrences
        ],
        key=lambda item: (item.occurrence.due_at, item.occurrence.occurrence_id),
    )


def _schedule_visible(
    schedule: AlertScheduleRecord,
    source_id: str,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
) -> bool:
    if schedule.target_scope == "local":
        return schedule.creator_source_id == source_id
    if schedule.target_scope == "room":
        return household.configured_associated_room_id(source_id) == schedule.target_id
    if schedule.target_scope == "household":
        return source_id in satellites.enabled_satellite_ids_by_source
    return False


def _select_subject(
    text: str,
    subjects: list[TimerSubject],
    *,
    context: dict[str, Any] | None,
) -> TimerSubject | None:
    explicit_name = _selector_name(text)
    if explicit_name:
        matches = [item for item in subjects if item.name and _normalize(item.name) == explicit_name]
        if len(matches) == 1:
            return matches[0]
    context_payload = context.get("payload") if isinstance(context, dict) else None
    if isinstance(context_payload, dict):
        occurrence_id = str(context_payload.get("occurrence_id") or "").strip()
        contextual = [item for item in subjects if item.occurrence.occurrence_id == occurrence_id]
        if len(contextual) == 1:
            return contextual[0]
    if len(subjects) == 1:
        return subjects[0]
    if _duration_selector_allowed(text):
        _duration_text, seconds = _duration_from_text(text)
        if seconds is not None:
            matches = [item for item in subjects if item.duration_seconds == seconds]
            return matches[0] if len(matches) == 1 else None
    return None


def _dismiss(
    subject: TimerSubject,
    *,
    source_id: str,
    now: datetime,
    idempotency_key: str | None = None,
    db_path: Path | None,
) -> None:
    if subject.delivery is None:
        raise ValueError("The active timer has no delivery on this destination.")
    acknowledge_alert_occurrence(
        occurrence_id=subject.occurrence.occurrence_id,
        actor_type="destination",
        actor_id=source_id,
        action="dismissed",
        idempotency_key=idempotency_key or f"timer-dismiss:{subject.occurrence.occurrence_id}:{source_id}",
        now=now,
        alert_id=subject.delivery.alert_id,
        db_path=db_path,
    )


def _cancel(subject: TimerSubject, *, source_id: str, now: datetime, db_path: Path | None) -> None:
    transition_alert_occurrence(
        subject.occurrence.occurrence_id,
        status="canceled",
        actor_type="system",
        actor_id=source_id,
        reason="timer_canceled",
        now=now,
        db_path=db_path,
    )


def _adjusted_deadline(
    text: str,
    operation: str,
    subject: TimerSubject,
    *,
    now: datetime,
    context: dict[str, Any] | None,
) -> tuple[datetime, int]:
    if operation == "restart":
        seconds = subject.duration_seconds
        if seconds <= 0:
            raise ValueError("That timer has no reusable duration.")
        return now + timedelta(seconds=seconds), seconds
    _duration_text, seconds = _duration_from_text(text)
    if seconds is None and operation == "set":
        bare = re.search(r"(?:make it|make the timer|change it to|set it to)\s+(.+)$", text)
        value = parse_number(bare.group(1)) if bare else None
        if value is not None and value > 0:
            unit_seconds = _context_unit_seconds(context, subject)
            seconds = int(value * unit_seconds)
    if seconds is None:
        raise ValueError("I need a duration for that timer adjustment.")
    if operation == "add":
        due = subject.occurrence.due_at + timedelta(seconds=seconds)
    elif operation == "subtract":
        due = subject.occurrence.due_at - timedelta(seconds=seconds)
    else:
        due = now + timedelta(seconds=seconds)
    return due, max(0, int((due - now).total_seconds()))


def _context_unit_seconds(context: dict[str, Any] | None, subject: TimerSubject) -> Decimal:
    payload = context.get("payload") if isinstance(context, dict) else None
    unit = str(payload.get("duration_unit") or "").strip() if isinstance(payload, dict) else ""
    if unit == "second":
        return Decimal(1)
    if unit == "hour":
        return Decimal(3600)
    seconds = subject.duration_seconds
    return Decimal(3600 if seconds and seconds % 3600 == 0 else 60 if seconds >= 60 else 1)


def _operation(text: str) -> str:
    if re.search(r"\b(?:cancel|clear|stop|delete|remove) all timers?\b", text):
        return "cancel_all"
    if "how many" in text:
        return "count"
    if "next timer" in text or "timer is next" in text:
        return "next"
    if any(phrase in text for phrase in ("what timers", "list timers", "timers do i have")):
        return "status"
    if any(phrase in text for phrase in ("time left", "remaining", "when does", "when is", "timer status")):
        return "status"
    if "dismiss" in text:
        return "dismiss"
    if re.search(r"\b(?:cancel|clear|stop|delete|remove)\b", text):
        return "cancel"
    if re.search(r"\b(?:add|give)\b.+\b(?:to|onto)\b", text):
        return "add"
    if re.search(r"\b(?:take|subtract|remove)\b.+\b(?:off|from)\b", text):
        return "subtract"
    if "restart" in text:
        return "restart"
    if any(phrase in text for phrase in ("change", "make it", "set it to", "make the timer")):
        return "set"
    return "status"


def _looks_like_creation(text: str) -> bool:
    return bool(
        re.search(r"\b(?:set|start|create|begin)\b.*\b(?:timer|countdown)\b", text)
        or re.search(r"\bgive me\b.*\b(?:timer|countdown)\b", text)
    ) and not any(phrase in text for phrase in ("set it to", "change", "make it"))


def _duration_from_text(text: str) -> tuple[str, int | None]:
    words = text.replace(",", " ").split()
    best: tuple[str, int] | None = None
    for start in range(len(words)):
        for end in range(start + 1, min(len(words), start + 12) + 1):
            phrase = " ".join(words[start:end]).strip(" .?!")
            parsed = parse_duration(phrase, max_seconds=MAX_TIMER_SECONDS)
            if parsed is not None and (best is None or len(phrase) > len(best[0])):
                best = (phrase, parsed.seconds)
    return best if best is not None else ("", None)


def _target_from_text(
    text: str, household: HouseholdRuntimeSettings
) -> tuple[str, str | None, str]:
    if any(
        phrase in text
        for phrase in (
            "throughout the house", "whole house", "house-wide", "housewide", "house timer",
        )
    ):
        return "household", household.household.id, "house"
    in_match = re.search(r"\bin (?:the )?(.+?)(?=\s+for\b|\s+timer\b|$)", text)
    if in_match:
        phrase = in_match.group(1).strip().removesuffix("'s room").removesuffix(" room")
        room_id = household.resolve_room_id(phrase) or household.resolve_room_id(f"{phrase} room")
        if room_id is None:
            raise ValueError(f"I could not resolve the timer room {in_match.group(1).strip()}.")
        return "room", room_id, in_match.group(0)
    return "local", None, ""


def _timer_name(text: str, *, duration_text: str, target_phrase: str) -> str:
    before = text.split("timer", 1)[0] if "timer" in text else text.split("countdown", 1)[0]
    before = re.sub(r"^(?:please )?(?:set|start|create|begin|give me)\s+", "", before).strip()
    before = re.sub(r"^(?:a|an|the|my)\s+", "", before).strip()
    for removable in (duration_text, target_phrase, "house", "whole house"):
        if removable:
            before = before.replace(removable, " ")
    before = re.sub(r"\b(?:for|in|throughout|the)\b", " ", before)
    name = " ".join(before.split()).strip()
    return "" if name in {"", "a", "an"} else name[:80]


def _selector_name(text: str) -> str:
    match = re.search(r"(?:the|my)\s+([a-z0-9][a-z0-9 '\-]{0,78}?)\s+timer\b", text)
    if not match:
        return ""
    candidate = _normalize(match.group(1))
    if candidate in {"next", "active", "running", "minute", "second", "hour"}:
        return ""
    if parse_duration(candidate + " timer") is not None:
        return ""
    return candidate


def _duration_selector_allowed(text: str) -> bool:
    return bool(re.search(r"\b(?:cancel|stop|status|remaining|left|which|what)\b", text))


def _plural_status(text: str) -> bool:
    return "timers" in text or "list" in text or "what timers" in text


def _clarification(text: str, subjects: list[TimerSubject], operation: str) -> tuple[str, dict[str, Any]]:
    options = [_subject_label(item) for item in subjects[:5]]
    prompt = "Which timer did you mean: " + ", ".join(options) + "?"
    return prompt, _details(
        operation,
        status="clarification_required",
        options=options,
        original_text=text,
        subject_text="timer",
    )


def _format_timer_list(subjects: list[TimerSubject], *, now: datetime) -> str:
    parts = []
    for item in subjects[:5]:
        remaining = max(0, int((item.occurrence.due_at - now).total_seconds()))
        parts.append(f"{_subject_label(item)} with {_format_duration(remaining)} left")
    return f"You have {len(subjects)} active {_plural('timer', len(subjects))}: " + "; ".join(parts) + "."


def _subject_label(subject: TimerSubject) -> str:
    return f"{subject.name} timer" if subject.name else f"{_format_duration(subject.duration_seconds)} timer"


def _serialize(subject: TimerSubject, now: datetime) -> dict[str, Any]:
    remaining = max(0, int((subject.occurrence.due_at - now).total_seconds()))
    return {
        "schedule_id": subject.schedule.schedule_id,
        "occurrence_id": subject.occurrence.occurrence_id,
        "alert_id": subject.delivery.alert_id if subject.delivery else None,
        "name": subject.name,
        "label": _subject_label(subject),
        "status": subject.occurrence.status,
        "due_at": subject.occurrence.due_at.isoformat(),
        "remaining_seconds": remaining,
        "duration_seconds": subject.duration_seconds,
        "target_scope": subject.schedule.target_scope,
        "target_id": subject.schedule.target_id,
        "message": subject.schedule.message,
        "late_seconds": max(0, int((now - subject.occurrence.due_at).total_seconds())),
    }


def _selected_details(operation: str, subject: TimerSubject, now: datetime) -> dict[str, Any]:
    return _details(operation, count=1, **_serialize(subject, now))


def _terminal_reply(word: str, subject: TimerSubject, *, operation: str) -> tuple[str, dict[str, Any]]:
    label = f" {subject.name}" if subject.name else ""
    return f"{word} your{label} timer.", _details(
        operation,
        schedule_id=subject.schedule.schedule_id,
        occurrence_id=subject.occurrence.occurrence_id,
        terminal=True,
    )


def _details(operation: str, **values: Any) -> dict[str, Any]:
    return {"kind": "timer", "operation": operation, **values}


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours} {_plural('hour', hours)}")
    if minutes:
        parts.append(f"{minutes} {_plural('minute', minutes)}")
    if secs and not hours:
        parts.append(f"{secs} {_plural('second', secs)}")
    return ", ".join(parts) if parts else "0 seconds"


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("’", "'").strip(" .?!").split())


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timer timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
