from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CalendarReminderIntent:
    """Provider-neutral concrete reminder intent emitted by a Calendar bridge."""

    reminder_id: str
    event_occurrence_id: str
    due_at: datetime
    event_summary: str
    event_start: datetime


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool
    location: str
    source_id: str = ""
    source_label: str = ""
    user_ids: tuple[str, ...] = ()
    recurring: bool = False
    recurrence_id: str | None = None
    reminder_intents: tuple[CalendarReminderIntent, ...] = ()
