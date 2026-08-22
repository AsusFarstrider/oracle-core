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

    def test_longform_play_retries_once_after_immediate_startup_failure(self) -> None:
        controller = LongformShellController(
            SimpleNamespace(
                play_longform_audio_cmd="play {manifest_path}",
                pause_longform_audio_cmd="pause",
                resume_longform_audio_cmd="resume",
                stop_longform_audio_cmd="stop",
                seek_longform_audio_cmd="seek {position_seconds}",
                longform_state_cmd="state",
            )
        )
        controller._startup_poll_attempts = 1
        first_launch = CommandResult(ok=True, state="accepted", payload={"state": "playing", "playback_id": "book-1"})
        second_launch = CommandResult(ok=True, state="accepted", payload={"state": "playing", "playback_id": "book-1"})
        observed_commands: list[str] = []
        observed_contexts: list[dict[str, object]] = []
        state_sequence = [
            {"ok": True, "state": "stopped", "playing": False, "playback_id": "book-1"},
            {"ok": True, "state": "playing", "playing": True, "playback_id": "book-1"},
        ]

        def fake_run_command(template: str | None, context: dict[str, object], **_: object) -> CommandResult:
            observed_commands.append(str(template))
            observed_contexts.append(dict(context))
            if template == "play {manifest_path}":
                return [first_launch, second_launch][len(observed_commands) - 1]
            self.fail(f"unexpected template {template}")

        def fake_get_state(*, use_cache: bool = True) -> dict[str, object]:
            return state_sequence.pop(0)

        with patch.object(controller, "_run_command", side_effect=fake_run_command), patch.object(
            controller,
            "get_longform_state",
            side_effect=fake_get_state,
        ), patch("satellite.control_service_runtime.longform.time.sleep"):
            result = controller.play_longform_audio(
                playback_id="book-1",
                session_id="session-1",
                title="Outlaw of Gor",
                author="John Norman",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example.test/track.mp3"}],
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.payload["state"], "playing")
        self.assertEqual(len(observed_commands), 2)
        self.assertEqual(observed_contexts[0]["playback_id"], "book-1")
        self.assertEqual(observed_contexts[1]["playback_id"], "book-1")

    def test_longform_play_fails_cleanly_when_retry_also_collapses(self) -> None:
        controller = LongformShellController(
            SimpleNamespace(
                play_longform_audio_cmd="play {manifest_path}",
                pause_longform_audio_cmd="pause",
                resume_longform_audio_cmd="resume",
                stop_longform_audio_cmd="stop",
                seek_longform_audio_cmd="seek {position_seconds}",
                longform_state_cmd="state",
            )
        )
        controller._startup_poll_attempts = 1
        state_payload = {"ok": True, "state": "stopped", "playing": False, "playback_id": "book-2"}

        with patch.object(
            controller,
            "_run_command",
            return_value=CommandResult(ok=True, state="accepted", payload={"state": "playing", "playback_id": "book-2"}),
        ) as mock_run_command, patch.object(
            controller,
            "get_longform_state",
            side_effect=[dict(state_payload), dict(state_payload)],
        ), patch("satellite.control_service_runtime.longform.time.sleep"):
            result = controller.play_longform_audio(
                playback_id="book-2",
                session_id="session-2",
                title="Outlaw of Gor",
                author="John Norman",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example.test/track.mp3"}],
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "failed")
        self.assertIn("did not reach a playable long-form state", result.detail)
        self.assertEqual(mock_run_command.call_count, 2)

    def test_plexamp_adapter_without_native_support_skips_native_state_probe(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://192.0.2.205:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            disable_plexamp_external=False,
            http_timeout_seconds=5.0,
            supports_oracle_native_music=False,
            oracle_native_music_player_bin="auto",
            output_volume_backend="",
            output_volume_card="",
            output_volume_control="",
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd="python longform play --manifest {manifest_path}",
            pause_longform_audio_cmd="python longform pause",
            resume_longform_audio_cmd="python longform resume",
            stop_longform_audio_cmd="python longform stop",
            seek_longform_audio_cmd="python longform seek --position-seconds {position_seconds}",
            longform_state_cmd="python longform state",
        )
        adapter = PlexampHttpAdapter(args)

        self.assertIsNone(adapter._safe_native_music_state())

    def test_plexamp_adapter_can_disable_external_plexamp_while_keeping_plex_credentials(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://192.0.2.205:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            disable_plexamp_external=True,
            http_timeout_seconds=5.0,
            supports_oracle_native_music=False,
            oracle_native_music_player_bin="auto",
            output_volume_backend="",
            output_volume_card="",
            output_volume_control="",
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd="python longform play --manifest {manifest_path}",
            pause_longform_audio_cmd="python longform pause",
            resume_longform_audio_cmd="python longform resume",
            stop_longform_audio_cmd="python longform stop",
            seek_longform_audio_cmd="python longform seek --position-seconds {position_seconds}",
            longform_state_cmd="python longform state",
        )
        adapter = PlexampHttpAdapter(args)

        with patch.object(adapter, "_timeline") as mock_timeline:
            state = adapter.get_now_playing()

        self.assertEqual(state, {"ok": True, "playing": False, "state": "stopped"})
        self.assertFalse(adapter.get_music_backend_expectation()["supports_plexamp"])
        mock_timeline.assert_not_called()

    def test_native_music_controller_passes_queue_tracks_json(self) -> None:
        from satellite.control_service_runtime.native_music import NativeMusicController

        args = SimpleNamespace(
            oracle_native_music_player_bin="auto",
            supports_oracle_native_music=True,
        )
        controller = NativeMusicController(args)

        with patch("satellite.control_service_runtime.native_music.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stdout='{"ok": true, "state": "playing"}', stderr="")
            result = controller.play_track(
                stream_url="http://example/stream.mp3",
                track_id="track-1",
                media_type="playlist",
                title="Song 1",
                artist="Artist",
                album="Album",
                queue_id="playlist-1",
                queue_position=2,
                queue_count=3,
                collection_title="Favorites",
                collection_type="playlist",
                queue_tracks=[
                    {"rating_key": "track-1", "title": "Song 1"},
                    {"rating_key": "track-2", "title": "Song 2"},
                ],
                duration_seconds=180.0,
            )

        self.assertTrue(result.ok)
        command = mock_run.call_args.args[0]
        self.assertIn("--queue-tracks-json", command)
        queue_json = command[command.index("--queue-tracks-json") + 1]
        self.assertIn("track-2", queue_json)

    def test_plexamp_play_longform_audio_stops_active_music_first(self) -> None:
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

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "state": "playing"}), \
             patch.object(adapter, "stop", return_value=CommandResult(ok=True, state="stopped")) as mock_stop, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertTrue(result.ok)
        mock_stop.assert_called_once_with()
        mock_play_longform.assert_called_once()

    def test_local_playback_without_plexamp_treats_external_backend_as_stopped(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="",
            http_timeout_seconds=5.0,
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="ffplay",
            output_volume_backend="",
            output_volume_card="",
            output_volume_control="",
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        with patch.object(adapter._native_music, "state", return_value={"ok": True, "state": "stopped", "playing": False}), \
             patch.object(adapter, "_timeline") as mock_timeline, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            self.assertEqual(adapter.get_now_playing(), {"ok": True, "playing": False, "state": "stopped"})
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertTrue(result.ok)
        mock_timeline.assert_not_called()
        mock_play_longform.assert_called_once()

    def test_plexamp_play_longform_audio_fails_when_music_stop_fails(self) -> None:
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

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "state": "playing"}), \
             patch.object(adapter, "stop", return_value=CommandResult(ok=False, state="failed", detail="music stop failed")) as mock_stop, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "music stop failed")
        mock_stop.assert_called_once_with()
        mock_play_longform.assert_not_called()

    def test_shell_play_longform_audio_stops_active_music_first(self) -> None:
        args = SimpleNamespace(
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = ShellPlexampAdapter(args)

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "state": "playing"}), \
             patch.object(adapter, "stop", return_value=CommandResult(ok=True, state="stopped")) as mock_stop, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertTrue(result.ok)
        mock_stop.assert_called_once_with()
        mock_play_longform.assert_called_once()

    def test_shell_play_longform_audio_fails_when_music_stop_fails(self) -> None:
        args = SimpleNamespace(
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = ShellPlexampAdapter(args)

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "state": "playing"}), \
             patch.object(adapter, "stop", return_value=CommandResult(ok=False, state="failed", detail="music stop failed")) as mock_stop, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "music stop failed")
        mock_stop.assert_called_once_with()
        mock_play_longform.assert_not_called()

if __name__ == "__main__":
    unittest.main()
