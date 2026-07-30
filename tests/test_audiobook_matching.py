from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.audiobook_runtime.matching import build_search_queries, choose_audiobook_match, score_audiobook_candidates
from oracle_app.audiobook_runtime.matching import find_audiobook_series_entry
from oracle_app.audiobook_runtime.parsing import parse_audiobook_intent
from oracle_app.audiobook_runtime.pending import (
    analyze_negative_candidate_elimination,
    looks_like_pending_audiobook_clarification,
    resolve_safe_pronoun_candidate,
)


class AudiobookMatchingTests(unittest.TestCase):
    def test_parse_audiobook_intent_handles_read_my_book(self) -> None:
        intent = parse_audiobook_intent("read my book")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "resume_current")

    def test_parse_audiobook_intent_handles_read_specific_title(self) -> None:
        intent = parse_audiobook_intent("read dune")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "dune")

    def test_parse_audiobook_intent_handles_pick_up_specific_title(self) -> None:
        intent = parse_audiobook_intent("pick up dune where i left off")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "dune")

    def test_parse_audiobook_intent_extracts_narrator_preference(self) -> None:
        intent = parse_audiobook_intent("play audiobook the jim dale version of prisoner of azkaban")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "prisoner of azkaban")
        self.assertEqual(intent.narrator_preference, "jim dale")

    def test_parse_audiobook_intent_extracts_narrator_from_narrated_by_phrase(self) -> None:
        intent = parse_audiobook_intent("play audiobook prisoner of azkaban narrated by stephen fry")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "prisoner of azkaban")
        self.assertEqual(intent.narrator_preference, "stephen fry")

    def test_parse_audiobook_intent_handles_series_play_phrase(self) -> None:
        intent = parse_audiobook_intent("play the third harry potter book")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.series, "harry potter")
        self.assertEqual(intent.ordinal, 3)
        self.assertEqual(intent.title, "harry potter")

    def test_parse_audiobook_intent_handles_book_number_of_series_phrase(self) -> None:
        intent = parse_audiobook_intent("start book two of dune")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.series, "dune")
        self.assertEqual(intent.ordinal, 2)
        self.assertEqual(intent.title, "dune")

    def test_parse_audiobook_intent_handles_series_book_number_phrase(self) -> None:
        intent = parse_audiobook_intent("start dune book two")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.series, "dune")
        self.assertEqual(intent.ordinal, 2)
        self.assertEqual(intent.title, "dune")

    def test_parse_audiobook_intent_handles_ordinal_book_in_series_phrase(self) -> None:
        intent = parse_audiobook_intent("play the second book in dune")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.series, "dune")
        self.assertEqual(intent.ordinal, 2)
        self.assertEqual(intent.title, "dune")

    def test_parse_audiobook_intent_handles_what_book_am_i_on(self) -> None:
        intent = parse_audiobook_intent("what book am i on")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "what_is_playing")

    def test_parse_audiobook_intent_handles_resume_current_with_sleep_timer(self) -> None:
        intent = parse_audiobook_intent("resume my audiobook and set a sleep timer for 15 minutes")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "resume_current")
        self.assertEqual(intent.sleep_timer_seconds, 900)

    def test_build_search_queries_include_strongest_meaningful_token(self) -> None:
        queries = build_search_queries("prisonor of azkaban")

        self.assertIn("azkaban", queries)

    def test_build_search_queries_include_narrator_aware_variants(self) -> None:
        queries = build_search_queries("prisoner of azkaban", "stephen fry")

        self.assertIn("prisoner of azkaban stephen fry", [item.lower() for item in queries])
        self.assertIn("stephen fry", [item.lower() for item in queries])

    def test_score_audiobook_candidates_demotes_series_neighbors_missing_distinctive_title_words(self) -> None:
        scored = score_audiobook_candidates(
            "harry potter and the prisoner of azkaban",
            [
                {
                    "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                    "author": "J.K. Rowling",
                    "subtitle": "",
                },
                {
                    "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                    "author": "J.K. Rowling",
                    "subtitle": "",
                },
                {
                    "title": "Harry Potter and the Order of the Phoenix, Book 5",
                    "author": "J.K. Rowling",
                    "subtitle": "",
                },
            ],
        )

        self.assertEqual(scored[0]["title"], "Harry Potter and the Prisoner of Azkaban, Book 3")
        self.assertEqual(scored[1]["title"], "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)")
        self.assertEqual(scored[2]["title"], "Harry Potter and the Order of the Phoenix, Book 5")
        self.assertGreaterEqual(int(scored[1]["score"]), 200)
        self.assertLessEqual(int(scored[2]["score"]), 45)

    def test_score_audiobook_candidates_prefers_matching_narrator(self) -> None:
        scored = score_audiobook_candidates(
            "prisoner of azkaban",
            [
                {
                    "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                    "author": "J.K. Rowling",
                    "subtitle": "",
                    "narrator": "Jim Dale",
                },
                {
                    "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                    "author": "J.K. Rowling",
                    "subtitle": "",
                    "narrator": "Full Cast",
                },
            ],
            narrator_preference="jim dale",
        )

        self.assertEqual(scored[0]["narrator"], "Jim Dale")

    def test_choose_audiobook_match_focuses_clarification_on_strong_candidates(self) -> None:
        scored = score_audiobook_candidates(
            "harry potter and the prisoner of azkaban",
            [
                {
                    "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                    "author": "J.K. Rowling",
                    "subtitle": "",
                },
                {
                    "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                    "author": "J.K. Rowling",
                    "subtitle": "",
                },
                {
                    "title": "Harry Potter and the Order of the Phoenix, Book 5",
                    "author": "J.K. Rowling",
                    "subtitle": "",
                },
            ],
        )

        decision, selected = choose_audiobook_match(scored)

        self.assertEqual(decision, "clarify")
        self.assertEqual(len(selected), 2)
        self.assertEqual(
            [item["title"] for item in selected],
            [
                "Harry Potter and the Prisoner of Azkaban, Book 3",
                "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
            ],
        )

    def test_find_audiobook_series_entry_prefers_series_metadata_sequence(self) -> None:
        match = find_audiobook_series_entry(
            "dune",
            2,
            candidates=[
                {
                    "library_item_id": "dune-1",
                    "title": "Dune",
                    "subtitle": "",
                    "series": [{"name": "Dune", "sequence": "1"}],
                },
                {
                    "library_item_id": "dune-2",
                    "title": "Dune Messiah",
                    "subtitle": "Dune Chronicles, Book 2",
                    "series": [{"name": "Dune", "sequence": "2"}],
                },
            ],
        )

        self.assertIsNotNone(match)
        self.assertEqual(match["library_item_id"], "dune-2")

    def test_looks_like_pending_audiobook_clarification_handles_narrator_reply(self) -> None:
        pending = {
            "candidates": [
                {
                    "library_item_id": "book-regular",
                    "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                    "author": "J. K. Rowling",
                    "subtitle": "",
                    "narrator": "Jim Dale",
                },
                {
                    "library_item_id": "book-cast",
                    "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                    "author": "J. K. Rowling",
                    "subtitle": "",
                    "narrator": "Full Cast",
                },
            ]
        }

        self.assertTrue(looks_like_pending_audiobook_clarification("the jim dale edition", pending))

    def test_looks_like_pending_audiobook_clarification_rejects_vague_yes_without_single_match(self) -> None:
        pending = {
            "candidates": [
                {
                    "library_item_id": "book-regular",
                    "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                    "author": "J. K. Rowling",
                    "subtitle": "",
                    "narrator": "Jim Dale",
                },
                {
                    "library_item_id": "book-cast",
                    "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                    "author": "J. K. Rowling",
                    "subtitle": "",
                    "narrator": "Full Cast",
                },
            ]
        }

        self.assertFalse(looks_like_pending_audiobook_clarification("yes", pending))

    def test_safe_pronoun_requires_single_candidate_for_that_one(self) -> None:
        candidates = [
            {"library_item_id": "book-a", "title": "Dune"},
            {"library_item_id": "book-b", "title": "Dune Messiah"},
        ]

        self.assertIsNone(resolve_safe_pronoun_candidate("that one", candidates))

    def test_safe_pronoun_allows_the_other_one_only_for_two_candidates(self) -> None:
        candidates = [
            {"library_item_id": "book-a", "title": "Dune"},
            {"library_item_id": "book-b", "title": "Dune Messiah"},
        ]

        resolved = resolve_safe_pronoun_candidate("the other one", candidates)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["library_item_id"], "book-b")

    def test_negative_elimination_resolves_other_candidate_for_not_that_one(self) -> None:
        candidates = [
            {"library_item_id": "book-a", "title": "Dune"},
            {"library_item_id": "book-b", "title": "Dune Messiah"},
        ]

        outcome = analyze_negative_candidate_elimination("not that one", candidates)

        self.assertEqual(outcome["action"], "resolve")
        self.assertEqual(outcome["candidate"]["library_item_id"], "book-b")

    def test_negative_elimination_marks_narrow_for_not_the_first_one_when_multiple_remain(self) -> None:
        candidates = [
            {"library_item_id": "book-a", "title": "Dune"},
            {"library_item_id": "book-b", "title": "Dune Messiah"},
            {"library_item_id": "book-c", "title": "Children of Dune"},
        ]

        outcome = analyze_negative_candidate_elimination("not the first one", candidates)

        self.assertEqual(outcome["action"], "narrow")
        self.assertEqual(
            [item["library_item_id"] for item in outcome["remaining"]],
            ["book-b", "book-c"],
        )

    def test_negative_elimination_rejects_ambiguous_bare_negative(self) -> None:
        candidates = [
            {"library_item_id": "book-a", "title": "Dune", "narrator": "Jim Dale"},
            {"library_item_id": "book-b", "title": "Dune Messiah", "narrator": "Jim Dale"},
        ]

        outcome = analyze_negative_candidate_elimination("not jim dale", candidates)

        self.assertEqual(outcome["action"], "none")


if __name__ == "__main__":
    unittest.main()
