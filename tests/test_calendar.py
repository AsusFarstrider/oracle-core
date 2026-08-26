from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from oracle_app import calendar as calendar_module
from oracle_app.calendar import CalendarEvent, CalendarQuery, parse_calendar_query
from oracle_app.calendar_runtime import CanonicalCalendarExecution


def test_parse_calendar_query_uses_explicit_timezone() -> None:
    query = parse_calendar_query(
        "what is on my calendar tomorrow",
        timezone_name="America/New_York",
    )
    assert query is not None
    assert query.intent == "list_events"
    assert query.start is not None
    assert str(query.start.tzinfo) == "America/New_York"


def test_list_events_for_today_omits_ended_events_by_default(monkeypatch) -> None:
    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 4, 4, 12, 0, tzinfo=zone)
    query = CalendarQuery(
        intent="list_events",
        start=datetime(2026, 4, 4, 0, 0, tzinfo=zone),
        end=datetime(2026, 4, 5, 0, 0, tzinfo=zone),
        search_text=None,
        original_text="what's on my calendar today",
    )
    events = [
        CalendarEvent(uid="past", summary="Breakfast", start=datetime(2026, 4, 4, 8, 0, tzinfo=zone), end=datetime(2026, 4, 4, 9, 0, tzinfo=zone), all_day=False, location=""),
        CalendarEvent(uid="future", summary="Dinner", start=datetime(2026, 4, 4, 18, 0, tzinfo=zone), end=datetime(2026, 4, 4, 19, 0, tzinfo=zone), all_day=False, location=""),
    ]
    monkeypatch.setattr(calendar_module, "_now_in_calendar_timezone", lambda _timezone: now)
    result = calendar_module._list_events(query, events, "America/New_York")
    assert [event["summary"] for event in result["events"]] == ["Dinner"]


def test_find_event_prefers_next_upcoming_occurrence(monkeypatch) -> None:
    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 4, 4, 12, 0, tzinfo=zone)
    query = CalendarQuery("find_event", None, None, "staff meeting", "when is staff meeting")
    events = [
        CalendarEvent(uid="past", summary="Staff Meeting", start=datetime(2026, 4, 3, 9, 0, tzinfo=zone), end=datetime(2026, 4, 3, 10, 0, tzinfo=zone), all_day=False, location=""),
        CalendarEvent(uid="next", summary="Staff Meeting", start=datetime(2026, 4, 5, 9, 0, tzinfo=zone), end=datetime(2026, 4, 5, 10, 0, tzinfo=zone), all_day=False, location=""),
    ]
    monkeypatch.setattr(calendar_module, "_now_in_calendar_timezone", lambda _timezone: now)
    result = calendar_module._find_matching_event(query, events, "America/New_York")
    assert result["events"][0]["uid"] == "next"


def test_canonical_calendar_write_invalidates_owned_event_cache() -> None:
    execution = CanonicalCalendarExecution.__new__(CanonicalCalendarExecution)
    execution.settings = SimpleNamespace()
    execution.bridge = SimpleNamespace(
        commit_typed_event=Mock(return_value={"event_draft": {"title": "Dentist"}})
    )
    execution._cache = SimpleNamespace(invalidate=Mock())

    result = execution.commit_event({"title": "Dentist"})

    assert result == {"event_draft": {"title": "Dentist"}}
    execution.bridge.commit_typed_event.assert_called_once_with(
        {"title": "Dentist"}, settings=execution.settings
    )
    execution._cache.invalidate.assert_called_once_with("calendar:events:")
