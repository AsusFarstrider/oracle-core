from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import replace
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .constants import ALERTS_STATE_PATH


@dataclass
class ScheduledAlert:
    alert_id: str
    kind: str
    source: str | None
    session_id: str | None
    due_at: datetime
    created_at: datetime
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    delivered: bool = False


_ALERTS: list[ScheduledAlert] = []
_LOCK = threading.Lock()


def _copy_alert(alert: ScheduledAlert) -> ScheduledAlert:
    return replace(alert, metadata=dict(alert.metadata))


def _serialize_alert(alert: ScheduledAlert) -> dict[str, Any]:
    return {
        "alert_id": alert.alert_id,
        "kind": alert.kind,
        "source": alert.source,
        "session_id": alert.session_id,
        "due_at": alert.due_at.isoformat(),
        "created_at": alert.created_at.isoformat(),
        "message": alert.message,
        "metadata": dict(alert.metadata),
        "expires_at": alert.expires_at.isoformat() if alert.expires_at is not None else None,
        "delivered": bool(alert.delivered),
    }


def _deserialize_alert(payload: dict[str, Any]) -> ScheduledAlert | None:
    try:
        due_at = datetime.fromisoformat(str(payload.get("due_at") or ""))
        created_at = datetime.fromisoformat(str(payload.get("created_at") or ""))
    except ValueError:
        return None
    expires_at: datetime | None = None
    if payload.get("expires_at") not in (None, ""):
        try:
            expires_at = datetime.fromisoformat(str(payload.get("expires_at")))
        except ValueError:
            return None
    alert_id = str(payload.get("alert_id") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not alert_id or not kind or not message:
        return None
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return ScheduledAlert(
        alert_id=alert_id,
        kind=kind,
        source=str(payload.get("source") or "").strip() or None,
        session_id=str(payload.get("session_id") or "").strip() or None,
        due_at=due_at,
        created_at=created_at,
        message=message,
        metadata=dict(metadata),
        expires_at=expires_at,
        delivered=bool(payload.get("delivered")),
    )


def _save_alerts_unlocked() -> None:
    ALERTS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ALERTS_STATE_PATH.with_suffix(".tmp")
    payload = [_serialize_alert(alert) for alert in _ALERTS]
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    tmp_path.replace(ALERTS_STATE_PATH)


def _load_alerts_from_disk() -> None:
    if not ALERTS_STATE_PATH.exists():
        with _LOCK:
            _ALERTS.clear()
        return
    try:
        with ALERTS_STATE_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        raw = []
    loaded: list[ScheduledAlert] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            alert = _deserialize_alert(item)
            if alert is not None:
                loaded.append(alert)
    with _LOCK:
        _ALERTS[:] = loaded


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _pluralize(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _is_count_query(normalized: str, kind: str) -> bool:
    plural = _pluralize(kind, 2)
    singular_article = "an" if kind == "alarm" else "a"
    return _contains_any(
        normalized,
        (
            f"how many {plural}",
            f"how many {kind}",
            f"how many active {plural}",
            f"do i have any {plural}",
            f"do i have {singular_article} {kind}",
            f"are there any {plural}",
            f"is there {singular_article} {kind}",
        ),
    )


def _is_next_query(normalized: str, kind: str) -> bool:
    plural = _pluralize(kind, 2)
    return _contains_any(
        normalized,
        (
            f"what's my next {kind}",
            f"what is my next {kind}",
            f"when is my next {kind}",
            f"what is the next {kind}",
            f"what's the next {kind}",
            f"when is the next {kind}",
            f"what {kind} is next",
            f"which {kind} is next",
            f"which of my {plural} is next",
        ),
    )


def _is_cancel_query(normalized: str, kind: str) -> bool:
    plural = _pluralize(kind, 2)
    return _contains_any(
        normalized,
        (
            f"cancel my {kind}",
            f"cancel the {kind}",
            f"cancel {kind}",
            f"cancel my {plural}",
            f"cancel all {plural}",
            f"cancel the {plural}",
            f"stop my {kind}",
            f"stop the {kind}",
            f"stop {kind}",
            f"delete my {kind}",
            f"delete the {kind}",
            f"remove my {kind}",
            f"remove the {kind}",
            f"clear my {plural}",
            f"clear the {plural}",
            f"clear all {plural}",
        ),
    )


def _cancel_all_requested(normalized: str, kind: str) -> bool:
    plural = _pluralize(kind, 2)
    return _contains_any(
        normalized,
        (
            " all ",
            f"all {plural}",
            f"my {plural}",
            f"the {plural}",
        ),
    )


def _is_timer_status_query(normalized: str) -> bool:
    return _contains_any(
        normalized,
        (
            "how much",
            "time left",
            "remaining",
            "what timers do i have",
            "what timer do i have",
            "what timers are set",
            "what timer is set",
            "list timers",
            "list timer",
            "do i have any timers",
            "do i have a timer",
            "is there a timer",
            "are there any timers",
            "when is my timer",
            "when is my next timer",
            "when does my timer go off",
            "what's my next timer",
            "what is my next timer",
            "timer status",
            "status of my timer",
            "how many timers do i have",
        ),
    )


def _is_alarm_status_query(normalized: str) -> bool:
    return _contains_any(
        normalized,
        (
            "what alarms",
            "what alarm",
            "list alarms",
            "list alarm",
            "alarms do i have",
            "alarm do i have",
            "alarms are set",
            "alarm is set",
            "do i have any alarms",
            "do i have an alarm",
            "is there an alarm",
            "are there any alarms",
            "when is my alarm",
            "when is my next alarm",
            "when does my alarm go off",
            "what's my next alarm",
            "what is my next alarm",
            "alarm status",
            "status of my alarm",
            "how many alarms do i have",
        ),
    )


def _is_reminder_status_query(normalized: str) -> bool:
    return _contains_any(
        normalized,
        (
            "what reminders",
            "what reminder",
            "list reminders",
            "list reminder",
            "reminders do i have",
            "reminder do i have",
            "do i have any reminders",
            "do i have a reminder",
            "is there a reminder",
            "are there any reminders",
            "when is my reminder",
            "when is my next reminder",
            "what is my reminder",
            "what's my next reminder",
            "what is my next reminder",
            "reminder status",
            "status of my reminder",
            "how many reminders do i have",
        ),
    )


def format_duration(total_seconds: float) -> str:
    seconds = max(0, int(round(total_seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {_pluralize('hour', hours)}")
    if minutes:
        parts.append(f"{minutes} {_pluralize('minute', minutes)}")
    if secs and not hours:
        parts.append(f"{secs} {_pluralize('second', secs)}")
    return ", ".join(parts) if parts else "0 seconds"


_NUMBER_WORDS: dict[str, int] = {
    "zero": 0,
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_NUMBER_WORD_PATTERN = (
    r"(?:zero|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|and)"
)


def _parse_number_phrase(text: str) -> float | None:
    cleaned = text.strip().lower().replace("-", " ")
    if not cleaned:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return float(cleaned)

    total = 0
    current = 0
    found = False
    for token in cleaned.split():
        if token == "and":
            continue
        if token in _NUMBER_WORDS:
            current += _NUMBER_WORDS[token]
            found = True
            continue
        if token == "hundred":
            if current == 0:
                current = 1
            current *= 100
            found = True
            continue
        if token == "thousand":
            if current == 0:
                current = 1
            total += current * 1000
            current = 0
            found = True
            continue
        return None
    if not found:
        return None
    return float(total + current)


def parse_duration(text: str) -> int | None:
    normalized = _normalize_text(text)
    matches = re.findall(
        rf"((?:\d+(?:\.\d+)?)|(?:{_NUMBER_WORD_PATTERN}(?:[\s-]+{_NUMBER_WORD_PATTERN})*))\s*"
        r"(second|seconds|minute|minutes|hour|hours)\b",
        normalized,
    )
    if not matches:
        return None

    total_seconds = 0.0
    for amount_text, unit in matches:
        amount = _parse_number_phrase(amount_text)
        if amount is None:
            continue
        if unit.startswith("hour"):
            total_seconds += amount * 3600
        elif unit.startswith("minute"):
            total_seconds += amount * 60
        else:
            total_seconds += amount
    return int(total_seconds) if total_seconds > 0 else None


def parse_clock_time(text: str, *, now: datetime | None = None) -> datetime | None:
    normalized = _normalize_text(text)
    current = now or _now_local()
    match = re.search(
        r"(?:(tomorrow)\s+)?(?:at\s+|for\s+)?(\d{1,4})(?:(?::|\s+)(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b",
        normalized,
    )
    if not match:
        return None

    tomorrow_flag = bool(match.group(1))
    hour_text = match.group(2)
    minute_text = match.group(3)
    meridiem = str(match.group(4) or "").replace(".", "")

    if minute_text is None and len(hour_text) in {3, 4} and meridiem:
        minute_text = hour_text[-2:]
        hour_text = hour_text[:-2]

    hour = int(hour_text)
    minute = int(minute_text or 0)
    if minute > 59:
        return None

    if meridiem:
        if hour == 12:
            hour = 0
        if meridiem == "pm":
            hour += 12
    elif hour < 24:
        hour = hour
    else:
        return None
    if hour > 23:
        return None

    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if tomorrow_flag or candidate <= current:
        candidate = candidate + timedelta(days=1)
    return candidate


def _prune_old_alerts(now: datetime | None = None) -> None:
    current = now or _now_local()
    cutoff = current - timedelta(days=2)
    with _LOCK:
        original_count = len(_ALERTS)
        _ALERTS[:] = [
            alert
            for alert in _ALERTS
            if not (alert.delivered and alert.due_at < cutoff)
        ]
        if len(_ALERTS) != original_count:
            _save_alerts_unlocked()


def create_alert(
    *,
    kind: str,
    due_at: datetime,
    message: str,
    source: str | None,
    session_id: str | None,
    metadata: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> ScheduledAlert:
    alert = ScheduledAlert(
        alert_id=uuid.uuid4().hex[:12],
        kind=kind,
        source=source,
        session_id=session_id,
        due_at=due_at,
        created_at=_now_local(),
        message=message,
        metadata=dict(metadata or {}),
        expires_at=expires_at,
    )
    with _LOCK:
        _ALERTS.append(alert)
        _save_alerts_unlocked()
    return alert


def create_alert_batch(
    *,
    kind: str,
    due_at: datetime,
    message: str,
    sources: list[str],
    session_id: str | None,
    metadata: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> tuple[list[ScheduledAlert], bool]:
    clean_key = str(idempotency_key or "").strip()
    with _LOCK:
        if clean_key and any(
            str(alert.metadata.get("idempotency_key") or "").strip() == clean_key
            for alert in _ALERTS
        ):
            return [], True

        created_at = _now_local()
        created: list[ScheduledAlert] = []
        for source in sources:
            alert_metadata = dict(metadata or {})
            if clean_key:
                alert_metadata["idempotency_key"] = clean_key
            alert = ScheduledAlert(
                alert_id=uuid.uuid4().hex[:12],
                kind=kind,
                source=str(source),
                session_id=session_id,
                due_at=due_at,
                created_at=created_at,
                message=message,
                metadata=alert_metadata,
                expires_at=expires_at,
            )
            _ALERTS.append(alert)
            created.append(alert)
        _save_alerts_unlocked()
    return [_copy_alert(alert) for alert in created], False


def record_alert_idempotency_key(
    idempotency_key: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> bool:
    clean_key = str(idempotency_key or "").strip()
    if not clean_key:
        raise ValueError("idempotency_key is required")
    with _LOCK:
        if any(
            str(alert.metadata.get("idempotency_key") or "").strip() == clean_key
            for alert in _ALERTS
        ):
            return True
        now = _now_local()
        receipt_metadata = dict(metadata or {})
        receipt_metadata["idempotency_key"] = clean_key
        _ALERTS.append(
            ScheduledAlert(
                alert_id=uuid.uuid4().hex[:12],
                kind="idempotency_receipt",
                source=None,
                session_id=None,
                due_at=now,
                created_at=now,
                message="Notification occurrence receipt.",
                metadata=receipt_metadata,
                delivered=True,
            )
        )
        _save_alerts_unlocked()
    return False


def clear_alerts() -> None:
    with _LOCK:
        _ALERTS.clear()
        _save_alerts_unlocked()


def _matching_alerts(
    *,
    source: str | None = None,
    kind: str | None = None,
    delivered: bool | None = None,
) -> list[ScheduledAlert]:
    with _LOCK:
        alerts = list(_ALERTS)
    results: list[ScheduledAlert] = []
    for alert in alerts:
        if source is not None and alert.source != source:
            continue
        if kind is not None and alert.kind != kind:
            continue
        if delivered is not None and alert.delivered != delivered:
            continue
        results.append(alert)
    return sorted(results, key=lambda item: item.due_at)


def list_due_alerts(source: str | None, *, kind: str | None = None) -> list[ScheduledAlert]:
    current = _now_local()
    return [
        _copy_alert(alert)
        for alert in _matching_alerts(source=source, kind=kind, delivered=False)
        if alert.due_at <= current
    ]


def consume_due_alerts(
    source: str | None,
    *,
    notification_decisions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    current = _now_local()
    _prune_old_alerts(current)
    due_alerts = [
        alert
        for alert in _matching_alerts(source=source, delivered=False)
        if alert.due_at <= current
    ]
    if not due_alerts:
        return []

    payload_alerts: list[ScheduledAlert] = []
    with _LOCK:
        by_id = {alert.alert_id: alert for alert in _ALERTS}
        changed = False
        for alert in due_alerts:
            stored = by_id.get(alert.alert_id)
            if stored is None:
                continue
            if alert.expires_at is not None and alert.expires_at <= current:
                stored.delivered = True
                changed = True
                continue
            if alert.kind == "notification":
                decision = str((notification_decisions or {}).get(alert.alert_id) or "defer")
                if decision == "defer":
                    continue
                stored.delivered = True
                changed = True
                if decision == "deliver":
                    payload_alerts.append(alert)
                continue
            stored.delivered = True
            changed = True
            payload_alerts.append(alert)
        if changed:
            _save_alerts_unlocked()

    payload: list[dict[str, Any]] = []
    for alert in payload_alerts:
        payload.append(
            {
                "alert_id": alert.alert_id,
                "kind": alert.kind,
                "message": alert.message,
                "due_at": alert.due_at.isoformat(),
                "source": alert.source,
                "session_id": alert.session_id,
                "metadata": dict(alert.metadata),
            }
        )
    return payload


def list_alerts(source: str | None, kind: str) -> list[ScheduledAlert]:
    current = _now_local()
    _prune_old_alerts(current)
    return [
        _copy_alert(alert)
        for alert in _matching_alerts(source=source, kind=kind, delivered=False)
        if alert.due_at >= current
    ]


def cancel_alerts(source: str | None, kind: str, *, all_matches: bool) -> int:
    with _LOCK:
        matches = [
            alert for alert in _ALERTS if alert.source == source and alert.kind == kind and not alert.delivered
        ]
        if not matches:
            return 0
        matches.sort(key=lambda item: item.due_at)
        remove_ids = {alert.alert_id for alert in matches} if all_matches else {matches[0].alert_id}
        _ALERTS[:] = [alert for alert in _ALERTS if alert.alert_id not in remove_ids]
        _save_alerts_unlocked()
    return len(remove_ids)


def _format_alert_listing(kind: str, alerts: list[ScheduledAlert], *, mode: str = "status") -> str:
    if not alerts:
        if kind == "timer":
            return "You have no active timers."
        if kind == "alarm":
            return "You have no active alarms."
        return "You have no active reminders."

    current = _now_local()
    if kind == "timer":
        if mode == "count":
            return f"You have {len(alerts)} active {_pluralize('timer', len(alerts))}."
        if mode == "next":
            remaining = format_duration((alerts[0].due_at - current).total_seconds())
            return f"Your next timer ends in {remaining}."
        if len(alerts) == 1:
            remaining = format_duration((alerts[0].due_at - current).total_seconds())
            return f"Your timer has {remaining} remaining."
        parts = []
        for alert in alerts[:3]:
            remaining = format_duration((alert.due_at - current).total_seconds())
            parts.append(remaining)
        return f"You have {len(alerts)} timers. The next ones end in {', '.join(parts)}."

    if kind == "alarm":
        if mode == "count":
            return f"You have {len(alerts)} active {_pluralize('alarm', len(alerts))}."
        if mode == "next":
            return f"Your next alarm is set for {alerts[0].due_at.strftime('%-I:%M %p')}."
        if len(alerts) == 1:
            return f"Your next alarm is set for {alerts[0].due_at.strftime('%-I:%M %p')}."
        return f"You have {len(alerts)} alarms. The next one is at {alerts[0].due_at.strftime('%-I:%M %p')}."

    if mode == "count":
        return f"You have {len(alerts)} active {_pluralize('reminder', len(alerts))}."
    if mode == "next":
        return f"Your next reminder is at {alerts[0].due_at.strftime('%-I:%M %p')}: {alerts[0].message}."
    if len(alerts) == 1:
        return f"Your next reminder is at {alerts[0].due_at.strftime('%-I:%M %p')}: {alerts[0].message}."
    return f"You have {len(alerts)} reminders. The next one is {alerts[0].message} at {alerts[0].due_at.strftime('%-I:%M %p')}."


def _build_timer_response(text: str, source: str | None, session_id: str | None) -> tuple[str, dict]:
    normalized = _normalize_text(text)
    if _is_cancel_query(normalized, "timer"):
        all_matches = _cancel_all_requested(normalized, "timer")
        count = cancel_alerts(source, "timer", all_matches=all_matches)
        if count == 0:
            return "You have no active timers to cancel.", {"kind": "timer", "operation": "cancel"}
        return (
            f"Canceled {count} {_pluralize('timer', count)}.",
            {"kind": "timer", "operation": "cancel", "count": count},
        )

    if _is_timer_status_query(normalized):
        alerts = list_alerts(source, "timer")
        mode = "status"
        if _is_count_query(normalized, "timer"):
            mode = "count"
        elif _is_next_query(normalized, "timer"):
            mode = "next"
        return _format_alert_listing("timer", alerts, mode=mode), {
            "kind": "timer",
            "operation": "status",
            "count": len(alerts),
        }

    duration_seconds = parse_duration(normalized)
    if duration_seconds is None:
        raise ValueError("I need a duration for the timer.")
    due_at = _now_local() + timedelta(seconds=duration_seconds)
    message = f"Timer finished after {format_duration(duration_seconds)}."
    alert = create_alert(
        kind="timer",
        due_at=due_at,
        message=message,
        source=source,
        session_id=session_id,
        metadata={"duration_seconds": duration_seconds},
    )
    return (
        f"Timer set for {format_duration(duration_seconds)}.",
        {
            "kind": "timer",
            "operation": "create",
            "alert_id": alert.alert_id,
            "due_at": alert.due_at.isoformat(),
        },
    )


def _build_alarm_response(text: str, source: str | None, session_id: str | None) -> tuple[str, dict]:
    normalized = _normalize_text(text)
    if _is_cancel_query(normalized, "alarm"):
        all_matches = _cancel_all_requested(normalized, "alarm")
        count = cancel_alerts(source, "alarm", all_matches=all_matches)
        if count == 0:
            return "You have no active alarms to cancel.", {"kind": "alarm", "operation": "cancel"}
        return (
            f"Canceled {count} {_pluralize('alarm', count)}.",
            {"kind": "alarm", "operation": "cancel", "count": count},
        )

    if _is_alarm_status_query(normalized):
        alerts = list_alerts(source, "alarm")
        mode = "status"
        if _is_count_query(normalized, "alarm"):
            mode = "count"
        elif _is_next_query(normalized, "alarm"):
            mode = "next"
        return _format_alert_listing("alarm", alerts, mode=mode), {
            "kind": "alarm",
            "operation": "status",
            "count": len(alerts),
        }

    due_at = parse_clock_time(normalized)
    if due_at is None:
        raise ValueError("I need a time for the alarm.")
    alert = create_alert(
        kind="alarm",
        due_at=due_at,
        message=f"Alarm for {due_at.strftime('%-I:%M %p')}.",
        source=source,
        session_id=session_id,
    )
    return (
        f"Alarm set for {due_at.strftime('%-I:%M %p')}.",
        {
            "kind": "alarm",
            "operation": "create",
            "alert_id": alert.alert_id,
            "due_at": alert.due_at.isoformat(),
        },
    )


def _build_reminder_response(text: str, source: str | None, session_id: str | None) -> tuple[str, dict]:
    normalized = _normalize_text(text)
    if _is_cancel_query(normalized, "reminder"):
        all_matches = _cancel_all_requested(normalized, "reminder")
        count = cancel_alerts(source, "reminder", all_matches=all_matches)
        if count == 0:
            return "You have no active reminders to cancel.", {"kind": "reminder", "operation": "cancel"}
        return (
            f"Canceled {count} {_pluralize('reminder', count)}.",
            {"kind": "reminder", "operation": "cancel", "count": count},
        )

    if _is_reminder_status_query(normalized):
        alerts = list_alerts(source, "reminder")
        mode = "status"
        if _is_count_query(normalized, "reminder"):
            mode = "count"
        elif _is_next_query(normalized, "reminder"):
            mode = "next"
        return _format_alert_listing("reminder", alerts, mode=mode), {
            "kind": "reminder",
            "operation": "status",
            "count": len(alerts),
        }

    match_in = re.match(r"remind me to (.+) in (.+)", normalized)
    if match_in:
        message = match_in.group(1).strip()
        duration_seconds = parse_duration(match_in.group(2))
        if duration_seconds is None:
            raise ValueError("I need a reminder time.")
        due_at = _now_local() + timedelta(seconds=duration_seconds)
    else:
        match_at = re.match(r"remind me to (.+) (tomorrow at .+|at .+)", normalized)
        if not match_at:
            raise ValueError("I need both a reminder message and a time.")
        message = match_at.group(1).strip()
        due_at = parse_clock_time(match_at.group(2))
        if due_at is None:
            raise ValueError("I need a valid reminder time.")

    alert = create_alert(
        kind="reminder",
        due_at=due_at,
        message=f"Reminder: {message}.",
        source=source,
        session_id=session_id,
        metadata={"reminder_text": message},
    )
    return (
        f"Reminder set for {due_at.strftime('%-I:%M %p')}: {message}.",
        {
            "kind": "reminder",
            "operation": "create",
            "alert_id": alert.alert_id,
            "due_at": alert.due_at.isoformat(),
            "message": message,
        },
    )


def build_alert_response(text: str, source: str | None, session_id: str | None) -> tuple[str, dict]:
    normalized = _normalize_text(text)
    if "timer" in normalized or "countdown" in normalized:
        return _build_timer_response(normalized, source, session_id)
    if "alarm" in normalized:
        return _build_alarm_response(normalized, source, session_id)
    if "remind me" in normalized or "reminder" in normalized:
        return _build_reminder_response(normalized, source, session_id)
    raise ValueError("I could not tell whether that was a timer, alarm, or reminder request.")


_load_alerts_from_disk()
