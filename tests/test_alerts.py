from __future__ import annotations

import tempfile
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import oracle_app.alerts as alerts_module
from alert_store_test_support import IsolatedAlertStoreTestCase
from oracle_app.alerts import (
    build_alert_response,
    clear_alerts,
    consume_due_alerts,
    create_alert,
    list_alerts,
    parse_clock_time,
    parse_duration,
)


class AlertsTests(IsolatedAlertStoreTestCase):
    def tearDown(self) -> None:
        clear_alerts()
        super().tearDown()

    def test_alert_store_is_outside_deployment_data(self) -> None:
        deployment_data = Path(__file__).resolve().parents[1] / "data"

        self.assertNotEqual(self.alert_state_path.parent, deployment_data)
        self.assertNotIn(deployment_data, self.alert_state_path.parents)

    def test_parse_duration(self) -> None:
        self.assertEqual(parse_duration("for 1 hour 30 minutes"), 5400)

    def test_parse_duration_accepts_spoken_numbers(self) -> None:
        self.assertEqual(parse_duration("for one minute"), 60)
        self.assertEqual(parse_duration("for ninety seconds"), 90)

    def test_parse_clock_time(self) -> None:
        current = parse_clock_time("at 7 am")
        self.assertIsNotNone(current)

    def test_parse_clock_time_with_spoken_minutes_and_meridiem(self) -> None:
        current = parse_clock_time("set an alarm for 6 30 pm")
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.hour, 18)
        self.assertEqual(current.minute, 30)

    def test_parse_clock_time_accepts_dotted_meridiem_for_near_future_alarm(self) -> None:
        now = datetime(2026, 5, 19, 20, 25).astimezone()

        current = parse_clock_time("set an alarm for 8 30 p.m.", now=now)

        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.date(), now.date())
        self.assertEqual(current.hour, 20)
        self.assertEqual(current.minute, 30)

    def test_parse_clock_time_accepts_dotted_am_for_near_future_alarm(self) -> None:
        now = datetime(2026, 5, 19, 8, 25).astimezone()

        current = parse_clock_time("set an alarm for 8 30 a.m.", now=now)

        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.date(), now.date())
        self.assertEqual(current.hour, 8)
        self.assertEqual(current.minute, 30)

    def test_parse_clock_time_accepts_compact_time_with_meridiem(self) -> None:
        now = datetime(2026, 5, 19, 20, 25).astimezone()

        current = parse_clock_time("set an alarm for 830 p.m.", now=now)

        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.date(), now.date())
        self.assertEqual(current.hour, 20)
        self.assertEqual(current.minute, 30)

    def test_create_timer_response(self) -> None:
        speech, details = build_alert_response("set a timer for 5 minutes", "test-source", "session-1")
        self.assertEqual(speech, "Timer set for 5 minutes.")
        self.assertEqual(details["kind"], "timer")

    def test_create_timer_response_accepts_spoken_duration(self) -> None:
        speech, details = build_alert_response("set a timer for one minute", "test-source", "session-1")
        self.assertEqual(speech, "Timer set for 1 minute.")
        self.assertEqual(details["kind"], "timer")

    def test_create_reminder_response(self) -> None:
        speech, details = build_alert_response(
            "remind me to check the oven in 10 minutes",
            "test-source",
            "session-1",
        )
        self.assertIn("Reminder set for", speech)
        self.assertEqual(details["kind"], "reminder")

    def test_timer_cancel_accepts_cancel_my_timer(self) -> None:
        create_alert(
            kind="timer",
            due_at=datetime.now().astimezone() + timedelta(minutes=5),
            message="Timer finished.",
            source="test-source",
            session_id="session-1",
        )

        speech, details = build_alert_response("cancel my timer", "test-source", "session-1")

        self.assertEqual(speech, "Canceled 1 timer.")
        self.assertEqual(details["operation"], "cancel")
        self.assertEqual(details["count"], 1)

    def test_timer_cancel_accepts_cancel_all_timers(self) -> None:
        create_alert(
            kind="timer",
            due_at=datetime.now().astimezone() + timedelta(minutes=5),
            message="Timer one finished.",
            source="test-source",
            session_id="session-1",
        )
        create_alert(
            kind="timer",
            due_at=datetime.now().astimezone() + timedelta(minutes=10),
            message="Timer two finished.",
            source="test-source",
            session_id="session-1",
        )

        speech, details = build_alert_response("cancel all timers", "test-source", "session-1")

        self.assertEqual(speech, "Canceled 2 timers.")
        self.assertEqual(details["operation"], "cancel")
        self.assertEqual(details["count"], 2)

    def test_alarm_cancel_accepts_stop_the_alarm(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=1)
        create_alert(
            kind="alarm",
            due_at=due_at,
            message=f"Alarm for {due_at.strftime('%-I:%M %p')}.",
            source="test-source",
            session_id="session-1",
        )

        speech, details = build_alert_response("stop the alarm", "test-source", "session-1")

        self.assertEqual(speech, "Canceled 1 alarm.")
        self.assertEqual(details["operation"], "cancel")
        self.assertEqual(details["count"], 1)

    def test_reminder_cancel_accepts_clear_my_reminders(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=2)
        create_alert(
            kind="reminder",
            due_at=due_at,
            message="Reminder: check the oven.",
            source="test-source",
            session_id="session-1",
        )

        speech, details = build_alert_response("clear my reminders", "test-source", "session-1")

        self.assertEqual(speech, "Canceled 1 reminder.")
        self.assertEqual(details["operation"], "cancel")
        self.assertEqual(details["count"], 1)

    def test_timer_cancel_reports_no_match_cleanly(self) -> None:
        speech, details = build_alert_response("cancel my timer", "test-source", "session-1")

        self.assertEqual(speech, "You have no active timers to cancel.")
        self.assertEqual(details["operation"], "cancel")

    def test_alarm_cancel_reports_no_match_cleanly(self) -> None:
        speech, details = build_alert_response("stop the alarm", "test-source", "session-1")

        self.assertEqual(speech, "You have no active alarms to cancel.")
        self.assertEqual(details["operation"], "cancel")

    def test_reminder_cancel_reports_no_match_cleanly(self) -> None:
        speech, details = build_alert_response("clear my reminders", "test-source", "session-1")

        self.assertEqual(speech, "You have no active reminders to cancel.")
        self.assertEqual(details["operation"], "cancel")

    def test_consume_due_alerts(self) -> None:
        with patch("oracle_app.alerts._now_local") as mock_now:
            base = datetime.now().astimezone()
            mock_now.return_value = base
            create_alert(
                kind="timer",
                due_at=base - timedelta(seconds=1),
                message="Timer finished.",
                source="test-source",
                session_id="session-1",
            )
            alerts = consume_due_alerts("test-source")
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0]["message"], "Timer finished.")

    def test_create_alert_copies_metadata(self) -> None:
        metadata = {"duration_seconds": 300}
        create_alert(
            kind="timer",
            due_at=datetime.now().astimezone() + timedelta(minutes=5),
            message="Timer finished.",
            source="test-source",
            session_id="session-1",
            metadata=metadata,
        )
        metadata["duration_seconds"] = 60

        alerts = list_alerts("test-source", "timer")

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].metadata["duration_seconds"], 300)

    def test_list_alerts_returns_snapshot_copy(self) -> None:
        create_alert(
            kind="timer",
            due_at=datetime.now().astimezone() + timedelta(minutes=5),
            message="Timer finished.",
            source="test-source",
            session_id="session-1",
            metadata={"duration_seconds": 300},
        )

        alerts = list_alerts("test-source", "timer")
        alerts[0].metadata["duration_seconds"] = 60

        fresh_alerts = list_alerts("test-source", "timer")

        self.assertEqual(len(fresh_alerts), 1)
        self.assertEqual(fresh_alerts[0].metadata["duration_seconds"], 300)

    def test_consume_due_alerts_is_scoped_to_source(self) -> None:
        base = datetime.now().astimezone()
        create_alert(
            kind="timer",
            due_at=base - timedelta(seconds=1),
            message="Timer A finished.",
            source="source-a",
            session_id="session-a",
        )
        create_alert(
            kind="timer",
            due_at=base - timedelta(seconds=1),
            message="Timer B finished.",
            source="source-b",
            session_id="session-b",
        )

        alerts = consume_due_alerts("source-a")

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["source"], "source-a")
        self.assertEqual(alerts[0]["message"], "Timer A finished.")
        remaining = consume_due_alerts("source-b")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["source"], "source-b")

    def test_alerts_persist_across_disk_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alerts-state.json"
            with patch.object(alerts_module, "ALERTS_STATE_PATH", state_path):
                clear_alerts()
                create_alert(
                    kind="timer",
                    due_at=datetime.now().astimezone() + timedelta(minutes=5),
                    message="Timer finished.",
                    source="test-source",
                    session_id="session-1",
                    metadata={"duration_seconds": 300},
                )

                alerts_module._ALERTS.clear()
                alerts_module._load_alerts_from_disk()

                alerts = list_alerts("test-source", "timer")
                self.assertEqual(len(alerts), 1)
                self.assertEqual(alerts[0].message, "Timer finished.")
                self.assertEqual(alerts[0].metadata["duration_seconds"], 300)

    def test_alarm_status_reports_current_alarm(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=1)
        create_alert(
            kind="alarm",
            due_at=due_at,
            message=f"Alarm for {due_at.strftime('%-I:%M %p')}.",
            source="test-source",
            session_id="session-1",
        )

        speech, details = build_alert_response("what alarms do i have", "test-source", "session-1")

        self.assertIn("Your next alarm is set for", speech)
        self.assertEqual(details["kind"], "alarm")
        self.assertEqual(details["operation"], "status")
        self.assertEqual(details["count"], 1)

    def test_alarm_status_accepts_when_is_my_alarm_wording(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=1)
        create_alert(
            kind="alarm",
            due_at=due_at,
            message=f"Alarm for {due_at.strftime('%-I:%M %p')}.",
            source="test-source",
            session_id="session-1",
        )

        speech, details = build_alert_response("when is my alarm", "test-source", "session-1")

        self.assertIn("Your next alarm is set for", speech)
        self.assertEqual(details["operation"], "status")

    def test_alarm_status_accepts_how_many_wording(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=1)
        create_alert(
            kind="alarm",
            due_at=due_at,
            message=f"Alarm for {due_at.strftime('%-I:%M %p')}.",
            source="test-source",
            session_id="session-1",
        )
        speech, details = build_alert_response("how many alarms do i have", "test-source", "session-1")

        self.assertEqual(speech, "You have 1 active alarm.")
        self.assertEqual(details["operation"], "status")

    def test_alarm_status_accepts_next_wording(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=1)
        create_alert(
            kind="alarm",
            due_at=due_at,
            message=f"Alarm for {due_at.strftime('%-I:%M %p')}.",
            source="test-source",
            session_id="session-1",
        )
        speech, details = build_alert_response("what's my next alarm", "test-source", "session-1")

        self.assertIn("Your next alarm is set for", speech)
        self.assertEqual(details["operation"], "status")

    def test_reminder_status_reports_current_reminder(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=2)
        create_alert(
            kind="reminder",
            due_at=due_at,
            message="Reminder: check the oven.",
            source="test-source",
            session_id="session-1",
            metadata={"reminder_text": "check the oven"},
        )

        speech, details = build_alert_response("what reminders do i have", "test-source", "session-1")

        self.assertIn("Your next reminder is at", speech)
        self.assertEqual(details["kind"], "reminder")
        self.assertEqual(details["operation"], "status")
        self.assertEqual(details["count"], 1)

    def test_reminder_status_accepts_how_many_wording(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=2)
        create_alert(
            kind="reminder",
            due_at=due_at,
            message="Reminder: check the oven.",
            source="test-source",
            session_id="session-1",
            metadata={"reminder_text": "check the oven"},
        )

        speech, details = build_alert_response("how many reminders do i have", "test-source", "session-1")

        self.assertEqual(speech, "You have 1 active reminder.")
        self.assertEqual(details["operation"], "status")

    def test_reminder_status_accepts_next_wording(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=2)
        create_alert(
            kind="reminder",
            due_at=due_at,
            message="Reminder: check the oven.",
            source="test-source",
            session_id="session-1",
            metadata={"reminder_text": "check the oven"},
        )

        speech, details = build_alert_response("what is my next reminder", "test-source", "session-1")

        self.assertIn("Your next reminder is at", speech)
        self.assertEqual(details["operation"], "status")

    def test_timer_status_accepts_do_i_have_any_timers_wording(self) -> None:
        create_alert(
            kind="timer",
            due_at=datetime.now().astimezone() + timedelta(minutes=5),
            message="Timer finished.",
            source="test-source",
            session_id="session-1",
            metadata={"duration_seconds": 300},
        )

        speech, details = build_alert_response("do i have any timers", "test-source", "session-1")

        self.assertIn("timer", speech.lower())
        self.assertEqual(details["kind"], "timer")
        self.assertEqual(details["operation"], "status")

    def test_timer_status_accepts_how_many_wording(self) -> None:
        create_alert(
            kind="timer",
            due_at=datetime.now().astimezone() + timedelta(minutes=5),
            message="Timer finished.",
            source="test-source",
            session_id="session-1",
            metadata={"duration_seconds": 300},
        )

        speech, details = build_alert_response("how many timers do i have", "test-source", "session-1")

        self.assertEqual(speech, "You have 1 active timer.")
        self.assertEqual(details["operation"], "status")

    def test_timer_status_accepts_next_wording(self) -> None:
        create_alert(
            kind="timer",
            due_at=datetime.now().astimezone() + timedelta(minutes=5),
            message="Timer finished.",
            source="test-source",
            session_id="session-1",
            metadata={"duration_seconds": 300},
        )

        speech, details = build_alert_response("what's my next timer", "test-source", "session-1")

        self.assertIn("Your next timer ends in", speech)
        self.assertEqual(details["operation"], "status")

    def test_reminder_status_accepts_do_i_have_any_reminders_wording(self) -> None:
        due_at = datetime.now().astimezone() + timedelta(hours=2)
        create_alert(
            kind="reminder",
            due_at=due_at,
            message="Reminder: check the oven.",
            source="test-source",
            session_id="session-1",
            metadata={"reminder_text": "check the oven"},
        )

        speech, details = build_alert_response("do i have any reminders", "test-source", "session-1")

        self.assertIn("reminder", speech.lower())
        self.assertEqual(details["kind"], "reminder")
        self.assertEqual(details["operation"], "status")


if __name__ == "__main__":
    unittest.main()
