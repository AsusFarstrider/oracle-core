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
    def test_collect_followup_pre_roll_frames_reads_available_audio(self) -> None:
        frame_queue: queue.Queue[bytes] = queue.Queue()
        frame_queue.put(b"\x01\x00\x02\x00")
        frame_queue.put(b"\x03\x00\x04\x00")

        frames = collect_followup_pre_roll_frames(frame_queue, max_frames=3, timeout_seconds=0.01)

        self.assertEqual(len(frames), 2)

    def test_resolve_audio_input_config_prefers_alsa_device(self) -> None:
        args = types.SimpleNamespace(input_alsa_device="plughw:CARD=acp,DEV=0", input_device_index=1)

        config = resolve_audio_input_config(args)

        self.assertEqual(config.backend, "alsa_arecord")
        self.assertEqual(config.device, "plughw:CARD=acp,DEV=0")
        self.assertTrue(config.explicitly_configured)

    def test_resolve_audio_input_config_uses_device_index_when_present(self) -> None:
        args = types.SimpleNamespace(input_alsa_device=None, input_device_index=1)

        config = resolve_audio_input_config(args)

        self.assertEqual(config.backend, "portaudio_device_index")
        self.assertEqual(config.device, 1)
        self.assertEqual(config.label, "1")
        self.assertTrue(config.explicitly_configured)

    def test_resolve_audio_input_config_uses_default_when_not_configured(self) -> None:
        args = types.SimpleNamespace(input_alsa_device=None, input_device_index=None)

        config = resolve_audio_input_config(args)

        self.assertEqual(
            config,
            AudioInputConfig(
                backend="default_input_device",
                device=None,
                label="default",
                explicitly_configured=False,
            ),
        )

    @patch.dict("os.environ", {"ORACLE_URL": "http://legacy.example:8011"}, clear=True)
    @patch("pi_runtime.config_runtime.resolve_audio_input_config")
    @patch("pi_runtime.config_runtime.open_input_stream")
    def test_build_satellite_runtime_report_warns_on_deprecated_env_and_probes_audio(
        self,
        mock_open_input_stream,
        mock_resolve_audio_input_config,
    ) -> None:
        class _DummyContext:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        mock_resolve_audio_input_config.return_value = AudioInputConfig(
            backend="default_input_device",
            device=None,
            label="default",
            explicitly_configured=False,
        )
        mock_open_input_stream.return_value = _DummyContext()
        args = types.SimpleNamespace(
            oracle_url="http://brain:8011",
            source="test_satellite_alpha",
            model_path=str(Path(__file__).resolve()),
            wake_threshold=0.2,
            wake_log_threshold=0.1,
            wake_playback_threshold=0.16,
            wake_playback_log_threshold=0.09,
            wake_playback_poll_seconds=0.35,
            wake_playback_hold_seconds=1.25,
            wake_playback_consecutive_frames=2,
            music_duck_trigger_threshold=0.12,
            music_duck_volume=18,
            music_duck_stage_one_volume=28,
            music_duck_stage_two_volume=22,
            music_duck_stage_three_volume=18,
            wake_cooldown_seconds=6.0,
            wake_retry_cooldown_seconds=1.0,
            input_gain=2.0,
            playback_gain=0.35,
            conversation_timeout_seconds=90.0,
            alerts_poll_seconds=2.0,
            silence_seconds=0.75,
            post_playback_block_seconds=2.0,
            max_record_seconds=8.0,
            min_speech_seconds=0.2,
            followup_silence_seconds=0.3,
            followup_max_record_seconds=4.0,
            followup_speech_start_timeout_seconds=2.5,
            music_duck_max_seconds=4.0,
            playback_interrupt_settle_seconds=0.35,
        )

        findings = build_satellite_runtime_report(args, probe_audio_input=True)

        self.assertTrue(any(item["status"] == "deprecated_env" for item in findings))
        self.assertTrue(any(item["status"] == "resolved_input" for item in findings))
        mock_open_input_stream.assert_called_once()

    @patch("pi_runtime.config_runtime.resolve_audio_input_config")
    def test_build_satellite_runtime_report_errors_on_missing_model(
        self,
        mock_resolve_audio_input_config,
    ) -> None:
        mock_resolve_audio_input_config.return_value = AudioInputConfig(
            backend="default_input_device",
            device=None,
            label="default",
            explicitly_configured=False,
        )
        args = types.SimpleNamespace(
            oracle_url="http://brain:8011",
            source="test_satellite_alpha",
            model_path="/tmp/not-a-real-wake-model.onnx",
            wake_threshold=0.2,
            wake_log_threshold=0.1,
            wake_playback_threshold=0.16,
            wake_playback_log_threshold=0.09,
            wake_playback_poll_seconds=0.35,
            wake_playback_hold_seconds=1.25,
            wake_playback_consecutive_frames=2,
            music_duck_trigger_threshold=0.12,
            music_duck_volume=18,
            music_duck_stage_one_volume=28,
            music_duck_stage_two_volume=22,
            music_duck_stage_three_volume=18,
            wake_cooldown_seconds=6.0,
            wake_retry_cooldown_seconds=1.0,
            input_gain=2.0,
            playback_gain=0.35,
            conversation_timeout_seconds=90.0,
            alerts_poll_seconds=2.0,
            silence_seconds=0.75,
            post_playback_block_seconds=2.0,
            max_record_seconds=8.0,
            min_speech_seconds=0.2,
            followup_silence_seconds=0.3,
            followup_max_record_seconds=4.0,
            followup_speech_start_timeout_seconds=2.5,
            music_duck_max_seconds=4.0,
            playback_interrupt_settle_seconds=0.35,
        )

        findings = build_satellite_runtime_report(args, probe_audio_input=False)

        self.assertTrue(any(item["setting"] == "model_path" and item["severity"] == "error" for item in findings))

    def test_build_satellite_runtime_report_rejects_invalid_phase_d_wake_tuning_values(self) -> None:
        args = types.SimpleNamespace(
            oracle_url="http://brain:8011",
            source="test_satellite_alpha",
            model_path=str(Path(__file__).resolve()),
            wake_threshold=0.2,
            wake_log_threshold=0.1,
            wake_playback_threshold=1.2,
            wake_playback_log_threshold=-0.1,
            wake_playback_poll_seconds=-0.35,
            wake_playback_hold_seconds=-1.25,
            wake_playback_consecutive_frames=0,
            music_duck_trigger_threshold=0.12,
            music_duck_volume=101,
            music_duck_stage_one_volume=-1,
            music_duck_stage_two_volume=22,
            music_duck_stage_three_volume=18,
            wake_cooldown_seconds=6.0,
            wake_retry_cooldown_seconds=1.0,
            input_gain=2.0,
            playback_gain=0.35,
            conversation_timeout_seconds=90.0,
            alerts_poll_seconds=2.0,
            silence_seconds=0.75,
            post_playback_block_seconds=2.0,
            max_record_seconds=8.0,
            min_speech_seconds=0.2,
            followup_silence_seconds=0.3,
            followup_max_record_seconds=4.0,
            followup_speech_start_timeout_seconds=2.5,
            music_duck_max_seconds=4.0,
            playback_interrupt_settle_seconds=0.35,
        )

        with patch.dict("os.environ", {"ORACLE_WAKE_CAPTURE_SYNC_TRANSPORT": "ftp"}, clear=True):
            findings = build_satellite_runtime_report(args, probe_audio_input=False)
        settings = {item["setting"] for item in findings if item["severity"] == "error"}

        self.assertIn("wake_playback_threshold", settings)
        self.assertIn("wake_playback_log_threshold", settings)
        self.assertIn("wake_playback_poll_seconds", settings)
        self.assertIn("wake_playback_hold_seconds", settings)
        self.assertIn("wake_playback_consecutive_frames", settings)
        self.assertIn("music_duck_volume", settings)
        self.assertIn("music_duck_stage_one_volume", settings)
        self.assertIn("wake_capture_sync_transport", settings)

    def test_config_http_handler_returns_text_when_requested(self) -> None:
        handler = _ConfigRequestHandler.__new__(_ConfigRequestHandler)
        captured: dict[str, object] = {}
        handler.path = "/health/config?format=text"
        handler.headers = {}
        handler.server = types.SimpleNamespace(
            build_config_report_payload=lambda: {"ok": True, "service": "oracle-pi-satellite", "sections": []},
            render_config_report_text=lambda: "Pi satellite config check:\n- OK",
        )
        handler._write_json = lambda status, payload: captured.update(json_status=int(status), payload=payload)
        handler._write_text = lambda status, payload: captured.update(status=int(status), text=payload)

        handler.do_GET()

        self.assertEqual(captured["status"], 200)
        self.assertIn("Pi satellite config check:", str(captured["text"]))

    def test_capture_utterance_after_wake_honors_speech_start_timeout(self) -> None:
        frame_queue: queue.Queue[bytes] = queue.Queue()
        silent_frame = (b"\x00\x00" * 1280)
        for _ in range(40):
            frame_queue.put(silent_frame)

        with patch("pi_runtime.wake.frame_rms", return_value=0.0):
            outcome = capture_utterance_after_wake(
                frame_queue=frame_queue,
                pre_roll_frames=[],
                vad_threshold=0.035,
                vad_noise_multiplier=1.6,
                vad_noise_offset=0.006,
                vad_release_multiplier=1.15,
                vad_release_offset=0.003,
                vad_max_speech_threshold=0.42,
                vad_max_silence_threshold=0.30,
                silence_seconds=0.3,
                max_record_seconds=4.0,
                min_speech_seconds=0.2,
                input_gain=1.0,
                speech_start_timeout_seconds=0.2,
            )

        self.assertIsNone(outcome.pcm_bytes)
        self.assertEqual(outcome.stop_reason, "insufficient_speech")

    def test_capture_utterance_after_wake_drops_false_start_after_silence(self) -> None:
        frame_queue: queue.Queue[bytes] = queue.Queue()
        frame = b"\x00\x00" * 1280
        for _ in range(40):
            frame_queue.put(frame)
        energies = [0.05, 0.0, 0.0, 0.0, 0.0]

        def fake_rms(_frame):
            return energies.pop(0) if energies else 0.0

        with patch("pi_runtime.wake.frame_rms", side_effect=fake_rms):
            outcome = capture_utterance_after_wake(
                frame_queue=frame_queue,
                pre_roll_frames=[],
                vad_threshold=0.035,
                vad_noise_multiplier=1.6,
                vad_noise_offset=0.006,
                vad_release_multiplier=1.15,
                vad_release_offset=0.003,
                vad_max_speech_threshold=0.42,
                vad_max_silence_threshold=0.30,
                silence_seconds=0.3,
                max_record_seconds=8.0,
                min_speech_seconds=0.2,
                input_gain=1.0,
                speech_start_timeout_seconds=1.6,
                false_start_silence_seconds=0.24,
            )

        self.assertIsNone(outcome.pcm_bytes)
        self.assertEqual(outcome.stop_reason, "insufficient_speech")
        self.assertLess(outcome.total_frames, 10)

    def test_capture_utterance_after_wake_closes_on_post_speech_release_band(self) -> None:
        frame_queue: queue.Queue[bytes] = queue.Queue()
        frame = b"\x00\x00" * 1280
        for _ in range(40):
            frame_queue.put(frame)
        energies = [0.08, 0.08, 0.08, 0.04, 0.04, 0.04]

        def fake_rms(_frame):
            return energies.pop(0) if energies else 0.04

        with patch("pi_runtime.wake.frame_rms", side_effect=fake_rms):
            outcome = capture_utterance_after_wake(
                frame_queue=frame_queue,
                pre_roll_frames=[],
                vad_threshold=0.05,
                vad_noise_multiplier=1.6,
                vad_noise_offset=0.006,
                vad_release_multiplier=1.15,
                vad_release_offset=0.003,
                vad_max_speech_threshold=0.42,
                vad_max_silence_threshold=0.30,
                silence_seconds=0.24,
                max_record_seconds=2.0,
                min_speech_seconds=0.2,
                input_gain=1.0,
                speech_start_timeout_seconds=1.6,
                false_start_silence_seconds=0.24,
            )

        self.assertIsNotNone(outcome.pcm_bytes)
        self.assertEqual(outcome.stop_reason, "silence")
        self.assertEqual(outcome.total_frames, 6)


if __name__ == "__main__":
    unittest.main()
