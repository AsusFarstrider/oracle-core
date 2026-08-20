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
    def test_followup_listen_cue_uses_handoff_retry_profile(self) -> None:
        playback = __import__("pi_runtime.audio.playback", fromlist=["play_followup_listen_cue"])
        self.assertEqual(playback._reply_retry_profile(playback_handoff_active=True), (6, 0.35))

    def test_reply_wake_interrupt_grace_ignores_immediate_wake_score(self) -> None:
        playback = __import__("pi_runtime.audio.playback", fromlist=["play_wav_bytes_with_wake_interrupt"])
        active_stream = types.SimpleNamespace(active=True)
        inactive_stream = types.SimpleNamespace(active=False)
        wake_model = types.SimpleNamespace(predict=lambda frame: {"oracle": 0.8})
        frame_queue: queue.Queue[bytes] = queue.Queue()
        frame_queue.put(b"\x00\x00")

        with patch.object(playback, "decode_wav_bytes", return_value=(b"audio", 48000)), \
             patch.object(playback, "_play_with_retry"), \
             patch.object(playback, "clear_reply_audio_stop_request"), \
             patch.object(playback, "write_reply_audio_state"), \
             patch.object(playback, "reply_audio_stop_requested", return_value=False), \
             patch.object(playback.sd, "get_stream", side_effect=[active_stream, inactive_stream], create=True), \
             patch.object(playback.sd, "wait", create=True), \
             patch.object(playback.sd, "stop", create=True) as mock_stop, \
             patch.object(playback.time, "monotonic", side_effect=[0.0, 0.1, 0.1]):
            interrupted = playback.play_wav_bytes_with_wake_interrupt(
                b"wav",
                None,
                0.3,
                reply_audio_state_path="/tmp/reply-state.json",
                reply_audio_stop_path="/tmp/reply-stop.flag",
                frame_queue=frame_queue,
                pre_roll=[],
                wake_model=wake_model,
                wake_key="oracle",
                wake_threshold=0.5,
                input_gain=1.0,
                interrupt_grace_seconds=0.35,
            )

        self.assertFalse(interrupted)
        mock_stop.assert_not_called()

    def test_pending_confirmation_triggers_followup_listen(self) -> None:
        outcome = CommandOutcome(
            transcript="unlock the side entry",
            spoken_reply="This will unlock a door. Say confirm to proceed or cancel to stop.",
            raw_response={
                "dispatch": {
                    "target": "home_assistant",
                    "status": "pending_confirmation",
                    "result": {"prompt": "confirm?"},
                }
            },
            status="pending_confirmation",
            effects={"follow_up": {"expected": True, "kind": "confirmation"}},
        )
        self.assertTrue(should_listen_for_followup_reply(outcome))

    def test_pending_clarification_triggers_followup_listen(self) -> None:
        outcome = CommandOutcome(
            transcript="play river heaven",
            spoken_reply="Did you mean the audiobook Heaven's River by Dennis E. Taylor?",
            raw_response={
                "dispatch": {
                    "target": "music",
                    "status": "pending_clarification",
                    "result": {"prompt": "Did you mean Heaven's River?"},
                }
            },
            status="pending_clarification",
            effects={"follow_up": {"expected": True, "kind": "clarification"}},
        )
        self.assertTrue(should_listen_for_followup_reply(outcome))

    def test_executed_reply_does_not_trigger_followup_listen(self) -> None:
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 1:19 PM.",
            raw_response={
                "dispatch": {
                    "target": "system",
                    "status": "executed",
                    "result": {"action": "current_time"},
                }
            },
        )
        self.assertFalse(should_listen_for_followup_reply(outcome))

    def test_non_media_command_does_not_block_resume(self) -> None:
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 1:19 PM.",
            raw_response={
                "dispatch": {
                    "target": "system",
                    "result": {"action": "current_time"},
                }
            },
        )
        self.assertFalse(is_transport_playback_command(outcome))

    def test_music_pause_blocks_auto_resume(self) -> None:
        outcome = CommandOutcome(
            transcript="pause music",
            spoken_reply="Paused.",
            raw_response={
                "dispatch": {
                    "target": "music",
                    "result": {"action": "pause"},
                }
            },
            status="executed",
            effects={"satellite_playback": {"disposition": "updated", "target_source_id": None}},
        )
        self.assertTrue(is_transport_playback_command(outcome))

    def test_music_now_playing_keeps_auto_resume(self) -> None:
        outcome = CommandOutcome(
            transcript="what song is this",
            spoken_reply="This is Heroes by David Bowie.",
            raw_response={
                "dispatch": {
                    "target": "music",
                    "result": {"action": "what_is_playing"},
                }
            },
        )
        self.assertFalse(is_transport_playback_command(outcome))

    @patch("pi_runtime.request_runtime.request_tts")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt")
    @patch("pi_runtime.request_runtime.get_active_session_id")
    def test_request_pipeline_logs_trace_events(
        self,
        mock_get_active_session_id,
        mock_send_stt,
        mock_send_command,
        mock_request_tts,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-trace-test")
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            brain_api_key="brain-token",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(active_session_id=None, last_conversation_activity_at=None)
        mock_send_stt.return_value = "what time is it"
        mock_get_active_session_id.return_value = ("session-1", 123.0)
        mock_send_command.return_value = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={
                "route": {"target": "system"},
                "dispatch": {
                    "target": "system",
                    "hook": "system.current_time",
                    "status": "executed",
                    "result": {"action": "current_time"},
                },
            },
            status="executed",
            source_id="test_satellite_alpha",
            session_id="session-1",
            trace_id="trace-1",
            effects={"satellite_playback": {"disposition": "stopped", "target_source_id": None}},
        )
        mock_request_tts.return_value = b"wav"

        with self.assertLogs("satellite-trace-test", level="INFO") as captured:
            result = request_runtime.run_request_pipeline(
                args=args,
                logger=logger,
                runtime_state=runtime_state,
                pcm_bytes=b"\x00\x00" * 64,
            )

        output = "\n".join(captured.output)
        self.assertIn("transcript_obtained", output)
        self.assertIn("command_response_received", output)
        self.assertIn("source=test_satellite_alpha", output)
        self.assertIn("session_id=session-1", output)
        self.assertIn("dispatch_hook=canonical_conversation_result", output)
        self.assertIn("status=executed", output)
        self.assertEqual(result.outcome.spoken_reply, "It is 3 PM.")

    @patch("pi_runtime.request_runtime.send_stt", side_effect=RuntimeError("stt backend offline"))
    def test_request_pipeline_raises_stt_failure_with_capture_context(self, _mock_send_stt) -> None:
        logger = __import__("logging").getLogger("satellite-stt-failure-test")
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(active_session_id="session-1", last_conversation_activity_at=None)

        with self.assertLogs("satellite-stt-failure-test", level="ERROR") as captured:
            with self.assertRaises(request_runtime.RequestPipelineError) as exc_info:
                request_runtime.run_request_pipeline(
                    args=args,
                    logger=logger,
                    runtime_state=runtime_state,
                    pcm_bytes=b"\x00\x00" * 64,
                )

        self.assertEqual(exc_info.exception.kind, "stt_failed")
        self.assertTrue(exc_info.exception.should_play_error_tone)
        self.assertTrue(exc_info.exception.capture_context["capture_succeeded"])
        output = "\n".join(captured.output)
        self.assertIn("stt_failed", output)
        self.assertIn("capture_bytes=", output)

    @patch("pi_runtime.request_runtime.send_command", side_effect=RuntimeError("brain offline"))
    @patch("pi_runtime.request_runtime.send_stt", return_value="what time is it")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("session-1", 123.0))
    def test_request_pipeline_raises_brain_request_failure(
        self,
        _mock_session,
        _mock_send_stt,
        _mock_send_command,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-brain-failure-test")
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(active_session_id=None, last_conversation_activity_at=None)

        with self.assertLogs("satellite-brain-failure-test", level="ERROR") as captured:
            with self.assertRaises(request_runtime.RequestPipelineError) as exc_info:
                request_runtime.run_request_pipeline(
                    args=args,
                    logger=logger,
                    runtime_state=runtime_state,
                    pcm_bytes=b"\x00\x00" * 64,
                )

        self.assertEqual(exc_info.exception.kind, "brain_request_failed")
        self.assertTrue(exc_info.exception.should_play_error_tone)
        self.assertIn("brain_request_failed", "\n".join(captured.output))

    @patch("pi_runtime.request_runtime.request_tts", side_effect=RuntimeError("tts failed"))
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt", return_value="what time is it")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("session-1", 123.0))
    def test_request_pipeline_logs_tts_failure_with_missed_reply_text(
        self,
        _mock_session,
        _mock_send_stt,
        mock_send_command,
        _mock_request_tts,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-tts-failure-test")
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(active_session_id=None, last_conversation_activity_at=None)
        mock_send_command.return_value = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={
                "route": {"target": "system"},
                "dispatch": {
                    "target": "system",
                    "hook": "system.current_time",
                    "status": "executed",
                    "result": {"action": "current_time"},
                },
            },
            status="failed",
            failure_code="satellite_command_failed",
            source_id="test_satellite_alpha",
            session_id="session-1",
            trace_id="trace-failed",
            effects={"satellite_playback": {"disposition": "failed", "target_source_id": None}},
        )

        with self.assertLogs("satellite-tts-failure-test", level="ERROR") as captured:
            with self.assertRaises(request_runtime.RequestPipelineError) as exc_info:
                request_runtime.run_request_pipeline(
                    args=args,
                    logger=logger,
                    runtime_state=runtime_state,
                    pcm_bytes=b"\x00\x00" * 64,
                )

        self.assertEqual(exc_info.exception.kind, "tts_failed")
        self.assertEqual(exc_info.exception.reply_text, "It is 3 PM.")
        self.assertIn("reply_text='It is 3 PM.'", "\n".join(captured.output))

    @patch("pi_runtime.request_runtime.request_tts")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt", return_value="pause music")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("session-1", 123.0))
    def test_request_pipeline_logs_failed_dispatch_without_raising(
        self,
        _mock_session,
        _mock_send_stt,
        mock_send_command,
        mock_request_tts,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-dispatch-failure-test")
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(active_session_id=None, last_conversation_activity_at=None)
        mock_send_command.return_value = CommandOutcome(
            transcript="pause music",
            spoken_reply="I couldn't reach the playback satellite.",
            raw_response={
                "route": {"target": "music"},
                "dispatch": {
                    "target": "music",
                    "hook": "music.execute",
                    "status": "failed",
                    "result": {"action": "pause", "error": "satellite_command_failed"},
                },
            },
            status="failed",
            failure_code="satellite_command_failed",
            source_id="test_satellite_alpha",
            session_id="session-1",
            trace_id="trace-failed",
            effects={"satellite_playback": {"disposition": "failed", "target_source_id": None}},
        )
        mock_request_tts.return_value = b"wav"

        with self.assertLogs("satellite-dispatch-failure-test", level="WARNING") as captured:
            result = request_runtime.run_request_pipeline(
                args=args,
                logger=logger,
                runtime_state=runtime_state,
                pcm_bytes=b"\x00\x00" * 64,
            )

        self.assertEqual(result.outcome.spoken_reply, "I couldn't reach the playback satellite.")
        self.assertIn("brain_dispatch_failed", "\n".join(captured.output))

    @patch("pi_runtime.request_runtime.finalize_foreground_handoff")
    @patch("pi_runtime.request_runtime.begin_foreground_handoff")
    @patch("pi_runtime.request_runtime.play_ack_tone")
    @patch("pi_runtime.request_runtime.request_tts", return_value=b"wav")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("session-1", 123.0))
    def test_request_pipeline_skips_ack_tone_during_playback_handoff(
        self,
        _mock_session,
        mock_send_stt,
        mock_send_command,
        _mock_request_tts,
        mock_play_ack_tone,
        mock_begin_handoff,
        mock_finalize_handoff,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-ack-skip-test")
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=True,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(
            active_session_id=None,
            last_conversation_activity_at=None,
            reply_output_handoff_until=10_000.0,
        )
        mock_send_stt.return_value = "what time is it"
        mock_send_command.return_value = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={"dispatch": {"target": "system", "status": "executed", "result": {"action": "current_time"}}},
        )

        with patch("pi_runtime.request_runtime.time.time", return_value=9_999.0):
            request_runtime.run_request_pipeline(
                args=args,
                logger=logger,
                runtime_state=runtime_state,
                pcm_bytes=b"\x00\x00" * 64,
            )

        on_upload_complete = mock_send_stt.call_args.kwargs["on_upload_complete"]
        on_upload_complete_error = mock_send_stt.call_args.kwargs["on_upload_complete_error"]
        self.assertIsNone(on_upload_complete)
        self.assertIsNone(on_upload_complete_error)
        mock_play_ack_tone.assert_not_called()
        mock_begin_handoff.assert_not_called()
        mock_finalize_handoff.assert_not_called()

    @patch("pi_runtime.request_runtime.finalize_foreground_handoff")
    @patch("pi_runtime.request_runtime.begin_foreground_handoff")
    @patch("pi_runtime.request_runtime.play_ack_tone")
    @patch("pi_runtime.request_runtime.request_tts", return_value=b"wav")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt", return_value="what time is it")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("session-1", 123.0))
    def test_request_pipeline_routes_ack_tone_through_foreground_handoff(
        self,
        _mock_session,
        _mock_send_stt,
        mock_send_command,
        _mock_request_tts,
        mock_play_ack_tone,
        mock_begin_handoff,
        mock_finalize_handoff,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-ack-handoff-test")
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=True,
            output_device_index=None,
            ack_tone_gain=0.3,
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
        )
        runtime_state = types.SimpleNamespace(
            active_session_id=None,
            last_conversation_activity_at=None,
            reply_output_handoff_until=0.0,
        )
        handoff = request_runtime.begin_foreground_handoff.return_value = types.SimpleNamespace()
        mock_send_command.return_value = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={"dispatch": {"target": "system", "status": "executed", "result": {"action": "current_time"}}},
        )

        request_runtime.run_request_pipeline(
            args=args,
            logger=logger,
            runtime_state=runtime_state,
            pcm_bytes=b"\x00\x00" * 64,
        )

        on_upload_complete = _mock_send_stt.call_args.kwargs["on_upload_complete"]
        self.assertIsNotNone(on_upload_complete)
        on_upload_complete()
        mock_begin_handoff.assert_called_once()
        request = mock_begin_handoff.call_args.kwargs["request"]
        self.assertEqual(request.kind, "ack")
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.resume_policy, "no_resume")
        self.assertTrue(request.correlation_id)
        mock_play_ack_tone.assert_called_once()
        mock_finalize_handoff.assert_called_once_with(
            control_url=args.music_control_url,
            api_key=args.music_control_api_key,
            handoff=handoff,
            logger=logger,
        )

    @patch("pi_runtime.request_runtime.finalize_foreground_handoff")
    @patch("pi_runtime.request_runtime.begin_foreground_handoff")
    @patch("pi_runtime.request_runtime.play_wav_bytes")
    @patch("pi_runtime.request_runtime.request_tts")
    @patch("pi_runtime.request_runtime.fetch_command_events")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt", return_value="what is photosynthesis")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("session-1", 123.0))
    def test_request_pipeline_plays_interim_facts_ack_once_while_command_is_in_flight(
        self,
        _mock_session,
        _mock_send_stt,
        mock_send_command,
        mock_fetch_events,
        mock_request_tts,
        mock_play_wav_bytes,
        mock_begin_handoff,
        mock_finalize_handoff,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-interim-ack-test")
        polled = threading.Event()
        ack_played = threading.Event()
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            interim_ack_enabled=True,
            brain_api_key="brain-token",
            interim_ack_poll_interval_seconds=0.01,
            interim_ack_request_timeout_seconds=0.05,
            output_device_index=None,
            ack_tone_gain=0.3,
            playback_gain=0.35,
            reply_audio_state_path="/tmp/state.json",
            reply_audio_stop_path="/tmp/stop.flag",
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
        )
        runtime_state = types.SimpleNamespace(
            active_session_id=None,
            last_conversation_activity_at=None,
            reply_output_handoff_until=0.0,
        )

        def fetch_events(*_args, **_kwargs):
            polled.set()
            return [
                {
                    "event_id": 7,
                    "event_type": "facts_summarizer_ack",
                    "source": "test_satellite_alpha",
                    "session_id": "session-1",
                    "domain": "facts",
                    "message": "One second while I look that up.",
                }
            ]

        def send_command_in_flight(*_args, **_kwargs):
            polled.wait(timeout=1.0)
            ack_played.wait(timeout=1.0)
            return CommandOutcome(
                transcript="what is photosynthesis",
                spoken_reply="Photosynthesis converts light into chemical energy.",
                raw_response={"dispatch": {"target": "facts", "status": "executed", "result": {"action": "facts_lookup"}}},
            )

        def play_ack(*_args, **_kwargs):
            ack_played.set()
            return True

        mock_fetch_events.side_effect = fetch_events
        mock_send_command.side_effect = send_command_in_flight
        mock_request_tts.side_effect = [b"ack-wav", b"final-wav"]
        mock_play_wav_bytes.side_effect = play_ack
        handoff = mock_begin_handoff.return_value = types.SimpleNamespace()

        result = request_runtime.run_request_pipeline(
            args=args,
            logger=logger,
            runtime_state=runtime_state,
            pcm_bytes=b"\x00\x00" * 64,
        )

        self.assertEqual(result.outcome.spoken_reply, "Photosynthesis converts light into chemical energy.")
        self.assertEqual(mock_request_tts.call_args_list[0].args, ("http://oracle", "One second while I look that up."))
        self.assertEqual(mock_request_tts.call_args_list[1].args, ("http://oracle", "Photosynthesis converts light into chemical energy."))
        mock_play_wav_bytes.assert_called_once()
        self.assertEqual(mock_play_wav_bytes.call_args.args[0], b"ack-wav")
        self.assertEqual(mock_play_wav_bytes.call_args.kwargs["reply_audio_kind"], "ack")
        self.assertEqual(mock_play_wav_bytes.call_args.kwargs["reply_audio_session_id"], "session-1")
        mock_begin_handoff.assert_called_once()
        mock_finalize_handoff.assert_called_once_with(
            control_url="http://127.0.0.1:8021",
            api_key="test-key",
            handoff=handoff,
            logger=logger,
        )

    @patch("pi_runtime.request_runtime.play_wav_bytes")
    @patch("pi_runtime.request_runtime.request_tts", return_value=b"final-wav")
    @patch("pi_runtime.request_runtime.fetch_command_events")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt", return_value="what time is it")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("session-1", 123.0))
    def test_request_pipeline_does_not_play_interim_ack_without_event(
        self,
        _mock_session,
        _mock_send_stt,
        mock_send_command,
        mock_fetch_events,
        mock_request_tts,
        mock_play_wav_bytes,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-no-interim-ack-test")
        polled = threading.Event()
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            brain_api_key="brain-token",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=False,
            interim_ack_enabled=True,
            interim_ack_poll_interval_seconds=0.01,
            interim_ack_request_timeout_seconds=0.05,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(
            active_session_id=None,
            last_conversation_activity_at=None,
            reply_output_handoff_until=0.0,
        )

        def fetch_events(*_args, **_kwargs):
            polled.set()
            return []

        def send_command_in_flight(*_args, **_kwargs):
            polled.wait(timeout=1.0)
            return CommandOutcome(
                transcript="what time is it",
                spoken_reply="It is 3 PM.",
                raw_response={"dispatch": {"target": "system", "status": "executed", "result": {"action": "current_time"}}},
            )

        mock_fetch_events.side_effect = fetch_events
        mock_send_command.side_effect = send_command_in_flight

        request_runtime.run_request_pipeline(
            args=args,
            logger=logger,
            runtime_state=runtime_state,
            pcm_bytes=b"\x00\x00" * 64,
        )

        mock_fetch_events.assert_called()
        mock_play_wav_bytes.assert_not_called()
        mock_request_tts.assert_called_once_with(
            "http://oracle",
            "It is 3 PM.",
            credential="brain-token",
        )

    def test_request_runtime_builds_ack_foreground_request(self) -> None:
        request = request_runtime._build_ack_foreground_request()

        self.assertEqual(request.kind, "ack")
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.interrupt_policy, "none")
        self.assertEqual(request.resume_policy, "no_resume")
        self.assertTrue(request.correlation_id)

    def test_local_output_gate_serializes_calls(self) -> None:
        playback = __import__("pi_runtime.audio.playback", fromlist=["_with_local_output_gate", "_LOCAL_OUTPUT_GATE"])
        entered = threading.Event()
        finished = threading.Event()

        def _run_gated_call() -> None:
            playback._with_local_output_gate(lambda: entered.set())
            finished.set()

        with playback._LOCAL_OUTPUT_GATE:
            worker = threading.Thread(target=_run_gated_call)
            worker.start()
            self.assertFalse(entered.wait(0.1))
        self.assertTrue(entered.wait(1.0))
        self.assertTrue(finished.wait(1.0))
        worker.join(timeout=1.0)

    @patch("pi_runtime.request_runtime.play_ack_tone")
    @patch("pi_runtime.request_runtime.request_tts", return_value=b"wav")
    @patch("pi_runtime.request_runtime.send_command")
    @patch("pi_runtime.request_runtime.send_stt")
    @patch("pi_runtime.request_runtime.get_active_session_id", return_value=("session-1", 123.0))
    def test_request_pipeline_skips_ack_tone_when_explicitly_suppressed(
        self,
        _mock_session,
        mock_send_stt,
        mock_send_command,
        _mock_request_tts,
        mock_play_ack_tone,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-ack-explicit-skip-test")
        args = types.SimpleNamespace(
            oracle_url="http://oracle",
            source="test_satellite_alpha",
            conversation_timeout_seconds=90.0,
            ack_tone_enabled=True,
            output_device_index=None,
            ack_tone_gain=0.3,
        )
        runtime_state = types.SimpleNamespace(
            active_session_id=None,
            last_conversation_activity_at=None,
            reply_output_handoff_until=0.0,
        )
        mock_send_stt.return_value = "what time is it"
        mock_send_command.return_value = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={"dispatch": {"target": "system", "status": "executed", "result": {"action": "current_time"}}},
        )

        request_runtime.run_request_pipeline(
            args=args,
            logger=logger,
            runtime_state=runtime_state,
            pcm_bytes=b"\x00\x00" * 64,
            suppress_ack_tone=True,
        )

        on_upload_complete = mock_send_stt.call_args.kwargs["on_upload_complete"]
        on_upload_complete_error = mock_send_stt.call_args.kwargs["on_upload_complete_error"]
        self.assertIsNone(on_upload_complete)
        self.assertIsNone(on_upload_complete_error)
        mock_play_ack_tone.assert_not_called()

    @patch("pi_runtime.pipeline_runtime.time.sleep")
    @patch("pi_runtime.pipeline_runtime.play_error_tone")
    @patch("pi_runtime.pipeline_runtime.run_request_pipeline")
    def test_capture_pipeline_rate_limits_error_tone(
        self,
        mock_run_request_pipeline,
        mock_play_error_tone,
        _mock_sleep,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-pipeline-failure-test")
        args = types.SimpleNamespace(
            source="test_satellite_alpha",
            interrupt_replies=False,
            output_device_index=None,
            playback_gain=0.24,
            reply_audio_state_path="/tmp/reply-state.json",
            reply_audio_stop_path="/tmp/reply-stop.flag",
            wake_threshold=0.07,
            input_gain=1.0,
            ack_tone_enabled=False,
            ack_tone_gain=0.3,
            playback_interrupt_settle_seconds=0.0,
            post_playback_block_seconds=2.0,
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="secret",
            error_tone_enabled=True,
            error_tone_cooldown_seconds=3.0,
        )
        runtime_state = types.SimpleNamespace(
            active_session_id="session-1",
            next_wake_time=0.0,
            next_error_tone_at=0.0,
        )
        mock_run_request_pipeline.side_effect = request_runtime.RequestPipelineError(
            kind="stt_failed",
            detail="stt backend offline",
            should_play_error_tone=True,
            capture_context={"capture_succeeded": True},
        )
        pipeline = pipeline_runtime.CapturePipeline(
            args=args,
            logger=logger,
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            ducked_music=types.SimpleNamespace(maybe_restore=lambda force=False: None),
            runtime_state=runtime_state,
        )

        with patch("pi_runtime.pipeline_runtime.time.time", side_effect=[100.0, 101.0]):
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0)
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0)

        mock_play_error_tone.assert_called_once()

    @patch("pi_runtime.reply_runtime.play_wav_bytes")
    def test_reply_runtime_logs_reply_playback_events(self, mock_play_wav_bytes) -> None:
        logger = __import__("logging").getLogger("satellite-reply-trace-test")
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_alpha",
                interrupt_replies=False,
                output_device_index=None,
                playback_gain=0.24,
                reply_audio_state_path="/tmp/reply-state.json",
                reply_audio_stop_path="/tmp/reply-stop.flag",
                wake_threshold=0.07,
                input_gain=1.0,
                ack_tone_enabled=False,
                ack_tone_gain=0.3,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=logger,
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(
                active_session_id="session-1",
                next_wake_time=0.0,
                reply_output_handoff_until=0.0,
            ),
        )
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={
                "route": {"target": "system"},
                "dispatch": {
                    "target": "system",
                    "hook": "system.current_time",
                    "status": "executed",
                    "result": {"action": "current_time"},
                },
            },
            status="executed",
            source_id="test_satellite_alpha",
            session_id="session-1",
            trace_id="trace-1",
            effects={"satellite_playback": {"disposition": "stopped", "target_source_id": None}},
        )
        handoff = types.SimpleNamespace(foreground_session_id="reply-1", authority_correlation_id="corr-1")

        with self.assertLogs("satellite-reply-trace-test", level="INFO") as captured:
            runtime.play_reply(
                tts_wav=b"wav",
                outcome=outcome,
                foreground_handoff=handoff,
                interrupted_playback=None,
                process_capture=lambda **kwargs: None,
            )

        output = "\n".join(captured.output)
        self.assertIn("reply_playback_started", output)
        self.assertIn("reply_playback_finished", output)
        self.assertIn("source=test_satellite_alpha", output)
        self.assertIn("session_id=session-1", output)
        self.assertIn("dispatch_hook=canonical_conversation_result", output)
        self.assertIn("status=executed", output)
        mock_play_wav_bytes.assert_called_once()

    @patch("pi_runtime.reply_runtime.play_wav_bytes", side_effect=RuntimeError("device unavailable"))
    def test_reply_runtime_logs_failed_playback_as_failed(self, _mock_play_wav_bytes) -> None:
        logger = __import__("logging").getLogger("satellite-reply-failure-trace-test")
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_alpha",
                interrupt_replies=False,
                output_device_index=None,
                playback_gain=0.24,
                reply_audio_state_path="/tmp/reply-state.json",
                reply_audio_stop_path="/tmp/reply-stop.flag",
                wake_threshold=0.07,
                input_gain=1.0,
                ack_tone_enabled=False,
                ack_tone_gain=0.3,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=logger,
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(
                active_session_id="session-1",
                next_wake_time=0.0,
                reply_output_handoff_until=0.0,
            ),
        )
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={
                "route": {"target": "system"},
                "dispatch": {
                    "target": "system",
                    "hook": "system.current_time",
                    "status": "executed",
                    "result": {"action": "current_time"},
                },
            },
            status="executed",
            source_id="test_satellite_alpha",
            session_id="session-1",
            trace_id="trace-1",
            effects={},
        )
        handoff = types.SimpleNamespace(foreground_session_id="reply-1", authority_correlation_id="corr-1")

        with self.assertLogs("satellite-reply-failure-trace-test", level="INFO") as captured:
            runtime.play_reply(
                tts_wav=b"wav",
                outcome=outcome,
                foreground_handoff=handoff,
                interrupted_playback=None,
                process_capture=lambda **kwargs: None,
            )

        output = "\n".join(captured.output)
        self.assertIn("Reply playback failed: device unavailable", output)
        self.assertIn("reply_playback_finished", output)
        self.assertIn("detail=failed", output)

    @patch("pi_runtime.reply_runtime.play_wav_bytes")
    def test_reply_runtime_treats_interrupted_playback_as_handoff_even_if_window_expired(
        self,
        mock_play_wav_bytes,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-reply-handoff-test")
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_alpha",
                interrupt_replies=False,
                output_device_index=None,
                playback_gain=0.24,
                reply_audio_state_path="/tmp/reply-state.json",
                reply_audio_stop_path="/tmp/reply-stop.flag",
                wake_threshold=0.07,
                input_gain=1.0,
                ack_tone_enabled=False,
                ack_tone_gain=0.3,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=logger,
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(
                active_session_id="session-1",
                next_wake_time=0.0,
                reply_output_handoff_until=0.0,
            ),
        )
        outcome = CommandOutcome(
            transcript="stop",
            spoken_reply="Stopped.",
            raw_response={
                "route": {"target": "music"},
                "dispatch": {
                    "target": "music",
                    "hook": "music.execute",
                    "status": "executed",
                    "result": {"action": "stop"},
                },
            },
            status="executed",
            effects={"satellite_playback": {"disposition": "stopped", "target_source_id": None}},
        )
        handoff = types.SimpleNamespace(foreground_session_id="reply-1", authority_correlation_id="corr-1")

        runtime.play_reply(
            tts_wav=b"wav",
            outcome=outcome,
            foreground_handoff=handoff,
            interrupted_playback=[
                __import__("pi_runtime.models", fromlist=["InterruptedPlayback"]).InterruptedPlayback(
                    kind="music",
                    backend_type="plexamp_external",
                    resume_action="resume",
                )
            ],
            process_capture=lambda **kwargs: None,
        )

        self.assertTrue(mock_play_wav_bytes.call_args.kwargs["playback_handoff_active"])

    @patch("pi_runtime.reply_runtime.time.sleep")
    @patch("pi_runtime.reply_runtime.play_wav_bytes")
    def test_reply_runtime_waits_before_stop_confirmation_after_interrupted_playback(
        self,
        mock_play_wav_bytes,
        mock_sleep,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-reply-stop-settle-test")
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_alpha",
                interrupt_replies=False,
                output_device_index=None,
                playback_gain=0.24,
                reply_audio_state_path="/tmp/reply-state.json",
                reply_audio_stop_path="/tmp/reply-stop.flag",
                wake_threshold=0.07,
                input_gain=1.0,
                ack_tone_enabled=False,
                ack_tone_gain=0.3,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                reply_output_settle_seconds=1.25,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=logger,
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(
                active_session_id="session-1",
                next_wake_time=0.0,
                reply_output_handoff_until=0.0,
            ),
        )
        outcome = CommandOutcome(
            transcript="stop",
            spoken_reply="Stopped.",
            raw_response={
                "route": {"target": "music"},
                "dispatch": {
                    "target": "music",
                    "hook": "music.execute",
                    "status": "executed",
                    "result": {"action": "stop"},
                },
            },
            status="executed",
            source_id="test_satellite_alpha",
            session_id="session-1",
            trace_id="trace-1",
            effects={"satellite_playback": {"disposition": "stopped", "target_source_id": None}},
        )
        handoff = types.SimpleNamespace(foreground_session_id="reply-1", authority_correlation_id="corr-1")

        runtime.play_reply(
            tts_wav=b"wav",
            outcome=outcome,
            foreground_handoff=handoff,
            interrupted_playback=[
                __import__("pi_runtime.models", fromlist=["InterruptedPlayback"]).InterruptedPlayback(
                    kind="music",
                    backend_type="plexamp_external",
                    resume_action="resume",
                )
            ],
            process_capture=lambda **kwargs: None,
        )

        mock_sleep.assert_any_call(1.25)

    @patch("pi_runtime.reply_runtime.time.sleep")
    @patch("pi_runtime.reply_runtime.play_wav_bytes")
    def test_reply_runtime_does_not_wait_for_normal_non_handoff_reply(
        self,
        mock_play_wav_bytes,
        mock_sleep,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-reply-normal-settle-test")
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_alpha",
                interrupt_replies=False,
                output_device_index=None,
                playback_gain=0.24,
                reply_audio_state_path="/tmp/reply-state.json",
                reply_audio_stop_path="/tmp/reply-stop.flag",
                wake_threshold=0.07,
                input_gain=1.0,
                ack_tone_enabled=False,
                ack_tone_gain=0.3,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                reply_output_settle_seconds=1.25,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=logger,
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(
                active_session_id="session-1",
                next_wake_time=0.0,
                reply_output_handoff_until=0.0,
            ),
        )
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={
                "route": {"target": "system"},
                "dispatch": {
                    "target": "system",
                    "hook": "system.current_time",
                    "status": "executed",
                    "result": {"action": "current_time"},
                },
            },
            status="executed",
            source_id="test_satellite_alpha",
            session_id="session-1",
            trace_id="trace-1",
            effects={},
        )
        handoff = types.SimpleNamespace(foreground_session_id="reply-1", authority_correlation_id="corr-1")

        runtime.play_reply(
            tts_wav=b"wav",
            outcome=outcome,
            foreground_handoff=handoff,
            interrupted_playback=None,
            process_capture=lambda **kwargs: None,
        )

        mock_sleep.assert_not_called()

    @patch("pi_runtime.reply_runtime.play_wav_bytes_with_wake_interrupt", return_value=True)
    def test_reply_runtime_preserves_command_correlation_for_interrupted_followup_logs(
        self,
        mock_interrupt_playback,
    ) -> None:
        logger = __import__("logging").getLogger("satellite-reply-interrupt-trace-test")
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime", "CaptureOutcome"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        CaptureOutcome = __import__("pi_runtime.models", fromlist=["CaptureOutcome"]).CaptureOutcome
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_alpha",
                interrupt_replies=True,
                output_device_index=None,
                playback_gain=0.24,
                reply_audio_state_path="/tmp/reply-state.json",
                reply_audio_stop_path="/tmp/reply-stop.flag",
                wake_threshold=0.07,
                input_gain=1.0,
                ack_tone_enabled=False,
                ack_tone_gain=0.3,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                wake_cooldown_seconds=6.0,
                vad_threshold=0.035,
                vad_noise_multiplier=1.6,
                vad_noise_offset=0.006,
                vad_release_multiplier=1.15,
                vad_release_offset=0.003,
                vad_max_speech_threshold=0.42,
                vad_max_silence_threshold=0.30,
                silence_seconds=0.45,
                max_record_seconds=8.0,
                min_speech_seconds=0.2,
                followup_silence_seconds=0.3,
                followup_max_record_seconds=4.0,
                followup_speech_start_timeout_seconds=2.5,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=logger,
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(
                active_session_id="session-1",
                next_wake_time=0.0,
                reply_output_handoff_until=0.0,
            ),
        )
        runtime._capture_after_reply_interrupt = lambda: CaptureOutcome(
            pcm_bytes=None,
            stop_reason="insufficient_speech",
            total_frames=0,
            voiced_frames=0,
            silence_frames=0,
            max_energy=0.0,
            noise_floor=0.0,
            speech_threshold=0.0,
            silence_threshold=0.0,
        )
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={
                "route": {"target": "system"},
                "dispatch": {
                    "target": "system",
                    "hook": "system.current_time",
                    "status": "executed",
                    "result": {"action": "current_time"},
                },
            },
            status="executed",
            source_id="test_satellite_alpha",
            session_id="session-1",
            trace_id="trace-1",
            effects={},
        )
        handoff = types.SimpleNamespace(foreground_session_id="reply-1", authority_correlation_id="corr-1")

        with self.assertLogs("satellite-reply-interrupt-trace-test", level="INFO") as captured:
            runtime.play_reply(
                tts_wav=b"wav",
                outcome=outcome,
                foreground_handoff=handoff,
                interrupted_playback=None,
                process_capture=lambda **kwargs: None,
            )

        output = "\n".join(captured.output)
        self.assertIn("followup_capture_started", output)
        self.assertIn("followup_capture_finished", output)
        self.assertIn("dispatch_hook=canonical_conversation_result", output)
        self.assertIn("status=executed", output)

    @patch("pi_runtime.reply_runtime.finalize_foreground_handoff")
    @patch("pi_runtime.reply_runtime.begin_foreground_handoff")
    @patch("pi_runtime.reply_runtime.should_listen_for_followup_reply", return_value=True)
    @patch("pi_runtime.reply_runtime.play_followup_listen_cue")
    def test_reply_runtime_keeps_followup_cue_during_playback_handoff(
        self,
        mock_play_followup_listen_cue,
        _mock_should_listen,
        mock_begin_handoff,
        mock_finalize_handoff,
    ) -> None:
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_bravo",
                ack_tone_enabled=True,
                ack_tone_gain=0.3,
                output_device_index=None,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                followup_silence_seconds=0.3,
                followup_max_record_seconds=4.0,
                followup_speech_start_timeout_seconds=2.5,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(
                active_session_id="session-1",
                next_wake_time=0.0,
                reply_output_handoff_until=10_000.0,
            ),
        )
        runtime._capture_followup_reply = lambda: reply_runtime_module.TimedCaptureOutcome(
            capture=reply_runtime_module.CaptureOutcome(
                pcm_bytes=None,
                stop_reason="timeout",
                total_frames=0,
                voiced_frames=0,
                silence_frames=0,
                max_energy=0.0,
                noise_floor=0.0,
                speech_threshold=0.0,
                silence_threshold=0.0,
            ),
            elapsed_ms=1.0,
        )
        outcome = CommandOutcome(transcript="what time is it", spoken_reply="It is 3 PM. Anything else?", raw_response={})
        handoff = types.SimpleNamespace()
        mock_begin_handoff.return_value = handoff

        with patch("pi_runtime.reply_runtime.time.time", return_value=9_999.0):
            runtime._handle_followup_or_post_playback(outcome, interrupted_playback=None)

        mock_begin_handoff.assert_called_once()
        request = mock_begin_handoff.call_args.kwargs["request"]
        self.assertEqual(request.kind, "followup_cue")
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.resume_policy, "no_resume")
        mock_play_followup_listen_cue.assert_called_once()
        mock_finalize_handoff.assert_called_once_with(
            control_url=runtime._args.music_control_url,
            api_key=runtime._args.music_control_api_key,
            handoff=handoff,
            logger=runtime._logger,
        )

    def test_reply_runtime_builds_followup_cue_foreground_request(self) -> None:
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_bravo",
                ack_tone_enabled=True,
                ack_tone_gain=0.3,
                output_device_index=None,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                followup_silence_seconds=0.3,
                followup_max_record_seconds=4.0,
                followup_speech_start_timeout_seconds=2.5,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(reply_output_handoff_until=0.0, next_wake_time=0.0),
        )

        request = runtime._build_followup_cue_foreground_request()

        self.assertEqual(request.kind, "followup_cue")
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.interrupt_policy, "none")
        self.assertEqual(request.resume_policy, "no_resume")
        self.assertTrue(request.correlation_id)

    @patch("pi_runtime.reply_runtime.play_wav_bytes")
    def test_reply_runtime_uses_foreground_handoff_session_ids(self, mock_play_wav_bytes) -> None:
        logger = __import__("logging").getLogger("satellite-reply-coordinator-test")
        reply_runtime_module = __import__("pi_runtime.reply_runtime", fromlist=["ReplyRuntime"])
        ReplyRuntime = reply_runtime_module.ReplyRuntime
        runtime = ReplyRuntime(
            args=types.SimpleNamespace(
                source="test_satellite_alpha",
                interrupt_replies=False,
                output_device_index=None,
                playback_gain=0.24,
                reply_audio_state_path="/tmp/reply-state.json",
                reply_audio_stop_path="/tmp/reply-stop.flag",
                wake_threshold=0.07,
                input_gain=1.0,
                ack_tone_enabled=False,
                ack_tone_gain=0.3,
                playback_interrupt_settle_seconds=0.0,
                post_playback_block_seconds=2.0,
                music_control_url="http://127.0.0.1:8021",
                music_control_api_key="secret",
            ),
            logger=logger,
            frame_queue=queue.Queue(),
            pre_roll=[],
            wake_model=object(),
            wake_key="oracle",
            runtime_state=types.SimpleNamespace(
                active_session_id="session-1",
                next_wake_time=0.0,
                reply_output_handoff_until=0.0,
            ),
        )
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={"dispatch": {"target": "system", "status": "executed", "result": {"action": "current_time"}}},
        )
        handoff = types.SimpleNamespace(foreground_session_id="reply-1", authority_correlation_id="corr-1")

        runtime.play_reply(
            tts_wav=b"wav",
            outcome=outcome,
            foreground_handoff=handoff,
            interrupted_playback=None,
            process_capture=lambda **kwargs: None,
        )

        self.assertEqual(mock_play_wav_bytes.call_args.kwargs["reply_audio_session_id"], "reply-1")
        self.assertEqual(mock_play_wav_bytes.call_args.kwargs["reply_audio_correlation_id"], "corr-1")


if __name__ == "__main__":
    unittest.main()
