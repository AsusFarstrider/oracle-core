from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.modules.setdefault("numpy", __import__("numpy"))
sys.modules.setdefault("sounddevice", types.SimpleNamespace())
openwakeword_module = types.ModuleType("openwakeword")
openwakeword_model_module = types.ModuleType("openwakeword.model")
openwakeword_model_module.Model = object
openwakeword_module.model = openwakeword_model_module
sys.modules.setdefault("openwakeword", openwakeword_module)
sys.modules.setdefault("openwakeword.model", openwakeword_model_module)

MODULE_PATH = Path(__file__).resolve().parents[1] / "satellite" / "pi_runtime" / "audio" / "playback.py"
SPEC = importlib.util.spec_from_file_location("pi_runtime_audio_playback", MODULE_PATH)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("pi_runtime_audio_playback", MODULE)
SPEC.loader.exec_module(MODULE)


class AudioPlaybackRetryTests(unittest.TestCase):
    def test_play_array_releases_output_stream_after_wait(self) -> None:
        stream = types.SimpleNamespace(
            stop=unittest.mock.Mock(),
            close=unittest.mock.Mock(),
        )

        with patch.object(MODULE.sd, "play", create=True) as mock_play, \
             patch.object(MODULE.sd, "wait", create=True) as mock_wait, \
             patch.object(MODULE.sd, "get_stream", return_value=stream, create=True):
            MODULE._play_array(b"audio", sample_rate=48000, output_device_index=None)

        mock_play.assert_called_once_with(b"audio", samplerate=48000, device=None)
        mock_wait.assert_called_once()
        stream.stop.assert_called_once_with(ignore_errors=True)
        stream.close.assert_called_once_with(ignore_errors=True)

    def test_play_array_releases_output_stream_when_wait_fails(self) -> None:
        stream = types.SimpleNamespace(
            stop=unittest.mock.Mock(),
            close=unittest.mock.Mock(),
        )

        with patch.object(MODULE.sd, "play", create=True), \
             patch.object(MODULE.sd, "wait", side_effect=RuntimeError("wait failed"), create=True), \
             patch.object(MODULE.sd, "get_stream", return_value=stream, create=True):
            with self.assertRaisesRegex(RuntimeError, "wait failed"):
                MODULE._play_array(b"audio", sample_rate=48000, output_device_index=None)

        stream.stop.assert_called_once_with(ignore_errors=True)
        stream.close.assert_called_once_with(ignore_errors=True)

    def test_play_with_retry_recovers_from_device_unavailable(self) -> None:
        calls = {"count": 0}

        def flaky_play() -> None:
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("Device unavailable [PaErrorCode -9985]")

        with patch.object(MODULE.time, "sleep") as mock_sleep:
            MODULE._play_with_retry(flaky_play)

        self.assertEqual(calls["count"], 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_play_with_retry_does_not_retry_non_device_errors(self) -> None:
        calls = {"count": 0}

        def broken_play() -> None:
            calls["count"] += 1
            raise RuntimeError("Some other playback failure")

        with patch.object(MODULE.time, "sleep") as mock_sleep:
            with self.assertRaisesRegex(RuntimeError, "Some other playback failure"):
                MODULE._play_with_retry(broken_play)

        self.assertEqual(calls["count"], 1)
        mock_sleep.assert_not_called()

    def test_reply_retry_profile_uses_longer_window_for_playback_handoff(self) -> None:
        attempts, retry_delay = MODULE._reply_retry_profile(playback_handoff_active=True)

        self.assertEqual(attempts, 6)
        self.assertEqual(retry_delay, 0.35)

    def test_reply_retry_profile_uses_default_window_without_playback_handoff(self) -> None:
        attempts, retry_delay = MODULE._reply_retry_profile(playback_handoff_active=False)

        self.assertEqual(attempts, 3)
        self.assertEqual(retry_delay, 0.2)


if __name__ == "__main__":
    unittest.main()
