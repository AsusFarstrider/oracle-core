from __future__ import annotations

import json
import unittest
from pathlib import Path


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "utterance_ledger.json"
MATRIX_PATH = Path(__file__).resolve().parent / "fixtures" / "utterance_capability_matrix.json"

ALLOWED_CATEGORIES = {
    "route_positive",
    "route_miss",
    "parser_miss",
    "clarification_policy_miss",
    "cross_domain_rescue_miss",
    "fallback_policy_miss",
    "hard_not_found",
}

ALLOWED_TARGETS = {
    "music",
    "audiobook",
    "home_assistant",
    "fallback_router",
    "calendar",
    "facts",
    "network",
    "news",
    "system",
    "weather",
}

ALLOWED_OWNERS = {
    "alerts",
    "audiobook",
    "calculation",
    "calendar",
    "facts",
    "fallback",
    "home_automation",
    "music",
    "network",
    "news",
    "system",
    "weather",
}

ALLOWED_BEHAVIORS = {
    "route_only",
    "deterministic_clarify",
    "clarification_narrow",
    "cross_domain_rescue",
    "fallback_router_last_resort",
    "hard_not_found",
}

ALLOWED_SETUPS = {
    "pending_audiobook_two_candidates",
    "pending_music_two_candidates",
    "strong_home_context_guest_room_off",
    "strong_home_context_generic",
    "strong_music_context_no_live_media",
    "strong_audiobook_context_no_live_media",
    "weak_facts_context",
}

ALLOWED_RISKS = {"high", "medium", "low"}
ALLOWED_ORIGINS = {"synthetic", "operator_observed"}
ALLOWED_EXECUTION_LEVELS = {"route_executed", "handler_executed"}
ALLOWED_DISPOSITIONS = {"supported"}


