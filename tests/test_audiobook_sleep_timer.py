from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from alert_store_test_support import IsolatedAlertStoreTestCase
from oracle_app import state
from oracle_app.alerts import clear_alerts, list_alerts
from oracle_app.audiobook import parse_audiobook_intent, parse_bare_audiobook_sleep_timer_intent
from oracle_app.handlers.audiobook import execute_audiobook as _execute_audiobook
from oracle_app.replies import build_reply_text
from oracle_app.routing import build_route_capability_registry, choose_route as _choose_route
from oracle_app.schemas import DispatchPlan
from oracle_app.session_state import clear_all_sessions
from canonical_test_support import neutral_brain_runtime_settings


_NEUTRAL_RUNTIME = neutral_brain_runtime_settings()
_NEUTRAL_ROUTE_REGISTRY = build_route_capability_registry(
    _NEUTRAL_RUNTIME.household,
    facts_enabled=False,
    news_settings=_NEUTRAL_RUNTIME.information.news if _NEUTRAL_RUNTIME.information else None,
    canonical_information=True,
    calendar_settings=_NEUTRAL_RUNTIME.calendar,
    canonical_calendar=True,
)


def choose_route(text: str, *, source: str | None = None):
    return _choose_route(
        text,
        source=source,
        registry=_NEUTRAL_ROUTE_REGISTRY,
        household_settings=_NEUTRAL_RUNTIME.household,
    )


def execute_audiobook(dispatch: DispatchPlan) -> DispatchPlan:
    source = str(dispatch.payload.get("source") or "").strip()
    if source and not dispatch.payload.get("playback_target_source_id"):
        dispatch.payload["playback_target_source_id"] = source
        dispatch.payload["playback_target_resolution"] = "authenticated_request_source"
    return _execute_audiobook(
        dispatch,
        household_settings=_NEUTRAL_RUNTIME.household,
        canonical_playback_target=True,
    )


