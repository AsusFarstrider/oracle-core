from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

from oracle_satellite_runtime_config import InteractionRuntimeEffectiveConfig


MODULE_PATH = Path(__file__).resolve().parents[1] / "satellite" / "pi_runtime" / "settings.py"
SPEC = importlib.util.spec_from_file_location("oracle_interaction_runtime_settings_test", MODULE_PATH)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("oracle_interaction_runtime_settings_test", MODULE)
SPEC.loader.exec_module(MODULE)

InteractionRuntimeHostBootstrap = MODULE.InteractionRuntimeHostBootstrap
InteractionRuntimeSettings = MODULE.InteractionRuntimeSettings


class SatelliteInteractionRuntimeSettingsTests(unittest.TestCase):
    def test_canonical_settings_map_component_behavior_and_host_bootstrap(self) -> None:
        effective = self._effective_config()
        bootstrap = InteractionRuntimeHostBootstrap(
            config_bind_host="127.0.0.1",
            config_bind_port=8022,
            reply_audio_state_path="/run/oracle/reply-state.json",
            reply_audio_stop_path="/run/oracle/reply-stop.flag",
            packaged_asset_paths={
                "wake_primary": "/opt/oracle/assets/hey_oracle.onnx",
                "alarm": "/opt/oracle/assets/alarm.wav",
                "timer": "/opt/oracle/assets/timer.wav",
            },
            wake_capture_default_storage_path="/var/lib/oracle/wake-capture",
            log_level="DEBUG",
        )

        settings = InteractionRuntimeSettings.from_canonical(effective, bootstrap)

        self.assertEqual(settings.oracle_url, "http://brain.example:8011")
        self.assertEqual(settings.brain_api_key, "brain-token")
        self.assertEqual(settings.satellite_id, "living_room_satellite")
        self.assertEqual(settings.source, "living_room_source")
        self.assertEqual(settings.model_path, "/opt/oracle/assets/hey_oracle.onnx")
        self.assertEqual(settings.input_device_name, "Living Room Microphone")
        self.assertIsNone(settings.input_device_index)
        self.assertEqual(settings.output_device_index, 7)
        self.assertIsNone(settings.output_device_name)
        self.assertEqual(settings.music_control_url, "http://127.0.0.1:8021")
        self.assertEqual(settings.music_control_api_key, "control-token")
        self.assertEqual(settings.alarm_sound_path, "/opt/oracle/assets/alarm.wav")
        self.assertEqual(settings.wake_capture_local_storage_path, "/var/lib/oracle/wake-capture")
        self.assertTrue(settings.wake_capture_sync_enabled)
        self.assertEqual(settings.wake_capture_sync_interval_seconds, 3600.0)
        self.assertEqual(settings.config_bind_host, "127.0.0.1")
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertNotIn("brain-token", repr(settings))
        self.assertNotIn("control-token", repr(settings))
        with self.assertRaisesRegex(Exception, "cannot assign"):
            settings.source = "replacement"  # type: ignore[misc]

    def test_canonical_machine_paths_do_not_require_packaged_asset_for_wake_model(self) -> None:
        effective = self._effective_config()
        effective.configuration["wake"]["model"] = {  # type: ignore[index]
            "format": "tflite",
            "asset_id": None,
            "path": "/home/oracle/hey_oracle.tflite",
        }
        bootstrap = self._bootstrap()

        settings = InteractionRuntimeSettings.from_canonical(effective, bootstrap)

        self.assertEqual(settings.model_path, "/home/oracle/hey_oracle.tflite")

    def test_canonical_settings_fail_closed_for_missing_runtime_or_asset(self) -> None:
        effective = self._effective_config()
        effective.configuration["audio"] = None  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "interaction audio"):
            InteractionRuntimeSettings.from_canonical(effective, self._bootstrap())

        effective = self._effective_config()
        with self.assertRaisesRegex(ValueError, "wake model asset is not installed"):
            InteractionRuntimeSettings.from_canonical(
                effective,
                InteractionRuntimeHostBootstrap(
                    config_bind_host="127.0.0.1",
                    config_bind_port=8022,
                    reply_audio_state_path="reply-state.json",
                    reply_audio_stop_path="reply-stop.flag",
                    packaged_asset_paths={"alarm": "alarm.wav", "timer": "timer.wav"},
                    wake_capture_default_storage_path="wake-capture",
                ),
            )

    @staticmethod
    def _bootstrap():
        return InteractionRuntimeHostBootstrap(
            config_bind_host="127.0.0.1",
            config_bind_port=8022,
            reply_audio_state_path="reply-state.json",
            reply_audio_stop_path="reply-stop.flag",
            packaged_asset_paths={
                "wake_primary": "hey_oracle.onnx",
                "alarm": "alarm.wav",
                "timer": "timer.wav",
            },
            wake_capture_default_storage_path="wake-capture",
        )

    @staticmethod
    def _effective_config() -> InteractionRuntimeEffectiveConfig:
        return InteractionRuntimeEffectiveConfig(
            satellite_id="living_room_satellite",
            source_id="living_room_source",
            activation_id="sat_activation_" + "a" * 32,
            projection_revision="oracle-projection-v1:sha256:" + "b" * 64,
            configuration={
                "control_service_client": {
                    "local_client_url": "http://127.0.0.1:8021",
                    "credential_secret": "CONTROL_TOKEN",
                },
                "audio": {
                    "input": {"type": "portaudio_name", "name": "Living Room Microphone"},
                    "interaction_output": {"type": "portaudio_index", "index": 7},
                    "input_gain": 2.0,
                    "playback_gain": 0.35,
                    "vad": {
                        "threshold": 0.015,
                        "noise_multiplier": 1.6,
                        "noise_offset": 0.006,
                        "release_multiplier": 1.15,
                        "release_offset": 0.003,
                        "max_speech_threshold": 0.42,
                        "max_silence_threshold": 0.30,
                        "silence_seconds": 0.75,
                        "speech_start_timeout_seconds": 1.6,
                        "false_start_silence_seconds": 0.45,
                        "max_record_seconds": 8.0,
                        "min_speech_seconds": 0.2,
                    },
                    "followup": {
                        "conversation_timeout_seconds": 90.0,
                        "silence_seconds": 0.3,
                        "max_record_seconds": 4.0,
                        "speech_start_timeout_seconds": 2.5,
                    },
                    "cues": {
                        "ack_enabled": True,
                        "ack_gain": 0.16,
                        "error_enabled": True,
                        "error_cooldown_seconds": 3.0,
                        "alarm_asset": "alarm",
                        "timer_asset": "timer",
                    },
                    "interim_acknowledgement": {
                        "enabled": True,
                        "poll_interval_seconds": 0.15,
                        "request_timeout_seconds": 0.75,
                    },
                    "alerts_poll_seconds": 2.0,
                    "playback": {
                        "interrupt_replies": True,
                        "post_playback_block_seconds": 2.0,
                        "interrupt_settle_seconds": 0.35,
                        "duck_volume": 18,
                        "duck_stage_one_volume": 28,
                        "duck_stage_two_volume": 22,
                        "duck_stage_three_volume": 18,
                        "duck_trigger_threshold": 0.12,
                        "duck_max_seconds": 4.0,
                    },
                },
                "wake": {
                    "enabled": True,
                    "model": {"format": "onnx", "asset_id": "wake_primary", "path": None},
                    "threshold": 0.2,
                    "log_threshold": 0.1,
                    "cooldown_seconds": 6.0,
                    "retry_cooldown_seconds": 1.0,
                    "arbitration_timeout_seconds": 5.0,
                    "arbitration_loser_suppression_ms": 10000,
                    "playback_suppression": {
                        "threshold": 0.16,
                        "log_threshold": 0.09,
                        "poll_seconds": 0.35,
                        "hold_seconds": 1.25,
                        "consecutive_frames": 2,
                    },
                    "capture": {
                        "enabled": True,
                        "capture_activation": True,
                        "capture_near_threshold": True,
                        "pre_roll_ms": 2500,
                        "post_roll_ms": 1500,
                        "near_threshold_fraction": 0.85,
                        "event_cooldown_seconds": 3.0,
                        "local_storage_path": None,
                        "sync": {
                            "enabled": True,
                            "interval_seconds": 3600.0,
                            "delete_local_after_sync": True,
                            "synced_local_retention_days": 7,
                        },
                    },
                },
            },
            brain_base_url="http://brain.example:8011",
            brain_credential_secret_id="BRAIN_TOKEN",
            brain_credential="brain-token",
            control_service_base_url="http://127.0.0.1:8021",
            control_service_credential_secret_id="CONTROL_TOKEN",
            control_service_credential="control-token",
        )


if __name__ == "__main__":
    unittest.main()
