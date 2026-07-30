from __future__ import annotations

import unittest
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("sounddevice", types.SimpleNamespace())

MODULE_PATH = Path(__file__).resolve().parents[1] / "satellite" / "pi_runtime" / "audio" / "playback.py"
SPEC = importlib.util.spec_from_file_location("pi_runtime_audio_playback", MODULE_PATH)
assert SPEC is not None
playback = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("pi_runtime_audio_playback", playback)
SPEC.loader.exec_module(playback)


class SatelliteAudioPlaybackTests(unittest.TestCase):
    def test_short_tone_keeps_explicit_output_device(self) -> None:
        with patch.object(playback.platform, "system", return_value="Windows"):
            self.assertEqual(playback._resolve_short_tone_output_device(4), 4)

    def test_short_tone_uses_none_on_non_windows_default(self) -> None:
        with patch.object(playback.platform, "system", return_value="Linux"):
            self.assertIsNone(playback._resolve_short_tone_output_device(None))

    def test_short_tone_uses_windows_wasapi_default_output(self) -> None:
        hostapis = (
            {"name": "MME", "default_output_device": 3},
            {"name": "Windows WASAPI", "default_output_device": 11},
        )
        with patch.object(playback.platform, "system", return_value="Windows"), patch.object(
            playback.sd, "query_hostapis", return_value=hostapis, create=True
        ):
            self.assertEqual(playback._resolve_short_tone_output_device(None), 11)


if __name__ == "__main__":
    unittest.main()
