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
        self.assertEqual(mock_play.call_count, 2)
        mock_tts.assert_called_once_with(
            args.oracle_url,
            "Timer finished after 1 minute.",
            credential="brain-token",
        )
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
            "Alarm for 7:00 AM. It's 7:00 AM.",
            credential="brain-token",
        )

    def test_alarm_uses_borrowing_handoff_and_requests_display_attention(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        runtime_state = SimpleNamespace(next_alert_poll_at=0.0, next_wake_time=0.0)
        handoff = SimpleNamespace(interrupted_sessions=[])
        alert = {
            "kind": "alarm", "message": "Your work alarm is going off.",
            "alert_id": "alarm-1", "lease_id": "lease-1",
            "metadata": {"occurrence_id": "alarm-occurrence-1"},
        }
        with patch.object(alerts_runtime, "claim_due_alerts", return_value=[alert]), patch.object(
            alerts_runtime, "fetch_alert_state", return_value={"ringing": [{**alert, "occurrence_id": "alarm-occurrence-1"}]}
        ), patch.object(alerts_runtime, "begin_foreground_handoff", return_value=handoff) as mock_begin, patch.object(
            alerts_runtime, "_play_alert_audio"
        ), patch.object(alerts_runtime, "clear_audio_queue"), patch.object(
            alerts_runtime, "acknowledge_alert"
        ), patch.object(alerts_runtime, "set_alarm_display_attention", return_value=True) as mock_attention:
            alerts_runtime.poll_due_alerts_if_needed(
                args=args, logger=logger, frame_queue=object(), pre_roll=object(), runtime_state=runtime_state
            )
        request = mock_begin.call_args.kwargs["request"]
        self.assertEqual((request.kind, request.handoff_mode, request.resume_policy), ("alarm", "borrow", "resume_previous"))
        mock_attention.assert_called_with(True)

    def test_alarm_followup_falls_back_to_message_when_due_time_invalid(self) -> None:
        followup = alerts_runtime._build_alarm_followup_text(
            {"kind": "alarm", "message": "Alarm for 7:00 AM.", "due_at": "not-a-date"}
        )

        self.assertEqual(followup, "Alarm for 7:00 AM.")

    def test_alarm_followup_formats_due_time_without_platform_specific_directives(self) -> None:
        followup = alerts_runtime._build_alarm_followup_text(
            {
                "kind": "alarm",
                "message": "Your work alarm is going off.",
                "due_at": "2026-08-29T20:29:00-04:00",
            }
        )

        self.assertEqual(followup, "Your work alarm is going off. It's 8:29 PM.")

    def test_alarm_followup_prefers_canonical_intended_local_over_utc_due_at(self) -> None:
        followup = alerts_runtime._build_alarm_followup_text(
            {
                "kind": "alarm",
                "message": "Your work alarm is going off.",
                "due_at": "2026-09-01T19:56:00+00:00",
                "metadata": {"intended_local": "2026-09-01T15:56:00"},
            }
        )

        self.assertEqual(followup, "Your work alarm is going off. It's 3:56 PM.")

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

    def test_reminder_plays_chime_then_speaks_with_late_context(self) -> None:
        args = self._build_args()
        with patch.object(alerts_runtime, "play_ack_tone") as mock_chime, patch.object(
            alerts_runtime, "_play_tts_alert"
        ) as mock_tts:
            alerts_runtime._play_alert_audio(
                args=args, logger=self._build_logger(),
                alert={"kind": "reminder", "message": "Reminder: take medicine.", "metadata": {"late_seconds": 120}},
            )
        mock_chime.assert_called_once()
        self.assertIn("delayed about 2 minutes", mock_tts.call_args.kwargs["message"])
        self.assertEqual(mock_tts.call_args.kwargs["reply_audio_kind"], "reminder")

    def test_reminder_borrows_media_and_releases_display_attention_after_speech(self) -> None:
        args = self._build_args()
        runtime_state = SimpleNamespace(next_alert_poll_at=0.0, next_wake_time=0.0)
        handoff = SimpleNamespace(interrupted_sessions=[])
        alert = {"kind": "reminder", "message": "Reminder: stretch.", "alert_id": "r-1", "lease_id": "l-1", "metadata": {"occurrence_id": "o-1"}}
        with patch.object(alerts_runtime, "claim_due_alerts", return_value=[alert]), patch.object(
            alerts_runtime, "fetch_alert_state", return_value={"ringing": []}
        ), patch.object(alerts_runtime, "begin_foreground_handoff", return_value=handoff) as mock_begin, patch.object(
            alerts_runtime, "finalize_foreground_handoff"
        ) as mock_finalize, patch.object(alerts_runtime, "_play_alert_audio"), patch.object(
            alerts_runtime, "clear_audio_queue"
        ), patch.object(alerts_runtime, "acknowledge_alert") as mock_ack, patch.object(
            alerts_runtime, "set_alarm_display_attention", return_value=True
        ) as mock_attention:
            alerts_runtime.poll_due_alerts_if_needed(
                args=args, logger=self._build_logger(), frame_queue=object(), pre_roll=object(), runtime_state=runtime_state,
            )
        request = mock_begin.call_args.kwargs["request"]
        self.assertEqual((request.kind, request.handoff_mode, request.resume_policy), ("reminder", "borrow", "resume_previous"))
        self.assertEqual([call.args[0] for call in mock_attention.call_args_list], [True, False])
        mock_finalize.assert_called_once()
        self.assertIsNotNone(runtime_state.active_session_id)
        mock_ack.assert_called_once_with(
            args.oracle_url, args.source, "r-1", "l-1", credential="brain-token",
            status="completed", session_id=runtime_state.active_session_id,
        )

    def test_build_timer_foreground_request_borrows_and_resumes_suitable_media(self) -> None:
        request = alerts_runtime._build_alert_foreground_request(
            alert={"kind": "timer", "alert_id": "timer-1"}
        )

        self.assertEqual(request.kind, "timer")
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.interrupt_policy, "pause_or_stronger")
        self.assertEqual(request.resume_policy, "resume_previous")
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

    def test_due_timer_interrupts_local_playback_before_sounding(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        runtime_state = SimpleNamespace(next_alert_poll_at=0.0, next_wake_time=0.0)
        handoff = SimpleNamespace(interrupted_sessions=[])
        alert = {
            "kind": "timer", "message": "Done", "alert_id": "timer-1", "lease_id": "lease-1",
            "metadata": {"occurrence_id": "occurrence-1"},
        }
        with patch.object(alerts_runtime, "claim_due_alerts", return_value=[alert]), patch.object(
            alerts_runtime, "begin_foreground_handoff", return_value=handoff
        ) as mock_begin, patch.object(alerts_runtime, "finalize_foreground_handoff") as mock_finalize, patch.object(
            alerts_runtime, "_play_alert_audio"
        ) as mock_play, patch.object(alerts_runtime, "clear_audio_queue") as mock_clear, patch.object(
            alerts_runtime, "acknowledge_alert"
        ) as mock_ack, patch.object(
            alerts_runtime, "fetch_alert_state",
            return_value={"ringing": [{"occurrence_id": "occurrence-1", "status": "ringing"}]},
        ):
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
        self.assertEqual(request.handoff_mode, "borrow")
        self.assertEqual(request.resume_policy, "resume_previous")
        mock_play.assert_called_once()
        mock_finalize.assert_not_called()
        self.assertIs(runtime_state.active_timer_handoff, handoff)
        mock_clear.assert_called_once()
        mock_ack.assert_called_once_with(
            args.oracle_url,
            args.source,
            "timer-1",
            "lease-1",
            credential="brain-token",
            status="acknowledged",
        )

    def test_timer_handoff_resumes_only_after_last_logical_timer_ends(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        handoff = SimpleNamespace(interrupted_sessions=[object()])
        runtime_state = SimpleNamespace(
            next_alert_poll_at=0.0,
            next_wake_time=0.0,
            active_timer_alerts={"occurrence-1": {"occurrence_id": "occurrence-1"}},
            active_timer_handoff=handoff,
        )
        with patch.object(alerts_runtime, "claim_due_alerts", return_value=[]), patch.object(
            alerts_runtime, "fetch_alert_state", return_value={"ringing": []}
        ), patch.object(alerts_runtime, "finalize_foreground_handoff") as mock_finalize:
            alerts_runtime.poll_due_alerts_if_needed(
                args=args,
                logger=logger,
                frame_queue=object(),
                pre_roll=object(),
                runtime_state=runtime_state,
            )

        mock_finalize.assert_called_once_with(
            control_url=args.music_control_url,
            api_key=args.music_control_api_key,
            handoff=handoff,
            logger=logger,
        )
        self.assertIsNone(runtime_state.active_timer_handoff)

    def test_overlapping_timer_expiries_share_one_handoff_and_both_sound(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        runtime_state = SimpleNamespace(next_alert_poll_at=0.0, next_wake_time=0.0)
        handoff = SimpleNamespace(interrupted_sessions=[object()])
        alerts = [
            {
                "kind": "timer", "message": "Pasta", "alert_id": "timer-1",
                "lease_id": "lease-1", "metadata": {"occurrence_id": "occurrence-1"},
            },
            {
                "kind": "timer", "message": "Laundry", "alert_id": "timer-2",
                "lease_id": "lease-2", "metadata": {"occurrence_id": "occurrence-2"},
            },
        ]
        with patch.object(alerts_runtime, "claim_due_alerts", return_value=alerts), patch.object(
            alerts_runtime, "fetch_alert_state", return_value={"ringing": [
                {"occurrence_id": "occurrence-1", "status": "ringing"},
                {"occurrence_id": "occurrence-2", "status": "ringing"},
            ]}
        ), patch.object(alerts_runtime, "begin_foreground_handoff", return_value=handoff) as mock_begin, patch.object(
            alerts_runtime, "_play_alert_audio"
        ) as mock_play, patch.object(alerts_runtime, "clear_audio_queue"), patch.object(
            alerts_runtime, "acknowledge_alert"
        ) as mock_ack:
            alerts_runtime.poll_due_alerts_if_needed(
                args=args, logger=logger, frame_queue=object(), pre_roll=object(), runtime_state=runtime_state
            )

        mock_begin.assert_called_once()
        self.assertEqual(mock_play.call_count, 2)
        self.assertEqual(mock_ack.call_count, 2)
        self.assertEqual(set(runtime_state.active_timer_alerts), {"occurrence-1", "occurrence-2"})

    def test_overlapping_alarm_and_timer_share_one_foreground_handoff(self) -> None:
        args = self._build_args()
        logger = self._build_logger()
        runtime_state = SimpleNamespace(next_alert_poll_at=0.0, next_wake_time=0.0)
        handoff = SimpleNamespace(interrupted_sessions=[object()])
        due = [
            {
                "kind": "alarm", "message": "Work alarm", "alert_id": "alarm-1",
                "lease_id": "lease-a", "metadata": {"occurrence_id": "alarm-occurrence"},
            },
            {
                "kind": "timer", "message": "Tea", "alert_id": "timer-1",
                "lease_id": "lease-t", "metadata": {"occurrence_id": "timer-occurrence"},
            },
        ]
        ringing = {
            "ringing": [
                {"kind": "alarm", "occurrence_id": "alarm-occurrence", "status": "ringing"},
                {"kind": "timer", "occurrence_id": "timer-occurrence", "status": "ringing"},
            ]
        }
        with patch.object(alerts_runtime, "claim_due_alerts", return_value=due), patch.object(
            alerts_runtime, "fetch_alert_state", return_value=ringing
        ), patch.object(alerts_runtime, "begin_foreground_handoff", return_value=handoff) as mock_begin, patch.object(
            alerts_runtime, "_play_alert_audio"
        ) as mock_play, patch.object(alerts_runtime, "clear_audio_queue"), patch.object(
            alerts_runtime, "acknowledge_alert"
        ), patch.object(alerts_runtime, "set_alarm_display_attention", return_value=True):
            alerts_runtime.poll_due_alerts_if_needed(
                args=args, logger=logger, frame_queue=object(), pre_roll=object(), runtime_state=runtime_state
            )

        mock_begin.assert_called_once()
        self.assertEqual(mock_play.call_count, 2)
        self.assertEqual(set(runtime_state.active_timer_alerts), {"alarm-occurrence", "timer-occurrence"})

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
            "claim_due_alerts",
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
        ), patch.object(alerts_runtime, "acknowledge_alert") as mock_ack:
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
        mock_ack.assert_called_once_with(
            args.oracle_url, args.source, "notification-1", "", credential="brain-token"
        )


if __name__ == "__main__":
    unittest.main()
