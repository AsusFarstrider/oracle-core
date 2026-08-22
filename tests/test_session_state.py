from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

python_multipart_stub = ModuleType("python_multipart")
python_multipart_stub.__version__ = "0.0.13"
python_multipart_stub.__all__ = []
python_multipart_stub.__author__ = ""
python_multipart_stub.__copyright__ = ""
python_multipart_stub.__license__ = ""
python_multipart_multipart_stub = ModuleType("python_multipart.multipart")
python_multipart_multipart_stub.parse_options_header = lambda value: (value, {})
sys.modules.setdefault("python_multipart", python_multipart_stub)
sys.modules.setdefault("python_multipart.multipart", python_multipart_multipart_stub)

from oracle_app.api import session_lookup
from oracle_app import state
from oracle_app.command_events import append_command_interim_event, list_command_interim_events
from oracle_app.conversation import append_turn, get_conversation
from oracle_app.session_state import describe_followup_resolution, set_active_context, set_user_context
from oracle_app.session_state import _SESSIONS, _SESSION_AUDIT, clear_all_sessions, clear_session_state, inspect_session, refresh_session, resolve_request_session, set_pending_state


class SessionStateTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_all_sessions()

    def test_resolve_request_session_keeps_valid_client_session_id(self) -> None:
        session = resolve_request_session("satellite-alpha", "session-1")

        self.assertEqual(session["source"], "satellite-alpha")
        self.assertEqual(session["client_session_id"], "session-1")
        self.assertEqual(session["effective_session_id"], "session-1")
        self.assertFalse(session["fallback_generated"])

    def test_resolve_request_session_generates_deterministic_fallback_per_source(self) -> None:
        first = resolve_request_session("satellite-alpha", None)
        second = resolve_request_session("satellite-alpha", "")

        self.assertIsNone(first["client_session_id"])
        self.assertTrue(first["fallback_generated"])
        self.assertEqual(first["effective_session_id"], second["effective_session_id"])

    def test_inspect_session_returns_session_meta_and_derived_fields(self) -> None:
        resolved = resolve_request_session("satellite-alpha", None)

        payload = inspect_session("satellite-alpha", resolved["effective_session_id"])

        assert payload is not None
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["session_meta"]["source"], "satellite-alpha")
        self.assertEqual(payload["session_meta"]["effective_session_id"], resolved["effective_session_id"])
        self.assertTrue(payload["session_meta"]["fallback_generated"])
        self.assertTrue(payload["derived"]["session_active"])
        self.assertFalse(payload["derived"]["pending_active"])
        self.assertEqual(payload["derived"]["anchor_strength"], "")
        self.assertEqual(payload["derived"]["follow_up_resolution_order"], "general_routing")
        self.assertFalse(payload["derived"]["waiting_on_user"])

    def test_session_lookup_raises_not_found_for_missing_session(self) -> None:
        with self.assertRaises(Exception) as exc_info:
            session_lookup(source="satellite-alpha", session_id="missing-session")

        self.assertEqual(exc_info.exception.status_code, 404)

    def test_session_lookup_includes_unified_pending_state(self) -> None:
        state.store_pending_confirmation(
            "satellite-alpha",
            "confirm-1",
            {
                "dispatch": {
                    "target": "home_assistant",
                    "hook": "home_assistant.execute",
                    "payload": {"text": "unlock the side entry"},
                },
                "prompt": "Please confirm.",
            },
        )

        response = session_lookup(source="satellite-alpha", session_id="confirm-1")
        payload = response.body.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn('"domain":"confirmation"', payload)
        self.assertIn('"type":"confirmation"', payload)

    def test_pending_state_sets_strong_active_context(self) -> None:
        state.store_pending_music_request(
            "satellite-alpha",
            "music-pending-1",
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [{"title": "Heroes"}],
            },
        )

        payload = inspect_session("satellite-alpha", "music-pending-1")

        assert payload is not None
        self.assertEqual(payload["active_context"]["route_target"], "music")
        self.assertEqual(payload["active_context"]["anchor_strength"], "strong")
        self.assertEqual(payload["derived"]["follow_up_resolution_order"], "pending_state")
        self.assertEqual(payload["derived"]["pending_domain"], "music")
        self.assertTrue(payload["derived"]["waiting_on_user"])

    def test_resolve_request_session_does_not_refresh_existing_session(self) -> None:
        resolve_request_session("satellite-alpha", "session-1")
        original_refresh = _SESSIONS["satellite-alpha:session-1"]["session_meta"]["refreshed_monotonic"]

        resolve_request_session("satellite-alpha", "session-1")

        self.assertEqual(
            _SESSIONS["satellite-alpha:session-1"]["session_meta"]["refreshed_monotonic"],
            original_refresh,
        )

    def test_refresh_session_updates_existing_session(self) -> None:
        resolve_request_session("satellite-alpha", "session-1")
        original_refresh = _SESSIONS["satellite-alpha:session-1"]["session_meta"]["refreshed_monotonic"]

        refreshed = refresh_session("satellite-alpha", "session-1")

        self.assertTrue(refreshed)
        self.assertGreater(
            _SESSIONS["satellite-alpha:session-1"]["session_meta"]["refreshed_monotonic"],
            original_refresh,
        )

    def test_inspect_session_reports_expired_pending_state(self) -> None:
        state.store_pending_confirmation(
            "satellite-alpha",
            "confirm-expire-1",
            {
                "dispatch": {
                    "target": "home_assistant",
                    "hook": "home_assistant.execute",
                    "payload": {"text": "unlock the side entry"},
                },
                "prompt": "Please confirm.",
            },
        )
        created = _SESSIONS["satellite-alpha:confirm-expire-1"]["pending_state"]["created_monotonic"]

        with patch("oracle_app.session_state.time.monotonic", return_value=created + 31.0):
            payload = inspect_session("satellite-alpha", "confirm-expire-1")
            pending = state.load_pending_confirmation("satellite-alpha", "confirm-expire-1")

        assert payload is not None
        self.assertTrue(payload["derived"]["session_active"])
        self.assertFalse(payload["derived"]["pending_active"])
        self.assertTrue(payload["derived"]["pending_expired"])
        self.assertIsNone(payload["pending_state"])
        self.assertIsNone(pending)

    def test_inspect_session_returns_none_after_session_timeout(self) -> None:
        resolve_request_session("satellite-alpha", "session-expire-1")
        refreshed = _SESSIONS["satellite-alpha:session-expire-1"]["session_meta"]["refreshed_monotonic"]

        with patch("oracle_app.session_state.time.monotonic", return_value=refreshed + 91.0):
            payload = inspect_session("satellite-alpha", "session-expire-1")

        self.assertIsNone(payload)

    def test_session_lookup_raises_not_found_after_session_timeout(self) -> None:
        resolve_request_session("satellite-alpha", "session-expire-2")
        refreshed = _SESSIONS["satellite-alpha:session-expire-2"]["session_meta"]["refreshed_monotonic"]

        with patch("oracle_app.session_state.time.monotonic", return_value=refreshed + 91.0):
            with self.assertRaises(Exception) as exc_info:
                session_lookup(source="satellite-alpha", session_id="session-expire-2")

        self.assertEqual(exc_info.exception.status_code, 404)

    def test_session_expiry_atomically_clears_owned_compartments(self) -> None:
        resolve_request_session("satellite-alpha", "session-expire-owned")
        append_turn("satellite-alpha", "session-expire-owned", "user", "What is new?")
        append_command_interim_event(
            source="satellite-alpha",
            session_id="session-expire-owned",
            event_type="facts_summarizer_ack",
            domain="facts",
            message="I am checking.",
        )
        refreshed = _SESSIONS["satellite-alpha:session-expire-owned"]["session_meta"]["refreshed_monotonic"]

        with patch("oracle_app.session_state.time.monotonic", return_value=refreshed + 91.0):
            payload = inspect_session("satellite-alpha", "session-expire-owned")

        self.assertIsNone(payload)
        self.assertIsNone(get_conversation("satellite-alpha", "session-expire-owned"))
        self.assertEqual(
            list_command_interim_events(
                source="satellite-alpha",
                session_id="session-expire-owned",
            ),
            [],
        )
        self.assertNotIn("satellite-alpha:session-expire-owned", _SESSION_AUDIT)

    def test_explicit_reset_clears_history_events_and_prior_audit(self) -> None:
        resolve_request_session("satellite-alpha", "session-reset-owned")
        set_active_context(
            "satellite-alpha",
            "session-reset-owned",
            route_target="music",
            dispatch_hook="music.execute",
            action="play",
            anchor_strength="strong",
        )
        append_turn("satellite-alpha", "session-reset-owned", "user", "Play music")
        append_command_interim_event(
            source="satellite-alpha",
            session_id="session-reset-owned",
            event_type="facts_summarizer_ack",
            domain="facts",
            message="I am checking.",
        )

        result = clear_session_state(
            "satellite-alpha",
            "session-reset-owned",
            reason="explicit_cancel",
        )
        inspected = inspect_session("satellite-alpha", "session-reset-owned")

        self.assertTrue(result["active_context_cleared"])
        self.assertTrue(result["conversation_cleared"])
        self.assertTrue(result["command_events_cleared"])
        self.assertTrue(result["audit_cleared"])
        self.assertIsNone(get_conversation("satellite-alpha", "session-reset-owned"))
        self.assertEqual(
            list_command_interim_events(
                source="satellite-alpha",
                session_id="session-reset-owned",
            ),
            [],
        )
        assert inspected is not None
        self.assertEqual(set(inspected["lifecycle"]), {"session"})
        self.assertEqual(inspected["lifecycle"]["session"]["event"], "session_reset")

    def test_concurrent_conversation_mutations_keep_six_turn_bound(self) -> None:
        resolve_request_session("satellite-alpha", "session-concurrent")

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(
                executor.map(
                    lambda index: append_turn(
                        "satellite-alpha",
                        "session-concurrent",
                        "user",
                        f"turn-{index}",
                    ),
                    range(100),
                )
            )

        conversation = get_conversation("satellite-alpha", "session-concurrent")

        assert conversation is not None
        self.assertEqual(len(conversation["history"]), 6)
        self.assertEqual(len({turn["text"] for turn in conversation["history"]}), 6)

    def test_inspect_session_includes_active_room_ref_when_present(self) -> None:
        set_active_context(
            "satellite-alpha",
            "home-room-1",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the study lights",
            active_room_ref="study",
        )

        payload = inspect_session("satellite-alpha", "home-room-1")

        assert payload is not None
        self.assertEqual(payload["active_context"]["active_room_ref"], "study")

    def test_inspect_session_includes_user_context_when_present(self) -> None:
        set_user_context(
            "satellite-alpha",
            "user-1",
            user_id="user-alpha",
            resolution_source="explicit_switch",
        )

        payload = inspect_session("satellite-alpha", "user-1")

        assert payload is not None
        self.assertEqual(payload["user_context"]["active_user_id"], "user-alpha")
        self.assertEqual(payload["derived"]["active_user_id"], "user-alpha")

    def test_clear_session_state_clears_user_context(self) -> None:
        set_user_context(
            "satellite-alpha",
            "user-2",
            user_id="user-beta",
            resolution_source="explicit_switch",
        )

        result = clear_session_state("satellite-alpha", "user-2")
        payload = inspect_session("satellite-alpha", "user-2")

        self.assertTrue(result["user_context_cleared"])
        assert payload is not None
        self.assertIsNone(payload["user_context"])

    def test_describe_followup_resolution_prefers_pending_state_over_active_context(self) -> None:
        set_active_context(
            "satellite-alpha",
            "precedence-1",
            route_target="music",
            dispatch_hook="music.execute",
            action="play",
            anchor_strength="strong",
        )
        state.store_pending_music_request(
            "satellite-alpha",
            "precedence-1",
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [{"title": "Heroes"}],
            },
        )

        followup = describe_followup_resolution("satellite-alpha", "precedence-1")

        self.assertEqual(followup["resolution_order"], "pending_state")
        self.assertEqual(followup["pending_domain"], "music")
        self.assertEqual(followup["route_target"], "music")

    def test_set_active_context_rejects_non_home_room_reference(self) -> None:
        stored = set_active_context(
            "satellite-alpha",
            "invalid-active-1",
            route_target="music",
            dispatch_hook="music.execute",
            action="play",
            anchor_strength="strong",
            active_room_ref="study",
        )

        self.assertFalse(stored)
        self.assertIsNone(inspect_session("satellite-alpha", "invalid-active-1"))

    def test_set_pending_state_rejects_non_session_owned_reference_keys(self) -> None:
        stored = set_pending_state(
            "satellite-alpha",
            "invalid-pending-1",
            pending_type="clarification",
            domain="music",
            payload={
                "candidates": [{"title": "Heroes"}],
                "playback_authority": {"active_sessions": []},
            },
        )

        self.assertFalse(stored)
        self.assertIsNone(inspect_session("satellite-alpha", "invalid-pending-1"))


if __name__ == "__main__":
    unittest.main()
