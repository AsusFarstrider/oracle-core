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
    def test_foreground_audio_request_rejects_invalid_borrow_replace_combination(self) -> None:
        models = __import__("pi_runtime.models", fromlist=["ForegroundAudioRequest"])

        with self.assertRaises(ValueError):
            models.ForegroundAudioRequest(
                kind="reply",
                handoff_mode="borrow",
                interrupt_policy="pause_or_stronger",
                resume_policy="replace_with_deferred",
            )

    def test_foreground_audio_request_rejects_invalid_replace_resume_previous_combination(self) -> None:
        models = __import__("pi_runtime.models", fromlist=["ForegroundAudioRequest"])

        with self.assertRaises(ValueError):
            models.ForegroundAudioRequest(
                kind="timer",
                handoff_mode="replace",
                interrupt_policy="pause_or_stronger",
                resume_policy="resume_previous",
            )

    def test_foreground_audio_request_generates_correlation_id_when_missing(self) -> None:
        models = __import__("pi_runtime.models", fromlist=["ForegroundAudioRequest"])

        request = models.ForegroundAudioRequest(
            kind="ack",
            handoff_mode="borrow",
            interrupt_policy="none",
            resume_policy="no_resume",
        )

        self.assertTrue(request.correlation_id)

    def test_foreground_handoff_generates_authority_correlation_id_when_missing(self) -> None:
        models = __import__("pi_runtime.models", fromlist=["ForegroundHandoff"])

        handoff = models.ForegroundHandoff(
            foreground_kind="ack",
            handoff_mode="borrow",
            interrupted_sessions=[],
            resume_policy="no_resume",
        )

        self.assertTrue(handoff.authority_correlation_id)

    def test_begin_foreground_handoff_without_api_key_is_noop_handoff(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["begin_foreground_handoff"])
        models = __import__("pi_runtime.models", fromlist=["ForegroundAudioRequest"])

        handoff = local_control.begin_foreground_handoff(
            control_url="http://127.0.0.1:8021",
            api_key="",
            request=models.ForegroundAudioRequest(
                kind="reply",
                handoff_mode="borrow",
                interrupt_policy="none",
                resume_policy="no_resume",
                correlation_id="corr-test",
            ),
            settle_seconds=0.0,
            logger=logging.getLogger("test"),
        )

        self.assertEqual(handoff.foreground_kind, "reply")
        self.assertEqual(handoff.interrupted_sessions, [])
        self.assertEqual(handoff.authority_correlation_id, "corr-test")
        self.assertEqual(handoff.foreground_session_id, "")

    def test_foreground_handoff_rejects_invalid_borrow_replace_combination(self) -> None:
        models = __import__("pi_runtime.models", fromlist=["ForegroundHandoff"])

        with self.assertRaises(ValueError):
            models.ForegroundHandoff(
                foreground_kind="reply",
                handoff_mode="borrow",
                interrupted_sessions=[],
                resume_policy="replace_with_deferred",
            )

    def test_foreground_handoff_rejects_invalid_replace_resume_previous_combination(self) -> None:
        models = __import__("pi_runtime.models", fromlist=["ForegroundHandoff"])

        with self.assertRaises(ValueError):
            models.ForegroundHandoff(
                foreground_kind="timer",
                handoff_mode="replace",
                interrupted_sessions=[],
                resume_policy="resume_previous",
            )

    def test_interrupt_local_playback_preserves_restore_volume_level(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["interrupt_local_playback"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)

        with patch.object(
            local_control,
            "send_local_control_command",
            return_value={
                "interrupted_sessions": [
                    {
                        "kind": "music",
                        "backend_type": "oracle_native_music",
                        "session_id": "track-1",
                        "resume_action": "restore_volume",
                        "restore_volume_level": 81,
                        "interruption_token": "token-1",
                    }
                ],
                "interrupted_any": True,
            },
        ):
            interrupted = local_control.interrupt_local_playback(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                settle_seconds=0.0,
                logger=logger,
            )

        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0].kind, "music")
        self.assertEqual(interrupted[0].resume_action, "restore_volume")
        self.assertEqual(interrupted[0].restore_volume_level, 81)
        self.assertEqual(interrupted[0].interruption_token, "token-1")

    def test_interrupt_local_playback_falls_back_to_top_level_interruption_token(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["interrupt_local_playback"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)

        with patch.object(
            local_control,
            "send_local_control_command",
            return_value={
                "interruption_token": "top-level-token",
                "interrupted_sessions": [
                    {
                        "kind": "audiobook",
                        "backend_type": "oracle_audiobook",
                        "session_id": "book-1",
                        "resume_action": "restore_volume",
                        "restore_volume_level": 48,
                        "interrupt_action": "duck",
                        "state": "playing",
                    }
                ],
                "interrupted_any": True,
            },
        ):
            interrupted = local_control.interrupt_local_playback(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                settle_seconds=0.0,
                logger=logger,
            )

        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0].session_id, "book-1")
        self.assertEqual(interrupted[0].interruption_token, "top-level-token")
        self.assertEqual(interrupted[0].interrupt_action, "duck")
        self.assertEqual(interrupted[0].playback_state, "playing")

    def test_interrupt_local_playback_logs_authority_mismatch_for_missing_lineage(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["interrupt_local_playback"])
        logger = logging.getLogger("test.interrupt_local_playback")

        with patch.object(
            local_control,
            "send_local_control_command",
            return_value={
                "ok": True,
                "command_id": "cmd-1",
                "state": "accepted",
                "interrupted_any": True,
                "active_session_count": 1,
                "interrupted_sessions": [],
            },
        ), patch.object(local_control, "fetch_local_longform_state", return_value={"state": "stopped", "playing": False}), patch.object(
            local_control,
            "fetch_local_music_state",
            return_value={"state": "stopped", "playing": False},
        ), self.assertLogs(level="WARNING") as captured:
            interrupted = local_control.interrupt_local_playback(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                settle_seconds=0.0,
                logger=logger,
            )

        self.assertEqual(interrupted, [])
        output = "\n".join(captured.output)
        self.assertIn("failure_path_selected", output)
        self.assertIn("failure_class=authority_mismatch", output)
        self.assertIn("owning_component=satellite.playback_authority", output)

    def test_resume_interrupted_local_playback_forwards_restore_volume_level(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["resume_interrupted_local_playback"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        interrupted = [
            local_control.InterruptedPlayback(
                kind="music",
                backend_type="oracle_native_music",
                session_id="track-1",
                resume_action="restore_volume",
                restore_volume_level=81,
                interruption_token="token-1",
            )
        ]

        with patch.object(local_control, "send_local_control_command", return_value={"ok": True}) as mock_send:
            local_control.resume_interrupted_local_playback(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                interrupted=interrupted,
                logger=logger,
            )

        self.assertEqual(mock_send.call_count, 1)
        sent_args = mock_send.call_args.args
        self.assertEqual(sent_args[2], "resume_after_oracle")
        self.assertEqual(
            sent_args[3],
            {
                "interrupted_sessions": [
                    {
                        "kind": "music",
                        "backend_type": "oracle_native_music",
                        "session_id": "track-1",
                        "resume_action": "restore_volume",
                        "restore_volume_level": 81,
                        "interruption_token": "token-1",
                        "interrupted_by_session_id": "",
                        "superseded_by_session_id": "",
                    }
                ]
            },
        )

    def test_resume_interrupted_local_playback_skips_local_superseded_sessions_when_authority_unavailable(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["resume_interrupted_local_playback"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        interrupted = [
            local_control.InterruptedPlayback(
                kind="music",
                backend_type="oracle_native_music",
                session_id="track-1",
                resume_action="resume",
                superseded_by_session_id="reply-2",
            )
        ]

        class _FakeRequestException(RuntimeError):
            pass

        with patch.object(local_control.requests, "RequestException", _FakeRequestException, create=True):
            with patch.object(local_control, "send_local_control_command", side_effect=_FakeRequestException("offline")) as mock_send:
                local_control.resume_interrupted_local_playback(
                    control_url="http://127.0.0.1:8021",
                    api_key="test-key",
                    interrupted=interrupted,
                    logger=logger,
                )

        self.assertEqual(mock_send.call_count, 1)

    def test_interrupt_local_playback_fallback_preserves_authority_audiobook_session_id(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["interrupt_local_playback"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)

        with patch.object(
            local_control,
            "send_local_control_command",
            side_effect=[
                {"ok": True, "interrupted_any": False, "active_session_count": 1, "interrupted_sessions": []},
                {"ok": True, "state": "paused", "command_id": "pause-book-1"},
            ],
        ), patch.object(
            local_control,
            "fetch_local_longform_state",
            return_value={"session_id": "book-1", "state": "playing", "playing": True},
        ), patch.object(
            local_control,
            "fetch_local_music_state",
            return_value={"state": "stopped", "playing": False},
        ):
            interrupted = local_control.interrupt_local_playback(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                settle_seconds=0.0,
                logger=logger,
            )

        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0].session_id, "book-1")

    def test_interrupt_local_playback_fallback_preserves_authority_music_session_id(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["interrupt_local_playback"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)

        with patch.object(
            local_control,
            "send_local_control_command",
            side_effect=[
                {"ok": True, "interrupted_any": False, "active_session_count": 1, "interrupted_sessions": []},
                {"ok": True, "state": "paused", "command_id": "pause-track-1"},
            ],
        ), patch.object(
            local_control,
            "fetch_local_longform_state",
            return_value={"state": "stopped", "playing": False},
        ), patch.object(
            local_control,
            "fetch_local_music_state",
            return_value={
                "session_id": "track-1",
                "backend_type": "oracle_native_music",
                "state": "playing",
                "playing": True,
            },
        ):
            interrupted = local_control.interrupt_local_playback(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                settle_seconds=0.0,
                logger=logger,
            )

        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0].session_id, "track-1")

    def test_resume_interrupted_local_playback_falls_back_after_authority_rejection(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["resume_interrupted_local_playback"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        interrupted = [
            local_control.InterruptedPlayback(
                kind="audiobook",
                backend_type="oracle_audiobook",
                session_id="book-1",
                resume_action="resume_longform_audio",
                interruption_token="token-1",
            )
        ]

        with patch.object(
            local_control,
            "send_local_control_command",
            side_effect=[
                {
                    "ok": True,
                    "resumed_any": False,
                    "resumed_sessions": [],
                    "skipped_sessions": [
                        {
                            "backend_type": "oracle_audiobook",
                            "session_id": "book-1",
                            "resume_action": "resume_longform_audio",
                            "skip_reason": "missing_ledger_entry",
                        }
                    ],
                },
                {"ok": True, "state": "playing", "command_id": "resume-book-1"},
            ],
        ) as mock_send:
            local_control.resume_interrupted_local_playback(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                interrupted=interrupted,
                logger=logger,
            )

        self.assertEqual(mock_send.call_count, 2)
        self.assertEqual(mock_send.call_args_list[1].args[2], "resume_longform_audio")

    def test_prepare_interrupted_playback_for_reply_promotes_ducked_music_to_pause(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["prepare_interrupted_playback_for_reply"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        interrupted = [
            local_control.InterruptedPlayback(
                kind="music",
                backend_type="oracle_native_music",
                session_id="track-1",
                resume_action="restore_volume",
                restore_volume_level=81,
            )
        ]

        with patch.object(local_control, "send_local_control_command", return_value={"ok": True}) as mock_send:
            prepared = local_control.prepare_interrupted_playback_for_reply(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                interrupted=interrupted,
                settle_seconds=0.0,
                logger=logger,
            )

        self.assertEqual(mock_send.call_count, 2)
        self.assertEqual(mock_send.call_args_list[0].args, ("http://127.0.0.1:8021", "test-key", "set_volume", {"level": 81}))
        self.assertEqual(mock_send.call_args_list[1].args, ("http://127.0.0.1:8021", "test-key", "pause"))
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].resume_action, "resume")

    def test_prepare_interrupted_playback_for_reply_promotes_ducked_audiobook_to_pause(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["prepare_interrupted_playback_for_reply"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        interrupted = [
            local_control.InterruptedPlayback(
                kind="audiobook",
                backend_type="oracle_audiobook",
                session_id="book-1",
                resume_action="restore_volume",
                restore_volume_level=81,
                interruption_token="token-book-1",
                interrupt_action="duck",
                playback_state="playing",
            )
        ]

        with patch.object(local_control, "send_local_control_command", return_value={"ok": True}) as mock_send:
            prepared = local_control.prepare_interrupted_playback_for_reply(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                interrupted=interrupted,
                settle_seconds=0.0,
                logger=logger,
            )

        self.assertEqual(mock_send.call_args_list[0].args, ("http://127.0.0.1:8021", "test-key", "set_volume", {"level": 81}))
        self.assertEqual(mock_send.call_args_list[1].args, ("http://127.0.0.1:8021", "test-key", "pause_longform_audio"))
        self.assertEqual(prepared[0].resume_action, "resume_longform_audio")
        self.assertEqual(prepared[0].session_id, "book-1")
        self.assertEqual(prepared[0].backend_type, "oracle_audiobook")
        self.assertEqual(prepared[0].interruption_token, "token-book-1")
        self.assertEqual(prepared[0].interrupt_action, "pause_longform_audio")
        self.assertEqual(prepared[0].playback_state, "paused")

    def test_resume_interrupted_local_playback_preserves_audiobook_lineage_after_duck_to_pause(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["resume_interrupted_local_playback"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        interrupted = [
            local_control.InterruptedPlayback(
                kind="audiobook",
                backend_type="oracle_audiobook",
                session_id="book-1",
                resume_action="resume_longform_audio",
                interruption_token="token-book-1",
                interrupt_action="pause_longform_audio",
                playback_state="paused",
            )
        ]

        with patch.object(local_control, "send_local_control_command", return_value={"ok": True}) as mock_send:
            local_control.resume_interrupted_local_playback(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                interrupted=interrupted,
                logger=logger,
            )

        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(
            mock_send.call_args.args[3],
            {
                "interrupted_sessions": [
                    {
                        "kind": "audiobook",
                        "backend_type": "oracle_audiobook",
                        "session_id": "book-1",
                        "resume_action": "resume_longform_audio",
                        "restore_volume_level": None,
                        "interruption_token": "token-book-1",
                        "interrupted_by_session_id": "",
                        "superseded_by_session_id": "",
                    }
                ]
            },
        )

    def test_finalize_foreground_handoff_logs_one_authoritative_decision(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["ForegroundHandoff", "InterruptedPlayback", "finalize_foreground_handoff"])
        logger = __import__("logging").getLogger("foreground-handoff-log-test")
        handoff = local_control.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="borrow",
            interrupted_sessions=[
                local_control.InterruptedPlayback(
                    kind="audiobook",
                    backend_type="oracle_audiobook",
                    session_id="book-1",
                    resume_action="resume_longform_audio",
                    interruption_token="token-book-1",
                )
            ],
            resume_policy="resume_previous",
            authority_correlation_id="corr-1",
        )

        with patch.object(local_control, "resume_interrupted_local_playback") as mock_resume:
            with self.assertLogs("foreground-handoff-log-test", level="INFO") as captured:
                local_control.finalize_foreground_handoff(
                    control_url="http://127.0.0.1:8021",
                    api_key="test-key",
                    handoff=handoff,
                    logger=logger,
                )

        mock_resume.assert_called_once()
        decision_logs = [line for line in captured.output if "foreground_handoff" in line]
        self.assertEqual(len(decision_logs), 1)

    def test_finalize_foreground_handoff_validates_reply_finalize_response(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["ForegroundHandoff", "finalize_foreground_handoff"])
        handoff = local_control.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="borrow",
            interrupted_sessions=[],
            resume_policy="no_resume",
            foreground_session_id="reply-1",
        )
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)

        with patch.object(local_control, "finalize_reply_audio", return_value={"ok": True, "state": "accepted"}):
            with self.assertRaisesRegex(RuntimeError, "command_id"):
                local_control.finalize_foreground_handoff(
                    control_url="http://127.0.0.1:8021",
                    api_key="test-key",
                    handoff=handoff,
                    logger=logger,
                    foreground_final_state="completed",
                )

    def test_finalize_foreground_handoff_logs_deferred_replacement_lineage(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["ForegroundHandoff", "InterruptedPlayback", "finalize_foreground_handoff"])
        logger = __import__("logging").getLogger("foreground-handoff-replace-log-test")
        handoff = local_control.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="replace",
            interrupted_sessions=[],
            resume_policy="replace_with_deferred",
            authority_correlation_id="corr-2",
            foreground_session_id="reply-1",
        )
        deferred = local_control.InterruptedPlayback(
            kind="audiobook",
            backend_type="oracle_audiobook",
            session_id="book-1",
            resume_action="resume_longform_audio",
        )

        with patch.object(local_control, "finalize_reply_audio", return_value={"ok": True, "state": "accepted", "command_id": "cmd-1"}), \
             patch.object(local_control, "resume_deferred_transport_after_reply") as mock_resume:
            with self.assertLogs("foreground-handoff-replace-log-test", level="INFO") as captured:
                local_control.finalize_foreground_handoff(
                    control_url="http://127.0.0.1:8021",
                    api_key="test-key",
                    handoff=handoff,
                    logger=logger,
                    deferred_resume=deferred,
                )

        mock_resume.assert_called_once()
        decision_logs = [line for line in captured.output if "foreground_handoff" in line]
        self.assertEqual(len(decision_logs), 1)
        self.assertIn("handoff_mode=replace", decision_logs[0])
        self.assertIn("foreground_session_id=reply-1", decision_logs[0])
        self.assertIn("resume_outcome=replace_with_deferred", decision_logs[0])
        self.assertIn("deferred_resume=oracle_audiobook:book-1:-", decision_logs[0])
        self.assertIn("authority_correlation_id=corr-2", decision_logs[0])

    def test_finalize_foreground_handoff_rejects_missing_deferred_resume_for_replace(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["ForegroundHandoff", "finalize_foreground_handoff"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        handoff = local_control.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="replace",
            interrupted_sessions=[],
            resume_policy="replace_with_deferred",
            authority_correlation_id="corr-1",
            foreground_session_id="reply-1",
        )

        with self.assertRaises(ValueError):
            local_control.finalize_foreground_handoff(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                handoff=handoff,
                logger=logger,
            )

    def test_finalize_foreground_handoff_rejects_deferred_resume_for_non_replace(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["ForegroundHandoff", "InterruptedPlayback", "finalize_foreground_handoff"])
        logger = types.SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
        handoff = local_control.ForegroundHandoff(
            foreground_kind="reply",
            handoff_mode="borrow",
            interrupted_sessions=[],
            resume_policy="resume_previous",
            authority_correlation_id="corr-1",
            foreground_session_id="reply-1",
        )
        deferred = local_control.InterruptedPlayback(
            kind="audiobook",
            backend_type="oracle_audiobook",
            session_id="book-1",
            resume_action="resume_longform_audio",
        )

        with self.assertRaises(ValueError):
            local_control.finalize_foreground_handoff(
                control_url="http://127.0.0.1:8021",
                api_key="test-key",
                handoff=handoff,
                logger=logger,
                deferred_resume=deferred,
            )

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
        )
        self.assertTrue(should_resume_after_reply_for_transport_command(outcome))

    def test_extract_deferred_transport_resume_returns_audiobook_resume(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["extract_deferred_transport_resume"])
        outcome = CommandOutcome(
            transcript="play dune",
            spoken_reply="Playing Dune by Frank Herbert.",
            raw_response={
                "dispatch": {
                    "target": "audiobook",
                    "status": "executed",
                    "result": {
                        "action": "play",
                        "deferred_audible_start": True,
                        "deferred_session": {
                            "kind": "audiobook",
                            "backend_type": "oracle_audiobook",
                            "session_id": "book-1",
                            "resume_action": "resume_longform_audio",
                        },
                    },
                }
            },
        )

        deferred = local_control.extract_deferred_transport_resume(outcome)

        self.assertIsNotNone(deferred)
        assert deferred is not None
        self.assertEqual(deferred.kind, "audiobook")
        self.assertEqual(deferred.backend_type, "oracle_audiobook")
        self.assertEqual(deferred.session_id, "book-1")
        self.assertEqual(deferred.resume_action, "resume_longform_audio")

    def test_extract_deferred_transport_resume_returns_music_play(self) -> None:
        local_control = __import__("pi_runtime.local_control", fromlist=["extract_deferred_transport_resume"])
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
                            "resume_args": {
                                "query": "Fortunate Son",
                                "media_type": "track",
                            },
                        },
                    },
                }
            },
        )

        deferred = local_control.extract_deferred_transport_resume(outcome)

        self.assertIsNotNone(deferred)
        assert deferred is not None
        self.assertEqual(deferred.kind, "music")
        self.assertEqual(deferred.backend_type, "plexamp")
        self.assertEqual(deferred.session_id, "track-1")
        self.assertEqual(deferred.resume_action, "play_media")
        self.assertEqual(deferred.resume_args, {"query": "Fortunate Son", "media_type": "track"})

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
                raw_response={
                    "dispatch": {
                        "target": "audiobook",
                        "status": "executed",
                        "result": {
                            "action": "play",
                            "deferred_audible_start": True,
                            "deferred_session": {
                                "kind": "audiobook",
                                "backend_type": "oracle_audiobook",
                                "session_id": "book-1",
                                "resume_action": "resume_longform_audio",
                            },
                        },
                    }
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
        self.assertEqual(deferred.resume_action, "resume_longform_audio")

    def test_should_resume_after_reply_for_transport_command_false_for_music_stop(self) -> None:
        outcome = CommandOutcome(
            transcript="stop music",
            spoken_reply="Stopped.",
            raw_response={"dispatch": {"target": "music", "result": {"action": "stop"}}},
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
        self.assertEqual(deferred.resume_action, "play_media")
        self.assertEqual(deferred.resume_args, {"query": "Fortunate Son"})

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
        self.assertEqual(deferred.session_id, "track-2")

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
        self.assertIn("route_target=system", output)
        self.assertIn("dispatch_hook=system.current_time", output)
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
        self.assertIn("dispatch_hook=system.current_time", output)
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
        self.assertIn("dispatch_hook=system.current_time", output)
        self.assertIn("route_target=system", output)

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
