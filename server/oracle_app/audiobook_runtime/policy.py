from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable


def set_sleep_timer(
    *,
    source: str | None,
    session_id: str | None,
    duration_seconds: int | None,
    get_active_playback_for_source: Callable[[str | None], dict[str, Any] | None],
    create_sleep_timer: Callable[[str | None, str | None, int], dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    active = get_active_playback_for_source(source)
    if active is None:
        return "failed", {
            "action": "sleep_timer",
            "error": "no_active_audiobook",
            "detail": "No audiobook is playing right now.",
        }
    if duration_seconds is None or duration_seconds <= 0:
        return "failed", {
            "action": "sleep_timer",
            "error": "invalid_sleep_timer_duration",
            "detail": "I need a duration for the sleep timer.",
        }

    details = create_sleep_timer(source, session_id, duration_seconds)
    return "executed", {
        "action": "sleep_timer",
        **details,
    }


def cancel_sleep_timer(
    *,
    source: str | None,
    cancel_alerts: Callable[[str | None, str, bool], int],
    kind: str,
) -> tuple[str, dict[str, Any]]:
    count = cancel_alerts(source, kind, all_matches=True)
    return "executed", {
        "action": "sleep_timer",
        "operation": "cancel",
        "count": count,
    }


def sleep_timer_status(
    *,
    source: str | None,
    list_alerts: Callable[[str | None, str], list[Any]],
    kind: str,
) -> tuple[str, dict[str, Any]]:
    timers = list_alerts(source, kind)
    result = {
        "action": "sleep_timer",
        "operation": "status",
        "count": len(timers),
        "remaining_seconds": None,
    }
    if timers:
        current = timers[0]
        result["alert_id"] = current.alert_id
        result["due_at"] = current.due_at.isoformat()
        result["remaining_seconds"] = max(
            0.0,
            (current.due_at - datetime.now().astimezone()).total_seconds(),
        )
    return "executed", result


def create_sleep_timer(
    *,
    source: str | None,
    session_id: str | None,
    duration_seconds: int,
    cancel_alerts: Callable[[str | None, str, bool], int],
    create_alert: Callable[..., Any],
    format_duration: Callable[[int], str],
    kind: str,
) -> dict[str, Any]:
    cancel_alerts(source, kind, all_matches=True)
    alert = create_alert(
        kind=kind,
        due_at=now_plus(duration_seconds),
        message="",
        source=source,
        session_id=session_id,
        metadata={"duration_seconds": duration_seconds, "silent": True, "target": "audiobook"},
    )
    return {
        "operation": "create",
        "alert_id": alert.alert_id,
        "due_at": alert.due_at.isoformat(),
        "duration_seconds": duration_seconds,
        "duration_speech": format_duration(duration_seconds),
    }


def now_plus(duration_seconds: int):
    return datetime.now().astimezone() + timedelta(seconds=duration_seconds)
