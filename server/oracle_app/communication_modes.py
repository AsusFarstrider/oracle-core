from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .deterministic_values import parse_duration
from .memory.communication_modes import CommunicationModeState, read_mode_state, set_mode_state
from .memory.alert_lifecycle import list_alert_occurrences, list_alert_schedules, transition_alert_occurrence
from .temporal import parse_clock_candidates, parse_relative_date, resolve_local_wall_time


@dataclass(frozen=True)
class PresentationDecision:
    audible: bool
    dnd_state: str
    reason: str


def is_dnd_request(text: str) -> bool:
    normalized = " ".join(str(text or "").casefold().split())
    return bool(re.search(r"\b(?:do not disturb|don't disturb|dnd|quiet mode)\b", normalized))


def reconcile_communication_modes(
    *, household: HouseholdRuntimeSettings, now: datetime,
    db_path: Path | None = None,
) -> CommunicationModeState | None:
    mode = _configured_dnd_mode(household)
    if mode is None:
        return None
    return read_mode_state(mode.id, now=now, db_path=db_path)


def _configured_dnd_mode(household: HouseholdRuntimeSettings):
    modes = getattr(household, "modes", {})
    values = modes.values() if hasattr(modes, "values") else modes
    return next((item for item in values if item.enabled and item.do_not_disturb is not None), None)


