from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app import calendar as calendar_module
from oracle_app.calendar import CalendarEvent, CalendarQuery, execute_calendar_query, load_calendar_events


PERSONAL_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:personal-1
DTSTART;VALUE=DATE:20260704
DTEND;VALUE=DATE:20260705
SUMMARY:Family Cookout
END:VEVENT
END:VCALENDAR
"""

HOLIDAY_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:holiday-1
DTSTART;VALUE=DATE:20261225
DTEND;VALUE=DATE:20261226
SUMMARY:Christmas Day
END:VEVENT
END:VCALENDAR
"""


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class CalendarFeedTests(unittest.TestCase):
    @patch("oracle_app.provider_bridges.nextcloud_calendar.request.urlopen")
    @patch(
        "oracle_app.calendar.get_calendar_settings",
        return_value={
            "ics_url": "https://example.invalid/personal.ics",
            "holiday_ics_url": "https://example.invalid/holidays.ics",
            "timezone": "America/New_York",
            "timeout_seconds": 8,
            "calendar_configured": True,
            "holiday_calendar_configured": True,
            "read_user": "ExampleUser",
            "read_app_password": "calendar-pass",
        },
    )
    def test_load_calendar_events_uses_holiday_feed_when_requested(self, _mock_settings, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResponse(HOLIDAY_ICS)

        events = load_calendar_events(scope="holiday")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].summary, "Christmas Day")
        self.assertIn("holidays.ics", mock_urlopen.call_args.args[0].full_url)
        self.assertEqual(
            mock_urlopen.call_args.args[0].headers["User-agent"],
            "oracle-brain-calendar/1.0",
        )
        self.assertNotIn("Authorization", mock_urlopen.call_args.args[0].headers)

    @patch("oracle_app.provider_bridges.nextcloud_calendar.request.urlopen")
    @patch(
        "oracle_app.calendar.get_calendar_settings",
        return_value={
            "ics_url": "https://example.invalid/personal.ics",
            "holiday_ics_url": "https://example.invalid/holidays.ics",
            "timezone": "America/New_York",
            "timeout_seconds": 8,
            "calendar_configured": True,
            "holiday_calendar_configured": True,
            "read_user": "ExampleUser",
            "read_app_password": "calendar-pass",
        },
    )
    def test_execute_calendar_query_stays_on_personal_feed(self, _mock_settings, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResponse(PERSONAL_ICS)

        result = execute_calendar_query(
            CalendarQuery(
                intent="list_events",
                start=load_calendar_events.__globals__["datetime"](2026, 7, 4, 0, 0, tzinfo=load_calendar_events.__globals__["ZoneInfo"]("America/New_York")),
                end=load_calendar_events.__globals__["datetime"](2026, 7, 5, 0, 0, tzinfo=load_calendar_events.__globals__["ZoneInfo"]("America/New_York")),
                search_text=None,
                original_text="what's on my calendar july 4",
            )
        )

        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["summary"], "Family Cookout")
        self.assertIn("personal.ics", mock_urlopen.call_args.args[0].full_url)
        self.assertEqual(
            mock_urlopen.call_args.args[0].headers["Authorization"],
            "Basic RXhhbXBsZVVzZXI6Y2FsZW5kYXItcGFzcw==",
        )

    def test_list_events_for_today_omits_ended_events_by_default(self) -> None:
        timezone = "America/New_York"
        zone = load_calendar_events.__globals__["ZoneInfo"](timezone)
        now = load_calendar_events.__globals__["datetime"](2026, 4, 4, 12, 0, tzinfo=zone)
        query = CalendarQuery(
            intent="list_events",
            start=load_calendar_events.__globals__["datetime"](2026, 4, 4, 0, 0, tzinfo=zone),
            end=load_calendar_events.__globals__["datetime"](2026, 4, 5, 0, 0, tzinfo=zone),
            search_text=None,
            original_text="what's on my calendar today",
        )
        events = [
            CalendarEvent(
                uid="past",
                summary="Breakfast",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 4, 8, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 4, 9, 0, tzinfo=zone),
                all_day=False,
                location="",
            ),
            CalendarEvent(
                uid="current",
                summary="Lunch",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 4, 11, 30, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 4, 12, 30, tzinfo=zone),
                all_day=False,
                location="",
            ),
            CalendarEvent(
                uid="future",
                summary="Dinner",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 4, 18, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 4, 19, 0, tzinfo=zone),
                all_day=False,
                location="",
            ),
        ]

        with patch("oracle_app.calendar._now_in_calendar_timezone", return_value=now):
            result = calendar_module._list_events(query, events, timezone)

        self.assertEqual([event["summary"] for event in result["events"]], ["Lunch", "Dinner"])

    def test_list_events_for_full_today_includes_already_ended_events(self) -> None:
        timezone = "America/New_York"
        zone = load_calendar_events.__globals__["ZoneInfo"](timezone)
        now = load_calendar_events.__globals__["datetime"](2026, 4, 4, 12, 0, tzinfo=zone)
        query = CalendarQuery(
            intent="list_events",
            start=load_calendar_events.__globals__["datetime"](2026, 4, 4, 0, 0, tzinfo=zone),
            end=load_calendar_events.__globals__["datetime"](2026, 4, 5, 0, 0, tzinfo=zone),
            search_text=None,
            original_text="what's on my full calendar today",
        )
        events = [
            CalendarEvent(
                uid="past",
                summary="Breakfast",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 4, 8, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 4, 9, 0, tzinfo=zone),
                all_day=False,
                location="",
            ),
            CalendarEvent(
                uid="future",
                summary="Dinner",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 4, 18, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 4, 19, 0, tzinfo=zone),
                all_day=False,
                location="",
            ),
        ]

        with patch("oracle_app.calendar._now_in_calendar_timezone", return_value=now):
            result = calendar_module._list_events(query, events, timezone)

        self.assertEqual([event["summary"] for event in result["events"]], ["Breakfast", "Dinner"])

    def test_list_events_returns_more_than_eight_events_when_relevant(self) -> None:
        timezone = "America/New_York"
        zone = load_calendar_events.__globals__["ZoneInfo"](timezone)
        query = CalendarQuery(
            intent="list_events",
            start=load_calendar_events.__globals__["datetime"](2026, 4, 6, 0, 0, tzinfo=zone),
            end=load_calendar_events.__globals__["datetime"](2026, 4, 7, 0, 0, tzinfo=zone),
            search_text=None,
            original_text="what's on my calendar monday",
        )
        events = [
            CalendarEvent(
                uid=f"event-{index}",
                summary=f"Event {index}",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 6, 8 + index, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 6, 8 + index, 30, tzinfo=zone),
                all_day=False,
                location="",
            )
            for index in range(9)
        ]

        result = calendar_module._list_events(query, events, timezone)

        self.assertEqual(len(result["events"]), 9)

    def test_find_event_does_not_match_on_single_shared_token_for_multiword_query(self) -> None:
        timezone = "America/New_York"
        zone = load_calendar_events.__globals__["ZoneInfo"](timezone)
        query = CalendarQuery(
            intent="find_event",
            start=None,
            end=None,
            search_text="oracle soak calendar test",
            original_text="when is oracle soak calendar test",
        )
        events = [
            CalendarEvent(
                uid="other",
                summary="Example party and test",
                start=load_calendar_events.__globals__["datetime"](2026, 3, 27, 10, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 3, 27, 11, 0, tzinfo=zone),
                all_day=False,
                location="",
            )
        ]

        result = calendar_module._find_matching_event(query, events, timezone)

        self.assertTrue(result["not_found"])
        self.assertEqual(result["events"], [])

    def test_find_event_prefers_next_upcoming_occurrence(self) -> None:
        timezone = "America/New_York"
        zone = load_calendar_events.__globals__["ZoneInfo"](timezone)
        query = CalendarQuery(
            intent="find_event",
            start=None,
            end=None,
            search_text="staff meeting",
            original_text="when is staff meeting",
        )
        events = [
            CalendarEvent(
                uid="past",
                summary="Staff Meeting",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 3, 9, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 3, 10, 0, tzinfo=zone),
                all_day=False,
                location="",
            ),
            CalendarEvent(
                uid="next",
                summary="Staff Meeting",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 5, 9, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 5, 10, 0, tzinfo=zone),
                all_day=False,
                location="",
            ),
            CalendarEvent(
                uid="later",
                summary="Staff Meeting",
                start=load_calendar_events.__globals__["datetime"](2026, 4, 12, 9, 0, tzinfo=zone),
                end=load_calendar_events.__globals__["datetime"](2026, 4, 12, 10, 0, tzinfo=zone),
                all_day=False,
                location="",
            ),
        ]

        with patch(
            "oracle_app.calendar._now_in_calendar_timezone",
            return_value=load_calendar_events.__globals__["datetime"](2026, 4, 4, 12, 0, tzinfo=zone),
        ):
            result = calendar_module._find_matching_event(query, events, timezone)

        self.assertEqual(result["events"][0]["uid"], "next")
