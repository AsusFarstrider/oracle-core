from __future__ import annotations

import json
import sys
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from satellite.control_service import ControlServer
from satellite.control_service_runtime import CommandResult
from satellite.control_service_runtime.adapters import PlexampHttpAdapter, ShellPlexampAdapter
from satellite.control_service_runtime.cache import CommandCache
from satellite.control_service_runtime.longform import LongformShellController
import satellite.control_service_runtime.playback_authority as playback_authority_runtime
from satellite.control_service_runtime.playback_authority import (
    build_playback_authority_state,
    interrupt_for_oracle,
    resume_after_oracle,
)
from satellite.control_service_runtime.reply_audio import ReplyAudioStateStore
from satellite.control_service_runtime.server import (
    ControlRequestHandler,
    _validate_control_request_payload,
)
from satellite.control_service_runtime.system_volume import (
    SystemVolumeController,
    build_system_volume_config,
    windows_default_endpoint_support_status,
)
import satellite.control_service_runtime.system_volume as system_volume_runtime
from oracle_app.config_reporting import choose_config_report_format


class ControlServiceTests(unittest.TestCase):
    def _build_server_like(
        self,
        *,
        reply_audio_state_path: str = "",
        reply_audio_stop_path: str = "",
    ) -> ControlServer:
        server = ControlServer.__new__(ControlServer)
        server.reply_audio = ReplyAudioStateStore(reply_audio_state_path, reply_audio_stop_path)
        return server

    def test_plexamp_play_media_waits_for_expected_or_changed_timeline(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        old_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Welcome to the Jungle" grandparentTitle="Guns N’ Roses" parentTitle="Appetite for Destruction" volume="100"><Track title="Welcome to the Jungle" grandparentTitle="Guns N’ Roses" parentTitle="Appetite for Destruction" /></Timeline></MediaContainer>"""
        new_timeline = """<MediaContainer><Timeline type="music" state="playing" title="September" grandparentTitle="Earth, Wind &amp; Fire" parentTitle="Collections" volume="100"><Track title="September" grandparentTitle="Earth, Wind &amp; Fire" parentTitle="Collections" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "title": "Welcome to the Jungle", "artist": "Guns N’ Roses", "album": "Appetite for Destruction"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[old_timeline, new_timeline]):
            result = adapter.play_media(
                media_type="artist",
                plex_key="/library/metadata/43374/children",
                title="Earth, Wind & Fire",
                artist="Earth, Wind & Fire",
            )

        self.assertTrue(result.ok)
        assert result.payload is not None
        self.assertEqual(result.payload["artist"], "Earth, Wind & Fire")
        self.assertEqual(result.payload["title"], "September")

    def test_plexamp_play_media_does_not_interrupt_longform(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        timeline = """<MediaContainer><Timeline type="music" state="playing" title="September" grandparentTitle="Earth, Wind &amp; Fire" parentTitle="Collections" volume="100"><Track title="September" grandparentTitle="Earth, Wind &amp; Fire" parentTitle="Collections" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": False, "state": "stopped"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[timeline]), \
             patch.object(adapter._longform, "stop_longform_audio") as mock_stop_longform, \
             patch.object(adapter._longform, "pause_longform_audio") as mock_pause_longform:
            result = adapter.play_media(
                media_type="artist",
                plex_key="/library/metadata/43374/children",
                title="Earth, Wind & Fire",
                artist="Earth, Wind & Fire",
            )

        self.assertTrue(result.ok)
        mock_stop_longform.assert_not_called()
        mock_pause_longform.assert_not_called()

    def test_plexamp_play_media_rejects_unchanged_stale_timeline(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        stale_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Welcome to the Jungle" grandparentTitle="Guns N’ Roses" parentTitle="Appetite for Destruction" volume="100"><Track title="Welcome to the Jungle" grandparentTitle="Guns N’ Roses" parentTitle="Appetite for Destruction" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "title": "Welcome to the Jungle", "artist": "Guns N’ Roses", "album": "Appetite for Destruction"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[stale_timeline] * 8):
            result = adapter.play_media(
                media_type="artist",
                plex_key="/library/metadata/43374/children",
                title="Earth, Wind & Fire",
                artist="Earth, Wind & Fire",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "failed")

    def test_plexamp_resume_uses_native_play_when_current_state_is_paused(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        timeline = """<MediaContainer><Timeline type="music" state="playing" title="Something" grandparentTitle="The Beatles" parentTitle="Abbey Road" volume="100"><Track title="Something" grandparentTitle="The Beatles" parentTitle="Abbey Road" /></Timeline></MediaContainer>"""

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": True, "state": "paused", "title": "Something"},
        ), patch.object(adapter, "_simple_action", return_value=CommandResult(ok=True, state="accepted")) as mock_simple, patch.object(
            adapter,
            "_timeline",
            return_value=timeline,
        ):
            result = adapter.resume()

        self.assertTrue(result.ok)
        mock_simple.assert_called_once_with("play")

    def test_plexamp_resume_fails_when_native_play_does_not_leave_paused_state(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        paused_timeline = """<MediaContainer><Timeline type="music" state="paused" title="Something" grandparentTitle="The Beatles" parentTitle="Abbey Road" volume="100"><Track title="Something" grandparentTitle="The Beatles" parentTitle="Abbey Road" /></Timeline></MediaContainer>"""

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": True, "state": "paused", "title": "Something"},
        ), patch.object(adapter, "_simple_action", return_value=CommandResult(ok=True, state="accepted")), patch.object(
            adapter,
            "_timeline",
            return_value=paused_timeline,
        ):
            result = adapter.resume()

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "Plexamp accepted resume but did not enter a playable state")

    def test_plexamp_resume_replays_fresh_paused_snapshot_when_not_currently_paused(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        adapter._paused_snapshot = adapter._resume_snapshot_from_state(
            {
                "type": "track",
                "plex_key": "/library/metadata/50248",
                "title": "Something",
                "artist": "The Beatles",
                "album": "Abbey Road",
            }
        )
        assert adapter._paused_snapshot is not None

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": False, "state": "stopped"},
        ), patch.object(adapter, "play_media", return_value=CommandResult(ok=True, state="accepted")) as mock_play_media:
            result = adapter.resume()

        self.assertTrue(result.ok)
        mock_play_media.assert_called_once_with(
            media_type="track",
            plex_key="/library/metadata/50248",
            title="Something",
            artist="The Beatles",
            album="Abbey Road",
        )


    def test_plexamp_resume_fails_cleanly_when_no_paused_snapshot_exists(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": False, "state": "stopped"},
        ):
            result = adapter.resume()

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "No paused Plex playback is available to resume")

    def test_plexamp_play_media_allows_longer_album_startup_window(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        stopped_timeline = """<MediaContainer><Timeline type="music" state="stopped" volume="100" /></MediaContainer>"""
        album_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Alexander Hamilton" grandparentTitle="Leslie Odom, Jr." parentTitle="Hamilton: An American Musical" volume="100"><Track title="Alexander Hamilton" grandparentTitle="Leslie Odom, Jr." parentTitle="Hamilton: An American Musical" /></Timeline></MediaContainer>"""

        side_effect = [stopped_timeline] * 9 + [album_timeline]

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": False},
        ), patch.object(
            adapter,
            "_request",
            return_value=CommandResult(ok=True, state="accepted"),
        ), patch.object(
            adapter,
            "_timeline",
            side_effect=side_effect,
        ), patch("satellite.control_service_runtime.adapters.plexamp_http.time.sleep", return_value=None):
            result = adapter.play_media(
                media_type="album",
                plex_key="/library/metadata/43879/children",
                title="Hamilton: An American Musical",
                artist="Lin‐Manuel Miranda",
                album="Hamilton: An American Musical",
            )

        self.assertTrue(result.ok)
        assert result.payload is not None
        self.assertEqual(result.payload["album"], "Hamilton: An American Musical")
        self.assertEqual(result.payload["title"], "Alexander Hamilton")

    def test_plexamp_stop_waits_for_non_playing_timeline(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        stopped_timeline = """<MediaContainer><Timeline type="music" state="stopped" volume="100"></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "title": "Come Together", "artist": "The Beatles", "album": "Abbey Road"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[stopped_timeline]):
            result = adapter.stop()

        self.assertTrue(result.ok)
        self.assertEqual(result.state, "stopped")

    def test_plexamp_native_queue_play_uses_selected_queue_track(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="auto",
        )
        adapter = PlexampHttpAdapter(args)

        queue_tracks = [
            {
                "rating_key": "track-1",
                "plex_key": "/library/metadata/track-1",
                "title": "Speed of Life",
                "artist": "David Bowie",
                "album": "Low",
                "duration_seconds": 125.0,
            },
            {
                "rating_key": "track-2",
                "plex_key": "/library/metadata/track-2",
                "title": "Breaking Glass",
                "artist": "David Bowie",
                "album": "Low",
                "duration_seconds": 111.0,
            },
        ]

        with patch.object(adapter, "_stop_plexamp_if_active"), patch.object(
            adapter,
            "_build_native_stream_url",
            return_value="http://127.0.0.1/stream.mp3",
        ), patch.object(
            adapter._native_music,
            "play_track",
            return_value=CommandResult(ok=True, state="accepted", payload={"state": "playing"}),
        ) as mock_play_track:
            result = adapter.play_media(
                media_type="album",
                plex_key="/library/metadata/album-low/children",
                rating_key="album-low",
                title="Low",
                artist="David Bowie",
                album="Low",
                backend_hint="oracle_native_music",
                queue_id="album-low",
                queue_position=2,
                queue_count=2,
                collection_title="Low",
                collection_type="album",
                queue_tracks=queue_tracks,
            )

        self.assertTrue(result.ok)
        mock_play_track.assert_called_once()
        self.assertEqual(mock_play_track.call_args.kwargs["track_id"], "track-2")
        self.assertEqual(mock_play_track.call_args.kwargs["title"], "Breaking Glass")
        self.assertEqual(mock_play_track.call_args.kwargs["queue_count"], 2)
        self.assertEqual(mock_play_track.call_args.kwargs["collection_type"], "album")

    def test_plexamp_native_queue_next_advances_to_next_track(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="auto",
        )
        adapter = PlexampHttpAdapter(args)
        native_state = {
            "ok": True,
            "state": "playing",
            "backend_type": "oracle_native_music",
            "media_type": "playlist",
            "queue_id": "playlist-1",
            "queue_position": 1,
            "queue_count": 2,
            "collection_title": "Favorites",
            "collection_type": "playlist",
            "position_seconds": 2.0,
            "queue_tracks": [
                {"rating_key": "track-1", "title": "Song 1", "artist": "Artist", "album": "Album", "duration_seconds": 100.0},
                {"rating_key": "track-2", "title": "Song 2", "artist": "Artist", "album": "Album", "duration_seconds": 110.0},
            ],
        }

        with patch.object(adapter, "_safe_native_music_state", return_value=native_state), \
             patch.object(adapter, "_build_native_stream_url", return_value="http://127.0.0.1/track-2.mp3"), \
             patch.object(
                 adapter._native_music,
                 "play_track",
                 return_value=CommandResult(ok=True, state="accepted", payload={"queue_position": 2, "title": "Song 2"}),
             ) as mock_play_track:
            result = adapter.next()

        self.assertTrue(result.ok)
        self.assertEqual(mock_play_track.call_args.kwargs["track_id"], "track-2")
        self.assertEqual(mock_play_track.call_args.kwargs["queue_position"], 2)

    def test_plexamp_native_queue_previous_moves_to_previous_track(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="auto",
        )
        adapter = PlexampHttpAdapter(args)
        native_state = {
            "ok": True,
            "state": "playing",
            "backend_type": "oracle_native_music",
            "media_type": "playlist",
            "queue_id": "playlist-1",
            "queue_position": 2,
            "queue_count": 2,
            "collection_title": "Favorites",
            "collection_type": "playlist",
            "position_seconds": 12.0,
            "queue_tracks": [
                {"rating_key": "track-1", "title": "Song 1", "artist": "Artist", "album": "Album", "duration_seconds": 100.0},
                {"rating_key": "track-2", "title": "Song 2", "artist": "Artist", "album": "Album", "duration_seconds": 110.0},
            ],
        }

        with patch.object(adapter, "_safe_native_music_state", return_value=native_state), \
             patch.object(adapter, "_build_native_stream_url", return_value="http://127.0.0.1/track-1.mp3"), \
             patch.object(
                 adapter._native_music,
                 "play_track",
                 return_value=CommandResult(ok=True, state="accepted", payload={"queue_position": 1, "title": "Song 1"}),
             ) as mock_play_track:
            result = adapter.previous()

        self.assertTrue(result.ok)
        self.assertEqual(mock_play_track.call_args.kwargs["track_id"], "track-1")
        self.assertEqual(mock_play_track.call_args.kwargs["queue_position"], 1)

    def test_plexamp_native_music_restart_restarts_current_track(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="auto",
        )
        adapter = PlexampHttpAdapter(args)
        native_state = {
            "ok": True,
            "state": "playing",
            "backend_type": "oracle_native_music",
            "media_type": "playlist",
            "queue_id": "playlist-1",
            "queue_position": 2,
            "queue_count": 2,
            "position_seconds": 12.0,
        }

        with patch.object(adapter, "_safe_native_music_state", return_value=native_state), \
             patch.object(
                 adapter._native_music,
                 "restart",
                 return_value=CommandResult(ok=True, state="accepted", payload={"queue_position": 2, "position_seconds": 0.0}),
             ) as mock_restart:
            result = adapter.restart()

        self.assertTrue(result.ok)
        mock_restart.assert_called_once_with()

    def test_plexamp_stop_rejects_stale_playing_timeline(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        stale_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Come Together" grandparentTitle="The Beatles" parentTitle="Abbey Road" volume="100"><Track title="Come Together" grandparentTitle="The Beatles" parentTitle="Abbey Road" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "title": "Come Together", "artist": "The Beatles", "album": "Abbey Road"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[stale_timeline] * 8):
            result = adapter.stop()

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "failed")
        self.assertIn("remained in a playable state", result.detail)


if __name__ == "__main__":
    unittest.main()
