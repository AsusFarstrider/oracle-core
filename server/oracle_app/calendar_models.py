from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool
    location: str
