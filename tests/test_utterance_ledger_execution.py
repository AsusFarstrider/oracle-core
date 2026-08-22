from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import sys
from types import MappingProxyType
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app import audiobook_state, state
from oracle_app.audiobook import build_longform_payload as _build_longform_payload
from oracle_app.handlers.audiobook import execute_audiobook as _execute_audiobook
from oracle_app.handlers.music import execute_music as _execute_music
from oracle_app.routing import build_route_capability_registry, choose_route as _BASE_CHOOSE_ROUTE
from oracle_app.schemas import DispatchPlan
from oracle_app.session_state import clear_all_sessions, set_active_context
from oracle_app.user_context import resolve_effective_user as _resolve_effective_user
from canonical_test_support import neutral_brain_runtime_settings


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "utterance_ledger.json"
_NEUTRAL_RUNTIME = neutral_brain_runtime_settings()
_NEUTRAL_HOUSEHOLD = _NEUTRAL_RUNTIME.household
_TEST_NEWS_SETTINGS = replace(
    _NEUTRAL_RUNTIME.information.news,
    enabled=True,
    resolution_terms=MappingProxyType({"npr": "example_news"}),
)
_TEST_CALENDAR_SETTINGS = replace(_NEUTRAL_RUNTIME.calendar, enabled=True)
_CANONICAL_ROUTE_ARGUMENTS = {
    "facts_enabled": False,
    "news_settings": _TEST_NEWS_SETTINGS,
    "canonical_information": True,
    "calendar_settings": _TEST_CALENDAR_SETTINGS,
    "canonical_calendar": True,
}
_NEUTRAL_ROUTE_REGISTRY = build_route_capability_registry(
    _NEUTRAL_HOUSEHOLD,
    **_CANONICAL_ROUTE_ARGUMENTS,
)
_BASELINE_ROUTE_REGISTRY = build_route_capability_registry(
    **_CANONICAL_ROUTE_ARGUMENTS,
)
_FACTS_ROUTE_REGISTRY = build_route_capability_registry(
    **(_CANONICAL_ROUTE_ARGUMENTS | {"facts_enabled": True}),
)
_CANONICAL_HOME_ROUTE_IDS = {
    "home-room-first-color",
    "home-room-first-cool-off",
}


def _choose_canonical_home_route(text: str, **kwargs):
    """Evaluate fixture routing with explicit neutral canonical authority."""

    kwargs.setdefault("registry", _NEUTRAL_ROUTE_REGISTRY)
    kwargs.setdefault("household_settings", _NEUTRAL_HOUSEHOLD)
    return _BASE_CHOOSE_ROUTE(text, **kwargs)


def choose_route(text: str, **kwargs):
    """Evaluate general fixtures with explicit canonical non-household dependencies."""

    kwargs.setdefault("registry", _BASELINE_ROUTE_REGISTRY)
    kwargs.setdefault("household_settings", _NEUTRAL_HOUSEHOLD)
    return _BASE_CHOOSE_ROUTE(text, **kwargs)


def _choose_canonical_facts_route(text: str, **kwargs):
    kwargs.setdefault("registry", _FACTS_ROUTE_REGISTRY)
    kwargs.setdefault("household_settings", _NEUTRAL_HOUSEHOLD)
    return _BASE_CHOOSE_ROUTE(text, **kwargs)


def execute_audiobook(dispatch: DispatchPlan) -> DispatchPlan:
    """Execute fixture behavior with explicit canonical household authority."""

    _add_canonical_playback_target(dispatch)
    with patch(
        "oracle_app.handlers.audiobook.build_longform_payload",
        side_effect=_build_neutral_longform_payload,
    ):
        return _execute_audiobook(
            dispatch,
            household_settings=_NEUTRAL_HOUSEHOLD,
            canonical_playback_target=True,
        )


def _build_neutral_longform_payload(session, **kwargs):
    """Build fixture playback payloads without consulting private Brain endpoint authority."""

    kwargs.setdefault("oracle_base_url", "http://brain.example.test")
    return _build_longform_payload(session, **kwargs)


