from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "satellite"))

import native_music_player


class NativeMusicPlayerTests(unittest.TestCase):
    def test_windows_player_presence_uses_one_process_inventory(self) -> None:
        completed = type("Completed", (), {"stdout": "mpv.exe  123 Console"})()
        with patch.object(native_music_player.subprocess, "run", return_value=completed) as mock_run:
            self.assertTrue(native_music_player._windows_supported_player_processes_present())

        mock_run.assert_called_once_with(
            ["tasklist", "/NH"], capture_output=True, text=True, check=False
        )

    def test_enumerate_oracle_native_music_player_pids_filters_by_player_and_url(self) -> None:
        track_url = "http://example.test/track.mp3"
        state = {"url": track_url, "player_bin": "/usr/bin/ffplay"}

        with patch.object(
            native_music_player,
            "_iter_process_argv",
            return_value=[
                (101, ["/usr/bin/ffplay", "-nodisp", track_url]),
                (102, ["/usr/bin/mpv", "--no-video", track_url]),
                (103, ["/usr/bin/ffplay", "-nodisp", "http://example.test/other.mp3"]),
                (104, ["/usr/bin/python3", track_url]),
            ],
        ):
            self.assertEqual(native_music_player._enumerate_oracle_native_music_player_pids(state), [101])

    def test_enumerate_oracle_native_music_player_pids_accepts_windows_ffplay_path(self) -> None:
        track_url = "http://example.test/track.mp3"
        state = {
            "url": track_url,
            "player_bin": r"C:\Users\OracleUser\AppData\Local\Microsoft\WinGet\Links\ffplay.exe",
        }

        with patch.object(
            native_music_player,
            "_iter_process_argv",
            return_value=[
                (
                    101,
                    [
                        r"C:\Users\OracleUser\AppData\Local\Microsoft\WinGet\Links\ffplay.exe",
                        "-nodisp",
                        track_url,
                    ],
                ),
                (102, [r"C:\Tools\ffplay.exe", "-nodisp", "http://example.test/other.mp3"]),
            ],
        ):
            self.assertEqual(native_music_player._enumerate_oracle_native_music_player_pids(state), [101])

    def test_ffplay_command_enables_http_reconnect(self) -> None:
        command = native_music_player._build_player_command(
            player_bin="ffplay",
            url="http://example.test/track.mp3",
            position_seconds=0.0,
        )

        self.assertIn("-reconnect", command)
        self.assertIn("-reconnect_at_eof", command)
        self.assertIn("-reconnect_streamed", command)
        self.assertIn("-reconnect_on_network_error", command)
        self.assertLess(command.index("-reconnect"), command.index("http://example.test/track.mp3"))

    def test_cmd_state_surfaces_degraded_state_for_surviving_orphan_process(self) -> None:
        state = {
            "track_id": "track-1",
            "media_type": "track",
            "url": "http://example.test/track.mp3",
            "title": "Sound and Vision",
            "artist": "David Bowie",
            "album": "Low",
            "duration_seconds": 100.0,
            "position_seconds": 12.0,
            "state": "stopped",
            "pid": 111,
            "started_monotonic": None,
            "player_bin": "/usr/bin/ffplay",
        }
        captured = io.StringIO()
        with patch.object(native_music_player, "_load_state", return_value=state), \
             patch.object(native_music_player, "_refresh_state"), \
             patch.object(native_music_player, "_save_state"), \
             patch.object(native_music_player, "_enumerate_oracle_native_music_player_pids", return_value=[222]), \
             patch.object(native_music_player, "_print_json", side_effect=lambda payload: captured.write(str(payload))):
            result = native_music_player._cmd_state()

        self.assertEqual(result, 0)
        rendered = captured.getvalue()
        self.assertIn("'playing': True", rendered)
        self.assertIn("'degraded_state': True", rendered)
        self.assertIn("'degraded_reason': 'orphan_native_music_process'", rendered)

    def test_cmd_state_skips_process_inventory_for_live_tracked_player(self) -> None:
        state = {
            "track_id": "track-1",
            "media_type": "track",
            "url": "http://example.test/track.mp3",
            "title": "Sound and Vision",
            "artist": "David Bowie",
            "album": "Low",
            "duration_seconds": 100.0,
            "position_seconds": 12.0,
            "state": "playing",
            "pid": 111,
            "started_monotonic": 1.0,
            "player_bin": "/usr/bin/ffplay",
        }
        captured: list[dict[str, object]] = []
        with patch.object(native_music_player, "_load_state", return_value=state), \
             patch.object(native_music_player, "_refresh_state"), \
             patch.object(native_music_player, "_save_state"), \
             patch.object(native_music_player, "_enumerate_oracle_native_music_player_pids") as mock_enumerate, \
             patch.object(native_music_player, "_print_json", side_effect=lambda payload: captured.append(payload)):
            result = native_music_player._cmd_state()

        self.assertEqual(result, 0)
        mock_enumerate.assert_not_called()
        self.assertEqual(captured[0]["state"], "playing")
        self.assertTrue(captured[0]["playing"])

    def test_cmd_state_trusts_clean_stopped_state_without_tracked_player(self) -> None:
        state = {
            "track_id": "track-1",
            "media_type": "track",
            "url": "http://example.test/track.mp3",
            "title": "Sound and Vision",
            "artist": "David Bowie",
            "album": "Low",
            "duration_seconds": 100.0,
            "position_seconds": 12.0,
            "state": "stopped",
            "pid": None,
            "started_monotonic": None,
            "player_bin": "/usr/bin/ffplay",
        }
        captured: list[dict[str, object]] = []
        with patch.object(native_music_player, "_load_state", return_value=state), \
             patch.object(native_music_player, "_refresh_state"), \
             patch.object(native_music_player, "_save_state"), \
             patch.object(native_music_player, "_enumerate_oracle_native_music_player_pids") as mock_enumerate, \
             patch.object(native_music_player, "_print_json", side_effect=lambda payload: captured.append(payload)):
            result = native_music_player._cmd_state()

        self.assertEqual(result, 0)
        mock_enumerate.assert_not_called()
        self.assertEqual(captured[0]["state"], "stopped")
        self.assertFalse(captured[0]["playing"])

    def test_refresh_state_does_not_inventory_clean_inactive_state(self) -> None:
        for inactive_state in ("paused", "stopped"):
            state = {"state": inactive_state, "pid": None}
            with self.subTest(state=inactive_state), patch.object(
                native_music_player,
                "_enumerate_oracle_native_music_player_pids",
            ) as mock_enumerate:
                native_music_player._refresh_state(state)

            mock_enumerate.assert_not_called()
            self.assertEqual(state["state"], inactive_state)

    def test_cmd_stop_fails_when_oracle_native_music_processes_remain(self) -> None:
        state = {
            "track_id": "track-1",
            "media_type": "track",
            "url": "http://example.test/track.mp3",
            "title": "Sound and Vision",
            "artist": "David Bowie",
            "album": "Low",
            "duration_seconds": 100.0,
            "position_seconds": 12.0,
            "state": "playing",
            "pid": 111,
            "started_monotonic": 1.0,
            "player_bin": "/usr/bin/ffplay",
        }
        stderr = io.StringIO()
        with patch.object(native_music_player, "_load_state", return_value=state), \
             patch.object(native_music_player, "_refresh_state"), \
             patch.object(native_music_player, "_current_position", return_value=12.0), \
             patch.object(native_music_player, "_save_state"), \
             patch.object(native_music_player, "_stop_existing_process", return_value=[222]), \
             patch("sys.stderr", stderr):
            result = native_music_player._cmd_stop()

        self.assertEqual(result, 1)
        self.assertIn("did not terminate all Oracle-managed playback processes", stderr.getvalue())

    def test_stop_existing_process_terminates_tracked_and_surviving_oracle_pids(self) -> None:
        state = {
            "pid": 111,
            "pgid": 444,
            "url": "http://example.test/track.mp3",
            "player_bin": "/usr/bin/ffplay",
        }
        killed: list[int] = []
        with patch.object(native_music_player, "_load_state", return_value=state), \
             patch.object(native_music_player, "_is_process_alive", side_effect=lambda pid: pid in {111, 222, 333, 444}), \
             patch.object(native_music_player, "_enumerate_oracle_native_music_player_pids", side_effect=[[222, 333], []]), \
             patch.object(native_music_player, "_terminate_process", side_effect=lambda pid: killed.append(pid)):
            remaining = native_music_player._stop_existing_process(remove_state=False)

        self.assertEqual(killed, [444, 222, 333])
        self.assertEqual(remaining, [])

    def test_stop_existing_process_does_not_repeat_empty_inventory(self) -> None:
        state = {
            "pid": None,
            "pgid": None,
            "url": "http://example.test/track.mp3",
            "player_bin": "/usr/bin/ffplay",
        }
        with patch.object(native_music_player, "_load_state", return_value=state), \
             patch.object(native_music_player, "_enumerate_oracle_native_music_player_pids", return_value=[]) as mock_enumerate, \
             patch.object(native_music_player, "_terminate_process") as mock_terminate:
            remaining = native_music_player._stop_existing_process(remove_state=False)

        self.assertEqual(remaining, [])
        mock_enumerate.assert_called_once_with(state)
        mock_terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
