from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.room_context.classifier import classify_room_sensitive_home_command
from oracle_app.room_context.home_routing import apply_room_context_to_home_text
from oracle_app.room_context.resolver import resolve_room_context
from oracle_app.room_context.vocabulary import canonical_pending_room_reply_name
from oracle_app.routing_helpers import canonicalize_home_command, detect_implied_home_command


class RoomContextTests(unittest.TestCase):
    @staticmethod
    def _canonical_household() -> unittest.mock.MagicMock:
        household = unittest.mock.MagicMock()
        household.rooms = {
            "living_room": SimpleNamespace(
                id="living_room",
                enabled=True,
                display_name="Living Room",
                aliases=("lounge",),
            )
        }
        household.resolve_room_id.side_effect = lambda value: {
            "living room": "living_room",
            "living_room": "living_room",
            "lounge": "living_room",
        }.get(str(value or "").strip().lower())
        household.room.side_effect = lambda value: household.rooms.get(str(value or "").strip())
        return household

    def test_classifier_marks_generic_lights_as_room_sensitive(self) -> None:
        self.assertEqual(classify_room_sensitive_home_command("turn on the lights"), "lights_room")

    def test_classifier_skips_explicit_entity_command(self) -> None:
        self.assertIsNone(classify_room_sensitive_home_command("turn on the office lamp"))

    def test_canonical_resolver_uses_associations_without_legacy_sources_or_vocabulary(self) -> None:
        household = unittest.mock.MagicMock()
        household.resolve_room_id.side_effect = lambda value: {
            "office": "office",
        }.get(str(value or "").strip().lower())
        household.configured_associated_room_id.return_value = "living_room"

        with (
            patch("oracle_app.room_context.resolver.get_source_entry") as legacy_sources,
            patch("oracle_app.room_context.resolver.canonical_room_name") as legacy_rooms,
            patch("oracle_app.room_context.resolver.extract_room_phrase") as legacy_vocabulary,
        ):
            ordinary = resolve_room_context(
                "turn on the lights",
                source="living_room_voice",
                active_room_ref="office",
                household_settings=household,
            )
            local = resolve_room_context(
                "turn on the lights here",
                source="living_room_voice",
                active_room_ref="office",
                household_settings=household,
            )

        self.assertEqual(ordinary.resolved_room, "office")
        self.assertEqual(ordinary.resolution_source, "session_room")
        self.assertEqual(local.resolved_room, "living_room")
        self.assertEqual(local.resolution_source, "deictic_source_association")
        legacy_sources.assert_not_called()
        legacy_rooms.assert_not_called()
        legacy_vocabulary.assert_not_called()

    def test_canonical_explicit_room_uses_household_terms_without_legacy_cache(self) -> None:
        household = unittest.mock.MagicMock()
        household.resolve_room_id.side_effect = lambda value: {
            "office": "office",
        }.get(str(value or "").strip().lower())

        with patch("oracle_app.room_context.resolver.extract_room_phrase") as legacy_vocabulary:
            result = resolve_room_context(
                "turn on the office lights",
                source="browser-ui",
                household_settings=household,
            )

        self.assertEqual(result.resolved_room, "office")
        self.assertEqual(result.resolution_source, "explicit_room")
        legacy_vocabulary.assert_not_called()

    def test_canonical_home_normalization_uses_household_room_aliases(self) -> None:
        household = self._canonical_household()

        with patch(
            "oracle_app.routing_helpers.load_home_assistant_cache",
            return_value={
                "rooms": [
                    {
                        "spoken_name": "wrong room",
                        "aliases": ["lounge"],
                    }
                ],
                "entities": [
                    {
                        "friendly_name": "Wrong Lamp",
                        "aliases": ["lounge"],
                        "domain": "light",
                    }
                ],
            },
        ):
            normalized = canonicalize_home_command(
                "turn on the lounge lights",
                household_settings=household,
            )
            implied = detect_implied_home_command(
                "make the lounge brighter",
                household_settings=household,
            )

        self.assertEqual(normalized, "turn on the living room lights")
        self.assertIsNotNone(implied)
        self.assertIn("living room", implied[0])

    def test_canonical_pending_room_reply_uses_household_vocabulary(self) -> None:
        household = self._canonical_household()

        with (
            patch("oracle_app.room_context.vocabulary.load_home_assistant_cache") as legacy_cache,
            patch("oracle_app.room_context.vocabulary.get_source_registry") as legacy_sources,
        ):
            resolved = canonical_pending_room_reply_name(
                "in the lounge please",
                household,
            )

        self.assertEqual(resolved, "living room")
        legacy_cache.assert_not_called()
        legacy_sources.assert_not_called()

    @patch("oracle_app.room_context.home_routing.get_active_context", return_value=None)
    @patch("oracle_app.room_context.home_routing.state.load_pending_home_request", return_value=None)
    def test_canonical_associated_room_keeps_id_in_context_and_display_name_in_command(
        self,
        _mock_pending,
        _mock_context,
    ) -> None:
        household = self._canonical_household()
        household.configured_associated_room_id.return_value = "living_room"

        with patch(
            "oracle_app.routing_helpers.load_home_assistant_cache",
            return_value={"rooms": [], "entities": []},
        ):
            resolved_text, context = apply_room_context_to_home_text(
                "turn on the lights here",
                source="living_room_voice",
                session_id="canonical-room-command",
                household_settings=household,
            )

        self.assertEqual(resolved_text, "turn on the living room lights")
        self.assertEqual(context["resolved_room"], "living_room")

    def test_canonical_resolver_uses_source_association_after_session_fallback(self) -> None:
        household = unittest.mock.MagicMock()
        household.resolve_room_id.return_value = None
        household.configured_associated_room_id.return_value = "living_room"

        result = resolve_room_context(
            "turn on the lights",
            source="living_room_voice",
            household_settings=household,
        )

        self.assertEqual(result.resolved_room, "living_room")
        self.assertEqual(result.resolution_source, "source_association_fallback")

    @patch(
        "oracle_app.room_context.resolver.canonical_room_name",
        side_effect=lambda text: {"kitchen": "kitchen", "office": "office"}.get(str(text or "").strip().lower()),
    )
    @patch(
        "oracle_app.room_context.resolver.get_source_entry",
        return_value={"source_type": "satellite", "fixed": True, "default_room": "kitchen"},
    )
    def test_resolver_uses_source_default_when_room_is_required(self, _mock_source, _mock_room_name) -> None:
        result = resolve_room_context("turn on the lights", source="kitchen-satellite")

        self.assertEqual(result.resolved_room, "kitchen")
        self.assertEqual(result.resolution_source, "source_default")
        self.assertTrue(result.room_required)
        self.assertFalse(result.needs_clarification)

    @patch(
        "oracle_app.room_context.resolver.canonical_room_name",
        side_effect=lambda text: {"kitchen": "kitchen", "office": "office"}.get(str(text or "").strip().lower()),
    )
    @patch(
        "oracle_app.room_context.resolver.get_source_entry",
        return_value={"source_type": "satellite", "fixed": True, "default_room": "kitchen"},
    )
    def test_resolver_prefers_session_room_before_source_default(self, _mock_source, _mock_room_name) -> None:
        result = resolve_room_context("turn on the lights", source="kitchen-satellite", active_room_ref="office")

        self.assertEqual(result.resolved_room, "office")
        self.assertEqual(result.resolution_source, "session_room")

    @patch(
        "oracle_app.room_context.resolver.canonical_room_name",
        side_effect=lambda text: {"kitchen": "kitchen", "office": "office"}.get(str(text or "").strip().lower()),
    )
    @patch(
        "oracle_app.room_context.resolver.get_source_entry",
        return_value={"source_type": "satellite", "fixed": True, "default_room": "kitchen"},
    )
    def test_resolver_keeps_explicit_room_over_source_default(self, _mock_source, _mock_room_name) -> None:
        result = resolve_room_context("turn on the office lights", source="kitchen-satellite")

        self.assertEqual(result.resolved_room, "office")
        self.assertEqual(result.resolution_source, "explicit_room")

    @patch("oracle_app.room_context.resolver.get_source_entry", return_value={"source_type": "mobile", "fixed": False})
    @patch("oracle_app.room_context.resolver.canonical_room_name", return_value=None)
    def test_resolver_marks_deictic_room_without_fixed_source_for_clarification(self, _mock_room_name, _mock_source) -> None:
        result = resolve_room_context("turn on the lights here", source="mobile-client")

        self.assertIsNone(result.resolved_room)
        self.assertEqual(result.resolution_source, "unresolved")
        self.assertTrue(result.room_required)
        self.assertTrue(result.needs_clarification)


if __name__ == "__main__":
    unittest.main()
