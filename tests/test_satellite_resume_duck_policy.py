from __future__ import annotations

import queue
import logging
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from oracle_runtime_config import choose_config_report_format

numpy_module = types.ModuleType("numpy")


class _DummyArray:
    def __init__(self, size: int) -> None:
        self.size = size

    def copy(self):
        return self


def _dummy_frombuffer(payload: bytes, dtype=None):
    return _DummyArray(len(payload) // 2)


class _DummyConcatenated:
    def astype(self, dtype=None):
        return self

    def tobytes(self):
        return b"pcm"


def _dummy_concatenate(_arrays):
    return _DummyConcatenated()


numpy_module.frombuffer = _dummy_frombuffer
numpy_module.concatenate = _dummy_concatenate
numpy_module.int16 = "int16"
sys.modules.setdefault("numpy", numpy_module)
sys.modules.setdefault("requests", types.ModuleType("requests"))
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))
openwakeword_module = types.ModuleType("openwakeword")
openwakeword_model_module = types.ModuleType("openwakeword.model")


class _DummyModel:
    pass


openwakeword_model_module.Model = _DummyModel
openwakeword_module.model = openwakeword_model_module
sys.modules.setdefault("openwakeword", openwakeword_module)
sys.modules.setdefault("openwakeword.model", openwakeword_model_module)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "satellite"))

import pi_runtime.request_runtime as request_runtime
import pi_runtime.pipeline_runtime as pipeline_runtime
from pi_wake_satellite import (
    AudioInputConfig,
    CommandOutcome,
    build_satellite_runtime_report,
    capture_utterance_after_wake,
    collect_followup_pre_roll_frames,
    is_transport_playback_command,
    resolve_audio_input_config,
    resolve_input_device,
    should_listen_for_followup_reply,
    should_resume_after_reply_for_transport_command,
)
from pi_runtime.config_http import _ConfigRequestHandler
from pi_runtime.local_control import DuckedMusicController


