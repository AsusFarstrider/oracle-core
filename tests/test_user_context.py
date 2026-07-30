from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.session_state import clear_all_sessions, set_active_context, set_user_context
from oracle_app.user_context import analyze_user_directive, resolve_effective_user, resolve_user_name
from canonical_test_support import neutral_household_runtime_settings


class UserContextTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_all_sessions()

    def test_canonical_resolution_uses_typed_household_without_legacy_registries(self) -> None:
        household = unittest.mock.MagicMock()
        household.resolve_user_id.side_effect = lambda value: {
            "resident one": "resident_one",
        }.get(value)
        household.user.side_effect = lambda value: (
            object() if value in {"resident_one", "resident_two"} else None
        )
        household.configured_associated_user_id.return_value = "resident_one"
        household.default_user.return_value = unittest.mock.MagicMock(id="resident_two")

        with (
            patch("oracle_app.user_context.get_user_registry") as legacy_users,
            patch("oracle_app.user_context.get_source_registry") as legacy_sources,
        ):
            explicit = resolve_effective_user(
                source="living_room_voice",
                requested_user_name="Resident One",
                household_settings=household,
            )
            associated = resolve_effective_user(
                source="living_room_voice",
                session_id="canonical-user-association",
                household_settings=household,
            )
            household.configured_associated_user_id.return_value = None
            defaulted = resolve_effective_user(
                source="unassociated_browser",
                session_id="canonical-household-default",
                household_settings=household,
            )

        self.assertEqual(explicit["user_id"], "resident_one")
        self.assertEqual(explicit["resolution_source"], "explicit_user")
        self.assertEqual(associated["user_id"], "resident_one")
        self.assertEqual(associated["resolution_source"], "source_association")
        self.assertEqual(defaulted["user_id"], "resident_two")
        self.assertEqual(defaulted["resolution_source"], "household_default")
        legacy_users.assert_not_called()
        legacy_sources.assert_not_called()

    def test_canonical_resolution_rejects_stale_disabled_session_user(self) -> None:
        household = unittest.mock.MagicMock()
        household.user.return_value = None
        household.configured_associated_user_id.return_value = "resident_one"
        household.default_user.return_value = None
        set_user_context(
            "living_room_voice",
            "stale-user-session",
            user_id="disabled_user",
            resolution_source="explicit_switch",
        )

        resolved = resolve_effective_user(
            source="living_room_voice",
            session_id="stale-user-session",
            household_settings=household,
        )

        self.assertEqual(resolved["user_id"], "resident_one")
        self.assertEqual(resolved["resolution_source"], "source_association")

    def test_analyze_user_directive_rewrites_possessive_audiobook_request(self) -> None:
        directive = analyze_user_directive("resume casey's audiobook and set a sleep timer for 20 minutes")

        self.assertEqual(directive.directive_type, "explicit_request_user")
        self.assertEqual(directive.requested_user_name, "casey")
        self.assertEqual(directive.rewritten_text, "resume my audiobook and set a sleep timer for 20 minutes")

    def test_analyze_user_directive_rewrites_punctuated_possessive_audiobook_request(self) -> None:
        directive = analyze_user_directive("resume casey's audiobook")

        self.assertEqual(directive.directive_type, "explicit_request_user")
        self.assertEqual(directive.requested_user_name, "casey")
        self.assertEqual(directive.rewritten_text, "resume my audiobook")

    def test_resolve_effective_user_prefers_session_user_before_default(self) -> None:
        household = unittest.mock.MagicMock()
        household.user.return_value = object()
        set_user_context("test-source", "user-session-1", user_id="casey", resolution_source="explicit_switch")

        resolved = resolve_effective_user(
            source="test-source",
            session_id="user-session-1",
            household_settings=household,
        )

        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["user_id"], "casey")
        self.assertEqual(resolved["resolution_source"], "session_user")

    def test_resolve_effective_user_prefers_source_association_before_household_default(self) -> None:
        household = unittest.mock.MagicMock()
        household.configured_associated_user_id.return_value = "resident_two"
        household.default_user.return_value = unittest.mock.MagicMock(id="resident_one")

        resolved = resolve_effective_user(
            source="satellite-alpha",
            session_id="user-session-2",
            household_settings=household,
        )

        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["user_id"], "resident_two")
        self.assertEqual(resolved["resolution_source"], "source_association")

    def test_resolve_effective_user_keeps_explicit_session_user_over_source_default(self) -> None:
        household = unittest.mock.MagicMock()
        household.user.return_value = object()
        set_user_context("satellite-alpha", "user-session-3", user_id="taylor", resolution_source="explicit_switch")

        resolved = resolve_effective_user(
            source="satellite-alpha",
            session_id="user-session-3",
            household_settings=household,
        )

        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["user_id"], "taylor")
        self.assertEqual(resolved["resolution_source"], "session_user")

    def test_analyze_user_directive_replays_active_context_for_do_that_as(self) -> None:
        set_active_context(
            "test-source",
            "user-session-2",
            route_target="audiobook",
            dispatch_hook="audiobook.execute",
            action="play",
            anchor_strength="strong",
            context_text="resume my audiobook and set a sleep timer for 15 minutes",
        )

        directive = analyze_user_directive(
            "do that as casey",
            source="test-source",
            session_id="user-session-2",
        )

        self.assertEqual(directive.directive_type, "execute_as")
        self.assertEqual(directive.requested_user_name, "casey")
        self.assertEqual(directive.rewritten_text, "resume my audiobook and set a sleep timer for 15 minutes")

    def test_resolve_user_name_matches_display_name(self) -> None:
        household = neutral_household_runtime_settings()
        self.assertEqual(
            resolve_user_name("Resident One", household_settings=household),
            "resident_one",
        )


if __name__ == "__main__":
    unittest.main()
