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


if __name__ == "__main__":
    unittest.main()
