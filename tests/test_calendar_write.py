from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.calendar_write import (
    build_confirmation_prompt,
    build_or_continue_event_draft,
    commit_calendar_event,
    parse_calendar_write_request,
)


class CalendarWriteTests(unittest.TestCase):
    def test_parse_calendar_write_request_extracts_core_fields(self) -> None:
        parsed = parse_calendar_write_request(
            "add dentist appointment to my calendar tomorrow at 2pm for 1 hour"
        )

        assert parsed is not None
        self.assertEqual(parsed.title, "dentist appointment")
        self.assertEqual(parsed.start_time, "14:00")
        self.assertEqual(parsed.end_time, "15:00")
        self.assertEqual(parsed.duration_minutes, 60)

    def test_build_or_continue_event_draft_requests_missing_date_first(self) -> None:
        stage, payload = build_or_continue_event_draft("add dentist appointment to my calendar")

        self.assertEqual(stage, "clarification")
        self.assertEqual(payload["missing_field"], "date")
        self.assertEqual(payload["prompt"], "What day is that for?")

    def test_build_or_continue_event_draft_completes_after_clarification(self) -> None:
        stage, payload = build_or_continue_event_draft("add dentist appointment to my calendar")
        self.assertEqual(stage, "clarification")

        stage, payload = build_or_continue_event_draft(
            "tomorrow",
            pending=payload,
        )
        self.assertEqual(stage, "clarification")
        self.assertEqual(payload["missing_field"], "start_time")

        stage, payload = build_or_continue_event_draft(
            "2pm",
            pending=payload,
        )
        self.assertEqual(stage, "clarification")
        self.assertEqual(payload["missing_field"], "end_time")

        stage, payload = build_or_continue_event_draft(
            "for 1 hour",
            pending=payload,
        )
        self.assertEqual(stage, "confirmation")
        self.assertEqual(payload["event_draft"]["title"], "dentist appointment")
        self.assertEqual(payload["event_draft"]["start_time"], "14:00")
        self.assertEqual(payload["event_draft"]["end_time"], "15:00")

    def test_build_or_continue_event_draft_steers_back_when_field_not_answered(self) -> None:
        stage, payload = build_or_continue_event_draft("add dentist appointment to my calendar tomorrow")
        self.assertEqual(stage, "clarification")
        self.assertEqual(payload["missing_field"], "start_time")

        stage, payload = build_or_continue_event_draft(
            "also i need to call jeff",
            pending=payload,
        )
        self.assertEqual(stage, "clarification")
        self.assertEqual(
            payload["prompt"],
            "Let's finish the event first. What time should it start?",
        )

    def test_build_confirmation_prompt_uses_canonical_shape(self) -> None:
        prompt = build_confirmation_prompt(
            {
                "title": "Dentist appointment",
                "date": "2026-04-07",
                "start_time": "14:00",
                "end_time": "15:00",
            }
        )

        self.assertEqual(
            prompt,
            "I've got 'Dentist appointment' on Tuesday, April 7, 2026 from 2:00 PM to 3:00 PM. Do you want me to add it?",
        )

    @patch("oracle_app.calendar.invalidate_calendar_cache")
    @patch("oracle_app.provider_bridges.nextcloud_calendar.request.urlopen")
    def test_commit_calendar_event_puts_ics_to_configured_calendar(
        self,
        mock_urlopen,
        mock_invalidate_calendar_cache,
    ) -> None:
        mock_response = mock_urlopen.return_value.__enter__.return_value
        mock_response.headers = {"ETag": '"etag-1"'}

        result = commit_calendar_event(
            {
                "title": "Dentist appointment",
                "date": "2026-04-07",
                "start_time": "14:00",
                "end_time": "15:00",
            },
            settings={
                "calendar_write_configured": True,
                "write_base_url": "https://calendar.example",
                "write_user": "ExampleUser",
                "write_app_password": "secret",
                "write_calendar_uri": "Joint",
                "timezone": "America/New_York",
                "timeout_seconds": 8,
            },
        )

        self.assertEqual(result["calendar_uri"], "Joint")
        req = mock_urlopen.call_args.args[0]
        self.assertIn("/remote.php/dav/calendars/ExampleUser/Joint/", req.full_url)
        self.assertEqual(req.get_method(), "PUT")
        payload = req.data.decode("utf-8")
        self.assertIn("SUMMARY:Dentist appointment", payload)
        self.assertIn("BEGIN:VEVENT", payload)
        mock_invalidate_calendar_cache.assert_called_once_with()
