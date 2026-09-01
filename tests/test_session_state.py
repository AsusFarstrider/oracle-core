from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace
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

from oracle_app.application_command import (
    _continue_from_fallback_router,
    _resolve_router_user_override,
    session_lookup,
)
from oracle_app import state
from oracle_app.command_events import append_command_interim_event, list_command_interim_events
from oracle_app.conversation import append_turn, get_conversation
from oracle_app.routing import build_route_capability_registry
from oracle_app.schemas import CommandRequest, DispatchPlan, RouteResponse
from oracle_app.session_state import (
    clear_utility_context,
    clear_utility_context_for_topic_change,
    describe_followup_resolution,
    get_utility_context,
    set_active_context,
    set_user_context,
    set_utility_context,
)
from oracle_app.session_state import _SESSIONS, _SESSION_AUDIT, clear_all_sessions, clear_session_state, inspect_session, refresh_session, resolve_request_session, set_pending_state
from canonical_test_support import neutral_brain_runtime_settings


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

    @patch("oracle_app.application_command.brain_application_composition")
    def test_fallback_continuation_fails_before_dispatch_when_owner_rejects_proposal(self, composition_lookup) -> None:
        runtime = neutral_brain_runtime_settings()
        composition_lookup.return_value = SimpleNamespace(
            route_registry=build_route_capability_registry(runtime.household),
            runtime=runtime,
            music_execution=None,
            audiobook_execution=None,
        )
        payload = CommandRequest(
            text="put something on",
            source="satellite-alpha",
            session_id="fallback-reentry-1",
        )
        initial_route = RouteResponse(
            target="fallback_router",
            confidence=0.64,
            reason="No deterministic capability matched",
            normalized_text="put something on",
        )
        fallback_dispatch = DispatchPlan(
            target="fallback_router",
            hook="fallback_router.decide",
            payload={},
            status="executed",
            result={
                "action": "route_proposed",
                "proposed_domain": "system",
                "normalized_text": "play david bowie",
                "user_id": "",
            },
        )

        final_route, final_dispatch = _continue_from_fallback_router(
            original_payload=payload,
            effective_payload=payload,
            route=initial_route,
            dispatch=fallback_dispatch,
            household_settings=runtime.household,
        )

        self.assertEqual(final_route.target, "system")
        self.assertEqual(final_dispatch.status, "failed")
        self.assertEqual(final_dispatch.result["error"], "fallback_router_unvalidated_proposal")
        self.assertEqual(final_dispatch.result["owning_component"], "brain.fallback_router")

    def test_fallback_user_override_requires_user_to_be_explicit_in_original_request(self) -> None:
        household = neutral_brain_runtime_settings().household

        explicit = _resolve_router_user_override(
            proposed_domain="audiobook",
            proposed_user_id="resident_one",
            dispatch_payload={"prompt": "resume resident one's audiobook"},
            household_settings=household,
        )
        implicit = _resolve_router_user_override(
            proposed_domain="audiobook",
            proposed_user_id="resident_one",
            dispatch_payload={"prompt": "resume my audiobook"},
            household_settings=household,
        )

        self.assertEqual(explicit, "resident_one")
        self.assertIsNone(implicit)

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

    def test_typed_utility_context_is_session_scoped_and_copied(self) -> None:
        payload = {"value": "48", "display_text": "48"}

        stored = set_utility_context(
            "satellite-alpha",
            "utility-1",
            kind="calculation",
            payload=payload,
        )
        payload["value"] = "changed"
        first = get_utility_context("satellite-alpha", "utility-1", kind="calculation")
        assert first is not None
        first["payload"]["value"] = "also changed"
        second = get_utility_context("satellite-alpha", "utility-1", kind="calculation")

        self.assertTrue(stored)
        assert second is not None
        self.assertEqual(second["payload"]["value"], "48")
        self.assertIsNone(get_utility_context("satellite-beta", "utility-1", kind="calculation"))
        inspected = inspect_session("satellite-alpha", "utility-1")
        assert inspected is not None
        self.assertEqual(inspected["derived"]["utility_context_kinds"], ["calculation"])

    def test_typed_utility_context_rejects_unbounded_or_live_references(self) -> None:
        nested = set_utility_context(
            "satellite-alpha",
            "utility-invalid-1",
            kind="calculation",
            payload={"value": {"nested": "not allowed"}},
        )
        extra = set_utility_context(
            "satellite-alpha",
            "utility-invalid-1",
            kind="calculation",
            payload={"value": "4", "config": "live-authority"},
        )
        unidentified_alert = set_utility_context(
            "satellite-alpha",
            "utility-invalid-1",
            kind="alert_subject",
            payload={"alert_kind": "timer"},
        )

        self.assertFalse(nested)
        self.assertFalse(extra)
        self.assertFalse(unidentified_alert)
        self.assertIsNone(inspect_session("satellite-alpha", "utility-invalid-1"))

    def test_utility_pending_state_routes_back_to_system_owner(self) -> None:
        stored = set_pending_state(
            "satellite-alpha",
            "utility-pending-1",
            pending_type="clarification",
            domain="utilities",
            payload={
                "clarification_kind": "recipient",
                "context_kind": "alert_subject",
                "prompt": "Who should receive it?",
                "options": ["everyone", "specific person"],
            },
        )

        followup = describe_followup_resolution("satellite-alpha", "utility-pending-1")

        self.assertTrue(stored)
        self.assertEqual(followup["resolution_order"], "pending_state")
        self.assertEqual(followup["pending_domain"], "utilities")
        self.assertEqual(followup["route_target"], "system")

    def test_clear_session_state_clears_utility_context_and_pending_state(self) -> None:
        set_utility_context(
            "satellite-alpha",
            "utility-reset-1",
            kind="repeat_output",
            payload={"reply_text": "It is 8 o'clock.", "route_target": "system"},
        )
        set_pending_state(
            "satellite-alpha",
            "utility-reset-1",
            pending_type="clarification",
            domain="utilities",
            payload={
                "clarification_kind": "location",
                "context_kind": "temporal",
                "prompt": "Which Springfield?",
                "options": ["Illinois", "Massachusetts"],
            },
        )

        result = clear_session_state("satellite-alpha", "utility-reset-1")
        inspected = inspect_session("satellite-alpha", "utility-reset-1")

        self.assertTrue(result["utility_context_cleared"])
        self.assertTrue(result["pending_cleared"])
        assert inspected is not None
        self.assertEqual(inspected["utility_context"], {})
        self.assertIsNone(inspected["pending_state"])

    def test_clear_utility_context_can_clear_one_typed_slot(self) -> None:
        set_utility_context(
            "satellite-alpha",
            "utility-clear-1",
            kind="calculation",
            payload={"value": "4"},
        )
        set_utility_context(
            "satellite-alpha",
            "utility-clear-1",
            kind="temporal",
            payload={"subject_type": "date", "iso_value": "2026-08-27"},
        )

        cleared = clear_utility_context(
            "satellite-alpha",
            "utility-clear-1",
            kind="calculation",
            reason="superseded",
        )

        self.assertTrue(cleared)
        self.assertIsNone(get_utility_context("satellite-alpha", "utility-clear-1", kind="calculation"))
        self.assertIsNotNone(get_utility_context("satellite-alpha", "utility-clear-1", kind="temporal"))

    def test_explicit_non_utility_topic_change_clears_utility_context(self) -> None:
        set_utility_context(
            "satellite-alpha",
            "utility-topic-1",
            kind="conversion",
            payload={
                "value": "12",
                "dimension": "length",
                "source_unit": "mile",
                "target_unit": "kilometer",
            },
        )

        system_clear = clear_utility_context_for_topic_change(
            "satellite-alpha",
            "utility-topic-1",
            route_target="system",
        )
        media_clear = clear_utility_context_for_topic_change(
            "satellite-alpha",
            "utility-topic-1",
            route_target="music",
        )

        self.assertFalse(system_clear)
        self.assertTrue(media_clear)
        self.assertEqual(get_utility_context("satellite-alpha", "utility-topic-1"), {})

    def test_utility_pending_state_rejects_unbounded_choices_and_live_references(self) -> None:
        too_many = set_pending_state(
            "satellite-alpha",
            "utility-pending-invalid",
            pending_type="clarification",
            domain="utilities",
            payload={
                "clarification_kind": "location",
                "prompt": "Which location?",
                "options": ["a", "b", "c", "d", "e", "f"],
            },
        )
        live_reference = set_pending_state(
            "satellite-alpha",
            "utility-pending-invalid",
            pending_type="clarification",
            domain="utilities",
            payload={
                "clarification_kind": "recipient",
                "prompt": "Who?",
                "options": ["everyone", "person"],
                "alerts": "global",
            },
        )

        self.assertFalse(too_many)
        self.assertFalse(live_reference)

    def test_utility_context_expires_with_canonical_session(self) -> None:
        set_utility_context(
            "satellite-alpha",
            "utility-expire-1",
            kind="temporal",
            payload={"subject_type": "date", "iso_value": "2026-08-27"},
        )
        refreshed = _SESSIONS["satellite-alpha:utility-expire-1"]["session_meta"]["refreshed_monotonic"]

        with patch("oracle_app.session_state.time.monotonic", return_value=refreshed + 91.0):
            payload = inspect_session("satellite-alpha", "utility-expire-1")

        self.assertIsNone(payload)

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