def execute_dnd_request(
    text: str,
    *,
    household: HouseholdRuntimeSettings,
    source_id: str | None,
    now: datetime,
    db_path: Path | None = None,
    conflict_policy: str | None = None,
) -> dict[str, object]:
    mode = _configured_dnd_mode(household)
    if mode is None:
        return {"ok": False, "error": "dnd_unconfigured", "speech": "Do Not Disturb is not configured."}
    normalized = " ".join(str(text or "").casefold().replace("’", "'").split())
    if re.search(r"\b(?:turn off|disable|clear|cancel|stop)\b", normalized):
        state = set_mode_state(mode.id, active=False, now=now, actor_source_id=source_id, db_path=db_path)
        return {"ok": True, "action": "disabled", "active": state.active, "speech": "Do Not Disturb is off."}
    if re.search(r"\b(?:status|is .* on|is .* active|check)\b", normalized):
        state = read_mode_state(mode.id, now=now, db_path=db_path)
        return _state_result(state)
    if not (
        re.search(r"\b(?:turn on|enable|start)\b", normalized)
        or re.search(r"\b(?:until|for the rest of the night|for .+)\b", normalized)
        or normalized in {"do not disturb", "don't disturb", "dnd", "quiet mode"}
    ):
        return {
            "ok": False, "error": "dnd_unrecognized",
            "speech": "I can turn Do Not Disturb on, off, or tell you its status.",
        }

    expiry, error = _resolve_dnd_expiry(normalized, now=now, timezone_name=household.household.timezone)
    if error:
        return {"ok": False, "error": error, "clarification_required": True, "speech": "What time should Do Not Disturb end?"}
    policy = mode.do_not_disturb
    assert policy is not None
    if expiry is None and not policy.allow_indefinite:
        return {"ok": False, "error": "dnd_indefinite_not_allowed", "speech": "How long should Do Not Disturb stay on?"}
    if expiry is not None:
        seconds = int((expiry.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
        if not policy.minimum_duration_seconds <= seconds <= policy.maximum_duration_seconds:
            return {"ok": False, "error": "dnd_duration_out_of_bounds", "speech": "That Do Not Disturb duration is outside the configured bounds."}
    schedules = {item.schedule_id: item for item in list_alert_schedules(statuses=("active",), db_path=db_path) if item.kind in {"timer", "alarm"}}
    conflicts = []
    for occurrence in list_alert_occurrences(statuses=("scheduled", "due", "ringing"), db_path=db_path):
        schedule = schedules.get(occurrence.schedule_id)
        if schedule is None or (expiry is not None and occurrence.due_at >= expiry.astimezone(UTC)):
            continue
        if schedule.metadata.get("dnd_override") or occurrence.metadata.get("dnd_occurrence_override"):
            continue
        conflicts.append(occurrence)
    if conflicts and conflict_policy is None:
        return {
            "ok": False, "error": "dnd_alert_conflict", "clarification_required": True,
            "speech": f"{len(conflicts)} scheduled alarm or timer occurrence{'s' if len(conflicts) != 1 else ''} falls during Do Not Disturb. Should it sound anyway?",
            "conflict_occurrence_ids": [item.occurrence_id for item in conflicts],
            "requested_expires_at": None if expiry is None else expiry.isoformat(),
        }
    if conflict_policy == "allow":
        for occurrence in conflicts:
            transition_alert_occurrence(
                occurrence.occurrence_id, status=occurrence.status,
                actor_type="person", actor_id=source_id,
                reason="dnd_occurrence_override_confirmed", now=now,
                metadata_update={"dnd_occurrence_override": True}, db_path=db_path,
            )
    state = set_mode_state(mode.id, active=True, now=now, expires_at=expiry, actor_source_id=source_id, db_path=db_path)
    suffix = " indefinitely" if expiry is None else f" until {expiry.astimezone(ZoneInfo(household.household.timezone)).strftime('%-I:%M %p')}"
    return {"ok": True, "action": "enabled", "active": True, "expires_at": None if expiry is None else expiry.isoformat(), "speech": f"Do Not Disturb is on{suffix}."}


def presentation_decision(
    *, kind: str, metadata: dict[str, object], household: HouseholdRuntimeSettings,
    now: datetime, db_path: Path | None = None,
) -> PresentationDecision:
    mode = _configured_dnd_mode(household)
    if mode is None:
        return PresentationDecision(True, "inactive", "dnd_unconfigured")
    try:
        state = read_mode_state(mode.id, now=now, db_path=db_path)
    except Exception:
        return PresentationDecision(False, "unknown", "dnd_state_unavailable")
    if not state.active:
        return PresentationDecision(True, "inactive", "dnd_inactive")
    if kind in {"timer", "alarm"} and bool(metadata.get("dnd_override") or metadata.get("dnd_occurrence_override")):
        return PresentationDecision(True, "active", "explicit_dnd_override")
    return PresentationDecision(False, "active", "dnd_suppresses_unsolicited_audio")


def new_alert_requires_dnd_clarification(
    text: str, *, household: HouseholdRuntimeSettings, now: datetime,
    due_at: datetime | None = None,
    db_path: Path | None = None,
) -> bool:
    normalized = " ".join(str(text or "").casefold().split())
    if "during dnd" in normalized or "during do not disturb" in normalized or "during quiet mode" in normalized:
        return False
    mode = _configured_dnd_mode(household)
    if mode is None:
        return False
    try:
        state = read_mode_state(mode.id, now=now, db_path=db_path)
        if not state.active:
            return False
        return due_at is None or state.expires_at is None or due_at.astimezone(UTC) < state.expires_at
    except Exception:
        return True


def _resolve_dnd_expiry(text: str, *, now: datetime, timezone_name: str) -> tuple[datetime | None, str | None]:
    local = now.astimezone(ZoneInfo(timezone_name))
    if re.search(r"\buntil tomorrow\s*$", text):
        return resolve_local_wall_time(local.date() + timedelta(days=1), time(7), timezone_name), None
    if "rest of the night" in text:
        if local.time() >= time(19):
            day = local.date() + timedelta(days=1)
        elif local.time() < time(7):
            day = local.date()
        else:
            return None, "dnd_night_context_ambiguous"
        return resolve_local_wall_time(day, time(7), timezone_name), None
    match = re.search(r"\bfor (.+?)(?:\s+(?:please|thanks))?$", text)
    if match:
        duration = parse_duration(match.group(1), max_seconds=366 * 24 * 3600)
        if duration is None:
            return None, "dnd_duration_unsupported"
        return now + timedelta(seconds=duration.seconds), None
    until = re.search(r"\buntil\s+(.+?)\s*$", text)
    if until:
        words = until.group(1).split()
        resolved: list[datetime] = []
        for index in range(len(words)):
            date_text = " ".join(words[:index]).removesuffix(" at").strip()
            clocks = parse_clock_candidates(" ".join(words[index:]))
            if not clocks:
                continue
            target_date = local.date() if not date_text else parse_relative_date(date_text, local.date())
            if target_date is None:
                continue
            for clock_value in clocks:
                candidate = resolve_local_wall_time(target_date, clock_value, timezone_name)
                if not date_text and candidate <= local:
                    candidate = resolve_local_wall_time(target_date + timedelta(days=1), clock_value, timezone_name)
                if candidate > local:
                    resolved.append(candidate)
        unique = {item.astimezone(UTC): item for item in resolved}
        if len(unique) == 1:
            return next(iter(unique.values())), None
        return None, "dnd_expiry_ambiguous"
    return None, None


def _state_result(state: CommunicationModeState) -> dict[str, object]:
    if not state.active:
        return {"ok": True, "action": "status", "active": False, "speech": "Do Not Disturb is off."}
    return {"ok": True, "action": "status", "active": True, "expires_at": None if state.expires_at is None else state.expires_at.isoformat(), "speech": "Do Not Disturb is on."}
