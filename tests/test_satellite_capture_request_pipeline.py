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
    def test_capture_pipeline_promotes_ducked_playback_before_reply(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        interrupted = [pipeline_runtime.InterruptedPlayback(kind="music", backend_type="oracle_native_music", session_id="track-1", resume_action="restore_volume", restore_volume_level=81)]
        request_result = types.SimpleNamespace(
            transcript="what time is it",
            outcome=CommandOutcome(transcript="what time is it", spoken_reply="It is 3 PM.", raw_response={"dispatch": {"target": "system", "status": "executed", "result": {"action": "current_time"}}}),
            tts_wav=b"wav",
            stt_elapsed_ms=1.0,
            command_elapsed_ms=1.0,
            tts_elapsed_ms=1.0,
        )
        handoff = pipeline_runtime.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="borrow",
            interrupted_sessions=interrupted,
            resume_policy="resume_previous",
            foreground_session_id="reply-1",
            authority_correlation_id="corr-1",
        )

        with patch("pi_runtime.pipeline_runtime.run_request_pipeline", return_value=request_result), \
             patch("pi_runtime.pipeline_runtime.begin_foreground_handoff", return_value=handoff), \
             patch("pi_runtime.pipeline_runtime.prepare_interrupted_playback_for_reply", return_value=[pipeline_runtime.InterruptedPlayback(kind="music", backend_type="oracle_native_music", session_id="track-1", resume_action="resume")]) as mock_prepare, \
             patch("pi_runtime.pipeline_runtime.finalize_foreground_handoff") as mock_finalize_handoff, \
             patch.object(pipeline._reply_runtime, "play_reply", return_value=types.SimpleNamespace(interrupted_playback=[], playback_elapsed_ms=1.0)):
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0, interrupted_playback=interrupted)

        self.assertEqual(mock_prepare.call_count, 0)
        mock_finalize_handoff.assert_called_once()

    def test_capture_pipeline_builds_replace_request_for_deferred_reply_start(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        outcome = CommandOutcome(
            transcript="play dune",
            spoken_reply="Playing Dune.",
            raw_response={"dispatch": {"target": "audiobook", "status": "executed", "result": {"action": "play"}}},
        )
        deferred = pipeline_runtime.InterruptedPlayback(
            kind="audiobook",
            backend_type="oracle_audiobook",
            session_id="book-1",
            resume_action="resume_longform_audio",
        )

        request = pipeline._build_reply_foreground_request(
            outcome=outcome,
            interrupted_playback=None,
            deferred_transport_resume=deferred,
        )

        self.assertEqual(request.handoff_mode, "replace")
        self.assertEqual(request.resume_policy, "replace_with_deferred")
        self.assertEqual(request.interrupt_policy, "none")

    def test_capture_pipeline_builds_no_resume_request_for_interrupted_stop_reply(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        outcome = CommandOutcome(
            transcript="stop the music",
            spoken_reply="Stopped.",
            raw_response={"dispatch": {"target": "music", "status": "executed", "result": {"action": "stop"}}},
            status="executed",
            effects={"satellite_playback": {"disposition": "stopped", "target_source_id": None}},
        )
        interrupted = [
            pipeline_runtime.InterruptedPlayback(
                kind="music",
                backend_type="oracle_native_music",
                session_id="track-1",
                resume_action="resume",
            )
        ]

        request = pipeline._build_reply_foreground_request(
            outcome=outcome,
            interrupted_playback=interrupted,
            deferred_transport_resume=None,
        )

        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.resume_policy, "no_resume")
        self.assertEqual(request.interrupt_policy, "none")

    def test_prepare_interrupted_playback_for_reply_falls_back_to_stop_when_pause_fails(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["prepare_interrupted_playback_for_reply"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        interrupted = [
            local_control.InterruptedPlayback(
                kind="audiobook",
                backend_type="oracle_audiobook",
                session_id="book-1",
                resume_action="restore_volume",
                restore_volume_level=81,
            )
        ]

        with patch.object(
            local_control,
            "send_local_control_command",
            side_effect=[
                {"ok": True},
                {"ok": False, "state": "failed", "detail": "device still busy"},
                {"ok": True},
            ],
        ) as mock_send:
            prepared = local_control.prepare_interrupted_playback_for_reply(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                interrupted=interrupted,
                settle_seconds=0.0,
                logger=logger,
            )

        self.assertEqual(mock_send.call_args_list[1].args, ("http://127.0.0.1:8021", "test-key", "pause_longform_audio"))
        self.assertEqual(mock_send.call_args_list[2].args, ("http://127.0.0.1:8021", "test-key", "stop_longform_audio"))
        self.assertEqual(prepared[0].resume_action, "resume_longform_audio")

    def test_should_resume_after_reply_for_transport_command_true_for_music_play(self) -> None:
        outcome = CommandOutcome(
            transcript="play fortunate son",
            spoken_reply="Playing Fortunate Son.",
            raw_response={"dispatch": {"target": "music", "result": {"action": "play"}}},
            status="executed",
            effects={"satellite_playback": {"disposition": "started", "target_source_id": None}},
        )
        self.assertTrue(should_resume_after_reply_for_transport_command(outcome))

    def test_extract_deferred_transport_resume_returns_audiobook_resume(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["extract_deferred_transport_resume"])
        outcome = CommandOutcome(
            transcript="play dune",
            spoken_reply="Playing Dune by Frank Herbert.",
            raw_response={},
            status="executed",
            session_id="session-1",
            effects={"deferred_satellite_playback": {"continuation_token": "opaque-book-token"}},
        )

        deferred = local_control.extract_deferred_transport_resume(
            outcome, oracle_url="http://oracle", source="satellite-one", credential="token"
        )

        self.assertIsNotNone(deferred)
        assert deferred is not None
        self.assertEqual(deferred.kind, "deferred_satellite_playback")
        self.assertEqual(deferred.backend_type, "oracle")
        self.assertEqual(deferred.session_id, "session-1")
        self.assertEqual(deferred.resume_action, "oracle_deferred_resume")
        self.assertEqual(deferred.resume_args["continuation_token"], "opaque-book-token")

    def test_extract_deferred_transport_resume_returns_music_play(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["extract_deferred_transport_resume"])
        outcome = CommandOutcome(
            transcript="play fortunate son",
            spoken_reply="Playing Fortunate Son.",
            raw_response={},
            status="executed",
            session_id="session-2",
            effects={"deferred_satellite_playback": {"continuation_token": "opaque-music-token"}},
        )

        deferred = local_control.extract_deferred_transport_resume(
            outcome, oracle_url="http://oracle", source="satellite-one", credential="token"
        )

        self.assertIsNotNone(deferred)
        assert deferred is not None
        self.assertEqual(deferred.kind, "deferred_satellite_playback")
        self.assertEqual(deferred.backend_type, "oracle")
        self.assertEqual(deferred.session_id, "session-2")
        self.assertEqual(deferred.resume_action, "oracle_deferred_resume")
        self.assertEqual(deferred.resume_args["continuation_token"], "opaque-music-token")

    def test_capture_pipeline_uses_deferred_transport_resume_without_interrupting(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        request_result = types.SimpleNamespace(
            transcript="play dune",
            outcome=CommandOutcome(
                transcript="play dune",
                spoken_reply="Playing Dune by Frank Herbert.",
                raw_response={},
                status="executed",
                session_id="session-book",
                effects={
                    "satellite_playback": {"disposition": "started", "target_source_id": None},
                    "deferred_satellite_playback": {"continuation_token": "opaque-book-token"},
                },
            ),
            tts_wav=b"wav",
            stt_elapsed_ms=1.0,
            command_elapsed_ms=1.0,
            tts_elapsed_ms=1.0,
        )

        handoff = pipeline_runtime.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="replace",
            interrupted_sessions=[],
            resume_policy="replace_with_deferred",
        )

        with patch("pi_runtime.pipeline_runtime.run_request_pipeline", return_value=request_result), \
             patch("pi_runtime.pipeline_runtime.begin_foreground_handoff", return_value=handoff) as mock_begin_handoff, \
             patch("pi_runtime.pipeline_runtime.finalize_foreground_handoff") as mock_finalize_handoff, \
             patch.object(pipeline._reply_runtime, "play_reply", return_value=types.SimpleNamespace(
                 interrupted_playback=[],
                 playback_elapsed_ms=1.0,
             )), \
             patch("pi_runtime.pipeline_runtime.resume_interrupted_local_playback") as mock_resume:
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0, interrupted_playback=None)

        mock_begin_handoff.assert_called_once()
        mock_finalize_handoff.assert_called_once()
        self.assertEqual(mock_resume.call_count, 0)
        deferred = mock_finalize_handoff.call_args.kwargs["deferred_resume"]
        self.assertEqual(deferred.resume_action, "oracle_deferred_resume")
        self.assertEqual(deferred.resume_args["continuation_token"], "opaque-book-token")

    def test_should_resume_after_reply_for_transport_command_false_for_music_stop(self) -> None:
        outcome = CommandOutcome(
            transcript="stop music",
            spoken_reply="Stopped.",
            raw_response={"dispatch": {"target": "music", "result": {"action": "stop"}}},
            status="executed",
            effects={"satellite_playback": {"disposition": "stopped", "target_source_id": None}},
        )
        self.assertFalse(should_resume_after_reply_for_transport_command(outcome))

    def test_capture_pipeline_uses_deferred_transport_resume_for_spoken_music_play(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        outcome = CommandOutcome(
            transcript="play fortunate son",
            spoken_reply="Playing Fortunate Son.",
            raw_response={
                "dispatch": {
                    "target": "music",
                    "status": "executed",
                    "result": {
                        "action": "play",
                        "deferred_audible_start": True,
                        "deferred_session": {
                            "kind": "music",
                            "backend_type": "plexamp",
                            "session_id": "track-1",
                            "resume_action": "play_media",
                            "resume_args": {"query": "Fortunate Son"},
                        },
                    },
                }
            },
            status="executed",
            session_id="session-music",
            effects={
                "satellite_playback": {"disposition": "started", "target_source_id": None},
                "deferred_satellite_playback": {"continuation_token": "opaque-music-token"},
            },
        )
        request_result = types.SimpleNamespace(
            transcript="play fortunate son",
            outcome=outcome,
            tts_wav=b"wav",
            stt_elapsed_ms=1.0,
            command_elapsed_ms=1.0,
            tts_elapsed_ms=1.0,
        )

        handoff = pipeline_runtime.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="replace",
            interrupted_sessions=[],
            resume_policy="replace_with_deferred",
        )

        with patch("pi_runtime.pipeline_runtime.run_request_pipeline", return_value=request_result), \
             patch("pi_runtime.pipeline_runtime.begin_foreground_handoff", return_value=handoff) as mock_begin_handoff, \
             patch("pi_runtime.pipeline_runtime.finalize_foreground_handoff") as mock_finalize_handoff, \
             patch("pi_runtime.pipeline_runtime.resume_interrupted_local_playback") as mock_resume, \
             patch.object(pipeline._reply_runtime, "play_reply", return_value=types.SimpleNamespace(interrupted_playback=[], playback_elapsed_ms=1.0)):
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0, interrupted_playback=None)

        mock_begin_handoff.assert_called_once()
        mock_finalize_handoff.assert_called_once()
        self.assertEqual(mock_resume.call_count, 0)
        deferred = mock_finalize_handoff.call_args.kwargs["deferred_resume"]
        self.assertEqual(deferred.resume_action, "oracle_deferred_resume")
        self.assertEqual(deferred.resume_args["continuation_token"], "opaque-music-token")

    def test_capture_pipeline_prefers_deferred_replacement_over_prior_interrupted_playback(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
            ack_tone_enabled=False,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        interrupted = [pipeline_runtime.InterruptedPlayback(kind="music", backend_type="oracle_native_music", session_id="track-1", resume_action="resume")]
        outcome = CommandOutcome(
            transcript="play fortunate son",
            spoken_reply="Playing Fortunate Son.",
            raw_response={
                "dispatch": {
                    "target": "music",
                    "status": "executed",
                    "result": {
                        "action": "play",
                        "deferred_audible_start": True,
                        "deferred_session": {
                            "kind": "music",
                            "backend_type": "oracle_native_music",
                            "session_id": "track-2",
                            "resume_action": "play_media",
                            "resume_args": {"query": "Fortunate Son"},
                        },
                    },
                }
            },
            status="executed",
            session_id="session-music-2",
            effects={
                "satellite_playback": {"disposition": "started", "target_source_id": None},
                "deferred_satellite_playback": {"continuation_token": "opaque-replacement-token"},
            },
        )
        request_result = types.SimpleNamespace(
            transcript="play fortunate son",
            outcome=outcome,
            tts_wav=b"wav",
            stt_elapsed_ms=1.0,
            command_elapsed_ms=1.0,
            tts_elapsed_ms=1.0,
        )
        handoff = pipeline_runtime.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="replace",
            interrupted_sessions=[],
            resume_policy="replace_with_deferred",
        )

        with patch("pi_runtime.pipeline_runtime.run_request_pipeline", return_value=request_result), \
             patch("pi_runtime.pipeline_runtime.begin_foreground_handoff", return_value=handoff) as mock_begin_handoff, \
             patch("pi_runtime.pipeline_runtime.finalize_foreground_handoff") as mock_finalize_handoff, \
             patch.object(pipeline._reply_runtime, "play_reply", return_value=types.SimpleNamespace(interrupted_playback=[], playback_elapsed_ms=1.0)):
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0, interrupted_playback=interrupted)

        request = mock_begin_handoff.call_args.kwargs["request"]
        self.assertEqual(request.handoff_mode, "replace")
        self.assertEqual(request.resume_policy, "replace_with_deferred")
        deferred = mock_finalize_handoff.call_args.kwargs["deferred_resume"]
        self.assertIsNotNone(deferred)
        self.assertEqual(deferred.session_id, "session-music-2")

    def test_capture_pipeline_does_not_resume_previous_media_for_stop_command(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
            ack_tone_enabled=False,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        interrupted = [pipeline_runtime.InterruptedPlayback(kind="music", backend_type="oracle_native_music", session_id="track-1", resume_action="resume")]
        outcome = CommandOutcome(
            transcript="stop the music",
            spoken_reply="Stopped.",
            raw_response={
                "dispatch": {
                    "target": "music",
                    "status": "executed",
                    "result": {"action": "stop"},
                }
            },
            status="executed",
            effects={"satellite_playback": {"disposition": "stopped", "target_source_id": None}},
        )
        request_result = types.SimpleNamespace(
            transcript="stop the music",
            outcome=outcome,
            tts_wav=b"wav",
            stt_elapsed_ms=1.0,
            command_elapsed_ms=1.0,
            tts_elapsed_ms=1.0,
        )
        handoff = pipeline_runtime.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="borrow",
            interrupted_sessions=interrupted,
            resume_policy="no_resume",
        )

        with patch("pi_runtime.pipeline_runtime.run_request_pipeline", return_value=request_result), \
             patch("pi_runtime.pipeline_runtime.begin_foreground_handoff", return_value=handoff) as mock_begin_handoff, \
             patch("pi_runtime.pipeline_runtime.finalize_foreground_handoff") as mock_finalize_handoff, \
             patch.object(pipeline._reply_runtime, "play_reply", return_value=types.SimpleNamespace(interrupted_playback=interrupted, playback_elapsed_ms=1.0)):
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0, interrupted_playback=interrupted)

        request = mock_begin_handoff.call_args.kwargs["request"]
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.resume_policy, "no_resume")
        self.assertEqual(mock_finalize_handoff.call_args.kwargs["handoff"].resume_policy, "no_resume")

    def test_capture_pipeline_prepares_interrupted_playback_before_ack_enabled_request(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
            ack_tone_enabled=True,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={"dispatch": {"target": "system", "status": "executed", "result": {"action": "current_time"}}},
        )
        request_result = types.SimpleNamespace(
            transcript="what time is it",
            outcome=outcome,
            tts_wav=b"wav",
            stt_elapsed_ms=1.0,
            command_elapsed_ms=1.0,
            tts_elapsed_ms=1.0,
        )
        interrupted = [pipeline_runtime.InterruptedPlayback(kind="music", backend_type="oracle_native_music", session_id="track-1", resume_action="restore_volume", restore_volume_level=42)]
        handoff = pipeline_runtime.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="borrow",
            interrupted_sessions=interrupted,
            resume_policy="resume_previous",
            foreground_session_id="reply-1",
            authority_correlation_id="corr-1",
        )

        with patch("pi_runtime.pipeline_runtime.run_request_pipeline", return_value=request_result), \
             patch("pi_runtime.pipeline_runtime.begin_foreground_handoff", return_value=handoff), \
             patch("pi_runtime.pipeline_runtime.prepare_interrupted_playback_for_reply", return_value=[pipeline_runtime.InterruptedPlayback(kind="music", backend_type="oracle_native_music", session_id="track-1", resume_action="resume")]) as mock_prepare, \
             patch("pi_runtime.pipeline_runtime.finalize_foreground_handoff") as mock_finalize_handoff, \
             patch.object(pipeline._reply_runtime, "play_reply", return_value=types.SimpleNamespace(interrupted_playback=[], playback_elapsed_ms=1.0)):
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0, interrupted_playback=interrupted)

        self.assertEqual(mock_prepare.call_count, 1)
        mock_finalize_handoff.assert_called_once()

    def test_capture_pipeline_resumes_audiobook_after_sleep_timer_reply(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
            ack_tone_enabled=False,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        outcome = CommandOutcome(
            transcript="set a sleep timer for 20 minutes",
            spoken_reply="Sleep timer set for 20 minutes.",
            raw_response={
                "dispatch": {
                    "target": "audiobook",
                    "status": "executed",
                    "result": {"action": "sleep_timer", "operation": "create"},
                }
            },
        )
        request_result = types.SimpleNamespace(
            transcript="set a sleep timer for 20 minutes",
            outcome=outcome,
            tts_wav=b"wav",
            stt_elapsed_ms=1.0,
            command_elapsed_ms=1.0,
            tts_elapsed_ms=1.0,
        )
        interrupted = [
            pipeline_runtime.InterruptedPlayback(
                kind="audiobook",
                backend_type="oracle_audiobook",
                session_id="book-1",
                resume_action="resume_longform_audio",
            )
        ]
        handoff = pipeline_runtime.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="borrow",
            interrupted_sessions=interrupted,
            resume_policy="resume_previous",
            foreground_session_id="reply-1",
            authority_correlation_id="corr-1",
        )

        with patch("pi_runtime.pipeline_runtime.run_request_pipeline", return_value=request_result), \
             patch("pi_runtime.pipeline_runtime.begin_foreground_handoff", return_value=handoff), \
             patch.object(pipeline._reply_runtime, "play_reply", return_value=types.SimpleNamespace(interrupted_playback=interrupted, playback_elapsed_ms=1.0)), \
             patch("pi_runtime.pipeline_runtime.finalize_foreground_handoff") as mock_finalize_handoff:
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0, interrupted_playback=interrupted)

        mock_finalize_handoff.assert_called_once()

    def test_capture_pipeline_finalizes_handoff_when_reply_playback_raises(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
            ack_tone_enabled=False,
            error_tone_enabled=False,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        outcome = CommandOutcome(
            transcript="what time is it",
            spoken_reply="It is 3 PM.",
            raw_response={"dispatch": {"target": "system", "status": "executed", "result": {"action": "current_time"}}},
        )
        request_result = types.SimpleNamespace(
            transcript="what time is it",
            outcome=outcome,
            tts_wav=b"wav",
            stt_elapsed_ms=1.0,
            command_elapsed_ms=1.0,
            tts_elapsed_ms=1.0,
        )
        handoff = pipeline_runtime.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="borrow",
            interrupted_sessions=[],
            resume_policy="no_resume",
            foreground_session_id="reply-1",
            authority_correlation_id="corr-1",
        )

        with patch("pi_runtime.pipeline_runtime.run_request_pipeline", return_value=request_result), \
             patch("pi_runtime.pipeline_runtime.begin_foreground_handoff", return_value=handoff), \
             patch("pi_runtime.pipeline_runtime.finalize_foreground_handoff") as mock_finalize_handoff, \
             patch.object(pipeline._reply_runtime, "play_reply", side_effect=RuntimeError("output busy")):
            pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0)

        mock_finalize_handoff.assert_called_once()
        self.assertEqual(mock_finalize_handoff.call_args.kwargs["foreground_final_state"], "failed")
        self.assertEqual(mock_finalize_handoff.call_args.kwargs["foreground_reason"], "output busy")

    def test_capture_pipeline_suppresses_upload_ack_for_interrupted_playback(self) -> None:
        args = types.SimpleNamespace(
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
            ack_tone_enabled=True,
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None)
        runtime_state = types.SimpleNamespace(next_error_tone_at=0.0)
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
        interrupted = [pipeline_runtime.InterruptedPlayback(kind="music", backend_type="oracle_native_music", session_id="track-1", resume_action="restore_volume", restore_volume_level=42)]

        with patch("pi_runtime.pipeline_runtime.prepare_interrupted_playback_for_reply", return_value=interrupted), \
             patch("pi_runtime.pipeline_runtime.resume_interrupted_local_playback"), \
             patch("pi_runtime.pipeline_runtime.run_request_pipeline") as mock_run_request_pipeline:
            mock_run_request_pipeline.side_effect = request_runtime.RequestPipelineError(
                kind="stt_failed",
                detail="backend offline",
                should_play_error_tone=False,
            )
            with patch.object(pipeline, "_play_error_tone_if_due"):
                pipeline.process_capture(b"\x00\x00" * 64, capture_elapsed_ms=50.0, interrupted_playback=interrupted)

        self.assertTrue(mock_run_request_pipeline.call_args.kwargs["suppress_ack_tone"])


if __name__ == "__main__":
    unittest.main()
