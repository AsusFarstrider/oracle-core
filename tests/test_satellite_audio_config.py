from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.modules.setdefault("sounddevice", types.SimpleNamespace(query_devices=lambda: []))

MODULE_PATH = Path(__file__).resolve().parents[1] / "satellite" / "pi_runtime" / "audio" / "config.py"
SPEC = importlib.util.spec_from_file_location("pi_runtime_audio_config", MODULE_PATH)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("pi_runtime_audio_config", MODULE)
SPEC.loader.exec_module(MODULE)


def _args(**kwargs):
    defaults = {
        "input_alsa_device": None,
        "input_device_index": None,
        "input_device_name": None,
        "output_device_index": None,
        "output_device_name": None,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


class SatelliteAudioConfigTests(unittest.TestCase):
    def test_input_index_still_resolves_to_portaudio_index(self) -> None:
        config = MODULE.resolve_audio_input_config(_args(input_device_index=4))

        self.assertEqual(config.backend, "portaudio_device_index")
        self.assertEqual(config.device, 4)
        self.assertTrue(config.explicitly_configured)

    def test_alsa_input_still_wins_over_portaudio_name(self) -> None:
        config = MODULE.resolve_audio_input_config(
            _args(input_alsa_device="plughw:CARD=Bar,DEV=0", input_device_name="Surface Mic")
        )

        self.assertEqual(config.backend, "alsa_arecord")
        self.assertEqual(config.device, "plughw:CARD=Bar,DEV=0")

    def test_default_input_stays_dynamic_default(self) -> None:
        config = MODULE.resolve_audio_input_config(_args())

        self.assertEqual(config.backend, "default_input_device")
        self.assertIsNone(config.device)
        self.assertFalse(config.explicitly_configured)

    def test_input_name_resolves_to_unambiguous_portaudio_device(self) -> None:
        devices = [
            {"name": "Surface Microphone", "max_input_channels": 1, "max_output_channels": 0},
            {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2},
        ]

        with patch.object(MODULE.sd, "query_devices", return_value=devices, create=True):
            config = MODULE.resolve_audio_input_config(_args(input_device_name="surface mic"))

        self.assertEqual(config.backend, "portaudio_device_name")
        self.assertEqual(config.device, 0)
        self.assertTrue(config.explicitly_configured)

    def test_output_index_resolves_to_pinned_output(self) -> None:
        config = MODULE.resolve_audio_output_config(_args(output_device_index=7))

        self.assertEqual(config.backend, "portaudio_output_device_index")
        self.assertEqual(config.device, 7)
        self.assertTrue(config.explicitly_configured)

    def test_missing_output_override_stays_dynamic_default(self) -> None:
        config = MODULE.resolve_audio_output_config(_args())

        self.assertEqual(config.backend, "default_output_device")
        self.assertIsNone(config.device)
        self.assertFalse(config.explicitly_configured)

    def test_output_name_resolves_to_unambiguous_portaudio_device(self) -> None:
        devices = [
            {"name": "Surface Microphone", "max_input_channels": 1, "max_output_channels": 0},
            {"name": "USB Speakers", "max_input_channels": 0, "max_output_channels": 2},
        ]

        with patch.object(MODULE.sd, "query_devices", return_value=devices, create=True):
            config = MODULE.resolve_audio_output_config(_args(output_device_name="usb"))

        self.assertEqual(config.backend, "portaudio_output_device_name")
        self.assertEqual(config.device, 1)
        self.assertTrue(config.explicitly_configured)

    def test_ambiguous_output_name_fails_closed(self) -> None:
        devices = [
            {"name": "USB Speakers", "max_input_channels": 0, "max_output_channels": 2},
            {"name": "USB Headphones", "max_input_channels": 0, "max_output_channels": 2},
        ]

        with patch.object(MODULE.sd, "query_devices", return_value=devices, create=True):
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                MODULE.resolve_audio_output_config(_args(output_device_name="USB"))


if __name__ == "__main__":
    unittest.main()
