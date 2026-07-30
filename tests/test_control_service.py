from __future__ import annotations

import json
import sys
import unittest
import tempfile
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

    def test_get_reply_audio_state_defaults_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = str(Path(temp_dir) / "reply-audio-state.json")
            server = self._build_server_like(reply_audio_state_path=state_path)

            state = server.get_reply_audio_state()

        self.assertEqual(state, {"ok": True, "playing": False, "kind": "tts"})

    def test_get_reply_audio_state_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "reply-audio-state.json"
            state_path.write_text("{not-json", encoding="utf-8")
            server = self._build_server_like(reply_audio_state_path=str(state_path))

            state = server.get_reply_audio_state()

        self.assertEqual(
            state,
            {"ok": False, "playing": False, "kind": "tts", "error": "invalid_reply_audio_state"},
        )

    def test_get_reply_audio_state_normalizes_payload_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "reply-audio-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "ok": 1,
                        "playing": "yes",
                        "kind": "  music  ",
                        "updated_at": 123.45,
                        "error": "  busy  ",
                        "ignored": "value",
                    }
                ),
                encoding="utf-8",
            )
            server = self._build_server_like(reply_audio_state_path=str(state_path))

            state = server.get_reply_audio_state()

        self.assertEqual(
            state,
            {
                "ok": True,
                "playing": True,
                "kind": "music",
                "updated_at": 123.45,
                "error": "busy",
            },
        )

    def test_longform_play_retries_once_after_immediate_startup_failure(self) -> None:
        controller = LongformShellController(
            SimpleNamespace(
                play_longform_audio_cmd="play {manifest_path}",
                pause_longform_audio_cmd="pause",
                resume_longform_audio_cmd="resume",
                stop_longform_audio_cmd="stop",
                seek_longform_audio_cmd="seek {position_seconds}",
                longform_state_cmd="state",
            )
        )
        controller._startup_poll_attempts = 1
        first_launch = CommandResult(ok=True, state="accepted", payload={"state": "playing", "playback_id": "book-1"})
        second_launch = CommandResult(ok=True, state="accepted", payload={"state": "playing", "playback_id": "book-1"})
        observed_commands: list[str] = []
        observed_contexts: list[dict[str, object]] = []
        state_sequence = [
            {"ok": True, "state": "stopped", "playing": False, "playback_id": "book-1"},
            {"ok": True, "state": "playing", "playing": True, "playback_id": "book-1"},
        ]

        def fake_run_command(template: str | None, context: dict[str, object], **_: object) -> CommandResult:
            observed_commands.append(str(template))
            observed_contexts.append(dict(context))
            if template == "play {manifest_path}":
                return [first_launch, second_launch][len(observed_commands) - 1]
            self.fail(f"unexpected template {template}")

        def fake_get_state(*, use_cache: bool = True) -> dict[str, object]:
            return state_sequence.pop(0)

        with patch.object(controller, "_run_command", side_effect=fake_run_command), patch.object(
            controller,
            "get_longform_state",
            side_effect=fake_get_state,
        ), patch("satellite.control_service_runtime.longform.time.sleep"):
            result = controller.play_longform_audio(
                playback_id="book-1",
                session_id="session-1",
                title="Outlaw of Gor",
                author="John Norman",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example.test/track.mp3"}],
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.payload["state"], "playing")
        self.assertEqual(len(observed_commands), 2)
        self.assertEqual(observed_contexts[0]["playback_id"], "book-1")
        self.assertEqual(observed_contexts[1]["playback_id"], "book-1")

    def test_longform_play_fails_cleanly_when_retry_also_collapses(self) -> None:
        controller = LongformShellController(
            SimpleNamespace(
                play_longform_audio_cmd="play {manifest_path}",
                pause_longform_audio_cmd="pause",
                resume_longform_audio_cmd="resume",
                stop_longform_audio_cmd="stop",
                seek_longform_audio_cmd="seek {position_seconds}",
                longform_state_cmd="state",
            )
        )
        controller._startup_poll_attempts = 1
        state_payload = {"ok": True, "state": "stopped", "playing": False, "playback_id": "book-2"}

        with patch.object(
            controller,
            "_run_command",
            return_value=CommandResult(ok=True, state="accepted", payload={"state": "playing", "playback_id": "book-2"}),
        ) as mock_run_command, patch.object(
            controller,
            "get_longform_state",
            side_effect=[dict(state_payload), dict(state_payload)],
        ), patch("satellite.control_service_runtime.longform.time.sleep"):
            result = controller.play_longform_audio(
                playback_id="book-2",
                session_id="session-2",
                title="Outlaw of Gor",
                author="John Norman",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example.test/track.mp3"}],
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "failed")
        self.assertIn("did not reach a playable long-form state", result.detail)
        self.assertEqual(mock_run_command.call_count, 2)

    def test_reply_audio_state_store_requests_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stop_path = Path(temp_dir) / "reply-audio-stop.flag"
            store = ReplyAudioStateStore("", str(stop_path))

            payload = store.request_stop()
            stop_exists = stop_path.exists()

        self.assertTrue(payload["reply_audio_stop_requested"])
        self.assertTrue(stop_exists)

    def test_reply_audio_state_store_begin_and_finalize_session(self) -> None:
        store = ReplyAudioStateStore("", "")

        started = store.begin_session(kind="tts", correlation_id="corr-1")
        active = store.get_state()
        finalized = store.finalize_session(
            session_id=str(started["session_id"]),
            correlation_id="corr-1",
            final_state="completed",
        )
        inactive = store.get_state()

        self.assertTrue(active["playing"])
        self.assertEqual(active["session_id"], started["session_id"])
        self.assertEqual(active["correlation_id"], "corr-1")
        self.assertTrue(finalized["reply_audio_finalized"])
        self.assertFalse(inactive["playing"])

    def test_reply_audio_state_store_discards_stale_session(self) -> None:
        store = ReplyAudioStateStore("", "", stale_after_seconds=1.0)
        started = store.begin_session(kind="tts", correlation_id="corr-1")
        store._active_session["updated_at"] = float(started["updated_at"]) - 10.0

        state = store.get_state()

        self.assertFalse(state["playing"])

    def test_reply_audio_state_store_replaces_existing_active_session(self) -> None:
        store = ReplyAudioStateStore("", "")
        first = store.begin_session(kind="tts", correlation_id="corr-1")

        second = store.begin_session(kind="tts", correlation_id="corr-2")

        self.assertEqual(second["replaced_session_id"], first["session_id"])
        self.assertEqual(store.get_state()["session_id"], second["session_id"])

    def test_command_cache_store_and_prune(self) -> None:
        cache = CommandCache()

        cache.store("cmd-1", {"ok": True})
        self.assertEqual(cache.get("cmd-1"), {"ok": True})

        cache.updated_at["cmd-1"] = 0.0
        cache.prune(max_age_seconds=0.0)

        self.assertIsNone(cache.get("cmd-1"))

    def test_dispatch_action_rejects_invalid_set_volume(self) -> None:
        handler = ControlRequestHandler.__new__(ControlRequestHandler)
        handler.server = SimpleNamespace(
            adapter=SimpleNamespace(),
            result_type=CommandResult,
            stop_reply_audio=lambda: {},
        )

        result = handler._dispatch_action("set_volume", {"level": "loud"})

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "invalid_request")
        self.assertEqual(result.failure_class, "contract_failure")
        self.assertEqual(result.owning_component, "satellite.control_service")

    def test_control_request_payload_rejects_extra_fields_before_dispatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported control request fields"):
            _validate_control_request_payload(
                {
                    "command_id": "cmd-1",
                    "action": "pause",
                    "args": {},
                    "unexpected": True,
                }
            )

    def test_control_request_payload_rejects_non_object_args(self) -> None:
        with self.assertRaisesRegex(ValueError, "args must be an object"):
            _validate_control_request_payload(
                {
                    "command_id": "cmd-1",
                    "action": "pause",
                    "args": [],
                }
            )

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

    def test_control_server_health_exposes_music_backend_expectation(self) -> None:
        server = ControlServer.__new__(ControlServer)
        server.adapter = SimpleNamespace(
            health=lambda: {
                "adapter": "local_playback",
                "music_backend_expectation": {
                    "default_backend": "oracle_native_music",
                    "oracle_native_music_enabled": True,
                    "supports_oracle_native_music": True,
                    "supports_plexamp": True,
                },
            }
        )
        payload = server.build_health_payload()

        self.assertEqual(payload["adapter"]["music_backend_expectation"]["default_backend"], "oracle_native_music")

    def test_system_volume_config_reads_alsa_args(self) -> None:
        args = SimpleNamespace(
            output_volume_backend="alsa",
            output_volume_card="4",
            output_volume_control="PCM",
        )

        config = build_system_volume_config(args)

        self.assertEqual(config.backend, "alsa")
        self.assertEqual(config.card, "4")
        self.assertEqual(config.control, "PCM")

    def test_dispatch_action_stop_reply_audio_uses_server_state(self) -> None:
        handler = ControlRequestHandler.__new__(ControlRequestHandler)
        handler.server = SimpleNamespace(
            adapter=SimpleNamespace(),
            result_type=CommandResult,
            stop_reply_audio=lambda: {"reply_audio_stop_requested": True},
        )

        result = handler._dispatch_action("stop_reply_audio", {})

        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"reply_audio_stop_requested": True})

    def test_dispatch_action_begin_reply_audio_uses_server_state(self) -> None:
        handler = ControlRequestHandler.__new__(ControlRequestHandler)
        handler.server = SimpleNamespace(
            adapter=SimpleNamespace(),
            result_type=CommandResult,
            begin_reply_audio=lambda kind, correlation_id="": {
                "reply_audio_registered": True,
                "session_id": "session-1",
                "correlation_id": correlation_id,
                "kind": kind,
            },
            stop_reply_audio=lambda: {},
        )

        result = handler._dispatch_action("begin_reply_audio", {"kind": "tts", "correlation_id": "corr-1"})

        self.assertTrue(result.ok)
        self.assertEqual(result.payload["session_id"], "session-1")
        self.assertEqual(result.payload["correlation_id"], "corr-1")

    def test_dispatch_action_finalize_reply_audio_requires_session_and_state(self) -> None:
        handler = ControlRequestHandler.__new__(ControlRequestHandler)
        handler.server = SimpleNamespace(
            adapter=SimpleNamespace(),
            result_type=CommandResult,
            stop_reply_audio=lambda: {},
        )

        result = handler._dispatch_action("finalize_reply_audio", {"session_id": "", "final_state": ""})

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "invalid_request")

    def test_dispatch_action_forwards_play_media_arguments(self) -> None:
        captured: dict[str, object] = {}

        class FakeAdapter:
            def play_media(self, **kwargs):
                captured.update(kwargs)
                return CommandResult(ok=True, state="accepted", payload={"ok": True})

        handler = ControlRequestHandler.__new__(ControlRequestHandler)
        handler.server = SimpleNamespace(
            adapter=FakeAdapter(),
            result_type=CommandResult,
            stop_reply_audio=lambda: {},
        )

        result = handler._dispatch_action(
            "play_media",
            {
                "media_type": "album",
                "plex_key": "/library/metadata/123",
                "parent_key": "/library/metadata/12",
                "rating_key": "123",
                "title": "Low",
                "artist": "David Bowie",
                "album": "Low",
                "queue_id": "album-low",
                "queue_position": 1,
                "queue_count": 11,
                "collection_title": "Low",
                "collection_type": "album",
                "queue_tracks": [{"rating_key": "track-1", "title": "Speed of Life"}],
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(captured["media_type"], "album")
        self.assertEqual(captured["plex_key"], "/library/metadata/123")
        self.assertEqual(captured["title"], "Low")
        self.assertEqual(captured["queue_id"], "album-low")
        self.assertEqual(captured["queue_count"], 11)
        self.assertEqual(captured["collection_type"], "album")
        self.assertEqual(captured["queue_tracks"], [{"rating_key": "track-1", "title": "Speed of Life"}])

    def test_plexamp_adapter_without_native_support_skips_native_state_probe(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://192.0.2.205:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            disable_plexamp_external=False,
            http_timeout_seconds=5.0,
            supports_oracle_native_music=False,
            oracle_native_music_player_bin="auto",
            output_volume_backend="",
            output_volume_card="",
            output_volume_control="",
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd="python longform play --manifest {manifest_path}",
            pause_longform_audio_cmd="python longform pause",
            resume_longform_audio_cmd="python longform resume",
            stop_longform_audio_cmd="python longform stop",
            seek_longform_audio_cmd="python longform seek --position-seconds {position_seconds}",
            longform_state_cmd="python longform state",
        )
        adapter = PlexampHttpAdapter(args)

        self.assertIsNone(adapter._safe_native_music_state())

    def test_plexamp_adapter_can_disable_external_plexamp_while_keeping_plex_credentials(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://192.0.2.205:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            disable_plexamp_external=True,
            http_timeout_seconds=5.0,
            supports_oracle_native_music=False,
            oracle_native_music_player_bin="auto",
            output_volume_backend="",
            output_volume_card="",
            output_volume_control="",
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd="python longform play --manifest {manifest_path}",
            pause_longform_audio_cmd="python longform pause",
            resume_longform_audio_cmd="python longform resume",
            stop_longform_audio_cmd="python longform stop",
            seek_longform_audio_cmd="python longform seek --position-seconds {position_seconds}",
            longform_state_cmd="python longform state",
        )
        adapter = PlexampHttpAdapter(args)

        with patch.object(adapter, "_timeline") as mock_timeline:
            state = adapter.get_now_playing()

        self.assertEqual(state, {"ok": True, "playing": False, "state": "stopped"})
        self.assertFalse(adapter.get_music_backend_expectation()["supports_plexamp"])
        mock_timeline.assert_not_called()

    def test_native_music_controller_passes_queue_tracks_json(self) -> None:
        from satellite.control_service_runtime.native_music import NativeMusicController

        args = SimpleNamespace(
            oracle_native_music_player_bin="auto",
            supports_oracle_native_music=True,
        )
        controller = NativeMusicController(args)

        with patch("satellite.control_service_runtime.native_music.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stdout='{"ok": true, "state": "playing"}', stderr="")
            result = controller.play_track(
                stream_url="http://example/stream.mp3",
                track_id="track-1",
                media_type="playlist",
                title="Song 1",
                artist="Artist",
                album="Album",
                queue_id="playlist-1",
                queue_position=2,
                queue_count=3,
                collection_title="Favorites",
                collection_type="playlist",
                queue_tracks=[
                    {"rating_key": "track-1", "title": "Song 1"},
                    {"rating_key": "track-2", "title": "Song 2"},
                ],
                duration_seconds=180.0,
            )

        self.assertTrue(result.ok)
        command = mock_run.call_args.args[0]
        self.assertIn("--queue-tracks-json", command)
        queue_json = command[command.index("--queue-tracks-json") + 1]
        self.assertIn("track-2", queue_json)

    def test_health_config_endpoint_returns_json_without_auth(self) -> None:
        handler = ControlRequestHandler.__new__(ControlRequestHandler)
        captured: dict[str, object] = {}
        handler.path = "/health/config"
        handler.headers = {}
        handler.server = SimpleNamespace(
            build_health_payload=lambda: {"ok": True},
            choose_config_report_format=choose_config_report_format,
            build_config_report_payload=lambda: {
                "ok": True,
                "service": "oracle-satellite-control",
                "has_errors": False,
                "has_warnings": True,
                "sections": [{"heading": "Satellite control service config check:", "findings": []}],
            },
            render_config_report_text=lambda: "Satellite control service config check:\n- OK",
            authorize=lambda _header: False,
        )
        handler._write_json = lambda status, payload: captured.update(status=int(status), payload=payload)
        handler._write_text = lambda status, payload: captured.update(text_status=int(status), text=payload)

        handler.do_GET()

        self.assertEqual(captured["status"], 200)
        payload = captured["payload"]
        assert isinstance(payload, dict)
        self.assertEqual(payload["service"], "oracle-satellite-control")
        self.assertTrue(payload["has_warnings"])

    def test_health_config_endpoint_returns_text_with_query_format(self) -> None:
        handler = ControlRequestHandler.__new__(ControlRequestHandler)
        captured: dict[str, object] = {}
        handler.path = "/health/config?format=text"
        handler.headers = {}
        handler.server = SimpleNamespace(
            build_health_payload=lambda: {"ok": True},
            choose_config_report_format=choose_config_report_format,
            build_config_report_payload=lambda: {"ok": True, "service": "oracle-satellite-control", "sections": []},
            render_config_report_text=lambda: "Satellite control service config check:\n- OK",
            authorize=lambda _header: False,
        )
        handler._write_json = lambda status, payload: captured.update(json_status=int(status), payload=payload)
        handler._write_text = lambda status, payload: captured.update(status=int(status), text=payload)

        handler.do_GET()

        self.assertEqual(captured["status"], 200)
        self.assertIn("Satellite control service config check:", str(captured["text"]))

    def test_plexamp_play_media_waits_for_expected_or_changed_timeline(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        old_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Welcome to the Jungle" grandparentTitle="Guns N’ Roses" parentTitle="Appetite for Destruction" volume="100"><Track title="Welcome to the Jungle" grandparentTitle="Guns N’ Roses" parentTitle="Appetite for Destruction" /></Timeline></MediaContainer>"""
        new_timeline = """<MediaContainer><Timeline type="music" state="playing" title="September" grandparentTitle="Earth, Wind &amp; Fire" parentTitle="Collections" volume="100"><Track title="September" grandparentTitle="Earth, Wind &amp; Fire" parentTitle="Collections" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "title": "Welcome to the Jungle", "artist": "Guns N’ Roses", "album": "Appetite for Destruction"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[old_timeline, new_timeline]):
            result = adapter.play_media(
                media_type="artist",
                plex_key="/library/metadata/43374/children",
                title="Earth, Wind & Fire",
                artist="Earth, Wind & Fire",
            )

        self.assertTrue(result.ok)
        assert result.payload is not None
        self.assertEqual(result.payload["artist"], "Earth, Wind & Fire")
        self.assertEqual(result.payload["title"], "September")

    def test_plexamp_play_longform_audio_stops_active_music_first(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "state": "playing"}), \
             patch.object(adapter, "stop", return_value=CommandResult(ok=True, state="stopped")) as mock_stop, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertTrue(result.ok)
        mock_stop.assert_called_once_with()
        mock_play_longform.assert_called_once()

    def test_local_playback_without_plexamp_treats_external_backend_as_stopped(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="",
            http_timeout_seconds=5.0,
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="ffplay",
            output_volume_backend="",
            output_volume_card="",
            output_volume_control="",
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        with patch.object(adapter._native_music, "state", return_value={"ok": True, "state": "stopped", "playing": False}), \
             patch.object(adapter, "_timeline") as mock_timeline, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            self.assertEqual(adapter.get_now_playing(), {"ok": True, "playing": False, "state": "stopped"})
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertTrue(result.ok)
        mock_timeline.assert_not_called()
        mock_play_longform.assert_called_once()

    def test_plexamp_play_longform_audio_fails_when_music_stop_fails(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "state": "playing"}), \
             patch.object(adapter, "stop", return_value=CommandResult(ok=False, state="failed", detail="music stop failed")) as mock_stop, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "music stop failed")
        mock_stop.assert_called_once_with()
        mock_play_longform.assert_not_called()

    def test_shell_play_longform_audio_stops_active_music_first(self) -> None:
        args = SimpleNamespace(
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = ShellPlexampAdapter(args)

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "state": "playing"}), \
             patch.object(adapter, "stop", return_value=CommandResult(ok=True, state="stopped")) as mock_stop, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertTrue(result.ok)
        mock_stop.assert_called_once_with()
        mock_play_longform.assert_called_once()

    def test_shell_play_longform_audio_fails_when_music_stop_fails(self) -> None:
        args = SimpleNamespace(
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = ShellPlexampAdapter(args)

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "state": "playing"}), \
             patch.object(adapter, "stop", return_value=CommandResult(ok=False, state="failed", detail="music stop failed")) as mock_stop, \
             patch.object(adapter._longform, "play_longform_audio", return_value=CommandResult(ok=True, state="accepted")) as mock_play_longform:
            result = adapter.play_longform_audio(
                playback_id="book-1",
                session_id="abs-1",
                title="Dune",
                author="Frank Herbert",
                duration_seconds=1000.0,
                start_position_seconds=120.0,
                tracks=[{"url": "http://example/part1.mp3"}],
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "music stop failed")
        mock_stop.assert_called_once_with()
        mock_play_longform.assert_not_called()

    def test_plexamp_play_media_does_not_interrupt_longform(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        timeline = """<MediaContainer><Timeline type="music" state="playing" title="September" grandparentTitle="Earth, Wind &amp; Fire" parentTitle="Collections" volume="100"><Track title="September" grandparentTitle="Earth, Wind &amp; Fire" parentTitle="Collections" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": False, "state": "stopped"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[timeline]), \
             patch.object(adapter._longform, "stop_longform_audio") as mock_stop_longform, \
             patch.object(adapter._longform, "pause_longform_audio") as mock_pause_longform:
            result = adapter.play_media(
                media_type="artist",
                plex_key="/library/metadata/43374/children",
                title="Earth, Wind & Fire",
                artist="Earth, Wind & Fire",
            )

        self.assertTrue(result.ok)
        mock_stop_longform.assert_not_called()
        mock_pause_longform.assert_not_called()

    def test_plexamp_play_media_rejects_unchanged_stale_timeline(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        stale_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Welcome to the Jungle" grandparentTitle="Guns N’ Roses" parentTitle="Appetite for Destruction" volume="100"><Track title="Welcome to the Jungle" grandparentTitle="Guns N’ Roses" parentTitle="Appetite for Destruction" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "title": "Welcome to the Jungle", "artist": "Guns N’ Roses", "album": "Appetite for Destruction"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[stale_timeline] * 8):
            result = adapter.play_media(
                media_type="artist",
                plex_key="/library/metadata/43374/children",
                title="Earth, Wind & Fire",
                artist="Earth, Wind & Fire",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "failed")

    def test_plexamp_resume_uses_native_play_when_current_state_is_paused(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        timeline = """<MediaContainer><Timeline type="music" state="playing" title="Something" grandparentTitle="The Beatles" parentTitle="Abbey Road" volume="100"><Track title="Something" grandparentTitle="The Beatles" parentTitle="Abbey Road" /></Timeline></MediaContainer>"""

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": True, "state": "paused", "title": "Something"},
        ), patch.object(adapter, "_simple_action", return_value=CommandResult(ok=True, state="accepted")) as mock_simple, patch.object(
            adapter,
            "_timeline",
            return_value=timeline,
        ):
            result = adapter.resume()

        self.assertTrue(result.ok)
        mock_simple.assert_called_once_with("play")

    def test_plexamp_resume_fails_when_native_play_does_not_leave_paused_state(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        paused_timeline = """<MediaContainer><Timeline type="music" state="paused" title="Something" grandparentTitle="The Beatles" parentTitle="Abbey Road" volume="100"><Track title="Something" grandparentTitle="The Beatles" parentTitle="Abbey Road" /></Timeline></MediaContainer>"""

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": True, "state": "paused", "title": "Something"},
        ), patch.object(adapter, "_simple_action", return_value=CommandResult(ok=True, state="accepted")), patch.object(
            adapter,
            "_timeline",
            return_value=paused_timeline,
        ):
            result = adapter.resume()

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "Plexamp accepted resume but did not enter a playable state")

    def test_plexamp_resume_replays_fresh_paused_snapshot_when_not_currently_paused(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        adapter._paused_snapshot = adapter._resume_snapshot_from_state(
            {
                "type": "track",
                "plex_key": "/library/metadata/50248",
                "title": "Something",
                "artist": "The Beatles",
                "album": "Abbey Road",
            }
        )
        assert adapter._paused_snapshot is not None

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": False, "state": "stopped"},
        ), patch.object(adapter, "play_media", return_value=CommandResult(ok=True, state="accepted")) as mock_play_media:
            result = adapter.resume()

        self.assertTrue(result.ok)
        mock_play_media.assert_called_once_with(
            media_type="track",
            plex_key="/library/metadata/50248",
            title="Something",
            artist="The Beatles",
            album="Abbey Road",
        )


    def test_plexamp_resume_fails_cleanly_when_no_paused_snapshot_exists(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": False, "state": "stopped"},
        ):
            result = adapter.resume()

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "No paused Plex playback is available to resume")

    def test_plexamp_set_volume_confirms_reported_level(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        volume_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Heroes" grandparentTitle="David Bowie" parentTitle="Heroes" volume="40"><Track title="Heroes" grandparentTitle="David Bowie" parentTitle="Heroes" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), patch.object(
            adapter,
            "_timeline",
            return_value=volume_timeline,
        ):
            result = adapter.set_volume(40)

        self.assertTrue(result.ok)
        assert result.payload is not None
        self.assertEqual(result.payload["volume_level"], 40)

    @patch("satellite.control_service_runtime.system_volume.subprocess.run")
    def test_system_volume_controller_sets_alsa_output_volume(self, run_mock) -> None:
        controller = SystemVolumeController(
            build_system_volume_config(
                SimpleNamespace(
                    output_volume_backend="alsa",
                    output_volume_card="4",
                    output_volume_control="PCM",
                )
            )
        )
        run_mock.side_effect = [
            SimpleNamespace(returncode=0, stdout="Playback 6 [40%] [on]\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Playback 6 [40%] [on]\n", stderr=""),
        ]

        result = controller.set_volume(40)

        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"volume_level": 40})
        self.assertEqual(run_mock.call_args_list[0].args[0], ["amixer", "-c", "4", "sset", "PCM", "40%"])
        self.assertEqual(run_mock.call_args_list[1].args[0], ["amixer", "-c", "4", "sget", "PCM"])

    @patch("satellite.control_service_runtime.system_volume.subprocess.run")
    def test_system_volume_controller_accepts_small_alsa_rounding_difference(self, run_mock) -> None:
        controller = SystemVolumeController(
            build_system_volume_config(
                SimpleNamespace(
                    output_volume_backend="alsa",
                    output_volume_card="4",
                    output_volume_control="PCM",
                )
            )
        )
        run_mock.side_effect = [
            SimpleNamespace(returncode=0, stdout="Playback 6 [38%] [on]\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Playback 6 [38%] [on]\n", stderr=""),
        ]

        result = controller.set_volume(40)

        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"volume_level": 38})

    def test_system_volume_controller_sets_windows_default_endpoint_volume(self) -> None:
        class Endpoint:
            scalar = 0.25

            def GetMasterVolumeLevelScalar(self):
                return self.scalar

            def SetMasterVolumeLevelScalar(self, scalar, _context):
                self.scalar = scalar

        endpoint = Endpoint()
        controller = SystemVolumeController(
            build_system_volume_config(
                SimpleNamespace(
                    output_volume_backend="windows_default_endpoint",
                    output_volume_card="",
                    output_volume_control="",
                )
            )
        )

        with patch(
            "satellite.control_service_runtime.system_volume._load_windows_endpoint",
            return_value=endpoint,
        ):
            result = controller.set_volume(40)
            current = controller.current_level()

        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"volume_level": 40})
        self.assertEqual(current, 40)

    def test_windows_default_endpoint_readiness_checks_platform_and_endpoint(self) -> None:
        endpoint = SimpleNamespace(GetMasterVolumeLevelScalar=lambda: 0.5)
        with patch("satellite.control_service_runtime.system_volume.sys.platform", "win32"), patch(
            "satellite.control_service_runtime.system_volume._load_windows_endpoint",
            return_value=endpoint,
        ):
            self.assertEqual(windows_default_endpoint_support_status(), (True, ""))
        with patch("satellite.control_service_runtime.system_volume.sys.platform", "linux"):
            available, message = windows_default_endpoint_support_status()
        self.assertFalse(available)
        self.assertIn("requires Windows", message)

    def test_windows_endpoint_initializes_com_once_per_calling_thread(self) -> None:
        endpoint = SimpleNamespace()
        audio_utilities = SimpleNamespace(
            GetSpeakers=lambda: SimpleNamespace(EndpointVolume=endpoint)
        )
        pycaw_package = ModuleType("pycaw")
        pycaw_module = ModuleType("pycaw.pycaw")
        pycaw_module.AudioUtilities = audio_utilities
        pycaw_package.pycaw = pycaw_module
        comtypes_module = ModuleType("comtypes")
        initialized: list[bool] = []
        comtypes_module.CoInitialize = lambda: initialized.append(True)

        with patch.object(system_volume_runtime.sys, "platform", "win32"), patch.object(
            system_volume_runtime,
            "_WINDOWS_COM_STATE",
            SimpleNamespace(),
        ), patch.dict(
            sys.modules,
            {
                "comtypes": comtypes_module,
                "pycaw": pycaw_package,
                "pycaw.pycaw": pycaw_module,
            },
        ):
            self.assertIs(system_volume_runtime._load_windows_endpoint(), endpoint)
            self.assertIs(system_volume_runtime._load_windows_endpoint(), endpoint)

        self.assertEqual(initialized, [True])

    @patch("satellite.control_service_runtime.system_volume.subprocess.run")
    def test_plexamp_adapter_prefers_system_volume_when_configured(self, run_mock) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            output_volume_backend="alsa",
            output_volume_card="4",
            output_volume_control="PCM",
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        run_mock.side_effect = [
            SimpleNamespace(returncode=0, stdout="Playback 6 [40%] [on]\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Playback 6 [40%] [on]\n", stderr=""),
        ]

        with patch.object(adapter, "_request") as request_mock:
            result = adapter.set_volume(40)

        self.assertTrue(result.ok)
        request_mock.assert_not_called()

    def test_plexamp_set_volume_fails_when_reported_level_does_not_change(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        stale_volume_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Heroes" grandparentTitle="David Bowie" parentTitle="Heroes" volume="100"><Track title="Heroes" grandparentTitle="David Bowie" parentTitle="Heroes" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), patch.object(
            adapter,
            "_timeline",
            return_value=stale_volume_timeline,
        ):
            result = adapter.set_volume(40)

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "Plexamp accepted set_volume but did not report the requested level")

    def test_plexamp_play_media_allows_longer_album_startup_window(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        stopped_timeline = """<MediaContainer><Timeline type="music" state="stopped" volume="100" /></MediaContainer>"""
        album_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Alexander Hamilton" grandparentTitle="Leslie Odom, Jr." parentTitle="Hamilton: An American Musical" volume="100"><Track title="Alexander Hamilton" grandparentTitle="Leslie Odom, Jr." parentTitle="Hamilton: An American Musical" /></Timeline></MediaContainer>"""

        side_effect = [stopped_timeline] * 9 + [album_timeline]

        with patch.object(
            adapter,
            "get_now_playing",
            return_value={"ok": True, "playing": False},
        ), patch.object(
            adapter,
            "_request",
            return_value=CommandResult(ok=True, state="accepted"),
        ), patch.object(
            adapter,
            "_timeline",
            side_effect=side_effect,
        ), patch("satellite.control_service_runtime.adapters.plexamp_http.time.sleep", return_value=None):
            result = adapter.play_media(
                media_type="album",
                plex_key="/library/metadata/43879/children",
                title="Hamilton: An American Musical",
                artist="Lin‐Manuel Miranda",
                album="Hamilton: An American Musical",
            )

        self.assertTrue(result.ok)
        assert result.payload is not None
        self.assertEqual(result.payload["album"], "Hamilton: An American Musical")
        self.assertEqual(result.payload["title"], "Alexander Hamilton")

    def test_plexamp_stop_waits_for_non_playing_timeline(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        stopped_timeline = """<MediaContainer><Timeline type="music" state="stopped" volume="100"></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "title": "Come Together", "artist": "The Beatles", "album": "Abbey Road"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[stopped_timeline]):
            result = adapter.stop()

        self.assertTrue(result.ok)
        self.assertEqual(result.state, "stopped")

    def test_plexamp_native_queue_play_uses_selected_queue_track(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="auto",
        )
        adapter = PlexampHttpAdapter(args)

        queue_tracks = [
            {
                "rating_key": "track-1",
                "plex_key": "/library/metadata/track-1",
                "title": "Speed of Life",
                "artist": "David Bowie",
                "album": "Low",
                "duration_seconds": 125.0,
            },
            {
                "rating_key": "track-2",
                "plex_key": "/library/metadata/track-2",
                "title": "Breaking Glass",
                "artist": "David Bowie",
                "album": "Low",
                "duration_seconds": 111.0,
            },
        ]

        with patch.object(adapter, "_stop_plexamp_if_active"), patch.object(
            adapter,
            "_build_native_stream_url",
            return_value="http://127.0.0.1/stream.mp3",
        ), patch.object(
            adapter._native_music,
            "play_track",
            return_value=CommandResult(ok=True, state="accepted", payload={"state": "playing"}),
        ) as mock_play_track:
            result = adapter.play_media(
                media_type="album",
                plex_key="/library/metadata/album-low/children",
                rating_key="album-low",
                title="Low",
                artist="David Bowie",
                album="Low",
                backend_hint="oracle_native_music",
                queue_id="album-low",
                queue_position=2,
                queue_count=2,
                collection_title="Low",
                collection_type="album",
                queue_tracks=queue_tracks,
            )

        self.assertTrue(result.ok)
        mock_play_track.assert_called_once()
        self.assertEqual(mock_play_track.call_args.kwargs["track_id"], "track-2")
        self.assertEqual(mock_play_track.call_args.kwargs["title"], "Breaking Glass")
        self.assertEqual(mock_play_track.call_args.kwargs["queue_count"], 2)
        self.assertEqual(mock_play_track.call_args.kwargs["collection_type"], "album")

    def test_plexamp_native_queue_next_advances_to_next_track(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="auto",
        )
        adapter = PlexampHttpAdapter(args)
        native_state = {
            "ok": True,
            "state": "playing",
            "backend_type": "oracle_native_music",
            "media_type": "playlist",
            "queue_id": "playlist-1",
            "queue_position": 1,
            "queue_count": 2,
            "collection_title": "Favorites",
            "collection_type": "playlist",
            "position_seconds": 2.0,
            "queue_tracks": [
                {"rating_key": "track-1", "title": "Song 1", "artist": "Artist", "album": "Album", "duration_seconds": 100.0},
                {"rating_key": "track-2", "title": "Song 2", "artist": "Artist", "album": "Album", "duration_seconds": 110.0},
            ],
        }

        with patch.object(adapter, "_safe_native_music_state", return_value=native_state), \
             patch.object(adapter, "_build_native_stream_url", return_value="http://127.0.0.1/track-2.mp3"), \
             patch.object(
                 adapter._native_music,
                 "play_track",
                 return_value=CommandResult(ok=True, state="accepted", payload={"queue_position": 2, "title": "Song 2"}),
             ) as mock_play_track:
            result = adapter.next()

        self.assertTrue(result.ok)
        self.assertEqual(mock_play_track.call_args.kwargs["track_id"], "track-2")
        self.assertEqual(mock_play_track.call_args.kwargs["queue_position"], 2)

    def test_plexamp_native_queue_previous_moves_to_previous_track(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="auto",
        )
        adapter = PlexampHttpAdapter(args)
        native_state = {
            "ok": True,
            "state": "playing",
            "backend_type": "oracle_native_music",
            "media_type": "playlist",
            "queue_id": "playlist-1",
            "queue_position": 2,
            "queue_count": 2,
            "collection_title": "Favorites",
            "collection_type": "playlist",
            "position_seconds": 12.0,
            "queue_tracks": [
                {"rating_key": "track-1", "title": "Song 1", "artist": "Artist", "album": "Album", "duration_seconds": 100.0},
                {"rating_key": "track-2", "title": "Song 2", "artist": "Artist", "album": "Album", "duration_seconds": 110.0},
            ],
        }

        with patch.object(adapter, "_safe_native_music_state", return_value=native_state), \
             patch.object(adapter, "_build_native_stream_url", return_value="http://127.0.0.1/track-1.mp3"), \
             patch.object(
                 adapter._native_music,
                 "play_track",
                 return_value=CommandResult(ok=True, state="accepted", payload={"queue_position": 1, "title": "Song 1"}),
             ) as mock_play_track:
            result = adapter.previous()

        self.assertTrue(result.ok)
        self.assertEqual(mock_play_track.call_args.kwargs["track_id"], "track-1")
        self.assertEqual(mock_play_track.call_args.kwargs["queue_position"], 1)

    def test_plexamp_native_music_restart_restarts_current_track(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
            supports_oracle_native_music=True,
            oracle_native_music_player_bin="auto",
        )
        adapter = PlexampHttpAdapter(args)
        native_state = {
            "ok": True,
            "state": "playing",
            "backend_type": "oracle_native_music",
            "media_type": "playlist",
            "queue_id": "playlist-1",
            "queue_position": 2,
            "queue_count": 2,
            "position_seconds": 12.0,
        }

        with patch.object(adapter, "_safe_native_music_state", return_value=native_state), \
             patch.object(
                 adapter._native_music,
                 "restart",
                 return_value=CommandResult(ok=True, state="accepted", payload={"queue_position": 2, "position_seconds": 0.0}),
             ) as mock_restart:
            result = adapter.restart()

        self.assertTrue(result.ok)
        mock_restart.assert_called_once_with()

    def test_plexamp_stop_rejects_stale_playing_timeline(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)

        stale_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Come Together" grandparentTitle="The Beatles" parentTitle="Abbey Road" volume="100"><Track title="Come Together" grandparentTitle="The Beatles" parentTitle="Abbey Road" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "get_now_playing", return_value={"ok": True, "playing": True, "title": "Come Together", "artist": "The Beatles", "album": "Abbey Road"}), \
             patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), \
             patch.object(adapter, "_timeline", side_effect=[stale_timeline] * 8):
            result = adapter.stop()

        self.assertFalse(result.ok)
        self.assertEqual(result.state, "failed")
        self.assertIn("remained in a playable state", result.detail)


if __name__ == "__main__":
    unittest.main()
