from __future__ import annotations

import json
import sys
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from satellite.control_service import ControlServer
from satellite.control_service_runtime import CommandResult
from satellite.control_service_runtime.adapters import PlexampHttpAdapter, ShellPlexampAdapter
from satellite.control_service_runtime.cache import CommandCache
from satellite.control_service_runtime.longform import LongformShellController
import satellite.control_service_runtime.playback_authority as playback_authority_runtime
from satellite.control_service_runtime.playback_authority import (
    build_playback_authority_state,
    interrupt_for_oracle,
    resume_after_oracle,
)
from satellite.control_service_runtime.reply_audio import ReplyAudioStateStore
from satellite.control_service_runtime.server import (
    ControlRequestHandler,
    _validate_control_request_payload,
)
from satellite.control_service_runtime.system_volume import (
    SystemVolumeController,
    build_system_volume_config,
    windows_default_endpoint_support_status,
)
import satellite.control_service_runtime.system_volume as system_volume_runtime
from oracle_app.config_reporting import choose_config_report_format


class ControlServiceTests(unittest.TestCase):
    def _build_server_like(
        self,
        *,
        reply_audio_state_path: str = "",
        reply_audio_stop_path: str = "",
    ) -> ControlServer:
        server = ControlServer.__new__(ControlServer)
        server.reply_audio = ReplyAudioStateStore(reply_audio_state_path, reply_audio_stop_path)
        return server

    def test_interrupt_for_oracle_ducks_plexamp_when_volume_available(self) -> None:
        adapter = SimpleNamespace(
            get_longform_state=lambda: {"ok": True, "state": "stopped", "playing": False},
            get_now_playing=lambda: {
                "ok": True,
                "playing": True,
                "state": "playing",
                "backend_type": "plexamp_external",
                "type": "track",
                "plex_key": "/library/metadata/1",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "volume": 42,
            },
            set_volume=lambda level: CommandResult(ok=True, state="accepted", payload={"volume_level": level}),
            pause=lambda: CommandResult(ok=True, state="accepted"),
            stop=lambda: CommandResult(ok=True, state="accepted"),
        )
        reply_audio = ReplyAudioStateStore("", "")

        payload = interrupt_for_oracle(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(payload["interrupted_any"])
        self.assertEqual(payload["active_session_count"], 1)
        self.assertFalse(payload["degraded_state"])
        self.assertEqual(payload["degraded_reasons"], [])
        self.assertEqual(payload["owning_component"], "satellite.playback_authority")
        interrupted = payload["interrupted_sessions"][0]
        self.assertEqual(interrupted["interrupt_action"], "duck")
        self.assertEqual(interrupted["resume_action"], "restore_volume")
        self.assertEqual(interrupted["restore_volume_level"], 42)
        self.assertTrue(interrupted["interruption_token"])

    def test_interrupt_for_oracle_ducks_native_music_when_volume_available(self) -> None:
        adapter = SimpleNamespace(
            get_longform_state=lambda: {"ok": True, "state": "stopped", "playing": False},
            get_now_playing=lambda: {
                "ok": True,
                "playing": True,
                "state": "playing",
                "backend_type": "oracle_native_music",
                "type": "track",
                "plex_key": "/library/metadata/1",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
                "volume": 36,
            },
            set_volume=lambda level: CommandResult(ok=True, state="accepted", payload={"volume_level": level}),
            pause=lambda: CommandResult(ok=True, state="accepted"),
            stop=lambda: CommandResult(ok=True, state="accepted"),
        )
        reply_audio = ReplyAudioStateStore("", "")

        payload = interrupt_for_oracle(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(payload["interrupted_any"])
        interrupted = payload["interrupted_sessions"][0]
        self.assertEqual(interrupted["interrupt_action"], "duck")
        self.assertEqual(interrupted["restore_volume_level"], 36)
        self.assertTrue(interrupted["interruption_token"])

    def test_interrupt_for_oracle_ducks_audiobook_when_output_volume_available(self) -> None:
        adapter = SimpleNamespace(
            get_output_volume=lambda: 48,
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {"ok": True, "state": "stopped", "playing": False},
            set_volume=lambda level: CommandResult(ok=True, state="accepted", payload={"volume_level": level}),
            pause_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
            stop_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
        )
        reply_audio = ReplyAudioStateStore("", "")

        payload = interrupt_for_oracle(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(payload["interrupted_any"])
        interrupted = payload["interrupted_sessions"][0]
        self.assertEqual(interrupted["backend_type"], "oracle_audiobook")
        self.assertEqual(interrupted["interrupt_action"], "duck")
        self.assertEqual(interrupted["restore_volume_level"], 48)

    def test_interrupt_for_oracle_classifies_active_but_uninterrupted_state_as_authority_mismatch(self) -> None:
        adapter = SimpleNamespace(
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {"ok": True, "state": "stopped", "playing": False},
            get_output_volume=lambda: None,
            pause_longform_audio=lambda: CommandResult(ok=False, state="failed", detail="busy"),
            stop_longform_audio=lambda: CommandResult(ok=False, state="failed", detail="still busy"),
        )
        reply_audio = ReplyAudioStateStore("", "")

        payload = interrupt_for_oracle(adapter=adapter, reply_audio=reply_audio)

        self.assertFalse(payload["interrupted_any"])
        self.assertEqual(payload["active_session_count"], 1)
        self.assertEqual(payload["failure_class"], "authority_mismatch")
        self.assertEqual(payload["owning_component"], "satellite.playback_authority")
        self.assertEqual(payload["error"], "authority_interrupt_failed")

    def test_interrupt_for_oracle_low_volume_duck_uses_zero_target(self) -> None:
        seen: dict[str, int] = {}

        def _set_volume(level: int) -> CommandResult:
            seen["level"] = level
            return CommandResult(ok=True, state="accepted", payload={"volume_level": 0})

        adapter = SimpleNamespace(
            get_output_volume=lambda: 6,
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {"ok": True, "state": "stopped", "playing": False},
            set_volume=_set_volume,
            pause_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
            stop_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
        )
        reply_audio = ReplyAudioStateStore("", "")

        payload = interrupt_for_oracle(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(payload["interrupted_any"])
        interrupted = payload["interrupted_sessions"][0]
        self.assertEqual(seen["level"], 0)
        self.assertEqual(interrupted["interrupt_action"], "duck")
        self.assertEqual(interrupted["restore_volume_level"], 6)

    def test_interrupt_for_oracle_falls_back_when_duck_does_not_lower_volume(self) -> None:
        adapter = SimpleNamespace(
            get_output_volume=lambda: 6,
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {"ok": True, "state": "stopped", "playing": False},
            set_volume=lambda level: CommandResult(ok=True, state="accepted", payload={"volume_level": 6}),
            pause_longform_audio=lambda: CommandResult(ok=True, state="accepted", payload={"state": "paused"}),
            stop_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
        )
        reply_audio = ReplyAudioStateStore("", "")

        payload = interrupt_for_oracle(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(payload["interrupted_any"])
        interrupted = payload["interrupted_sessions"][0]
        self.assertEqual(interrupted["interrupt_action"], "pause_longform_audio")
        self.assertEqual(interrupted["resume_action"], "resume_longform_audio")

    def test_interrupt_for_oracle_falls_back_to_stop_when_pause_fails(self) -> None:
        adapter = SimpleNamespace(
            get_output_volume=lambda: None,
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {"ok": True, "state": "stopped", "playing": False},
            pause_longform_audio=lambda: CommandResult(ok=False, state="failed", detail="device still busy"),
            stop_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
        )
        reply_audio = ReplyAudioStateStore("", "")

        payload = interrupt_for_oracle(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(payload["interrupted_any"])
        interrupted = payload["interrupted_sessions"][0]
        self.assertEqual(interrupted["interrupt_action"], "stop_longform_audio")
        self.assertEqual(interrupted["resume_action"], "resume_longform_audio")

    def test_resume_after_oracle_restores_ducked_volume(self) -> None:
        seen: dict[str, int] = {}
        def _set_volume(level: int) -> CommandResult:
            seen["level"] = level
            return CommandResult(ok=True, state="accepted", payload={"volume_level": level})
        adapter = SimpleNamespace(
            set_volume=_set_volume,
            resume=lambda: CommandResult(ok=True, state="accepted"),
            resume_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
        )
        playback_authority_runtime._INTERRUPTION_LEDGER.register(
            {
                "backend_type": "plexamp_external",
                "session_id": "/library/metadata/1",
                "interruption_token": "token-1",
                "resume_action": "restore_volume",
            }
        )

        payload = resume_after_oracle(
            adapter=adapter,
            interrupted_sessions=[
                {
                    "backend_type": "plexamp_external",
                    "media_kind": "music",
                    "session_id": "/library/metadata/1",
                    "interruption_token": "token-1",
                    "resume_action": "restore_volume",
                    "restore_volume_level": 42,
                }
            ],
        )

        self.assertTrue(payload["resumed_any"])
        self.assertEqual(seen["level"], 42)

    def test_resume_after_oracle_restores_ducked_audiobook_volume(self) -> None:
        seen: dict[str, int] = {}

        def _set_volume(level: int) -> CommandResult:
            seen["level"] = level
            return CommandResult(ok=True, state="accepted", payload={"volume_level": level})

        adapter = SimpleNamespace(
            set_volume=_set_volume,
            resume=lambda: CommandResult(ok=True, state="accepted"),
            resume_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
        )
        playback_authority_runtime._INTERRUPTION_LEDGER.register(
            {
                "backend_type": "oracle_audiobook",
                "session_id": "book-1",
                "interruption_token": "token-2",
                "resume_action": "restore_volume",
            }
        )

        payload = resume_after_oracle(
            adapter=adapter,
            interrupted_sessions=[
                {
                    "backend_type": "oracle_audiobook",
                    "media_kind": "audiobook",
                    "session_id": "book-1",
                    "interruption_token": "token-2",
                    "resume_action": "restore_volume",
                    "restore_volume_level": 12,
                }
            ],
        )

        self.assertTrue(payload["resumed_any"])
        self.assertEqual(seen["level"], 12)

    def test_resume_after_oracle_resumes_ducked_then_paused_audiobook_with_same_lineage(self) -> None:
        adapter = SimpleNamespace(
            set_volume=lambda level: CommandResult(ok=True, state="accepted", payload={"volume_level": level}),
            resume=lambda: CommandResult(ok=True, state="accepted"),
            resume_longform_audio=lambda: CommandResult(ok=True, state="playing"),
        )
        playback_authority_runtime._INTERRUPTION_LEDGER.register(
            {
                "backend_type": "oracle_audiobook",
                "session_id": "book-1",
                "interruption_token": "token-restore",
                "resume_action": "restore_volume",
            }
        )

        payload = resume_after_oracle(
            adapter=adapter,
            interrupted_sessions=[
                {
                    "backend_type": "oracle_audiobook",
                    "media_kind": "audiobook",
                    "session_id": "book-1",
                    "interruption_token": "token-restore",
                    "resume_action": "resume_longform_audio",
                }
            ],
        )

        self.assertTrue(payload["resumed_any"])
        self.assertEqual(payload["skipped_sessions"], [])
        self.assertEqual(payload["resumed_sessions"][0]["resume_action"], "resume_longform_audio")

    def test_interruption_ledger_register_uses_default_token_when_session_token_missing(self) -> None:
        adapter = SimpleNamespace(
            set_volume=lambda level: CommandResult(ok=True, state="accepted", payload={"volume_level": level}),
            resume=lambda: CommandResult(ok=True, state="accepted"),
            resume_longform_audio=lambda: CommandResult(ok=True, state="playing"),
        )

        playback_authority_runtime._INTERRUPTION_LEDGER.register(
            {
                "backend_type": "oracle_audiobook",
                "session_id": "book-default",
                "resume_action": "restore_volume",
            },
            default_interruption_token="token-default",
        )

        payload = resume_after_oracle(
            adapter=adapter,
            interrupted_sessions=[
                {
                    "backend_type": "oracle_audiobook",
                    "media_kind": "audiobook",
                    "session_id": "book-default",
                    "interruption_token": "token-default",
                    "resume_action": "resume_longform_audio",
                }
            ],
        )

        self.assertTrue(payload["resumed_any"])
        self.assertEqual(payload["skipped_sessions"], [])

    def test_resume_after_oracle_skips_superseded_session(self) -> None:
        adapter = SimpleNamespace(
            set_volume=lambda level: CommandResult(ok=True, state="accepted", payload={"volume_level": level}),
            resume=lambda: CommandResult(ok=True, state="accepted"),
            resume_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
        )

        payload = resume_after_oracle(
            adapter=adapter,
            interrupted_sessions=[
                {
                    "backend_type": "oracle_native_music",
                    "media_kind": "music",
                    "session_id": "track-1",
                    "resume_action": "resume",
                    "superseded_by_session_id": "reply-2",
                }
            ],
        )

        self.assertFalse(payload["resumed_any"])
        self.assertEqual(payload["resumed_sessions"], [])
        self.assertEqual(len(payload["skipped_sessions"]), 1)
        self.assertEqual(payload["skipped_sessions"][0]["skip_reason"], "superseded")

    def test_resume_after_oracle_skips_missing_ledger_entry(self) -> None:
        adapter = SimpleNamespace(
            set_volume=lambda level: CommandResult(ok=True, state="accepted", payload={"volume_level": level}),
            resume=lambda: CommandResult(ok=True, state="accepted"),
            resume_longform_audio=lambda: CommandResult(ok=True, state="accepted"),
        )

        payload = resume_after_oracle(
            adapter=adapter,
            interrupted_sessions=[
                {
                    "backend_type": "oracle_native_music",
                    "media_kind": "music",
                    "session_id": "track-404",
                    "interruption_token": "token-missing",
                    "resume_action": "resume",
                }
            ],
        )

        self.assertFalse(payload["resumed_any"])
        self.assertEqual(payload["resumed_sessions"], [])
        self.assertEqual(len(payload["skipped_sessions"]), 1)
        self.assertEqual(payload["skipped_sessions"][0]["skip_reason"], "missing_ledger_entry")
        self.assertEqual(payload["skipped_sessions"][0]["failure_class"], "authority_mismatch")
        self.assertEqual(payload["skipped_sessions"][0]["owning_component"], "satellite.playback_authority")
        self.assertEqual(payload["failure_class"], "authority_mismatch")
        self.assertEqual(payload["owning_component"], "satellite.playback_authority")

    def test_playback_authority_read_does_not_probe_output_volume(self) -> None:
        adapter = SimpleNamespace(
            get_output_volume=lambda: (_ for _ in ()).throw(AssertionError("get_output_volume should not be called")),
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {"ok": True, "state": "stopped", "playing": False},
        )
        reply_audio = ReplyAudioStateStore("", "")

        authority = build_playback_authority_state(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(authority["playback_active"])
        self.assertEqual(authority["active_sessions"][0]["backend_type"], "oracle_audiobook")
        self.assertIsNone(authority["active_sessions"][0]["volume"])
        self.assertFalse(authority["active_sessions"][0]["can_duck"])
        self.assertFalse(authority["degraded_state"])
        self.assertEqual(authority["degraded_reasons"], [])

    def test_playback_authority_reports_active_reply_as_output_owner(self) -> None:
        adapter = SimpleNamespace(
            get_longform_state=lambda: {"ok": True, "state": "stopped", "playing": False},
            get_now_playing=lambda: {"ok": True, "state": "stopped", "playing": False},
        )
        reply_audio = ReplyAudioStateStore("", "")
        started = reply_audio.begin_session(kind="tts", correlation_id="corr-1")

        authority = build_playback_authority_state(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(authority["playback_active"])
        self.assertEqual(authority["output_owner"]["backend_type"], "reply_audio")
        self.assertEqual(authority["output_owner"]["session_id"], started["session_id"])

    def test_playback_authority_reports_dual_active_music_and_audiobook_degraded_state(self) -> None:
        adapter = SimpleNamespace(
            get_music_backend_expectation=lambda: {"default_backend": "plexamp_external"},
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {
                "ok": True,
                "playing": True,
                "state": "playing",
                "backend_type": "plexamp_external",
                "type": "track",
                "plex_key": "/library/metadata/1",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
            },
        )
        reply_audio = ReplyAudioStateStore("", "")

        authority = build_playback_authority_state(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(authority["degraded_state"])
        self.assertEqual(authority["degraded_reasons"], ["dual_active_music_audiobook"])
        self.assertEqual(authority["failure_class"], "authority_mismatch")
        self.assertEqual(authority["owning_component"], "satellite.playback_authority")
        self.assertEqual(authority["output_owner"]["backend_type"], "oracle_audiobook")
        self.assertEqual(len(authority["active_sessions"]), 2)

    def test_playback_authority_reports_music_backend_default_mismatch_as_degraded(self) -> None:
        adapter = SimpleNamespace(
            get_music_backend_expectation=lambda: {
                "default_backend": "oracle_native_music",
                "oracle_native_music_enabled": True,
                "supports_oracle_native_music": True,
                "supports_plexamp": True,
            },
            get_longform_state=lambda: {"ok": True, "state": "stopped", "playing": False},
            get_now_playing=lambda: {
                "ok": True,
                "playing": True,
                "state": "playing",
                "backend_type": "plexamp_external",
                "type": "track",
                "plex_key": "/library/metadata/1",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
            },
        )
        reply_audio = ReplyAudioStateStore("", "")

        authority = build_playback_authority_state(adapter=adapter, reply_audio=reply_audio)

        self.assertTrue(authority["degraded_state"])
        self.assertEqual(authority["degraded_reasons"], ["music_backend_default_mismatch"])
        self.assertEqual(authority["music_backend_expectation"]["default_backend"], "oracle_native_music")
        self.assertEqual(authority["active_sessions"][0]["expected_backend"], "oracle_native_music")

    def test_playback_authority_does_not_report_dual_active_for_paused_music_plus_playing_audiobook(self) -> None:
        adapter = SimpleNamespace(
            get_music_backend_expectation=lambda: {"default_backend": "plexamp_external"},
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {
                "ok": True,
                "playing": True,
                "state": "paused",
                "backend_type": "plexamp_external",
                "type": "track",
                "plex_key": "/library/metadata/1",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
            },
        )
        reply_audio = ReplyAudioStateStore("", "")

        authority = build_playback_authority_state(adapter=adapter, reply_audio=reply_audio)

        self.assertFalse(authority["degraded_state"])
        self.assertEqual(authority["degraded_reasons"], [])
        self.assertEqual(len(authority["active_sessions"]), 1)
        self.assertEqual(authority["active_sessions"][0]["backend_type"], "oracle_audiobook")

    @patch("satellite.control_service_runtime.playback_authority.logging.warning")
    def test_playback_authority_logs_warning_for_dual_active_music_and_audiobook(self, mock_warning) -> None:
        adapter = SimpleNamespace(
            get_music_backend_expectation=lambda: {"default_backend": "plexamp_external"},
            get_longform_state=lambda: {
                "ok": True,
                "state": "playing",
                "playing": True,
                "playback_id": "book-1",
                "title": "Dune",
                "author": "Frank Herbert",
                "position_seconds": 120.0,
                "duration_seconds": 1000.0,
            },
            get_now_playing=lambda: {
                "ok": True,
                "playing": True,
                "state": "playing",
                "backend_type": "plexamp_external",
                "type": "track",
                "plex_key": "/library/metadata/1",
                "title": "Song",
                "artist": "Artist",
                "album": "Album",
            },
        )
        reply_audio = ReplyAudioStateStore("", "")

        build_playback_authority_state(adapter=adapter, reply_audio=reply_audio)

        mock_warning.assert_called_once()
        self.assertIn("playback_authority_degraded", mock_warning.call_args.args[0])

if __name__ == "__main__":
    unittest.main()
