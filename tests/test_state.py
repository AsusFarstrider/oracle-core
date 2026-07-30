from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app import state
from oracle_app.session_state import clear_all_sessions, inspect_session


class StateTests(unittest.TestCase):
    def tearDown(self) -> None:
        state.clear_all_active_audiobook_playbacks()
        clear_all_sessions()

    def test_register_active_audiobook_playback_replaces_previous_source_mapping(self) -> None:
        state.register_active_audiobook_playback(
            "playback-1",
            {
                "playback_id": "playback-1",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-1",
            },
        )
        state.register_active_audiobook_playback(
            "playback-2",
            {
                "playback_id": "playback-2",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-2",
            },
        )

        self.assertIsNone(state.get_active_audiobook_playback("playback-1"))
        self.assertEqual(
            state.get_active_audiobook_playback_for_source("test_satellite_bravo")["playback_id"],
            "playback-2",
        )

    def test_clear_active_audiobook_playback_removes_source_mapping(self) -> None:
        state.register_active_audiobook_playback(
            "playback-1",
            {
                "playback_id": "playback-1",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-1",
            },
        )

        state.clear_active_audiobook_playback("playback-1")

        self.assertIsNone(state.get_active_audiobook_playback("playback-1"))
        self.assertIsNone(state.get_active_audiobook_playback_for_source("test_satellite_bravo"))

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

    def test_get_active_audiobook_playback_returns_deep_copy(self) -> None:
        state.register_active_audiobook_playback(
            "playback-1",
            {
                "playback_id": "playback-1",
                "source": "test_satellite_bravo",
                "tracks": [{"content_url": "/audio/book.mp3"}],
            },
        )

        payload = state.get_active_audiobook_playback("playback-1")

        assert payload is not None
        payload["tracks"][0]["content_url"] = "/mutated"
        self.assertEqual(
            state.ACTIVE_AUDIOBOOK_PLAYBACKS["playback-1"]["tracks"][0]["content_url"],
            "/audio/book.mp3",
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


if __name__ == "__main__":
    unittest.main()