class AudiobookSleepTimerTests(IsolatedAlertStoreTestCase):
    def tearDown(self) -> None:
        clear_alerts()
        state.clear_all_active_audiobook_playbacks()
        clear_all_sessions()
        super().tearDown()

    def test_parse_play_with_sleep_timer(self) -> None:
        intent = parse_audiobook_intent("play audiobook harry potter and set a sleep timer for 15 minutes")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "harry potter")
        self.assertEqual(intent.sleep_timer_seconds, 900)

    def test_parse_start_audiobook(self) -> None:
        intent = parse_audiobook_intent("start audiobook dune")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "dune")

    def test_parse_queue_up_audiobook(self) -> None:
        intent = parse_audiobook_intent("queue up audiobook harry potter")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "harry potter")

    def test_parse_standalone_sleep_timer(self) -> None:
        intent = parse_bare_audiobook_sleep_timer_intent("set a sleep timer for 20 minutes")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.intent, "sleep_timer")
        self.assertEqual(intent.sleep_timer_seconds, 1200)

    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_context_session")
    def test_active_sleep_timer_routes_to_audiobook(self, mock_audiobook_session) -> None:
        mock_audiobook_session.return_value = {"session_id": "book-1", "backend_type": "oracle_audiobook", "media_kind": "audiobook", "state": "playing"}

        route = choose_route("set a sleep timer for 15 minutes", source="test_satellite_bravo")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched active audiobook sleep timer request")

    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_context_session")
    def test_sleep_timer_routes_to_audiobook_when_current_book_is_paused_for_handoff(self, mock_audiobook_session) -> None:
        mock_audiobook_session.return_value = {
            "session_id": "book-1",
            "backend_type": "oracle_audiobook",
            "media_kind": "audiobook",
            "state": "paused",
            "resumable": True,
        }

        route = choose_route("set a sleep timer for 15 minutes", source="test_satellite_bravo")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched active audiobook sleep timer request")

    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_context_session")
    def test_sleep_timer_routes_to_audiobook_with_noisy_prefix_when_current_book_is_paused(self, mock_audiobook_session) -> None:
        mock_audiobook_session.return_value = {
            "session_id": "book-1",
            "backend_type": "oracle_audiobook",
            "media_kind": "audiobook",
            "state": "paused",
            "resumable": True,
        }

        route = choose_route("i'm going to hold the set of sleep timer for one minute", source="test_satellite_bravo")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched active audiobook sleep timer request")

    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_context_session")
    def test_sleep_timer_without_active_audiobook_falls_back_to_system_timer(self, mock_audiobook_session) -> None:
        mock_audiobook_session.return_value = None

        route = choose_route("set a sleep timer for 15 minutes", source="test_satellite_bravo")

        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched timer/alarm/reminder query")

    def test_sleep_timer_status_routes_to_audiobook_without_active_playback(self) -> None:
        route = choose_route("what sleep timer is set", source="test_satellite_bravo")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched audiobook sleep timer request")

    def test_sleep_timer_status_routes_to_audiobook_for_do_i_have_wording(self) -> None:
        route = choose_route("do i have a sleep timer", source="test_satellite_bravo")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched audiobook sleep timer request")

    def test_sleep_timer_cancel_routes_to_audiobook_without_active_playback(self) -> None:
        route = choose_route("cancel sleep timer", source="test_satellite_bravo")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched audiobook sleep timer request")

    def test_set_sleep_timer_requires_active_audiobook(self) -> None:
        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "set a sleep timer for 15 minutes",
                "normalized_text": "set a sleep timer for 15 minutes",
                "source": "test_satellite_bravo",
                "session_id": "sleep-1",
            },
            status="planned",
        )

        result = execute_audiobook(dispatch)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["error"], "no_active_audiobook")

    def test_set_sleep_timer_creates_source_scoped_alert(self) -> None:
        state.register_active_audiobook_playback(
            "playback-1",
            {
                "playback_id": "playback-1",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-1",
                "duration_seconds": 100.0,
            },
        )
        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "set a sleep timer for 15 minutes",
                "normalized_text": "set a sleep timer for 15 minutes",
                "source": "test_satellite_bravo",
                "session_id": "sleep-2",
            },
            status="planned",
        )

        result = execute_audiobook(dispatch)

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["action"], "sleep_timer")
        self.assertEqual(result.result["operation"], "create")
        timers = list_alerts("test_satellite_bravo", "sleep_timer")
        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0].metadata.get("target"), "audiobook")
        self.assertTrue(timers[0].metadata.get("silent"))

    @patch("oracle_app.handlers.audiobook.close_audiobook_session")
    @patch("oracle_app.handlers.audiobook.execute_satellite_command")
    def test_stop_audiobook_cancels_active_sleep_timer(
        self,
        mock_execute_satellite_command,
        mock_close_audiobook_session,
    ) -> None:
        mock_close_audiobook_session.return_value = None
        state.register_active_audiobook_playback(
            "playback-2",
            {
                "playback_id": "playback-2",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-2",
                "duration_seconds": 100.0,
            },
        )
        create_dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "set a sleep timer for 15 minutes",
                "normalized_text": "set a sleep timer for 15 minutes",
                "source": "test_satellite_bravo",
                "session_id": "sleep-3",
            },
            status="planned",
        )
        execute_audiobook(create_dispatch)
        mock_execute_satellite_command.side_effect = [
            {"ok": True, "state": "playing", "position_seconds": 42.0},
            {"ok": True, "state": "stopped"},
        ]
        stop_dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "stop audiobook",
                "normalized_text": "stop audiobook",
                "source": "test_satellite_bravo",
                "session_id": "sleep-3",
            },
            status="planned",
        )

        result = execute_audiobook(stop_dispatch)

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["action"], "stop")
        self.assertEqual(list_alerts("test_satellite_bravo", "sleep_timer"), [])

    def test_reply_text_mentions_sleep_timer_after_play(self) -> None:
        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={"text": "play audiobook dune and set a sleep timer for 15 minutes"},
            status="executed",
            result={
                "action": "play",
                "selected": {
                    "title": "Dune",
                    "author": "Frank Herbert",
                    "start_position_seconds": 0.0,
                },
                "sleep_timer": {
                    "operation": "create",
                    "duration_speech": "15 minutes",
                },
            },
        )

        self.assertEqual(
            build_reply_text(dispatch),
            "Playing Dune by Frank Herbert. Sleep timer set for 15 minutes.",
        )

    def test_reply_text_for_sleep_timer_status(self) -> None:
        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={"text": "what is my sleep timer"},
            status="executed",
            result={
                "action": "sleep_timer",
                "operation": "status",
                "count": 0,
            },
        )

        self.assertEqual(build_reply_text(dispatch), "There is no active audiobook sleep timer.")

    def test_sleep_timer_status_reports_active_timer(self) -> None:
        state.register_active_audiobook_playback(
            "playback-3",
            {
                "playback_id": "playback-3",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-3",
                "duration_seconds": 100.0,
            },
        )
        create_dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "set a sleep timer for 15 minutes",
                "normalized_text": "set a sleep timer for 15 minutes",
                "source": "test_satellite_bravo",
                "session_id": "sleep-4",
            },
            status="planned",
        )
        execute_audiobook(create_dispatch)
        status_dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "what sleep timer is set",
                "normalized_text": "what sleep timer is set",
                "source": "test_satellite_bravo",
                "session_id": "sleep-4",
            },
            status="planned",
        )

        result = execute_audiobook(status_dispatch)

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["action"], "sleep_timer")
        self.assertEqual(result.result["operation"], "status")
        self.assertEqual(result.result["count"], 1)

    def test_sleep_timer_status_accepts_when_does_it_go_off_wording(self) -> None:
        state.register_active_audiobook_playback(
            "playback-4",
            {
                "playback_id": "playback-4",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-4",
                "duration_seconds": 100.0,
            },
        )
        create_dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "set a sleep timer for 15 minutes",
                "normalized_text": "set a sleep timer for 15 minutes",
                "source": "test_satellite_bravo",
                "session_id": "sleep-5",
            },
            status="planned",
        )
        execute_audiobook(create_dispatch)
        status_dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "when does my sleep timer go off",
                "normalized_text": "when does my sleep timer go off",
                "source": "test_satellite_bravo",
                "session_id": "sleep-5",
            },
            status="planned",
        )

        result = execute_audiobook(status_dispatch)

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["action"], "sleep_timer")
        self.assertEqual(result.result["operation"], "status")
        self.assertEqual(result.result["count"], 1)


if __name__ == "__main__":
    unittest.main()
