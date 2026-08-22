from __future__ import annotations

import unittest

from oracle_app.media_execution_context import (
    MediaExecutionContext,
    MediaExecutionContextError,
)
from oracle_app.schemas import DispatchPlan


class MediaExecutionContextTests(unittest.TestCase):
    def test_noncanonical_target_resolution_fails_closed(self) -> None:
        with self.assertRaises(MediaExecutionContextError) as failure:
            MediaExecutionContext.from_dispatch(
                DispatchPlan(
                    target="music",
                    hook="music.execute",
                    payload={"source": "room_voice", "session_id": "request-1"},
                    status="planned",
                ),
                canonical_playback_target=False,
            )

        self.assertEqual(failure.exception.code, "playback_target_required")

    def test_explicit_canonical_target_does_not_replace_request_source(self) -> None:
        context = MediaExecutionContext.from_dispatch(
            DispatchPlan(
                target="music",
                hook="music.execute",
                payload={
                    "source": "ephemeral_http",
                    "session_id": "ui-1",
                    "playback_target_source_id": "living_room_voice",
                    "playback_target_resolution": "explicit",
                },
                status="planned",
            ),
            canonical_playback_target=True,
        )

        self.assertEqual(context.request_source_id, "ephemeral_http")
        self.assertEqual(context.playback_target_source_id, "living_room_voice")
        self.assertFalse(context.defer_audible_start)

    def test_authenticated_same_source_target_preserves_voice_defer(self) -> None:
        context = MediaExecutionContext.from_dispatch(
            DispatchPlan(
                target="audiobook",
                hook="audiobook.execute",
                payload={
                    "source": "living_room_voice",
                    "session_id": "voice-1",
                    "playback_target_source_id": "living_room_voice",
                    "playback_target_resolution": "authenticated_request_source",
                },
                status="planned",
            ),
            canonical_playback_target=True,
        )

        self.assertTrue(context.defer_audible_start)

    def test_canonical_target_error_fails_before_media_execution(self) -> None:
        with self.assertRaises(MediaExecutionContextError) as failure:
            MediaExecutionContext.from_dispatch(
                DispatchPlan(
                    target="music",
                    hook="music.execute",
                    payload={
                        "source": "ephemeral_http",
                        "playback_target_error": "playback_target_required",
                    },
                    status="planned",
                ),
                canonical_playback_target=True,
            )

        self.assertEqual(failure.exception.code, "playback_target_required")


if __name__ == "__main__":
    unittest.main()
