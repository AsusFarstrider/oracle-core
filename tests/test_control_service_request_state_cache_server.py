from __future__ import annotations

import json
import sys
import time
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, RLock
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
        server.runtime_lock = RLock()
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

    def test_command_cache_serializes_duplicate_in_flight_commands(self) -> None:
        cache = CommandCache()
        barrier = Barrier(8)
        counter_lock = Lock()
        execution_count = 0

        def invoke() -> tuple[dict[str, object], bool]:
            barrier.wait()

            def execute() -> dict[str, object]:
                nonlocal execution_count
                with counter_lock:
                    execution_count += 1
                return {"ok": True, "state": "paused"}

            return cache.get_or_store("shared-command", execute)

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _index: invoke(), range(8)))

        self.assertEqual(execution_count, 1)
        self.assertEqual(sum(1 for _payload, cached in results if not cached), 1)
        self.assertTrue(all(payload == {"ok": True, "state": "paused"} for payload, _cached in results))

    def test_command_cache_returns_snapshot_copies(self) -> None:
        cache = CommandCache()
        cache.store("cmd-copy", {"ok": True, "nested": {"state": "paused"}})

        first = cache.get("cmd-copy")
        assert first is not None
        first["nested"]["state"] = "mutated"

        self.assertEqual(cache.get("cmd-copy"), {"ok": True, "nested": {"state": "paused"}})

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

    def test_control_server_health_exposes_music_backend_expectation(self) -> None:
        server = ControlServer.__new__(ControlServer)
        server.runtime_lock = RLock()
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

    def test_control_server_serializes_shared_adapter_access(self) -> None:
        server = ControlServer.__new__(ControlServer)
        server.runtime_lock = RLock()
        counter_lock = Lock()
        active_calls = 0
        maximum_active_calls = 0

        def health() -> dict[str, bool]:
            nonlocal active_calls, maximum_active_calls
            with counter_lock:
                active_calls += 1
                maximum_active_calls = max(maximum_active_calls, active_calls)
            time.sleep(0.01)
            with counter_lock:
                active_calls -= 1
            return {"ok": True}

        server.adapter = SimpleNamespace(health=health)

        with ThreadPoolExecutor(max_workers=8) as executor:
            payloads = list(executor.map(lambda _index: server.build_health_payload(), range(8)))

        self.assertEqual(maximum_active_calls, 1)
        self.assertTrue(all(payload["adapter"] == {"ok": True} for payload in payloads))

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

if __name__ == "__main__":
    unittest.main()
