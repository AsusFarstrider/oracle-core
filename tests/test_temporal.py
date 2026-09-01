from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import Mock

import pytest

from oracle_app.calendar import CalendarEvent
from oracle_app.temporal import (
    build_temporal_response,
    parse_temporal_query,
    resolve_local_wall_time,
    resolve_timezone,
)


FIXED_NOW = datetime(2026, 4, 4, 15, 0, tzinfo=UTC)


def _respond(text: str, **kwargs):
    return build_temporal_response(
        text,
        household_timezone="America/New_York",
        now=FIXED_NOW,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("what time is it", "current_time"),
        ("tell me the time", "current_time"),
        ("time right now", "current_time"),
        ("what is the date", "current_date"),
        ("tell me the date", "current_date"),
        ("todays date", "current_date"),
        ("what day of the week is it", "current_date"),
        ("what time and date is it", "current_time_date"),
        ("what time is it in London", "world_time"),
        ("time difference between here and Arizona", "world_difference"),
        ("if it is 3 pm here what time is it in London", "world_convert"),
        ("how long until noon", "time_until"),
        ("how long until christmas", "date_until"),
        ("how long since 2 am", "time_since"),
        ("what time will it be forty five minutes from now", "time_shift"),
        ("what time will it be in forty five minutes", "time_shift"),
        ("what time was it two hours ago", "time_shift"),
        ("what is the date three weeks from today", "date_shift"),
        ("what date was it two weeks ago", "date_shift"),
        ("what date was thirty days ago", "date_shift"),
        ("how many days until july fourth", "date_until"),
        ("how many days between july fourth and july tenth", "date_between"),
        ("how many days are between july fourth and july tenth", "date_between"),
        ("what day of the week is next friday", "date_weekday"),
        ("when is thanksgiving", "date_when"),
    ],
)
def test_supported_surface_parses_deterministically(text: str, kind: str) -> None:
    query = parse_temporal_query(text)
    assert query is not None
    assert query.kind == kind


@pytest.mark.parametrize("text", ["what about weather", "when is my appointment", "what day is my appointment", "update the date field", "sunset today", "when is sunrise tomorrow"])
def test_temporal_parser_does_not_claim_adjacent_domains(text: str) -> None:
    assert parse_temporal_query(text) is None


def test_household_timezone_not_host_timezone_controls_current_answer() -> None:
    speech, details = _respond("what time is it")
    assert speech == "It is 11:00 AM."
    assert details["timezone"] == "America/New_York"
    assert details["timestamp"] == "2026-04-04T11:00:00-04:00"


def test_world_time_uses_date_sensitive_offsets() -> None:
    speech, details = _respond("what time is it in London")
    assert speech == "In London, it is 4:00 PM."
    assert details["timezone"] == "Europe/London"


def test_explicit_iana_timezone_is_supported_case_insensitively() -> None:
    resolution = resolve_timezone("europe/london")
    assert resolution.status == "resolved"
    assert resolution.timezone == "Europe/London"


def test_ambiguous_place_requests_bounded_clarification() -> None:
    speech, details = _respond("what time is it in Washington")
    assert speech == "Which place did you mean: Washington DC or Washington state?"
    assert details["status"] == "clarification_required"
    assert details["options"] == ["Washington DC", "Washington state"]


def test_unsupported_place_is_rejected_honestly() -> None:
    speech, details = _respond("what time is it in Smallville")
    assert speech == "I don't support smallville as a time location yet."
    assert details["status"] == "unsupported_location"


def test_relative_weekday_semantics_distinguish_this_next_and_last() -> None:
    this_speech, _ = _respond("what day of the week is this friday")
    next_speech, _ = _respond("what day of the week is next friday")
    last_speech, _ = _respond("what day of the week is last tuesday")
    assert "April 10, 2026" in this_speech
    assert "April 17, 2026" in next_speech
    assert "March 31, 2026" in last_speech