class SatellitePlaybackResumeTests(unittest.TestCase):
    def test_audiobook_resume_blocks_auto_resume(self) -> None:
        outcome = CommandOutcome(
            transcript="resume my audiobook",
            spoken_reply="Resuming Dune by Frank Herbert.",
            raw_response={
                "dispatch": {
                    "target": "audiobook",
                    "result": {"action": "resume"},
                }
            },
            status="executed",
            effects={"satellite_playback": {"disposition": "started", "target_source_id": None}},
        )
        self.assertTrue(is_transport_playback_command(outcome))

    @patch("pi_runtime.local_control.send_local_control_command")
    @patch("pi_runtime.local_control.fetch_local_music_state", return_value={"state": "playing", "playing": True})
    @patch("pi_runtime.local_control.fetch_local_longform_state", return_value={"state": "stopped", "playing": False})
    def test_interrupt_local_playback_uses_pause_for_music(
        self,
        _mock_longform_state,
        _mock_music_state,
        mock_send_local_control_command,
    ) -> None:
        logger = __import__("logging").getLogger("interrupt-music-stop-test")

        interrupted = __import__("pi_runtime.local_control", fromlist=["interrupt_local_playback"]).interrupt_local_playback(
            control_url="http://127.0.0.1:8021",
            api_key="key",
            settle_seconds=0.0,
            logger=logger,
        )

        self.assertEqual(mock_send_local_control_command.call_args_list[0].args, ("http://127.0.0.1:8021", "key", "interrupt_for_oracle"))
        self.assertEqual(mock_send_local_control_command.call_args_list[1].args, ("http://127.0.0.1:8021", "key", "pause"))
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0].kind, "music")
        self.assertEqual(interrupted[0].resume_action, "resume")

    def test_failed_audiobook_pause_still_blocks_auto_resume(self) -> None:
        outcome = CommandOutcome(
            transcript="pause audiobook",
            spoken_reply="I couldn't complete that audiobook request.",
            raw_response={
                "dispatch": {
                    "target": "audiobook",
                    "status": "failed",
                    "result": {"action": "pause_longform_audio"},
                }
            },
            status="failed",
            failure_code="satellite_command_failed",
            effects={"satellite_playback": {"disposition": "failed", "target_source_id": None}},
        )
        self.assertTrue(is_transport_playback_command(outcome))

    @patch("pi_runtime.local_control.send_local_music_command")
    @patch(
        "pi_runtime.local_control.interrupt_local_playback",
        return_value=[
            __import__("pi_runtime.models", fromlist=["InterruptedPlayback"]).InterruptedPlayback(
                kind="music",
                backend_type="oracle_native_music",
                session_id="track-1",
                resume_action="restore_volume",
                restore_volume_level=42,
            )
        ],
    )
    def test_ducked_music_controller_uses_stage_volume_targets(
        self,
        mock_interrupt_local_playback,
        mock_send_local_music_command,
    ) -> None:
        controller = DuckedMusicController(
            types.SimpleNamespace(
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
                music_duck_max_seconds=4.0,
                music_duck_stage_one_volume=30,
                music_duck_stage_two_volume=24,
                music_duck_stage_three_volume=18,
            ),
            __import__("logging").getLogger("duck-stage-test"),
        )

        controller.apply_duck_stage(2)

        mock_interrupt_local_playback.assert_called_once()
        mock_send_local_music_command.assert_called_once_with(
            "http://127.0.0.1:8021",
            "secret",
            "set_volume",
            {"level": 24},
        )

    @patch("pi_runtime.local_control.send_local_music_command")
    @patch(
        "pi_runtime.local_control.interrupt_local_playback",
        return_value=[
            __import__("pi_runtime.models", fromlist=["InterruptedPlayback"]).InterruptedPlayback(
                kind="music",
                backend_type="oracle_native_music",
                session_id="track-1",
                resume_action="restore_volume",
                restore_volume_level=42,
            )
        ],
    )
    def test_ducked_music_controller_only_deepens_active_duck(
        self,
        mock_interrupt_local_playback,
        mock_send_local_music_command,
    ) -> None:
        controller = DuckedMusicController(
            types.SimpleNamespace(
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
                music_duck_max_seconds=4.0,
                music_duck_stage_one_volume=30,
                music_duck_stage_two_volume=24,
                music_duck_stage_three_volume=18,
            ),
            __import__("logging").getLogger("duck-stage-test"),
        )

        controller.apply_duck_stage(1)
        controller.apply_duck_stage(3)
        controller.apply_duck_stage(1)

        self.assertEqual(mock_interrupt_local_playback.call_count, 1)
        self.assertEqual(mock_send_local_music_command.call_count, 2)
        self.assertEqual(mock_send_local_music_command.call_args_list[0].args[3], {"level": 30})
        self.assertEqual(mock_send_local_music_command.call_args_list[1].args[3], {"level": 18})

    @patch("pi_runtime.local_control.resume_interrupted_local_playback")
    def test_ducked_music_controller_restores_through_interrupted_playback(self, mock_resume) -> None:
        controller = DuckedMusicController(
            types.SimpleNamespace(
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
                music_duck_max_seconds=4.0,
                music_duck_stage_one_volume=30,
                music_duck_stage_two_volume=24,
                music_duck_stage_three_volume=18,
            ),
            __import__("logging").getLogger("duck-stage-test"),
        )
        controller._interrupted_playback = [
            __import__("pi_runtime.models", fromlist=["InterruptedPlayback"]).InterruptedPlayback(
                kind="music",
                backend_type="oracle_native_music",
                session_id="track-1",
                resume_action="restore_volume",
                restore_volume_level=42,
            )
        ]

        controller.maybe_restore(force=True)

        self.assertEqual(mock_resume.call_count, 1)


if __name__ == "__main__":
    unittest.main()