class UtteranceLedgerSchemaTests(unittest.TestCase):
    def test_fixture_entries_follow_agreed_shape(self) -> None:
        entries = json.loads(FIXTURE_PATH.read_text())

        self.assertIsInstance(entries, list)
        self.assertGreaterEqual(len(entries), 12)

        seen_ids: set[str] = set()
        seen_categories: set[str] = set()
        seen_behaviors: set[str] = set()

        for entry in entries:
            self.assertIsInstance(entry, dict)

            entry_id = entry.get("id")
            category = entry.get("category")
            utterance = entry.get("utterance")
            expected_target = entry.get("expected_target")
            expected_behavior = entry.get("expected_behavior")
            setup = entry.get("setup")
            risk = entry.get("risk")
            origin = entry.get("origin")
            owner = entry.get("owner")
            execution_level = entry.get("execution_level")
            capability_disposition = entry.get("capability_disposition")

            self.assertIsInstance(entry_id, str)
            self.assertTrue(entry_id)
            self.assertNotIn(entry_id, seen_ids)
            seen_ids.add(entry_id)

            self.assertIn(category, ALLOWED_CATEGORIES)
            seen_categories.add(category)

            self.assertIsInstance(utterance, str)
            self.assertTrue(utterance.strip())

            self.assertIn(expected_target, ALLOWED_TARGETS)
            self.assertIn(expected_behavior, ALLOWED_BEHAVIORS)
            seen_behaviors.add(expected_behavior)
            if setup is not None:
                self.assertIn(setup, ALLOWED_SETUPS)
            self.assertIn(risk, ALLOWED_RISKS)
            self.assertIn(origin, ALLOWED_ORIGINS)
            self.assertIn(owner, ALLOWED_OWNERS)
            self.assertIn(execution_level, ALLOWED_EXECUTION_LEVELS)
            self.assertIn(capability_disposition, ALLOWED_DISPOSITIONS)
            if "expected_action" in entry:
                self.assertIsInstance(entry["expected_action"], str)
                self.assertTrue(entry["expected_action"])

        self.assertTrue(
            {
                "route_miss",
                "parser_miss",
                "clarification_policy_miss",
                "cross_domain_rescue_miss",
                "fallback_policy_miss",
                "hard_not_found",
            }.issubset(seen_categories)
        )
        self.assertTrue(
            {
                "route_only",
                "deterministic_clarify",
                "clarification_narrow",
                "cross_domain_rescue",
                "fallback_router_last_resort",
                "hard_not_found",
            }.issubset(seen_behaviors)
        )
        self.assertIn("operator_observed", {entry.get("origin") for entry in entries})

    def test_migrated_media_cases_retain_their_characterization_strength(self) -> None:
        entries = json.loads(FIXTURE_PATH.read_text())

        def classify_surface(entry: dict) -> set[str]:
            surfaces: set[str] = set()
            target = entry.get("expected_target")
            category = entry.get("category")
            setup = entry.get("setup")

            if target in {"music", "audiobook", "home_assistant"}:
                surfaces.add(str(target))
            if target == "fallback_router" or category == "fallback_policy_miss":
                surfaces.add("fallback router")
            if category == "cross_domain_rescue_miss":
                surfaces.add("cross-domain media rescue")
            if setup is not None or category == "clarification_policy_miss":
                surfaces.add("pending/session follow-up behavior")
            return surfaces

        required_surfaces = {
            "music",
            "audiobook",
            "home_assistant",
            "fallback router",
            "cross-domain media rescue",
            "pending/session follow-up behavior",
        }

        meaningful_surface_coverage = {surface: False for surface in required_surfaces}
        operator_observed_required = {
            "music",
            "audiobook",
            "home_assistant",
            "cross-domain media rescue",
        }
        operator_observed_coverage = {surface: False for surface in operator_observed_required}

        for entry in entries:
            surfaces = classify_surface(entry)
            risk = entry.get("risk")
            origin = entry.get("origin")
            execution_level = entry.get("execution_level")
            category = entry.get("category")

            for surface in surfaces:
                if risk in {"high", "medium"} and surface in meaningful_surface_coverage:
                    meaningful_surface_coverage[surface] = True
                if origin == "operator_observed" and surface in operator_observed_coverage:
                    operator_observed_coverage[surface] = True

            if risk == "high":
                self.assertIn(execution_level, {"route_executed", "handler_executed"})
                if category in {"cross_domain_rescue_miss", "clarification_policy_miss", "hard_not_found"}:
                    self.assertEqual(execution_level, "handler_executed")

        self.assertEqual(
            {surface for surface, present in meaningful_surface_coverage.items() if not present},
            set(),
        )
        self.assertEqual(
            {surface for surface, present in operator_observed_coverage.items() if not present},
            set(),
        )

    def test_all_legacy_facts_cases_are_owned_by_the_ledger(self) -> None:
        entries = json.loads(FIXTURE_PATH.read_text())
        facts = [entry for entry in entries if entry["owner"] == "facts"]

        self.assertEqual(len(facts), 6)
        self.assertEqual(
            {entry["facts_expectations"]["case_id"] for entry in facts},
            {
                "lifespan_sloth",
                "author_frankenstein",
                "location_machu_picchu",
                "date_eiffel_tower",
                "definition_photosynthesis",
                "superlative_largest_animal",
            },
        )

    def test_capability_matrix_covers_every_supported_ledger_owner(self) -> None:
        entries = json.loads(FIXTURE_PATH.read_text())
        matrix = json.loads(MATRIX_PATH.read_text())
        entries_by_id = {entry["id"]: entry for entry in entries}
        supported = matrix["supported"]

        self.assertEqual(matrix["format_version"], 1)
        self.assertEqual(
            {row["owner"] for row in supported},
            {entry["owner"] for entry in entries},
        )
        for row in supported:
            with self.subTest(owner=row["owner"]):
                self.assertTrue(row["case_ids"])
                for case_id in row["case_ids"]:
                    self.assertIn(case_id, entries_by_id)
                    self.assertEqual(entries_by_id[case_id]["owner"], row["owner"])
                    self.assertEqual(entries_by_id[case_id]["expected_target"], row["target"])

    def test_deferred_capabilities_have_durable_roadmap_dispositions_not_fake_cases(self) -> None:
        matrix = json.loads(MATRIX_PATH.read_text())
        for row in matrix["deferred"]:
            with self.subTest(owner=row["owner"]):
                self.assertTrue(row["capabilities"])
                self.assertRegex(row["roadmap_destination"], r"^V2 Stage [6-9]$")
                self.assertTrue(row["reason"])
                self.assertNotIn("case_ids", row)
