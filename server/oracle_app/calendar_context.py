from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .calendar import CalendarQuery
from .session_state import get_informational_context, set_informational_context


@dataclass(frozen=True)
class CalendarContextResolution:
    query: CalendarQuery | None = None
    result: dict[str, object] | None = None


def is_calendar_context_followup(text: str, *, source: str | None, session_id: str | None) -> bool:
    normalized = _normalize(text)
    if normalized not in {
        "what's next", "what is next", "which calendar was that from",
        "which calendar is that from", "what about the week after",
        "is that up to date", "is that updated",
    }:
        return False
    return get_informational_context(source, session_id, domain="calendar") is not None


def resolve_calendar_context(
    text: str,
    *,
    source: str | None,
    session_id: str | None,
    timezone_name: str,
    person_id: str | None,
    calendar_id: str | None,
) -> CalendarContextResolution:
    normalized = _normalize(text)
    context = get_informational_context(source, session_id, domain="calendar")
    subject = context.get("subject") if isinstance(context, dict) else None
    if not isinstance(subject, dict):
        return CalendarContextResolution()

    if normalized in {"which calendar was that from", "which calendar is that from"}:
        labels = [str(item) for item in subject.get("source_ids") or [] if str(item).strip()]
        return CalendarContextResolution(result={
            "action": "calendar_provenance",
            "source_labels": labels,
            "calendar_ids": list(subject.get("calendar_ids") or []),
        })

    start = _parse_datetime(subject.get("window_start"))
    end = _parse_datetime(subject.get("window_end"))
    retained_person = str(subject.get("user_id") or "").strip() or person_id
    retained_calendars = [str(item) for item in subject.get("calendar_ids") or [] if str(item).strip()]
    retained_calendar = retained_calendars[0] if len(retained_calendars) == 1 else calendar_id
    if normalized == "what about the week after" and start is not None and end is not None:
        return CalendarContextResolution(query=CalendarQuery(
            "list_events", start + timedelta(days=7), end + timedelta(days=7), None,
            normalized, person_id=retained_person, calendar_id=retained_calendar,
        ))
    if normalized in {"is that up to date", "is that updated"} and start is not None and end is not None:
        return CalendarContextResolution(query=CalendarQuery(
            "list_events", start, end, None, normalized,
            person_id=retained_person, force_refresh=True, calendar_id=retained_calendar,
        ))
    if normalized in {"what's next", "what is next"}:
        return CalendarContextResolution(query=CalendarQuery(
            "next_event", None, None, None, normalized,
            person_id=retained_person, calendar_id=retained_calendar,
        ))
    return CalendarContextResolution()


def retain_calendar_context(
    result: dict[str, object],
    *,
    source: str | None,
    session_id: str | None,
) -> bool:
    events = [item for item in result.get("events") or [] if isinstance(item, dict)]
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    event_calendar_ids = [str(item.get("source_id")) for item in events if str(item.get("source_id") or "").strip()]
    event_source_labels = [str(item.get("source_label")) for item in events if str(item.get("source_label") or "").strip()]
    calendar_ids = list(dict.fromkeys(event_calendar_ids)) or [
        str(item) for item in result.get("source_ids") or [] if str(item).strip()
    ]
    source_labels = list(dict.fromkeys(event_source_labels)) or [
        str(item.get("source_label"))
        for item in result.get("source_availability") or []
        if isinstance(item, dict) and str(item.get("source_label") or "").strip()
    ]
    subject_text = str(events[0].get("summary") or "") if events else "calendar query"
    return set_informational_context(
        source,
        session_id,
        domain="calendar",
        subject={
            "subject_id": f"calendar:{result.get('person_id') or 'household'}:{calendar_ids[0] if calendar_ids else 'none'}"[:256],
            "subject_text": subject_text[:512],
            "user_id": str(result.get("person_id") or "")[:512],
            "calendar_ids": calendar_ids[:16],
            "event_ids": [str(item.get("uid")) for item in events if str(item.get("uid") or "").strip()][:16],
            "window_start": str(query.get("start") or "")[:512],
            "window_end": str(query.get("end") or "")[:512],
            "source_ids": source_labels[:16],
            "evidence_ids": [str(item.get("uid")) for item in events if str(item.get("uid") or "").strip()][:16],
        },
    )


def _parse_datetime(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().strip(" .?!").split())
