from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SATELLITE_PATH = ROOT / "satellite"

sys.path.insert(0, str(SATELLITE_PATH))
sys.modules.setdefault("requests", types.SimpleNamespace(RequestException=Exception))
numpy_module = types.ModuleType("numpy")
numpy_module.ndarray = object
sys.modules.setdefault("numpy", numpy_module)
sys.modules.setdefault("sounddevice", types.SimpleNamespace())
openwakeword_module = types.ModuleType("openwakeword")
openwakeword_model_module = types.ModuleType("openwakeword.model")
openwakeword_model_module.Model = object
openwakeword_module.model = openwakeword_model_module
sys.modules.setdefault("openwakeword", openwakeword_module)
sys.modules.setdefault("openwakeword.model", openwakeword_model_module)

import pi_runtime.alerts_runtime as alerts_runtime


class SatelliteAlertsRuntimeTests(unittest.TestCase):
    def _build_args(self, **overrides):
        base = dict(
            oracle_url="http://127.0.0.1:8011",
            brain_api_key="brain-token",
            source="test_satellite_server",
            output_device_index=None,
            playback_gain=0.35,
            music_control_url="http://127.0.0.1:8021",
            music_control_api_key="test-key",
            playback_interrupt_settle_seconds=0.0,
            reply_audio_state_path="/tmp/reply-audio-state.json",
            reply_audio_stop_path="/tmp/reply-audio-stop.flag",
            post_playback_block_seconds=2.0,
            alerts_poll_seconds=2.0,
            alarm_sound_path=str(ROOT / "satellite" / "sounds" / "alarm.wav"),
            timer_sound_path=str(ROOT / "satellite" / "sounds" / "timer.wav"),
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def _build_logger(self):
        return SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        )

    def test_timer_alert_prefers_local_sound(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        with patch("pathlib.Path.read_bytes", return_value=b"wav-data") as mock_read, patch.object(
            alerts_runtime, "play_wav_bytes"
        ) as mock_play, patch.object(alerts_runtime, "request_tts") as mock_tts:
            alerts_runtime._play_alert_audio(
                args=args,
                logger=logger,
                alert={"kind": "timer", "message": "Timer finished after 1 minute."},
            )

        mock_read.assert_called_once()
        mock_play.assert_called_once()
        mock_tts.assert_not_called()
        self.assertEqual(mock_play.call_args.kwargs["reply_audio_kind"], "timer")

    def test_timer_alert_falls_back_to_tts_when_sound_missing(self) -> None:
        args = self._build_args(timer_sound_path="/tmp/does-not-exist.mp3")
        logger = self._build_logger()
        with patch.object(alerts_runtime, "play_wav_bytes") as mock_play, patch.object(
            alerts_runtime, "request_tts", return_value=b"tts-wav"
        ) as mock_tts:
            alerts_runtime._play_alert_audio(
                args=args,
                logger=logger,
                alert={"kind": "timer", "message": "Timer finished after 1 minute."},
            )

        mock_tts.assert_called_once_with(
            args.oracle_url,
            "Timer finished after 1 minute.",
            credential="brain-token",
        )
        mock_play.assert_called_once()
        self.assertEqual(mock_play.call_args.kwargs["reply_audio_kind"], "alert")

    def test_alarm_alert_plays_local_sound_then_speaks_due_time(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        with patch("pathlib.Path.read_bytes", return_value=b"wav-data") as mock_read, patch.object(
            alerts_runtime, "play_wav_bytes"
        ) as mock_play, patch.object(alerts_runtime, "request_tts", return_value=b"tts-wav") as mock_tts:
            alerts_runtime._play_alert_audio(
                args=args,
                logger=logger,
                alert={
                    "kind": "alarm",
                    "message": "Alarm for 7:00 AM.",
                    "due_at": "2026-04-04T07:00:00-04:00",
                },
            )

        mock_read.assert_called_once()
        self.assertEqual(mock_play.call_count, 2)
        self.assertEqual(mock_play.call_args_list[0].kwargs["reply_audio_kind"], "alarm")
        self.assertEqual(mock_play.call_args_list[1].kwargs["reply_audio_kind"], "alarm")
        mock_tts.assert_called_once_with(
            args.oracle_url,
            "It's 7:00 AM.",
            credential="brain-token",
        )

    def test_alarm_followup_falls_back_to_message_when_due_time_invalid(self) -> None:
        followup = alerts_runtime._build_alarm_followup_text(
            {"kind": "alarm", "message": "Alarm for 7:00 AM.", "due_at": "not-a-date"}
        )

        self.assertEqual(followup, "Alarm for 7:00 AM.")

    def test_notification_uses_brain_tts(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        with patch.object(
            alerts_runtime,
            "request_tts",
            return_value=b"tts-wav",
        ) as mock_tts, patch.object(alerts_runtime, "play_wav_bytes") as mock_play:
            alerts_runtime._play_alert_audio(
                args=args,
                logger=logger,
                alert={
                    "kind": "notification",
                    "message": "The side entry is still open. Please close it.",
                },
            )

        mock_tts.assert_called_once_with(
            args.oracle_url,
            "The side entry is still open. Please close it.",
            credential="brain-token",
        )
        self.assertEqual(mock_play.call_args.kwargs["reply_audio_kind"], "alert")

    def test_build_alert_foreground_request_uses_replace_no_resume(self) -> None:
        request = alerts_runtime._build_alert_foreground_request(
            alert={"kind": "timer", "alert_id": "timer-1"}
        )

        self.assertEqual(request.kind, "timer")
        self.assertEqual(request.handoff_mode, "replace")
        self.assertEqual(request.interrupt_policy, "pause_or_stronger")
        self.assertEqual(request.resume_policy, "no_resume")
        self.assertEqual(request.correlation_id, "timer-1")

    def test_notification_borrows_speaker_and_resumes_previous_media(self) -> None:
        request = alerts_runtime._build_alert_foreground_request(
            alert={"kind": "notification", "alert_id": "notification-1"}
        )

        self.assertEqual(request.kind, "notification")
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.interrupt_policy, "pause_or_stronger")
        self.assertEqual(request.resume_policy, "resume_previous")
        self.assertEqual(request.correlation_id, "notification-1")

    def test_build_sleep_expiry_foreground_request_uses_stop_required(self) -> None:
        request = alerts_runtime._build_sleep_expiry_foreground_request(
            alert={"kind": "sleep_timer", "alert_id": "sleep-1"}
        )

        self.assertEqual(request.kind, "sleep_expiry")
        self.assertEqual(request.handoff_mode, "replace")
        self.assertEqual(request.interrupt_policy, "stop_required")
        self.assertEqual(request.resume_policy, "no_resume")
        self.assertEqual(request.correlation_id, "sleep-1")

    def test_due_timer_interrupts_local_playback_before_sounding(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        runtime_state = SimpleNamespace(next_alert_poll_at=0.0, next_wake_time=0.0)
        handoff = SimpleNamespace(interrupted_sessions=[])
        with patch.object(alerts_runtime, "fetch_pending_alerts", return_value=[{"kind": "timer", "message": "Done", "alert_id": "timer-1"}]), patch.object(
            alerts_runtime, "begin_foreground_handoff", return_value=handoff
        ) as mock_begin, patch.object(alerts_runtime, "finalize_foreground_handoff") as mock_finalize, patch.object(
            alerts_runtime, "_play_alert_audio"
        ) as mock_play, patch.object(alerts_runtime, "clear_audio_queue") as mock_clear:
            alerts_runtime.poll_due_alerts_if_needed(
                args=args,
                logger=logger,
                frame_queue=object(),
                pre_roll=object(),
                runtime_state=runtime_state,
            )

        mock_begin.assert_called_once()
        request = mock_begin.call_args.kwargs["request"]
        self.assertEqual(request.kind, "timer")
        self.assertEqual(request.handoff_mode, "replace")
        self.assertEqual(request.resume_policy, "no_resume")
        mock_play.assert_called_once()
        mock_finalize.assert_called_once_with(
            control_url=args.music_control_url,
            api_key=args.music_control_api_key,
            handoff=handoff,
            logger=logger,
        )
        mock_clear.assert_called_once()

    def test_due_notification_finalizes_resume_handoff_after_speaking(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        runtime_state = SimpleNamespace(next_alert_poll_at=0.0, next_wake_time=0.0)
        handoff = SimpleNamespace(interrupted_sessions=[object()])
        alert = {
            "kind": "notification",
            "message": "The side entry is still open. Please close it.",
            "alert_id": "notification-1",
            "metadata": {"audio_policy": "pause_resume"},
        }
        with patch.object(
            alerts_runtime,
            "fetch_pending_alerts",
            return_value=[alert],
        ), patch.object(
            alerts_runtime,
            "begin_foreground_handoff",
            return_value=handoff,
        ) as mock_begin, patch.object(
            alerts_runtime,
            "finalize_foreground_handoff",
        ) as mock_finalize, patch.object(
            alerts_runtime,
            "_play_alert_audio",
        ) as mock_play, patch.object(
            alerts_runtime,
            "clear_audio_queue",
        ):
            alerts_runtime.poll_due_alerts_if_needed(
                args=args,
                logger=logger,
                frame_queue=object(),
                pre_roll=object(),
                runtime_state=runtime_state,
            )

        request = mock_begin.call_args.kwargs["request"]
        self.assertEqual(request.kind, "notification")
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.resume_policy, "resume_previous")
        mock_play.assert_called_once_with(args=args, logger=logger, alert=alert)
        mock_finalize.assert_called_once_with(
            control_url=args.music_control_url,
            api_key=args.music_control_api_key,
            handoff=handoff,
            logger=logger,
        )

    def test_sleep_timer_prefers_brain_stop_so_audiobookshelf_syncs(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        handoff = SimpleNamespace(interrupted_sessions=[])
        with patch.object(
            alerts_runtime,
            "begin_foreground_handoff",
            return_value=handoff,
        ) as mock_begin, patch.object(
            alerts_runtime,
            "send_local_control_command",
            return_value={"ok": True, "state": "stopped", "playback_id": "book-1"},
        ) as mock_local_stop, patch.object(alerts_runtime, "send_silent_audiobook_stop") as mock_fallback, patch.object(
            alerts_runtime, "finalize_foreground_handoff"
        ) as mock_finalize:
            alerts_runtime._stop_audiobook_for_sleep_timer(
                args=args,
                logger=logger,
                alert={"kind": "sleep_timer", "alert_id": "alert-1"},
            )

        mock_begin.assert_not_called()
        mock_fallback.assert_called_once_with(
            args.oracle_url,
            args.source,
            "alert-1",
            credential="brain-token",
        )
        mock_local_stop.assert_not_called()
        mock_finalize.assert_not_called()

    def test_sleep_timer_falls_back_to_local_longform_stop_when_brain_stop_fails(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        handoff = SimpleNamespace(interrupted_sessions=[])
        with patch.object(
            alerts_runtime,
            "begin_foreground_handoff",
            return_value=handoff,
        ) as mock_begin, patch.object(
            alerts_runtime,
            "send_local_control_command",
            return_value={"ok": True, "state": "stopped", "playback_id": "book-1"},
        ) as mock_local_stop, patch.object(
            alerts_runtime,
            "send_silent_audiobook_stop",
            side_effect=alerts_runtime.requests.RequestException("brain unavailable"),
        ) as mock_brain_stop, patch.object(alerts_runtime, "finalize_foreground_handoff") as mock_finalize:
            alerts_runtime._stop_audiobook_for_sleep_timer(
                args=args,
                logger=logger,
                alert={"kind": "sleep_timer", "alert_id": "alert-1"},
            )

        mock_begin.assert_called_once()
        mock_brain_stop.assert_called_once_with(
            args.oracle_url,
            args.source,
            "alert-1",
            credential="brain-token",
        )
        mock_local_stop.assert_called_once_with(
            args.music_control_url,
            args.music_control_api_key,
            "stop_longform_audio",
        )
        mock_finalize.assert_called_once_with(
            control_url=args.music_control_url,
            api_key=args.music_control_api_key,
            handoff=handoff,
            logger=logger,
        )


if __name__ == "__main__":
    unittest.main()