def test_contextual_clock_without_meridiem_selects_nearest_direction() -> None:
    until_speech, until_details = _respond("how long until 4:30")
    since_speech, since_details = _respond("how long has it been since 2:00")
    assert until_speech == "It is 5 hours and 30 minutes until 4:30 PM."
    assert until_details["seconds"] == 19_800
    assert since_speech == "It is 9 hours since 2:00 AM."
    assert since_details["seconds"] == 32_400


def test_in_duration_and_weekend_relative_dates() -> None:
    duration_speech, duration_details = _respond("what is the date in three weeks")
    weekend_speech, weekend_details = _respond("what day is this weekend")
    assert duration_details["date"] == "2026-04-25"
    assert "April 25, 2026" in duration_speech
    assert weekend_details["date"] == "2026-04-04"
    assert "Saturday" in weekend_speech


def test_spoken_ordinal_and_leap_day_are_validated() -> None:
    speech, details = build_temporal_response(
        "what day of the week is february twenty ninth 2028",
        household_timezone="America/New_York",
        now=FIXED_NOW,
    )
    assert "Tuesday" in speech
    assert details["date"] == "2028-02-29"
    with pytest.raises(ValueError, match="not a valid calendar date"):
        _respond("what day of the week is february thirtieth")


def test_date_since_selects_prior_unqualified_year() -> None:
    speech, details = _respond("how many days since december thirty first")
    assert details["date"] == "2025-12-31"
    assert "94 days since" in speech


def test_holiday_queries_use_only_configured_holiday_execution() -> None:
    execution = Mock()
    execution.load_events.return_value.value = [
        CalendarEvent(
            uid="holiday-1",
            summary="Thanksgiving Day",
            start=datetime.fromisoformat("2026-11-26T00:00:00-05:00"),
            end=datetime.fromisoformat("2026-11-27T00:00:00-05:00"),
            all_day=True,
            location="",
        )
    ]
    speech, details = _respond("when is thanksgiving", calendar_execution=execution)
    assert speech == "Thanksgiving Day is Thursday, November 26, 2026."
    assert details["date"] == "2026-11-26"
    execution.load_events.assert_called_once_with(scope="holiday", require_config=True)


def test_missing_holiday_is_not_invented() -> None:
    execution = Mock()
    execution.load_events.return_value.value = []
    with pytest.raises(ValueError, match="not present in the configured holiday calendar"):
        _respond("when is christmas", calendar_execution=execution)


def test_spring_gap_uses_first_valid_instant_after_gap() -> None:
    resolved = resolve_local_wall_time(date(2026, 3, 8), time(2, 30), "America/New_York")
    assert resolved.isoformat() == "2026-03-08T03:00:00-04:00"


def test_fall_fold_uses_earlier_occurrence_once() -> None:
    resolved = resolve_local_wall_time(date(2026, 11, 1), time(1, 30), "America/New_York")
    assert resolved.fold == 0
    assert resolved.isoformat() == "2026-11-01T01:30:00-04:00"


def test_time_until_across_spring_gap_uses_elapsed_instant_difference() -> None:
    speech, details = build_temporal_response(
        "how long until 3:30 am",
        household_timezone="America/New_York",
        now=datetime(2026, 3, 8, 6, 30, tzinfo=UTC),
    )
    assert speech == "It is 1 hour until 3:30 AM."
    assert details["seconds"] == 3600


def test_temporal_followup_requires_typed_temporal_context() -> None:
    with pytest.raises(ValueError, match="no current temporal context"):
        _respond("what about Tokyo")
    speech, details = build_temporal_response(
        "what about Tokyo",
        household_timezone="America/New_York",
        now=FIXED_NOW,
        temporal_context={"payload": {"subject_type": "world_time"}},
    )
    assert speech == "In Tokyo, it is 12:00 AM."
    assert details["timezone"] == "Asia/Tokyo"
