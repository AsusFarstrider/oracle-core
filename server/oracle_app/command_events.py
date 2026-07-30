from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from threading import Lock
from typing import Literal

from oracle_app.schemas import CommandInterimEvent


_MAX_EVENTS_PER_SESSION = 20
_LOCK = Lock()
_NEXT_EVENT_ID = 1
_EVENTS: dict[tuple[str, str], list[CommandInterimEvent]] = defaultdict(list)


def append_command_interim_event(
    *,
    source: str | None,
    session_id: str | None,
    event_type: Literal["facts_summarizer_ack"],
    domain: str,
    message: str,
) -> CommandInterimEvent | None:
    normalized_source = str(source or "").strip()
    normalized_session = str(session_id or "").strip()
    normalized_message = str(message or "").strip()
    if not normalized_source or not normalized_session or not normalized_message:
        return None

    global _NEXT_EVENT_ID
    with _LOCK:
        event = CommandInterimEvent(
            event_id=_NEXT_EVENT_ID,
            event_type=event_type,
            source=normalized_source,
            session_id=normalized_session,
            domain=domain,
            message=normalized_message,
            created_at=_utc_now_iso(),
        )
        _NEXT_EVENT_ID += 1
        key = (normalized_source, normalized_session)
        events = _EVENTS[key]
        events.append(event)
        if len(events) > _MAX_EVENTS_PER_SESSION:
            del events[: len(events) - _MAX_EVENTS_PER_SESSION]
        return event


def list_command_interim_events(
    *,
    source: str | None,
    session_id: str | None,
    after_event_id: int = 0,
) -> list[CommandInterimEvent]:
    normalized_source = str(source or "").strip()
    normalized_session = str(session_id or "").strip()
    if not normalized_source or not normalized_session:
        return []
    with _LOCK:
        events = list(_EVENTS.get((normalized_source, normalized_session), []))
    return [event for event in events if event.event_id > after_event_id]


def clear_command_interim_events() -> None:
    global _NEXT_EVENT_ID
    with _LOCK:
        _EVENTS.clear()
        _NEXT_EVENT_ID = 1


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
