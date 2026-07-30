from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from oracle_app.configuration.playback_target_resolution import (
    CanonicalPlaybackTargetResolver,
    PlaybackTargetResolutionError,
)
from oracle_app.configuration.request_source_resolution import ResolvedRequestSource


class CanonicalPlaybackTargetResolverTests(unittest.TestCase):
    def test_explicit_target_is_independent_of_ephemeral_request_source(self) -> None:
        fleet = MagicMock()
        fleet.control_target_for_source.return_value = MagicMock(
            source_id="living_room_voice",
            satellite_id="living_room_satellite",
        )
        request_source = ResolvedRequestSource("ephemeral_http", "ephemeral", "none")

        resolved = CanonicalPlaybackTargetResolver(fleet).resolve(
            explicit_source_id="living_room_voice",
            request_source=request_source,
        )

        self.assertEqual(resolved.source_id, "living_room_voice")
        self.assertEqual(resolved.satellite_id, "living_room_satellite")
        self.assertEqual(resolved.resolution, "explicit")
        self.assertEqual(request_source.request_source_id, "ephemeral_http")

    def test_authenticated_playback_source_defaults_to_itself(self) -> None:
        fleet = MagicMock()
        fleet.control_target_for_source.return_value = MagicMock(
            source_id="living_room_voice",
            satellite_id="living_room_satellite",
        )

        resolved = CanonicalPlaybackTargetResolver(fleet).resolve(
            explicit_source_id=None,
            request_source=ResolvedRequestSource(
                "living_room_voice",
                "stable",
                "satellite_credential",
            ),
        )

        self.assertEqual(resolved.source_id, "living_room_voice")
        self.assertEqual(resolved.resolution, "authenticated_request_source")

    def test_ephemeral_source_without_explicit_target_fails_cleanly(self) -> None:
        fleet = MagicMock()

        with self.assertRaises(PlaybackTargetResolutionError) as failure:
            CanonicalPlaybackTargetResolver(fleet).resolve(
                explicit_source_id=None,
                request_source=ResolvedRequestSource(
                    "ephemeral_http",
                    "ephemeral",
                    "none",
                ),
            )

        self.assertEqual(failure.exception.code, "playback_target_required")
        fleet.control_target_for_source.assert_not_called()

    def test_invalid_explicit_target_does_not_fall_back_to_request_source(self) -> None:
        fleet = MagicMock()
        fleet.control_target_for_source.return_value = None

        with self.assertRaises(PlaybackTargetResolutionError) as failure:
            CanonicalPlaybackTargetResolver(fleet).resolve(
                explicit_source_id="retired_satellite",
                request_source=ResolvedRequestSource(
                    "living_room_voice",
                    "stable",
                    "satellite_credential",
                ),
            )

        self.assertEqual(failure.exception.code, "invalid_playback_target")
        fleet.control_target_for_source.assert_called_once_with("retired_satellite")


if __name__ == "__main__":
    unittest.main()