def _resolve_neutral_effective_user(*args, **kwargs):
    """Resolve nested media fallbacks against explicit neutral household authority."""

    if kwargs.get("household_settings") is None:
        kwargs["household_settings"] = _NEUTRAL_HOUSEHOLD
    return _resolve_effective_user(*args, **kwargs)


def execute_music(dispatch: DispatchPlan) -> DispatchPlan:
    """Execute fixture behavior with neutral authority across audiobook fallbacks."""

    _add_canonical_playback_target(dispatch)
    with patch(
        "oracle_app.handlers.audiobook.resolve_effective_user",
        side_effect=_resolve_neutral_effective_user,
    ), patch(
        "oracle_app.handlers.audiobook.build_longform_payload",
        side_effect=_build_neutral_longform_payload,
    ), patch(
        "oracle_app.handlers.music.resolve_with_ollama",
        return_value=None,
    ):
        return _execute_music(dispatch, canonical_playback_target=True)


def _add_canonical_playback_target(dispatch: DispatchPlan) -> None:
    source = str(dispatch.payload.get("source") or "").strip()
    if source and not dispatch.payload.get("playback_target_source_id"):
        dispatch.payload["playback_target_source_id"] = source
        dispatch.payload["playback_target_resolution"] = "authenticated_request_source"


class UtteranceLedgerExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = json.loads(FIXTURE_PATH.read_text())
        cls.entries_by_id = {entry["id"]: entry for entry in cls.entries}

    def tearDown(self) -> None:
        clear_all_sessions()
        audiobook_state.clear_all_active_audiobook_playbacks()

    def _entries_with_behavior(self, behavior: str) -> list[dict[str, str]]:
        return [entry for entry in self.entries if entry["expected_behavior"] == behavior]

    def _entries_with_behavior_and_setup(self, behavior: str) -> list[dict[str, str]]:
        return [entry for entry in self._entries_with_behavior(behavior) if entry.get("setup")]

    def _store_two_candidate_audiobook_pending(self, session_id: str, *, source: str = "satellite-beta") -> None:
        state.store_pending_audiobook_request(
            source,
            session_id,
            {
                "intent": {"intent": "play", "title": "prisoner of azkaban"},
                "candidates": [
                    {
                        "library_item_id": "book-jim",
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
                ],
            },
        )

    def _store_two_candidate_music_pending(self, session_id: str) -> None:
        state.store_pending_music_request(
            "satellite-alpha",
            session_id,
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [
                    {"title": "Heroes", "artist": "David Bowie", "album": "Heroes"},
                    {"title": "Heroes", "artist": "Peter Gabriel", "album": "Scratch My Back"},
                ],
            },
        )

    def _route_with_fixture_setup(self, entry: dict[str, str]):
        setup = entry.get("setup")
        utterance = entry["utterance"]
        if entry["owner"] == "facts":
            return _choose_canonical_facts_route(utterance)
        if entry["id"] in _CANONICAL_HOME_ROUTE_IDS:
            return _choose_canonical_home_route(utterance)
        if not setup:
            return choose_route(utterance)

        with ExitStack() as stack:
            if setup == "pending_audiobook_two_candidates":
                source = "satellite-alpha"
                session_id = f"utterance-ledger-{entry['id']}"
                self._store_two_candidate_audiobook_pending(session_id, source=source)
                return choose_route(utterance, source=source, session_id=session_id)
            if setup == "pending_music_two_candidates":
                source = "satellite-alpha"
                session_id = f"utterance-ledger-{entry['id']}"
                self._store_two_candidate_music_pending(session_id)
                return choose_route(utterance, source=source, session_id=session_id)
            if setup == "strong_home_context_guest_room_off":
                set_active_context(
                    "satellite-alpha",
                    f"utterance-ledger-{entry['id']}",
                    route_target="home_assistant",
                    dispatch_hook="home_assistant.execute",
                    action="execute",
                    anchor_strength="strong",
                    context_text="turn off the guest room lights",
                )
                return choose_route(utterance, source="satellite-alpha", session_id=f"utterance-ledger-{entry['id']}")
            if setup == "strong_home_context_generic":
                set_active_context(
                    "satellite-alpha",
                    f"utterance-ledger-{entry['id']}",
                    route_target="home_assistant",
                    dispatch_hook="home_assistant.execute",
                    action="execute",
                    anchor_strength="strong",
                    context_text="turn off the kitchen lights",
                )
                return choose_route(utterance, source="satellite-alpha", session_id=f"utterance-ledger-{entry['id']}")
            if setup == "strong_music_context_no_live_media":
                stack.enter_context(patch("oracle_app.route_refinement.fetch_satellite_music_session", return_value=None))
                stack.enter_context(patch("oracle_app.route_refinement.fetch_satellite_audiobook_session", return_value=None))
                stack.enter_context(patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session", return_value=None))
                set_active_context(
                    "satellite-alpha",
                    f"utterance-ledger-{entry['id']}",
                    route_target="music",
                    dispatch_hook="music.execute",
                    action="play",
                    anchor_strength="strong",
                )
                return choose_route(utterance, source="satellite-alpha", session_id=f"utterance-ledger-{entry['id']}")
            if setup == "strong_audiobook_context_no_live_media":
                stack.enter_context(patch("oracle_app.route_refinement.fetch_satellite_music_session", return_value=None))
                stack.enter_context(patch("oracle_app.route_refinement.fetch_satellite_audiobook_session", return_value=None))
                stack.enter_context(patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session", return_value=None))
                set_active_context(
                    "satellite-alpha",
                    f"utterance-ledger-{entry['id']}",
                    route_target="audiobook",
                    dispatch_hook="audiobook.execute",
                    action="play",
                    anchor_strength="strong",
                )
                return choose_route(utterance, source="satellite-alpha", session_id=f"utterance-ledger-{entry['id']}")
            if setup == "weak_facts_context":
                set_active_context(
                    "satellite-alpha",
                    f"utterance-ledger-{entry['id']}",
                    route_target="facts",
                    dispatch_hook="facts.lookup",
                    action="facts_lookup",
                    anchor_strength="weak",
                )
                return choose_route(utterance, source="satellite-alpha", session_id=f"utterance-ledger-{entry['id']}")

        raise AssertionError(f"Unknown fixture setup: {setup}")

    def test_route_only_fixture_entries_choose_expected_targets(self) -> None:
        for entry in self._entries_with_behavior("route_only"):
            with self.subTest(entry_id=entry["id"]):
                route = self._route_with_fixture_setup(entry)
                self.assertEqual(route.target, entry["expected_target"])

    def test_fixture_fallback_router_last_resort_entries_route_to_fallback_router(self) -> None:
        for entry in self._entries_with_behavior("fallback_router_last_resort"):
            with self.subTest(entry_id=entry["id"]):
                route = choose_route(entry["utterance"])
                self.assertEqual(route.target, entry["expected_target"])

    def test_high_risk_fixture_entries_have_execution_coverage(self) -> None:
        explicitly_executed_ids = {
            "media-generic-dune-cross-domain-rescue",
            "media-put-on-dune-cross-domain-rescue",
            "media-queue-up-dune-cross-domain-rescue",
            "media-cue-up-dune-cross-domain-rescue",
            "audiobook-edition-shorthand-clarify",
            "audiobook-jim-dale-shorthand-clarify",
            "audiobook-full-cast-shorthand-clarify",
            "audiobook-negative-narrow-reprompt",
            "audiobook-unsafe-vague-followup-rejected",
            "audiobook-unsafe-vague-pronoun-rejected",
            "audiobook-safe-other-pronoun",
            "weak-single-match-should-not-overcommit",
            "music-ultra-generic-single-word-one",
            "music-ultra-generic-single-word-hello",
            "music-ultra-generic-single-word-stay",
            "no-defensible-media-candidate-hard-not-found",
        }
        setup_backed_route_ids = {
            entry["id"]
            for entry in self._entries_with_behavior("route_only")
            if entry.get("setup")
        }
        route_only_ids = {
            entry["id"]
            for entry in self._entries_with_behavior("route_only")
            if not entry.get("setup")
        }
        fallback_ids = {entry["id"] for entry in self._entries_with_behavior("fallback_router_last_resort")}
        covered_ids = explicitly_executed_ids | setup_backed_route_ids | route_only_ids | fallback_ids

        missing = [
            entry["id"]
            for entry in self.entries
            if entry.get("risk") == "high"
            and entry.get("execution_level") not in {"route_executed", "handler_executed"}
        ]

        self.assertEqual(missing, [])

    def test_fixture_coverage_labels_match_execution_surface(self) -> None:
        handler_executed_ids = {
            "media-generic-dune-cross-domain-rescue",
            "media-put-on-dune-cross-domain-rescue",
            "media-queue-up-dune-cross-domain-rescue",
            "media-cue-up-dune-cross-domain-rescue",
            "audiobook-edition-shorthand-clarify",
            "audiobook-jim-dale-shorthand-clarify",
            "audiobook-full-cast-shorthand-clarify",
            "audiobook-regular-edition-shorthand-clarify",
            "audiobook-negative-narrow-reprompt",
            "audiobook-safe-other-pronoun",
            "audiobook-unsafe-vague-followup-rejected",
            "audiobook-unsafe-vague-pronoun-rejected",
            "weak-single-match-should-not-overcommit",
            "music-ultra-generic-single-word-one",
            "music-ultra-generic-single-word-hello",
            "music-ultra-generic-single-word-stay",
            "no-defensible-media-candidate-hard-not-found",
        }
        route_executed_ids = {entry["id"] for entry in self.entries} - handler_executed_ids

        for entry in self.entries:
            with self.subTest(entry_id=entry["id"]):
                if entry["id"] in handler_executed_ids:
                    self.assertEqual(entry.get("execution_level"), "handler_executed")
                elif entry["id"] in route_executed_ids:
                    self.assertEqual(entry.get("execution_level"), "route_executed")
                else:
                    self.fail(f"Entry {entry['id']} is not mapped to an execution coverage class")

    def test_high_risk_media_and_clarification_entries_are_handler_executed(self) -> None:
        for entry in self.entries:
            with self.subTest(entry_id=entry["id"]):
                if entry.get("risk") != "high":
                    continue
                if entry.get("category") not in {"cross_domain_rescue_miss", "clarification_policy_miss", "hard_not_found"}:
                    continue
                self.assertEqual(entry.get("execution_level"), "handler_executed")

    @patch("oracle_app.handlers.music.search_music_catalog")
    @patch("oracle_app.handlers.music.choose_best_guess_with_ollama")
    def test_fixture_weak_single_match_should_not_overcommit(self, mock_choose_best_guess_with_ollama, mock_search_plex) -> None:
        entry = self.entries_by_id["weak-single-match-should-not-overcommit"]
        mock_search_plex.return_value = []
        mock_choose_best_guess_with_ollama.return_value = None

        with patch("oracle_app.handlers.music._load_audiobook_guess_candidates") as mock_load_audiobook_guess_candidates:
            mock_load_audiobook_guess_candidates.return_value = [
                {
                    "route_target": "audiobook",
                    "media_type": "audiobook",
                    "library_item_id": "book-3",
                    "title": "Dune",
                    "author": "Frank Herbert",
                    "score": 26,
                }
            ]

            dispatch = DispatchPlan(
                target="music",
                hook="music.execute",
                payload={
                    "text": entry["utterance"],
                    "normalized_text": entry["utterance"],
                    "source": "satellite-alpha",
                    "session_id": "utterance-ledger-weak-single-match",
                },
                status="planned",
            )

            result = execute_music(dispatch)

        self.assertEqual(entry["expected_behavior"], "deterministic_clarify")
        self.assertEqual(result.status, "pending_clarification")
        self.assertIn("Did you mean the audiobook Dune by Frank Herbert?", result.result["prompt"])

    @patch("oracle_app.handlers.music.choose_music_match")
    @patch("oracle_app.handlers.music.score_music_candidates")
    @patch("oracle_app.handlers.music.search_music_catalog")
    def test_fixture_cross_domain_rescue_executes_audiobook_path(
        self,
        mock_search_plex,
        mock_score_music_candidates,
        mock_choose_music_match,
    ) -> None:
        mock_search_plex.return_value = [
            {
                "type": "track",
                "title": "Dune Buggy",
                "artist": "The Presidents of the United States of America",
                "album": "II",
                "score": 52,
                "plex_key": "/library/metadata/1",
                "rating_key": "1",
            }
        ]
        mock_score_music_candidates.return_value = [
            {
                "type": "track",
                "title": "Dune Buggy",
                "artist": "The Presidents of the United States of America",
                "album": "II",
                "score": 52,
                "plex_key": "/library/metadata/1",
                "rating_key": "1",
            }
        ]
        mock_choose_music_match.return_value = (
            "execute",
            [
                {
                    "type": "track",
                    "title": "Dune Buggy",
                    "artist": "The Presidents of the United States of America",
                    "album": "II",
                    "score": 52,
                    "plex_key": "/library/metadata/1",
                    "rating_key": "1",
                }
            ],
        )

        with patch("oracle_app.handlers.music._load_audiobook_guess_candidates") as mock_load_audiobook_guess_candidates, patch(
            "oracle_app.handlers.audiobook.execute_audiobook"
        ) as mock_execute_audiobook:
            mock_load_audiobook_guess_candidates.return_value = [
                {
                    "route_target": "audiobook",
                    "media_type": "audiobook",
                    "library_item_id": "book-dune",
                    "title": "Dune",
                    "author": "Frank Herbert",
                    "score": 70,
                }
            ]
            mock_execute_audiobook.return_value = DispatchPlan(
                target="audiobook",
                hook="audiobook.execute",
                payload={"text": "play audiobook dune"},
                status="executed",
                result={
                    "action": "play",
                    "selected": {
                        "library_item_id": "book-dune",
                        "title": "Dune",
                        "author": "Frank Herbert",
                    },
                },
            )

            for entry_id in (
                "media-generic-dune-cross-domain-rescue",
                "media-put-on-dune-cross-domain-rescue",
                "media-queue-up-dune-cross-domain-rescue",
                "media-cue-up-dune-cross-domain-rescue",
            ):
                with self.subTest(entry_id=entry_id):
                    entry = self.entries_by_id[entry_id]
                    dispatch = DispatchPlan(
                        target="music",
                        hook="music.execute",
                        payload={
                            "text": entry["utterance"],
                            "normalized_text": entry["utterance"],
                            "source": "satellite-alpha",
                            "session_id": f"utterance-ledger-{entry_id}",
                        },
                        status="planned",
                    )

                    result = execute_music(dispatch)

                    self.assertEqual(entry["expected_behavior"], "cross_domain_rescue")
                    self.assertEqual(result.status, "executed")
                    self.assertEqual(result.target, "audiobook")
                    self.assertEqual(result.result["selected"]["title"], "Dune")

    @patch("oracle_app.handlers.music.search_music_catalog")
    @patch("oracle_app.handlers.music.choose_best_guess_with_ollama")
    def test_fixture_no_defensible_media_candidate_hard_not_found(self, mock_choose_best_guess_with_ollama, mock_search_plex) -> None:
        entry = self.entries_by_id["no-defensible-media-candidate-hard-not-found"]
        mock_search_plex.return_value = []
        mock_choose_best_guess_with_ollama.return_value = None

        with patch("oracle_app.handlers.music._load_audiobook_guess_candidates") as mock_load_audiobook_guess_candidates:
            mock_load_audiobook_guess_candidates.return_value = []

            dispatch = DispatchPlan(
                target="music",
                hook="music.execute",
                payload={
                    "text": entry["utterance"],
                    "normalized_text": entry["utterance"],
                    "source": "satellite-alpha",
                    "session_id": "utterance-ledger-hard-not-found",
                },
                status="planned",
            )

            result = execute_music(dispatch)

        self.assertEqual(entry["expected_behavior"], "hard_not_found")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["error"], "music_not_found")

    @patch("oracle_app.handlers.music._load_audiobook_guess_candidates")
    @patch("oracle_app.handlers.music.choose_music_match_with_ollama")
    @patch("oracle_app.handlers.music.choose_music_match")
    @patch("oracle_app.handlers.music.score_music_candidates")
    @patch("oracle_app.handlers.music.search_music_catalog")
    def test_fixture_ultra_generic_single_word_clarifies_and_trims_substring_spillover(
        self,
        mock_search_plex,
        mock_score_music_candidates,
        mock_choose_music_match,
        mock_choose_music_match_with_ollama,
        mock_load_audiobook_guess_candidates,
    ) -> None:
        cases = (
            (
                "music-ultra-generic-single-word-one",
                [
                    {
                        "type": "track",
                        "title": "One",
                        "artist": "U2",
                        "album": "U218 Singles",
                        "score": 132,
                        "plex_key": "/library/metadata/101",
                        "rating_key": "101",
                    },
                    {
                        "type": "track",
                        "title": "One",
                        "artist": "The Beatles",
                        "album": "One",
                        "score": 104,
                        "plex_key": "/library/metadata/102",
                        "rating_key": "102",
                    },
                    {
                        "type": "track",
                        "title": "One of Them Girls",
                        "artist": "Lee Brice",
                        "album": "One of Them Girls",
                        "score": 96,
                        "plex_key": "/library/metadata/103",
                        "rating_key": "103",
                    },
                ],
                ("One by U2", "One by The Beatles"),
                "One of Them Girls",
            ),
            (
                "music-ultra-generic-single-word-hello",
                [
                    {
                        "type": "track",
                        "title": "Hello",
                        "artist": "Eminem",
                        "album": "Relapse",
                        "score": 134,
                        "plex_key": "/library/metadata/201",
                        "rating_key": "201",
                    },
                    {
                        "type": "track",
                        "title": "Hello",
                        "artist": "Beyonce",
                        "album": "I Am... Sasha Fierce",
                        "score": 118,
                        "plex_key": "/library/metadata/202",
                        "rating_key": "202",
                    },
                    {
                        "type": "track",
                        "title": "Hello Goodbye",
                        "artist": "The Beatles",
                        "album": "Magical Mystery Tour",
                        "score": 101,
                        "plex_key": "/library/metadata/203",
                        "rating_key": "203",
                    },
                ],
                ("Hello by Eminem", "Hello by Beyonce"),
                "Hello Goodbye",
            ),
            (
                "music-ultra-generic-single-word-stay",
                [
                    {
                        "type": "track",
                        "title": "Stay",
                        "artist": "Jackson Browne",
                        "album": "Running on Empty",
                        "score": 133,
                        "plex_key": "/library/metadata/301",
                        "rating_key": "301",
                    },
                    {
                        "type": "track",
                        "title": "Stay",
                        "artist": "Giant",
                        "album": "Time to Burn",
                        "score": 110,
                        "plex_key": "/library/metadata/302",
                        "rating_key": "302",
                    },
                    {
                        "type": "track",
                        "title": "Stayin Alive",
                        "artist": "Bee Gees",
                        "album": "Saturday Night Fever",
                        "score": 99,
                        "plex_key": "/library/metadata/303",
                        "rating_key": "303",
                    },
                ],
                ("Stay by Jackson Browne", "Stay by Giant"),
                "Stayin Alive",
            ),
        )
        mock_load_audiobook_guess_candidates.return_value = []

        for entry_id, candidates, included_lines, excluded_line in cases:
            with self.subTest(entry_id=entry_id):
                entry = self.entries_by_id[entry_id]
                mock_search_plex.return_value = candidates
                mock_score_music_candidates.return_value = candidates
                mock_choose_music_match.return_value = ("execute", candidates)
                mock_choose_music_match_with_ollama.return_value = candidates[0]

                dispatch = DispatchPlan(
                    target="music",
                    hook="music.execute",
                    payload={
                        "text": entry["utterance"],
                        "normalized_text": entry["utterance"],
                        "source": "satellite-alpha",
                        "session_id": f"utterance-ledger-{entry_id}",
                    },
                    status="planned",
                )

                result = execute_music(dispatch)

                self.assertEqual(entry["expected_behavior"], "deterministic_clarify")
                self.assertEqual(result.status, "pending_clarification")
                for included_line in included_lines:
                    self.assertIn(included_line, result.result["prompt"])
                self.assertNotIn(excluded_line, result.result["prompt"])
                self.assertEqual(
                    [item["title"].lower() for item in result.result["candidates"]],
                    [str(entry["utterance"]).split()[-1].lower()] * 2,
                )

        mock_choose_music_match_with_ollama.assert_not_called()

    def test_fixture_audiobook_deterministic_clarification_entries_resolve_pending_clarification(self) -> None:
        with patch("oracle_app.handlers.audiobook.fetch_audiobook_item") as mock_fetch_audiobook_item, patch(
            "oracle_app.handlers.audiobook.open_audiobook_playback_session"
        ) as mock_open_audiobook_playback_session, patch(
            "oracle_app.handlers.audiobook.execute_satellite_command"
        ) as mock_execute_satellite_command:
            mock_fetch_audiobook_item.return_value = {
                "userMediaProgress": {"isFinished": False, "currentTime": 0}
            }
            mock_execute_satellite_command.return_value = {"ok": True, "state": "accepted"}
            deterministic_entries = (
                ("audiobook-edition-shorthand-clarify", "book-jim", "session-jim"),
                ("audiobook-jim-dale-shorthand-clarify", "book-jim", "session-jim-short"),
                ("audiobook-full-cast-shorthand-clarify", "book-cast", "session-cast"),
                ("audiobook-regular-edition-shorthand-clarify", "book-jim", "session-regular"),
            )
            for entry_id, expected_library_item_id, playback_session_id in deterministic_entries:
                with self.subTest(entry_id=entry_id):
                    entry = self.entries_by_id[entry_id]
                    session_id = f"utterance-ledger-{entry_id}"
                    self._store_two_candidate_audiobook_pending(session_id)
                    mock_open_audiobook_playback_session.return_value = {
                        "id": playback_session_id,
                        "libraryItemId": expected_library_item_id,
                        "displayTitle": "Harry Potter and the Prisoner of Azkaban, Book 3",
                        "displayAuthor": "J. K. Rowling",
                        "duration": 1000,
                        "currentTime": 0,
                        "audioTracks": [
                            {
                                "contentUrl": f"/audio/{expected_library_item_id}.mp3",
                                "mimeType": "audio/mpeg",
                                "duration": 1000,
                                "startOffset": 0,
                                "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                            }
                        ],
                        "mediaMetadata": {"authors": [{"name": "J. K. Rowling"}]},
                    }

                    dispatch = DispatchPlan(
                        target="audiobook",
                        hook="audiobook.execute",
                        payload={
                            "text": entry["utterance"],
                            "normalized_text": entry["utterance"],
                            "source": "satellite-beta",
                            "session_id": session_id,
                        },
                        status="planned",
                    )

                    result = execute_audiobook(dispatch)

                    self.assertEqual(entry["expected_behavior"], "deterministic_clarify")
                    self.assertEqual(result.status, "executed")
                    self.assertEqual(result.result["selected"]["library_item_id"], expected_library_item_id)

    def test_fixture_audiobook_negative_narrowing_reprompts_from_narrowed_set(self) -> None:
        entry = self.entries_by_id["audiobook-negative-narrow-reprompt"]
        state.store_pending_audiobook_request(
            "satellite-alpha",
            "utterance-ledger-book-narrow",
            {
                "intent": {"intent": "play", "title": "harry potter and the prisoner of azkaban"},
                "candidates": [
                    {
                        "library_item_id": "book-regular",
                        "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                        "author": "J. K. Rowling",
                        "subtitle": "None",
                        "narrator": "Jim Dale",
                    },
                    {
                        "library_item_id": "book-cast",
                        "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                        "author": "J. K. Rowling",
                        "subtitle": "None",
                        "narrator": "Full Cast",
                    },
                    {
                        "library_item_id": "book-five",
                        "title": "Harry Potter and the Order of the Phoenix, Book 5",
                        "author": "J. K. Rowling",
                        "subtitle": "",
                    },
                ],
            },
        )

        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": entry["utterance"],
                "normalized_text": entry["utterance"],
                "source": "satellite-alpha",
                "session_id": "utterance-ledger-book-narrow",
            },
            status="planned",
        )

        result = execute_audiobook(dispatch)

        self.assertEqual(entry["expected_behavior"], "clarification_narrow")
        self.assertEqual(result.status, "pending_clarification")
        self.assertTrue(result.result["narrowed"])
        self.assertEqual(
            [item["library_item_id"] for item in result.result["candidates"]],
            ["book-cast", "book-five"],
        )

    def test_fixture_safe_other_pronoun_resolves_pending_clarification(self) -> None:
        entry = self.entries_by_id["audiobook-safe-other-pronoun"]
        self._store_two_candidate_audiobook_pending("utterance-ledger-other-one")

        with patch("oracle_app.handlers.audiobook.fetch_audiobook_item") as mock_fetch_audiobook_item, patch(
            "oracle_app.handlers.audiobook.open_audiobook_playback_session"
        ) as mock_open_audiobook_playback_session, patch(
            "oracle_app.handlers.audiobook.execute_satellite_command"
        ) as mock_execute_satellite_command:
            mock_fetch_audiobook_item.return_value = {
                "userMediaProgress": {"isFinished": False, "currentTime": 0}
            }
            mock_open_audiobook_playback_session.return_value = {
                "id": "session-cast",
                "libraryItemId": "book-cast",
                "displayTitle": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                "displayAuthor": "J. K. Rowling",
                "duration": 1000,
                "currentTime": 0,
                "audioTracks": [
                    {
                        "contentUrl": "/audio/book-cast.mp3",
                        "mimeType": "audio/mpeg",
                        "duration": 1000,
                        "startOffset": 0,
                        "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                    }
                ],
                "mediaMetadata": {"authors": [{"name": "J. K. Rowling"}]},
            }
            mock_execute_satellite_command.return_value = {"ok": True, "state": "accepted"}

            dispatch = DispatchPlan(
                target="audiobook",
                hook="audiobook.execute",
                payload={
                    "text": entry["utterance"],
                    "normalized_text": entry["utterance"],
                    "source": "satellite-beta",
                    "session_id": "utterance-ledger-other-one",
                },
                status="planned",
            )

            result = execute_audiobook(dispatch)

        self.assertEqual(entry["expected_behavior"], "deterministic_clarify")
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["selected"]["library_item_id"], "book-cast")

    def test_fixture_vague_audiobook_followup_does_not_consume_pending_clarification(self) -> None:
        entry = self.entries_by_id["audiobook-unsafe-vague-followup-rejected"]
        pending_payload = {
            "intent": {"intent": "play", "title": "prisoner of azkaban"},
            "candidates": [
                {
                    "library_item_id": "book-jim",
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
            ],
        }
        self._store_two_candidate_audiobook_pending("utterance-ledger-vague-followup")

        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": entry["utterance"],
                "normalized_text": entry["utterance"],
                "source": "satellite-beta",
                "session_id": "utterance-ledger-vague-followup",
            },
            status="planned",
        )

        result = execute_audiobook(dispatch)

        self.assertEqual(entry["expected_behavior"], "hard_not_found")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["error"], "audiobook_unrecognized")
        self.assertEqual(
            state.load_pending_audiobook_request("satellite-beta", "utterance-ledger-vague-followup"),
            pending_payload,
        )

    def test_fixture_unsafe_pronoun_followup_does_not_consume_pending_clarification(self) -> None:
        entry = self.entries_by_id["audiobook-unsafe-vague-pronoun-rejected"]
        pending_payload = {
            "intent": {"intent": "play", "title": "prisoner of azkaban"},
            "candidates": [
                {
                    "library_item_id": "book-jim",
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
            ],
        }
        self._store_two_candidate_audiobook_pending("utterance-ledger-unsafe-pronoun")

        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={
                "text": entry["utterance"],
                "normalized_text": entry["utterance"],
                "source": "satellite-beta",
                "session_id": "utterance-ledger-unsafe-pronoun",
            },
            status="planned",
        )

        result = execute_audiobook(dispatch)

        self.assertEqual(entry["expected_behavior"], "hard_not_found")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result["error"], "audiobook_unrecognized")
        self.assertEqual(
            state.load_pending_audiobook_request("satellite-beta", "utterance-ledger-unsafe-pronoun"),
            pending_payload,
        )


if __name__ == "__main__":
    unittest.main()
