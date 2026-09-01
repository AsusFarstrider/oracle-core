from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from oracle_app.alert_recurrence import RecurrenceRule, expand_recurrence


def test_daily_recurrence_uses_temporal_owner_for_dst_gap_and_returns_to_wall_time() -> None:
    instants = expand_recurrence(
        anchor=datetime.fromisoformat("2026-03-07T02:30:00-05:00"),
        timezone_name="America/New_York",
        rule=RecurrenceRule("daily"),
        through=datetime(2026, 3, 9, 12, tzinfo=UTC),
    )
    local = [item.due_at.astimezone(ZoneInfo("America/New_York")) for item in instants]
    assert [(item.day, item.hour, item.minute) for item in local] == [
        (7, 2, 30),
        (8, 3, 0),
        (9, 2, 30),
    ]


def test_daily_recurrence_uses_earlier_fold_once() -> None:
    instants = expand_recurrence(
        anchor=datetime.fromisoformat("2026-10-31T01:30:00-04:00"),
        timezone_name="America/New_York",
        rule=RecurrenceRule("daily"),
        through=datetime(2026, 11, 2, 12, tzinfo=UTC),
    )
    fold = instants[1].due_at
    assert fold == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert len([item for item in instants if item.intended_local.startswith("2026-11-01")]) == 1


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (RecurrenceRule("weekly", interval=2, weekdays=(0, 4)), ["2026-01-02", "2026-01-12", "2026-01-16", "2026-01-26", "2026-01-30", "2026-02-09", "2026-02-13", "2026-02-23", "2026-02-27"]),
        (RecurrenceRule("monthly", weekdays=(0,), ordinals=(1, 3)), ["2026-01-05", "2026-01-19", "2026-02-02", "2026-02-16"]),
        (RecurrenceRule("monthly", weekdays=(4,), ordinals=(-1,)), ["2026-01-30", "2026-02-27"]),
        (RecurrenceRule("monthly", month_days=(15,)), ["2026-01-15", "2026-02-15"]),
    ],
)
def test_governed_weekly_and_monthly_recurrence(rule: RecurrenceRule, expected: list[str]) -> None:
    instants = expand_recurrence(
        anchor=datetime(2026, 1, 1, 12, tzinfo=UTC),
        timezone_name="UTC",
        rule=rule,
        through=datetime(2026, 2, 28, 23, tzinfo=UTC),
    )
    assert [item.intended_local[:10] for item in instants] == expected


def test_recurrence_validation_and_expansion_are_bounded() -> None:
    with pytest.raises(ValueError, match="requires weekdays"):
        RecurrenceRule("weekly")
    with pytest.raises(ValueError, match="month days or ordinal"):
        RecurrenceRule("monthly", weekdays=(0,))
    instants = expand_recurrence(
        anchor=datetime(2026, 1, 1, tzinfo=UTC),
        timezone_name="UTC",
        rule=RecurrenceRule("daily"),
        through=datetime(2026, 12, 31, tzinfo=UTC),
        limit=10,
    )
    assert len(instants) == 10
