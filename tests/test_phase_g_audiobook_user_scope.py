from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.handlers.audiobook import execute_audiobook
from oracle_app.schemas import DispatchPlan


class PhaseGAudiobookUserScopeTests(unittest.TestCase):
    @patch("oracle_app.handlers.audiobook._play_selected")
    @patch("oracle_app.handlers.audiobook.fetch_current_audiobook_progress")
    def test_resume_current_uses_effective_user_and_sleep_timer(self, mock_fetch_progress, mock_play_selected) -> None:
        mock_fetch_progress.return_value = {"libraryItemId": "book-123"}

        def _fake_play_selected(dispatch, **kwargs):
            dispatch.status = "executed"
            dispatch.result = {
                "action": "play",
                "selected": kwargs["selection"],
                "sleep_timer_seconds": kwargs["sleep_timer_seconds"],
                "user_id": kwargs["user_id"],
            }
            return dispatch

        mock_play_selected.side_effect = _fake_play_selected

        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "resume my audiobook and set a sleep timer for 15 minutes",
                "normalized_text": "resume my audiobook and set a sleep timer for 15 minutes",
                "source": "test_satellite_bravo",
                "session_id": "phase-g-book-1",
                "effective_user_id": "reader_two",
                "playback_target_source_id": "test_satellite_bravo",
                "playback_target_resolution": "authenticated_request_source",
            },
            status="planned",
        )

        result = execute_audiobook(dispatch, canonical_playback_target=True)

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["selected"]["library_item_id"], "book-123")
        self.assertEqual(result.result["sleep_timer_seconds"], 900)
        self.assertEqual(result.result["user_id"], "reader_two")
        mock_fetch_progress.assert_called_once_with(user_id="reader_two")

    @patch("oracle_app.handlers.audiobook._play_selected")
    @patch(
        "oracle_app.handlers.audiobook.fetch_current_audiobook_progress",
        side_effect=AssertionError("canonical handler used V1 audiobook provider"),
    )
    def test_canonical_handler_uses_injected_provider_execution(
        self,
        _legacy_fetch_progress,
        mock_play_selected,
    ) -> None:
        class FakeCanonicalExecution:
            def search_audiobooks(self, *args, **kwargs):
                raise AssertionError("search was not expected")

            def find_series_entry(self, *args, **kwargs):
                raise AssertionError("series lookup was not expected")

            def fetch_current_progress(self, *, user_id=None):
                self.user_id = user_id
                return {"library_item_id": "canonical-book"}

        canonical = FakeCanonicalExecution()

        def _fake_play_selected(dispatch, **kwargs):
            self.assertIs(kwargs["canonical_execution"], canonical)
            dispatch.status = "executed"
            dispatch.result = {"selected": kwargs["selection"]}
            return dispatch

        mock_play_selected.side_effect = _fake_play_selected
        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": "resume my audiobook",
                "normalized_text": "resume my audiobook",
                "source": "living_room_voice",
                "playback_target_source_id": "living_room_voice",
                "playback_target_resolution": "explicit",
                "session_id": "canonical-book-1",
                "effective_user_id": "reader_one",
            },
            status="planned",
        )

        result = execute_audiobook(
            dispatch,
            canonical_playback_target=True,
            canonical_execution=canonical,  # type: ignore[arg-type]
            canonical_authority=True,
        )

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["selected"]["library_item_id"], "canonical-book")
        self.assertEqual(canonical.user_id, "reader_one")


if __name__ == "__main__":
    unittest.main()
