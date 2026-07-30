from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "satellite"))

import longform_player


class LongformPlayerTests(unittest.TestCase):
    def test_windows_player_presence_uses_one_process_inventory(self) -> None:
        completed = type("Completed", (), {"stdout": "ffplay.exe  123 Console"})()
        with patch.object(longform_player.subprocess, "run", return_value=completed) as mock_run:
            self.assertTrue(longform_player._windows_supported_player_processes_present())

        mock_run.assert_called_once_with(
            ["tasklist", "/NH"], capture_output=True, text=True, check=False
        )

    def test_enumerate_oracle_longform_player_pids_filters_by_player_and_playlist_path(self) -> None:
        playlist_path = str(longform_player.PLAYLIST_PATH)
        with patch.object(
            longform_player,
            "_iter_process_argv",
            return_value=[
                (101, ["/usr/bin/ffplay", "-nodisp", playlist_path]),
                (102, ["/usr/bin/mpv", "--playlist", playlist_path]),
                (103, ["/usr/bin/ffplay", "-nodisp", "/tmp/other.ffconcat"]),
                (104, ["/usr/bin/mpv", "--playlist", "/tmp/other.ffconcat"]),
                (105, ["/usr/bin/python3", playlist_path]),
            ],
        ):
            self.assertEqual(longform_player._enumerate_oracle_longform_player_pids(), [101, 102])

    def test_oracle_longform_process_detection_accepts_windows_ffplay_path(self) -> None:
        argv = [
            r"C:\Users\OracleTest\AppData\Local\Microsoft\WinGet\Links\ffplay.exe",
            "-nodisp",
            r"C:\tmp\oracle-longform-player\playlist.ffconcat",
        ]

        self.assertTrue(longform_player._is_oracle_longform_process_argv(argv))

    def test_cmd_state_surfaces_degraded_state_for_surviving_orphan_process(self) -> None:
        state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "position_seconds": 120.0,
            "state": "stopped",
            "pid": 111,
            "started_monotonic": None,
            "manifest": {},
            "player_bin": "/usr/bin/ffplay",
        }
        captured = io.StringIO()
        with patch.object(longform_player, "_load_state", return_value=state), \
             patch.object(longform_player, "_refresh_state"), \
             patch.object(longform_player, "_save_state"), \
             patch.object(longform_player, "_enumerate_oracle_longform_player_pids", return_value=[222]), \
             patch.object(longform_player, "_print_json", side_effect=lambda payload: captured.write(str(payload))):
            result = longform_player._cmd_state()

        self.assertEqual(result, 0)
        rendered = captured.getvalue()
        self.assertIn("'playing': True", rendered)
        self.assertIn("'degraded_state': True", rendered)
        self.assertIn("'degraded_reason': 'orphan_longform_process'", rendered)

    def test_cmd_state_skips_process_inventory_for_live_tracked_player(self) -> None:
        state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "position_seconds": 120.0,
            "state": "playing",
            "pid": 111,
            "started_monotonic": 1.0,
            "manifest": {},
            "player_bin": "/usr/bin/ffplay",
        }
        captured: list[dict[str, object]] = []
        with patch.object(longform_player, "_load_state", return_value=state), \
             patch.object(longform_player, "_refresh_state"), \
             patch.object(longform_player, "_save_state"), \
             patch.object(longform_player, "_enumerate_oracle_longform_player_pids") as mock_enumerate, \
             patch.object(longform_player, "_print_json", side_effect=lambda payload: captured.append(payload)):
            result = longform_player._cmd_state()

        self.assertEqual(result, 0)
        mock_enumerate.assert_not_called()
        self.assertEqual(captured[0]["state"], "playing")
        self.assertTrue(captured[0]["playing"])

    def test_cmd_state_trusts_clean_stopped_state_without_tracked_player(self) -> None:
        state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "position_seconds": 120.0,
            "state": "stopped",
            "pid": None,
            "started_monotonic": None,
            "manifest": {},
            "player_bin": "/usr/bin/ffplay",
        }
        captured: list[dict[str, object]] = []
        with patch.object(longform_player, "_load_state", return_value=state), \
             patch.object(longform_player, "_refresh_state"), \
             patch.object(longform_player, "_save_state"), \
             patch.object(longform_player, "_enumerate_oracle_longform_player_pids") as mock_enumerate, \
             patch.object(longform_player, "_print_json", side_effect=lambda payload: captured.append(payload)):
            result = longform_player._cmd_state()

        self.assertEqual(result, 0)
        mock_enumerate.assert_not_called()
        self.assertEqual(captured[0]["state"], "stopped")
        self.assertFalse(captured[0]["playing"])

    def test_cmd_stop_terminates_surviving_oracle_longform_processes(self) -> None:
        state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "position_seconds": 120.0,
            "state": "playing",
            "pid": 111,
            "started_monotonic": 1.0,
            "manifest": {},
            "player_bin": "/usr/bin/ffplay",
        }
        captured = []
        with patch.object(longform_player, "_load_state", return_value=state), \
             patch.object(longform_player, "_refresh_state"), \
             patch.object(longform_player, "_current_position", return_value=120.0), \
             patch.object(longform_player, "_save_state"), \
             patch.object(longform_player, "_stop_existing_process", return_value=[]), \
             patch.object(longform_player, "_print_json", side_effect=lambda payload: captured.append(payload)):
            result = longform_player._cmd_stop()

        self.assertEqual(result, 0)
        self.assertEqual(captured[0]["state"], "stopped")
        self.assertFalse(captured[0]["playing"])

    def test_stop_existing_process_does_not_repeat_empty_inventory(self) -> None:
        with patch.object(longform_player, "_load_state", return_value=None), \
             patch.object(longform_player, "_enumerate_oracle_longform_player_pids", return_value=[]) as mock_enumerate, \
             patch.object(longform_player, "_terminate_process") as mock_terminate:
            remaining = longform_player._stop_existing_process(remove_state=False)

        self.assertEqual(remaining, [])
        mock_enumerate.assert_called_once_with()
        mock_terminate.assert_not_called()

    def test_cmd_pause_fails_when_oracle_longform_processes_remain(self) -> None:
        state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "position_seconds": 120.0,
            "state": "playing",
            "pid": 111,
            "started_monotonic": 1.0,
            "manifest": {},
            "player_bin": "/usr/bin/ffplay",
        }
        stderr = io.StringIO()
        with patch.object(longform_player, "_load_state", return_value=state), \
             patch.object(longform_player, "_refresh_state"), \
             patch.object(longform_player, "_current_position", return_value=120.0), \
             patch.object(longform_player, "_save_state"), \
             patch.object(longform_player, "_stop_existing_process", return_value=[222]), \
             patch("sys.stderr", stderr):
            result = longform_player._cmd_pause()

        self.assertEqual(result, 1)
        self.assertIn("did not terminate all Oracle-managed playback processes", stderr.getvalue())

    def test_cmd_resume_restarts_from_stopped_state_when_manifest_exists(self) -> None:
        state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "position_seconds": 120.0,
            "state": "stopped",
            "pid": None,
            "started_monotonic": None,
            "manifest": {"tracks": [{"url": "http://example.test/ch1.mp3"}], "duration_seconds": 1000.0},
            "player_bin": "/usr/bin/ffplay",
        }
        captured: list[dict[str, object]] = []
        restarted_state = dict(state, state="playing", pid=222, started_monotonic=3.0)
        with patch.object(longform_player, "_load_state", return_value=state), \
             patch.object(longform_player, "_refresh_state"), \
             patch.object(longform_player, "_current_position", return_value=120.0), \
             patch.object(longform_player, "_restart_from_position", return_value=restarted_state) as mock_restart, \
             patch.object(longform_player, "_print_json", side_effect=lambda payload: captured.append(payload)):
            result = longform_player._cmd_resume(player_bin="auto")

        self.assertEqual(result, 0)
        mock_restart.assert_called_once_with(state, 120.0, player_bin="auto")
        self.assertEqual(captured[0]["state"], "playing")

    def test_cmd_play_start_paused_persists_paused_state_without_launching_player(self) -> None:
        manifest = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "start_position_seconds": 120.0,
            "start_paused": True,
            "tracks": [{"url": "http://example.test/ch1.mp3"}],
        }
        captured: list[dict[str, object]] = []
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            Path(handle.name).write_text(__import__("json").dumps(manifest), encoding="utf-8")
            manifest_path = Path(handle.name)
        try:
            with patch.object(longform_player, "_ensure_state_dir"), \
                 patch.object(longform_player, "_stop_existing_process", return_value=[]), \
                 patch.object(longform_player, "_save_state") as mock_save, \
                 patch.object(longform_player, "which", side_effect=AssertionError("player discovery is not allowed")) as mock_which, \
                 patch.object(longform_player, "_build_player_command", side_effect=AssertionError("command construction is not allowed")) as mock_command, \
                 patch.object(longform_player.subprocess, "Popen", side_effect=AssertionError("process launch is not allowed")) as mock_popen, \
                 patch.object(longform_player, "_print_json", side_effect=lambda payload: captured.append(payload)):
                result = longform_player._cmd_play(manifest_path, player_bin="auto")
        finally:
            manifest_path.unlink(missing_ok=True)

        self.assertEqual(result, 0)
        mock_which.assert_not_called()
        mock_command.assert_not_called()
        mock_popen.assert_not_called()
        self.assertEqual(mock_save.call_args.args[0]["state"], "paused")
        self.assertEqual(mock_save.call_args.args[0]["player_bin"], "auto")
        self.assertEqual(captured[0]["state"], "paused")

    def test_cmd_play_resolves_and_persists_player_only_when_starting(self) -> None:
        manifest = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "start_position_seconds": 120.0,
            "tracks": [{"url": "http://example.test/ch1.mp3"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
            process = type("Process", (), {"pid": 321})()
            with patch.object(longform_player, "PLAYLIST_PATH", root / "playlist.ffconcat"), \
                 patch.object(longform_player, "LOG_PATH", root / "player.log"), \
                 patch.object(longform_player, "_ensure_state_dir"), \
                 patch.object(longform_player, "_stop_existing_process", return_value=[]), \
                 patch.object(longform_player, "which", side_effect=lambda value: "/opt/oracle-test/bin/ffplay" if value == "ffplay" else None) as mock_which, \
                 patch.object(longform_player.subprocess, "Popen", return_value=process) as mock_popen, \
                 patch.object(longform_player, "_save_state") as mock_save, \
                 patch.object(longform_player, "_print_json"):
                result = longform_player._cmd_play(manifest_path, player_bin="auto")

        self.assertEqual(result, 0)
        mock_which.assert_called_once_with("ffplay")
        self.assertEqual(mock_popen.call_args.args[0][0], "/opt/oracle-test/bin/ffplay")
        self.assertEqual(mock_save.call_args.args[0]["state"], "playing")
        self.assertEqual(mock_save.call_args.args[0]["player_bin"], "/opt/oracle-test/bin/ffplay")

    def test_cmd_resume_resolves_unresolved_player_selector(self) -> None:
        state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "position_seconds": 120.0,
            "state": "paused",
            "pid": None,
            "started_monotonic": None,
            "manifest": {
                "tracks": [{"url": "http://example.test/ch1.mp3"}],
                "duration_seconds": 1000.0,
            },
            "player_bin": "auto",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = type("Process", (), {"pid": 322})()
            with patch.object(longform_player, "PLAYLIST_PATH", root / "playlist.ffconcat"), \
                 patch.object(longform_player, "LOG_PATH", root / "player.log"), \
                 patch.object(longform_player, "_load_state", return_value=state), \
                 patch.object(longform_player, "_refresh_state"), \
                 patch.object(longform_player, "_stop_existing_process", return_value=[]), \
                 patch.object(longform_player, "which", side_effect=lambda value: "/opt/oracle-test/bin/mpv" if value == "mpv" else None), \
                 patch.object(longform_player.subprocess, "Popen", return_value=process), \
                 patch.object(longform_player, "_save_state") as mock_save, \
                 patch.object(longform_player, "_print_json"):
                result = longform_player._cmd_resume(player_bin="auto")

        self.assertEqual(result, 0)
        self.assertEqual(mock_save.call_args.args[0]["state"], "playing")
        self.assertEqual(mock_save.call_args.args[0]["player_bin"], "/opt/oracle-test/bin/mpv")

    def test_player_absent_immediate_play_and_resume_fail_at_execution_boundary(self) -> None:
        manifest = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "duration_seconds": 1000.0,
            "tracks": [{"url": "http://example.test/ch1.mp3"}],
        }
        paused_state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "duration_seconds": 1000.0,
            "position_seconds": 0.0,
            "state": "paused",
            "pid": None,
            "started_monotonic": None,
            "manifest": manifest,
            "player_bin": "auto",
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
            with patch.object(longform_player, "_ensure_state_dir"), \
                 patch.object(longform_player, "_stop_existing_process", return_value=[]), \
                 patch.object(longform_player, "which", return_value=None):
                with self.assertRaisesRegex(SystemExit, "No supported long-form player found"):
                    longform_player._cmd_play(manifest_path, player_bin="auto")

            with patch.object(longform_player, "_load_state", return_value=paused_state), \
                 patch.object(longform_player, "_refresh_state"), \
                 patch.object(longform_player, "_stop_existing_process", return_value=[]), \
                 patch.object(longform_player, "which", return_value=None):
                with self.assertRaisesRegex(SystemExit, "No supported long-form player found"):
                    longform_player._cmd_resume(player_bin="auto")

    def test_supported_longform_player_command_shapes(self) -> None:
        self.assertEqual(
            longform_player._build_player_command("/opt/oracle-test/bin/ffplay"),
            [
                "/opt/oracle-test/bin/ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "error",
                "-safe",
                "0",
                "-protocol_whitelist",
                "file,http,https,tcp,tls",
            ],
        )
        self.assertEqual(
            longform_player._build_player_command("/opt/oracle-test/bin/mpv"),
            [
                "/opt/oracle-test/bin/mpv",
                "--no-video",
                "--really-quiet",
                "--playlist",
            ],
        )

    def test_cmd_stop_fails_when_oracle_longform_processes_remain(self) -> None:
        state = {
            "playback_id": "book-1",
            "session_id": "session-1",
            "title": "Dune",
            "author": "Frank Herbert",
            "duration_seconds": 1000.0,
            "position_seconds": 120.0,
            "state": "playing",
            "pid": 111,
            "started_monotonic": 1.0,
            "manifest": {},
            "player_bin": "/usr/bin/ffplay",
        }
        stderr = io.StringIO()
        with patch.object(longform_player, "_load_state", return_value=state), \
             patch.object(longform_player, "_refresh_state"), \
             patch.object(longform_player, "_current_position", return_value=120.0), \
             patch.object(longform_player, "_save_state"), \
             patch.object(longform_player, "_stop_existing_process", return_value=[222]), \
             patch("sys.stderr", stderr):
            result = longform_player._cmd_stop()

        self.assertEqual(result, 1)
        self.assertIn("did not terminate all Oracle-managed playback processes", stderr.getvalue())

    def test_stop_existing_process_terminates_tracked_and_surviving_oracle_pids(self) -> None:
        state = {
            "pid": 111,
        }
        killed: list[int] = []
        with patch.object(longform_player, "_load_state", return_value=state), \
             patch.object(longform_player, "_is_process_alive", side_effect=lambda pid: pid in {111, 222, 333}), \
             patch.object(longform_player, "_enumerate_oracle_longform_player_pids", side_effect=[[222, 333], []]), \
             patch.object(longform_player, "_terminate_process", side_effect=lambda pid: killed.append(pid)):
            remaining = longform_player._stop_existing_process(remove_state=False)

        self.assertEqual(killed, [111, 222, 333])
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
