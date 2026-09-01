from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
import calendar
from typing import Iterable
from zoneinfo import ZoneInfo

from .temporal import resolve_local_wall_time


_FREQUENCIES = frozenset({"daily", "weekly", "monthly"})


@dataclass(frozen=True)
class RecurrenceRule:
    """Bounded calendar recurrence, never cron or an open-ended expression."""

    frequency: str
    interval: int = 1
    weekdays: tuple[int, ...] = ()
    month_days: tuple[int, ...] = ()
    ordinals: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.frequency not in _FREQUENCIES:
            raise ValueError(f"Unsupported recurrence frequency {self.frequency!r}")
        if isinstance(self.interval, bool) or not 1 <= self.interval <= 52:
            raise ValueError("Recurrence interval must be between 1 and 52")
        if any(isinstance(value, bool) or not 0 <= value <= 6 for value in self.weekdays):
            raise ValueError("Recurrence weekdays must be integers from zero through six")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("Recurrence weekdays cannot repeat")
        if any(isinstance(value, bool) or not 1 <= value <= 31 for value in self.month_days):
            raise ValueError("Monthly recurrence days must be between 1 and 31")
        if len(set(self.month_days)) != len(self.month_days):
            raise ValueError("Monthly recurrence days cannot repeat")
        if any(value not in {-1, 1, 2, 3, 4, 5} for value in self.ordinals):
            raise ValueError("Monthly weekday ordinals must be first through fifth or last")
        if len(set(self.ordinals)) != len(self.ordinals):
            raise ValueError("Monthly weekday ordinals cannot repeat")
        if self.frequency == "daily" and (self.weekdays or self.month_days or self.ordinals):
            raise ValueError("Daily recurrence does not accept weekday or monthly selectors")
        if self.frequency == "weekly" and (not self.weekdays or self.month_days or self.ordinals):
            raise ValueError("Weekly recurrence requires weekdays only")
        if self.frequency == "monthly":
            ordinal_weekdays = bool(self.weekdays) and bool(self.ordinals)
            if bool(self.month_days) == ordinal_weekdays:
                raise ValueError("Monthly recurrence requires month days or ordinal weekdays")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RecurrenceRule:
        return cls(
            frequency=str(value.get("frequency") or ""),
            interval=int(value.get("interval") or 1),
            weekdays=tuple(int(item) for item in _sequence(value.get("weekdays"))),
            month_days=tuple(int(item) for item in _sequence(value.get("month_days"))),
            ordinals=tuple(int(item) for item in _sequence(value.get("ordinals"))),
        )


@dataclass(frozen=True)
class RecurrenceInstant:
    key: str
    due_at: datetime
    intended_local: str


def expand_recurrence(
    *,
    anchor: datetime,
    timezone_name: str,
    rule: RecurrenceRule | None,
    through: datetime,
    after: datetime | None = None,
    limit: int = 128,
) -> tuple[RecurrenceInstant, ...]:
    """Expand a finite UTC window while preserving local wall-clock intent."""

    if anchor.tzinfo is None or through.tzinfo is None or (after is not None and after.tzinfo is None):
        raise ValueError("Recurrence timestamps must include a timezone")
    if isinstance(limit, bool) or not 1 <= limit <= 512:
        raise ValueError("Recurrence expansion limit must be between 1 and 512")
    zone = ZoneInfo(timezone_name)
    local_anchor = anchor.astimezone(zone)
    horizon = through.astimezone(UTC)
    lower = after.astimezone(UTC) if after is not None else None
    if rule is None:
        due = anchor.astimezone(UTC)
        if due <= horizon and (lower is None or due > lower):
            intended = local_anchor.replace(tzinfo=None).isoformat(timespec="seconds")
            return (RecurrenceInstant(due.isoformat(), due, intended),)
        return ()

    horizon_local = through.astimezone(zone).date() + timedelta(days=1)
    current = local_anchor.date()
    results: list[RecurrenceInstant] = []
    scanned = 0
    while current <= horizon_local:
        scanned += 1
        if scanned > 3660:
            raise ValueError("Recurrence expansion exceeds the ten-year safety window")
        if _matches(current, local_anchor.date(), rule):
            resolved = resolve_local_wall_time(current, local_anchor.timetz().replace(tzinfo=None), timezone_name)
            due = resolved.astimezone(UTC)
            if due >= anchor.astimezone(UTC) and due <= horizon and (lower is None or due > lower):
                intended = datetime.combine(current, local_anchor.timetz().replace(tzinfo=None)).isoformat(timespec="seconds")
                results.append(RecurrenceInstant(due.isoformat(), due, intended))
                if len(results) >= limit:
                    break
        current += timedelta(days=1)
    return tuple(results)


def _matches(candidate: date, anchor: date, rule: RecurrenceRule) -> bool:
    if candidate < anchor:
        return False
    if rule.frequency == "daily":
        return (candidate - anchor).days % rule.interval == 0
    if rule.frequency == "weekly":
        anchor_week = anchor - timedelta(days=anchor.weekday())
        candidate_week = candidate - timedelta(days=candidate.weekday())
        return ((candidate_week - anchor_week).days // 7) % rule.interval == 0 and candidate.weekday() in rule.weekdays

    month_delta = (candidate.year - anchor.year) * 12 + candidate.month - anchor.month
    if month_delta % rule.interval != 0:
        return False
    if rule.month_days:
        return candidate.day in rule.month_days
    return candidate.weekday() in rule.weekdays and any(
        candidate.day == _ordinal_weekday(candidate.year, candidate.month, candidate.weekday(), ordinal)
        for ordinal in rule.ordinals
    )


def _ordinal_weekday(year: int, month: int, weekday: int, ordinal: int) -> int:
    days = [
        day for day in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, day).weekday() == weekday
    ]
    return days[-1] if ordinal == -1 else days[ordinal - 1] if ordinal <= len(days) else -1


def _sequence(value: object) -> Iterable[object]:
    return value if isinstance(value, (list, tuple)) else ()
