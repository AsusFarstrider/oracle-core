from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app import state
from oracle_app.session_state import clear_all_sessions, inspect_session


class StateTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_all_sessions()

    def test_load_pending_music_request_returns_deep_copy(self) -> None:
        state.store_pending_music_request(
            "test_satellite_bravo",
            "session-1",
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [{"title": "Heroes"}],
            },
        )

        payload = state.load_pending_music_request("test_satellite_bravo", "session-1")

        assert payload is not None
        payload["candidates"][0]["title"] = "Mutated"
        fresh_payload = state.load_pending_music_request("test_satellite_bravo", "session-1")
        assert fresh_payload is not None
        self.assertEqual(
            fresh_payload["candidates"][0]["title"],
            "Heroes",
        )

    def test_pending_state_logs_create_and_resolve_events(self) -> None:
        with self.assertLogs("oracle-brain.trace", level="INFO") as captured:
            state.store_pending_music_request(
                "test_satellite_bravo",
                "session-logs",
                {"intent": {"intent": "play", "title": "heroes"}, "candidates": [{"title": "Heroes"}]},
            )
            state.clear_pending_music_request("test_satellite_bravo", "session-logs")

        output = "\n".join(captured.output)
        self.assertIn("pending_created", output)
        self.assertIn("pending_resolved", output)
        self.assertIn("pending_kind=music", output)
        self.assertIn("source=test_satellite_bravo", output)
        self.assertIn("session_id=session-logs", output)

    def test_pending_music_request_is_backed_by_session_store(self) -> None:
        state.store_pending_music_request(
            "test_satellite_bravo",
            "session-backed",
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [{"title": "Heroes"}],
            },
        )

        session = inspect_session("test_satellite_bravo", "session-backed")

        assert session is not None
        self.assertEqual(session["pending_state"]["domain"], "music")
        self.assertEqual(session["pending_state"]["type"], "clarification")
        self.assertEqual(session["pending_state"]["payload"]["intent"]["title"], "heroes")

    def test_pending_and_active_context_publish_as_one_atomic_mutation(self) -> None:
        active_context_entered = Event()
        allow_active_context = Event()
        original_set_active_context = state.session_state.set_active_context

        def delayed_set_active_context(*args, **kwargs):
            active_context_entered.set()
            allow_active_context.wait(timeout=2.0)
            return original_set_active_context(*args, **kwargs)

        with patch.object(state.session_state, "set_active_context", delayed_set_active_context):
            with ThreadPoolExecutor(max_workers=2) as executor:
                writer = executor.submit(
                    state.store_pending_music_request,
                    "test_satellite_bravo",
                    "session-atomic",
                    {
                        "intent": {"intent": "play", "title": "heroes"},
                        "candidates": [{"title": "Heroes"}],
                    },
                )
                self.assertTrue(active_context_entered.wait(timeout=1.0))
                reader = executor.submit(
                    inspect_session,
                    "test_satellite_bravo",
                    "session-atomic",
                )
                self.assertFalse(reader.done())
                allow_active_context.set()

                self.assertTrue(writer.result(timeout=1.0))
                session = reader.result(timeout=1.0)

        assert session is not None
        self.assertEqual(session["pending_state"]["domain"], "music")
        self.assertEqual(session["active_context"]["route_target"], "music")


if __name__ == "__main__":
    unittest.main()
