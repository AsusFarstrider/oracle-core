from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.music import (
    MusicIntent,
    dedupe_music_candidates,
    looks_like_pending_music_clarification,
    match_pending_music_candidate,
    normalize_music_alias_text,
    normalize_music_compact_text,
    parse_music_intent,
    score_music_candidates,
)
from oracle_app.music_runtime.client import (
    build_native_queue_manifest,
    search_track_from_album_fallback,
    search_track_from_artist_fallback,
)
from oracle_app.music_runtime.matching import build_query_variants, build_search_queries
from oracle_app.music_runtime.ollama import choose_music_match_with_ollama, resolve_with_ollama
from oracle_app.music_runtime.playback import build_music_play_media_args, music_playback_selection
from oracle_app.music_runtime.policy import audiobook_is_clearly_stronger_than_music
from oracle_app.music_runtime.selection import music_pending_option, music_selection_with_provider_fields


class MusicMatchingTests(unittest.TestCase):
    @patch(
        "oracle_app.music_runtime.ollama.get_ollama_request_settings",
        return_value={"keep_alive": "5m", "options": {}, "timeout_seconds": 1},
    )
    @patch(
        "oracle_app.music_runtime.ollama.get_ollama_settings",
        return_value=("http://inference.example.test", "test-model"),
    )
    @patch("oracle_app.music_runtime.ollama.request.urlopen", side_effect=TimeoutError("timed out"))
    def test_music_ollama_timeout_fails_as_no_intent(
        self,
        _mock_urlopen,
        _mock_ollama_settings,
        _mock_request_settings,
    ) -> None:
        self.assertIsNone(resolve_with_ollama("the first one"))

    def test_music_playback_selection_keeps_provider_fields_at_playback_edge(self) -> None:
        selection = music_playback_selection(
            {
                "type": "track",
                "title": "Heroes",
                "artist": "David Bowie",
                "album": "Heroes",
                "plex_key": "/library/metadata/1",
                "rating_key": "1",
                "parent_key": "/library/metadata/album/children",
                "duration_seconds": 211.0,
            }
        )

        self.assertEqual(selection["selection_id"], "plex:track:1")
        self.assertEqual(selection["media_type"], "track")
        self.assertNotIn("plex_key", selection)

        args = build_music_play_media_args(
            "test_satellite_charlie",
            selection,
            get_backend_hint=lambda _source, *, media_type: "plexamp_external",
            build_manifest=lambda _selection: None,
        )

        self.assertEqual(args["media_type"], "track")
        self.assertEqual(args["plex_key"], "/library/metadata/1")
        self.assertEqual(args["rating_key"], "1")
        self.assertEqual(args["parent_key"], "/library/metadata/album/children")
        self.assertEqual(args["title"], "Heroes")

    def test_music_pending_option_uses_oracle_selection_and_provider_ref(self) -> None:
        option = music_pending_option(
            {
                "type": "track",
                "title": "Heroes",
                "artist": "David Bowie",
                "album": "Heroes",
                "plex_key": "/library/metadata/1",
                "rating_key": "1",
                "parent_key": "/library/metadata/album/children",
                "score": 91,
            }
        )

        self.assertEqual(option["selection_id"], "plex:track:1")
        self.assertEqual(option["media_type"], "track")
        self.assertEqual(option["provider_ref"]["provider"], "plex")
        self.assertEqual(option["provider_ref"]["item_id"], "1")
        self.assertEqual(option["provider_ref"]["item_path"], "/library/metadata/1")
        self.assertNotIn("rating_key", option["provider_ref"])
        self.assertNotIn("plex_key", option)

        rehydrated = music_selection_with_provider_fields(option)
        self.assertEqual(rehydrated["plex_key"], "/library/metadata/1")
        self.assertEqual(rehydrated["parent_key"], "/library/metadata/album/children")
        self.assertEqual(rehydrated["rating_key"], "1")

    def test_clearly_stronger_audiobook_match_uses_normal_threshold(self) -> None:
        result = audiobook_is_clearly_stronger_than_music(
            requested_title="dune",
            top_music={"title": "Dune Buggy", "score": 52},
            top_audiobook={"title": "Dune", "score": 70},
        )

        self.assertTrue(result)

    def test_clearly_stronger_audiobook_match_requires_stricter_threshold_for_title_match_music(self) -> None:
        result = audiobook_is_clearly_stronger_than_music(
            requested_title="dune",
            top_music={"title": "Dune", "score": 54},
            top_audiobook={"title": "Dune", "score": 78},
        )

        self.assertFalse(result)

    def test_clearly_stronger_audiobook_match_allows_title_match_music_only_with_strict_gap(self) -> None:
        result = audiobook_is_clearly_stronger_than_music(
            requested_title="dune",
            top_music={"title": "Dune", "score": 54},
            top_audiobook={"title": "Dune", "score": 80},
        )

        self.assertTrue(result)

    def test_parse_music_intent_handles_what_song_is_playing(self) -> None:
        intent = parse_music_intent("what song is playing")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "what_is_playing")

    def test_parse_music_intent_handles_play_me_some(self) -> None:
        intent = parse_music_intent("play me some david bowie")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "david bowie")

    def test_parse_music_intent_handles_listen_to(self) -> None:
        intent = parse_music_intent("listen to heroes by david bowie")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "heroes")
        self.assertEqual(intent.artist, "david bowie")

    def test_parse_music_intent_handles_queue_up(self) -> None:
        intent = parse_music_intent("queue up heroes by david bowie")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.title, "heroes")
        self.assertEqual(intent.artist, "david bowie")

    def test_parse_music_intent_handles_throw_on_soundtrack(self) -> None:
        intent = parse_music_intent("throw on the soundtrack to k-pop demon hunters")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "album")
        self.assertEqual(intent.album, "k-pop demon hunters")

    def test_parse_music_intent_handles_track_from_album(self) -> None:
        intent = parse_music_intent("play how it's done from k-pop demon hunters")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "track")
        self.assertEqual(intent.title, "how it's done")
        self.assertEqual(intent.album, "k-pop demon hunters")

    def test_parse_music_intent_handles_album_by_artist_shape(self) -> None:
        intent = parse_music_intent("play album rumors by fleetwood mac")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "album")
        self.assertEqual(intent.album, "rumors")
        self.assertEqual(intent.artist, "fleetwood mac")

    def test_parse_music_intent_handles_artist_album_shape(self) -> None:
        intent = parse_music_intent("play the fleetwood mac album rumors")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "album")
        self.assertEqual(intent.album, "rumors")
        self.assertEqual(intent.artist, "fleetwood mac")

    def test_parse_music_intent_handles_album_suffix_shape(self) -> None:
        intent = parse_music_intent("play the dark side of the moon album")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "album")
        self.assertEqual(intent.album, "dark side of the moon")

    def test_parse_music_intent_handles_album_suffix_by_artist_shape(self) -> None:
        intent = parse_music_intent("play the abbey road album by the beatles")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "album")
        self.assertEqual(intent.album, "abbey road")
        self.assertEqual(intent.artist, "the beatles")

    def test_parse_music_intent_handles_song_by_artist_shorthand(self) -> None:
        intent = parse_music_intent("play a song by the beatles")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "artist")
        self.assertEqual(intent.artist, "the beatles")

    def test_parse_music_intent_handles_track_off_album_shape(self) -> None:
        intent = parse_music_intent("play something off abbey road")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "track")
        self.assertEqual(intent.title, "something")
        self.assertEqual(intent.album, "abbey road")

    def test_parse_music_intent_handles_put_on_the_album_shape(self) -> None:
        intent = parse_music_intent("put on the album abbey road")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "album")
        self.assertEqual(intent.album, "abbey road")
        self.assertIsNone(intent.artist)

    def test_parse_music_intent_handles_title_song_by_artist_shape(self) -> None:
        intent = parse_music_intent("play the fortnight song by taylor swift")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "track")
        self.assertEqual(intent.title, "fortnight")
        self.assertEqual(intent.artist, "taylor swift")

    def test_parse_music_intent_handles_artist_song_title_shape(self) -> None:
        intent = parse_music_intent("play the new taylor swift song fortnight")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "track")
        self.assertEqual(intent.title, "fortnight")
        self.assertEqual(intent.artist, "taylor swift")

    def test_parse_music_intent_handles_artist_version_of_title_shape(self) -> None:
        intent = parse_music_intent("put on taylor's version of all too well")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "track")
        self.assertEqual(intent.title, "all too well")
        self.assertEqual(intent.artist, "taylor swift")

    def test_parse_music_intent_handles_songs_by_artist(self) -> None:
        intent = parse_music_intent("play songs by david bowie")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "artist")
        self.assertEqual(intent.artist, "david bowie")

    def test_parse_music_intent_handles_music_from_soundtrack(self) -> None:
        intent = parse_music_intent("play music from hamilton")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "album")
        self.assertEqual(intent.album, "hamilton")

    def test_parse_music_intent_handles_soundtrack_to(self) -> None:
        intent = parse_music_intent("play the soundtrack to k-pop demon hunters")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.intent, "play")
        self.assertEqual(intent.media_type, "album")
        self.assertEqual(intent.album, "k-pop demon hunters")

    def test_parse_music_intent_accepts_refined_volume_up_form(self) -> None:
        intent = parse_music_intent("volume_up")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.intent, "volume_up")

    def test_parse_music_intent_accepts_refined_volume_down_form(self) -> None:
        intent = parse_music_intent("volume_down")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.intent, "volume_down")

    def test_parse_music_intent_accepts_natural_volume_up_phrases(self) -> None:
        for phrase in (
            "turn up the volume",
            "turn the volume up",
            "increase the volume",
            "make oracle louder",
        ):
            with self.subTest(phrase=phrase):
                intent = parse_music_intent(phrase)

                self.assertIsNotNone(intent)
                assert intent is not None
                self.assertEqual(intent.intent, "volume_up")

    def test_parse_music_intent_accepts_natural_volume_down_phrases(self) -> None:
        for phrase in (
            "turn down the volume",
            "turn the volume down",
            "decrease the volume",
            "make oracle quieter",
        ):
            with self.subTest(phrase=phrase):
                intent = parse_music_intent(phrase)

                self.assertIsNotNone(intent)
                assert intent is not None
                self.assertEqual(intent.intent, "volume_down")

    def test_parse_music_intent_handles_restart(self) -> None:
        intent = parse_music_intent("restart track")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.intent, "restart")

    def test_dedupe_music_candidates_collapses_duplicate_track_copies(self) -> None:
        candidates = [
            {
                "type": "track",
                "title": "Heroes",
                "artist": "David Bowie",
                "album": "Heroes",
                "plex_key": "/library/metadata/1",
                "rating_key": "1",
            },
            {
                "type": "track",
                "title": "Heroes",
                "artist": "David Bowie",
                "album": "Heroes",
                "plex_key": "/library/metadata/2",
                "rating_key": "2",
            },
            {
                "type": "album",
                "title": "Heroes",
                "artist": "David Bowie",
                "album": "Heroes",
                "plex_key": "/library/metadata/3",
                "rating_key": "3",
            },
        ]

        deduped = dedupe_music_candidates(candidates)

        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["type"], "track")
        self.assertEqual(deduped[1]["type"], "album")

    def test_dedupe_music_candidates_collapses_same_track_across_compilations(self) -> None:
        candidates = [
            {
                "type": "track",
                "title": "Fortunate Son",
                "artist": "Creedence Clearwater Revival",
                "album": "Chronicle: The 20 Greatest Hits",
                "plex_key": "/library/metadata/10",
                "rating_key": "10",
            },
            {
                "type": "track",
                "title": "Fortunate Son",
                "artist": "Creedence Clearwater Revival",
                "album": "The Best of Creedence Clearwater Revival",
                "plex_key": "/library/metadata/11",
                "rating_key": "11",
            },
            {
                "type": "track",
                "title": "Fortunate Son",
                "artist": "Creedence Clearwater Revival",
                "album": "Greatest Hits",
                "plex_key": "/library/metadata/12",
                "rating_key": "12",
            },
        ]

        deduped = dedupe_music_candidates(candidates)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["title"], "Fortunate Son")

    def test_dedupe_music_candidates_keeps_live_variant_separate(self) -> None:
        candidates = [
            {
                "type": "track",
                "title": "Fortunate Son",
                "artist": "Creedence Clearwater Revival",
                "album": "Chronicle: The 20 Greatest Hits",
                "plex_key": "/library/metadata/10",
                "rating_key": "10",
            },
            {
                "type": "track",
                "title": "Fortunate Son",
                "artist": "Creedence Clearwater Revival",
                "album": "Live at Woodstock",
                "plex_key": "/library/metadata/13",
                "rating_key": "13",
            },
        ]

        deduped = dedupe_music_candidates(candidates)

        self.assertEqual(len(deduped), 2)

    def test_dedupe_music_candidates_preserves_album_variants_when_requested(self) -> None:
        candidates = [
            {
                "type": "track",
                "title": "How It's Done",
                "artist": "KPop Demon Hunters Cast",
                "album": "KPop Demon Hunters (Soundtrack from the Netflix Film)",
                "plex_key": "/library/metadata/20",
                "rating_key": "20",
            },
            {
                "type": "track",
                "title": "How It's Done",
                "artist": "KPop Demon Hunters Cast",
                "album": "KPop Demon Hunters Deluxe",
                "plex_key": "/library/metadata/21",
                "rating_key": "21",
            },
        ]

        deduped = dedupe_music_candidates(candidates, preserve_album_variants=True)

        self.assertEqual(len(deduped), 2)

    def test_match_pending_music_candidate_handles_second_one(self) -> None:
        pending = {
            "candidates": [
                {"title": "Heroes", "artist": "David Bowie", "album": "Heroes", "media_type": "track"},
                {"title": "Low", "artist": "David Bowie", "album": "Low", "media_type": "album"},
            ]
        }

        selected = match_pending_music_candidate("the second one", pending)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["title"], "Low")

    def test_match_pending_music_candidate_handles_the_album(self) -> None:
        pending = {
            "candidates": [
                {"title": "Heroes", "artist": "David Bowie", "album": "Heroes", "media_type": "track"},
                {"title": "Heroes", "artist": "David Bowie", "album": "Heroes", "media_type": "album"},
            ]
        }

        selected = match_pending_music_candidate("the album", pending)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["media_type"], "album")

    def test_looks_like_pending_music_clarification_handles_artist_followup(self) -> None:
        pending = {
            "candidates": [
                {"title": "Heroes", "artist": "David Bowie", "album": "Heroes", "media_type": "track"},
                {"title": "Heroes", "artist": "Peter Gabriel", "album": "Scratch My Back", "media_type": "track"},
            ]
        }

        self.assertTrue(looks_like_pending_music_clarification("the bowie one", pending))

    def test_score_music_candidates_prefers_exact_artist_after_softener_cleanup(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="david bowie",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play me some david bowie",
        )
        candidates = [
            {"type": "artist", "title": "David Bowie", "artist": "David Bowie", "album": "", "plex_key": "1"},
            {"type": "album", "title": "David Bowie Legacy", "artist": "Various Artists", "album": "David Bowie Legacy", "plex_key": "2"},
        ]

        scored = score_music_candidates(intent, candidates)

        self.assertEqual(scored[0]["type"], "artist")
        self.assertGreater(int(scored[0]["score"]), int(scored[1]["score"]))

    def test_normalize_music_alias_text_strips_common_version_noise(self) -> None:
        self.assertEqual(
            normalize_music_alias_text("How It's Done (Soundtrack from the Netflix Film)"),
            "how its done",
        )
        self.assertEqual(
            normalize_music_alias_text("Fortnight feat. Post Malone - Remastered"),
            "fortnight",
        )

    def test_normalize_music_compact_text_collapses_punctuation_heavy_artist_name(self) -> None:
        self.assertEqual(normalize_music_compact_text("AC/DC"), "acdc")
        self.assertEqual(normalize_music_compact_text("ac dc"), "acdc")

    def test_score_music_candidates_prefers_core_title_over_series_neighbor_noise(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="fortnight",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play fortnight",
        )
        candidates = [
            {
                "type": "track",
                "title": "Fortnight feat. Post Malone",
                "artist": "Taylor Swift",
                "album": "The Tortured Poets Department",
                "plex_key": "1",
            },
            {
                "type": "track",
                "title": "Fortnight Remix",
                "artist": "Taylor Swift",
                "album": "Fortnight Remix EP",
                "plex_key": "2",
            },
            {
                "type": "track",
                "title": "The Fortnight Show",
                "artist": "Various Artists",
                "album": "Compilation",
                "plex_key": "3",
            },
        ]

        scored = score_music_candidates(intent, candidates)

        self.assertEqual(scored[0]["title"], "Fortnight feat. Post Malone")
        self.assertGreater(int(scored[0]["score"]), int(scored[1]["score"]))
        self.assertGreater(int(scored[1]["score"]), int(scored[2]["score"]))

    def test_score_music_candidates_prefers_core_album_title_over_soundtrack_suffix_noise(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type="album",
            title=None,
            artist=None,
            album="k-pop demon hunters",
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play the soundtrack to k-pop demon hunters",
        )
        candidates = [
            {
                "type": "album",
                "title": "KPop Demon Hunters (Soundtrack from the Netflix Film)",
                "artist": "KPop Demon Hunters Cast",
                "album": "KPop Demon Hunters (Soundtrack from the Netflix Film)",
                "plex_key": "10",
            },
            {
                "type": "album",
                "title": "KPop Demon Hunters Deluxe",
                "artist": "KPop Demon Hunters Cast",
                "album": "KPop Demon Hunters Deluxe",
                "plex_key": "11",
            },
        ]

        scored = score_music_candidates(intent, candidates)

        self.assertEqual(scored[0]["plex_key"], "10")
        self.assertGreater(int(scored[0]["score"]), int(scored[1]["score"]))

    def test_score_music_candidates_matches_punctuation_heavy_artist_alias(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type="artist",
            title=None,
            artist="ac dc",
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play songs by ac dc",
        )
        candidates = [
            {"type": "artist", "title": "AC/DC", "artist": "AC/DC", "album": "", "plex_key": "1"},
            {"type": "artist", "title": "AC", "artist": "AC", "album": "", "plex_key": "2"},
        ]

        scored = score_music_candidates(intent, candidates)

        self.assertEqual(scored[0]["plex_key"], "1")
        self.assertGreater(int(scored[0]["score"]), int(scored[1]["score"]))

    def test_build_query_variants_adds_punctuated_artist_alias_for_short_spoken_name(self) -> None:
        variants = build_query_variants("ac dc")
        self.assertIn("AC/DC", variants)

    def test_build_query_variants_adds_ampersand_and_comma_aliases_for_spoken_band_name(self) -> None:
        variants = build_query_variants("earth wind and fire")
        self.assertIn("earth, wind & fire", variants)

    def test_build_query_variants_adds_n_apostrophe_alias_for_spoken_band_name(self) -> None:
        variants = build_query_variants("guns and roses")
        self.assertIn("guns n’ roses", variants)
        self.assertIn("guns n' roses", variants)

    def test_build_query_variants_adds_spelling_alias(self) -> None:
        variants = build_query_variants("rumors")
        self.assertIn("rumours", variants)

    def test_normalize_music_alias_text_handles_rumours_spelling(self) -> None:
        self.assertEqual(normalize_music_alias_text("Rumours"), "rumors")

    def test_score_music_candidates_penalizes_explicit_artist_mismatch_for_tracks(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type="track",
            title="rumors",
            artist="fleetwood mac",
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="put on rumors by fleetwood mac",
        )
        candidates = [
            {
                "type": "track",
                "title": "Rumors",
                "artist": "Lindsay Lohan",
                "album": "Now That's What I Call Music! 18",
                "plex_key": "track-mismatch",
            },
            {
                "type": "album",
                "title": "Rumours",
                "artist": "Fleetwood Mac",
                "album": "Rumours",
                "plex_key": "album-correct",
            },
        ]

        scored = score_music_candidates(intent, candidates)

        self.assertEqual(scored[0]["plex_key"], "album-correct")

    def test_build_search_queries_includes_track_and_artist_combinations(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type="track",
            title="fortnight",
            artist="Taylor Swift",
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play fortnight by taylor swift",
        )

        queries = build_search_queries(intent)

        self.assertIn("fortnight Taylor Swift", queries)
        self.assertIn("Taylor Swift fortnight", queries)

    def test_build_search_queries_expands_track_queries_with_artist_aliases(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type="track",
            title="thunderstruck",
            artist="ac dc",
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play thunderstruck ac dc",
        )

        queries = build_search_queries(intent)

        self.assertIn("thunderstruck AC/DC", queries)
        self.assertIn("AC/DC thunderstruck", queries)

    def test_build_search_queries_includes_album_and_artist_combinations(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type="album",
            title=None,
            artist="Fleetwood Mac",
            album="Rumors",
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="put on rumors by fleetwood mac",
        )

        queries = build_search_queries(intent)

        self.assertIn("Rumors Fleetwood Mac", queries)
        self.assertIn("Fleetwood Mac Rumors", queries)

    def test_build_search_queries_adds_artist_leading_title_only_reordering(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="taylor swift fortnight",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play taylor swift fortnight",
        )

        queries = build_search_queries(intent)

        self.assertIn("fortnight taylor swift", queries)

    def test_build_search_queries_adds_taylor_version_spoken_shorthand_queries(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="all too well taylor version",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play all too well taylor version",
        )

        queries = build_search_queries(intent)

        self.assertIn("all too well", queries)
        self.assertIn("all too well Taylor Swift", queries)

    @patch("oracle_app.music_runtime.ollama.request.urlopen")
    @patch("oracle_app.music_runtime.ollama.get_ollama_request_settings")
    @patch("oracle_app.music_runtime.ollama.get_ollama_settings")
    def test_choose_music_match_with_ollama_returns_selected_candidate(
        self,
        mock_settings,
        mock_request_settings,
        mock_urlopen,
    ) -> None:
        class _Response:
            def read(self) -> bytes:
                return b'{"response":"{\\"choice_index\\":1,\\"reason\\":\\"exact artist match\\"}"}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        mock_settings.return_value = ("http://127.0.0.1:11434", "phi4-mini:latest")
        mock_request_settings.return_value = {
            "timeout_seconds": 20,
            "keep_alive": "-1",
            "options": {"temperature": 0.1},
        }
        mock_urlopen.return_value = _Response()

        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="heroes",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play heroes",
        )
        candidates = [
            {"type": "track", "title": "Heroes", "artist": "Peter Gabriel", "album": "Scratch My Back"},
            {"type": "track", "title": "Heroes", "artist": "David Bowie", "album": "Heroes"},
        ]

        selected = choose_music_match_with_ollama(intent, candidates)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["artist"], "David Bowie")

    @patch("oracle_app.provider_bridges.plex_music.PlexMusicBridge.fetch_xml")
    def test_search_track_from_album_fallback_extracts_track_from_album_children(self, mock_fetch_xml) -> None:
        empty_album_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="0"></MediaContainer>"""
        album_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="1">
  <Directory type="album" title="KPop Demon Hunters (Soundtrack from the Netflix Film)" parentTitle="KPop Demon Hunters Cast" key="/library/metadata/80793/children" ratingKey="80793" />
</MediaContainer>"""
        track_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="1">
  <Track type="track" title="How It's Done" parentTitle="KPop Demon Hunters (Soundtrack from the Netflix Film)" grandparentTitle="KPop Demon Hunters Cast" key="/library/metadata/90001" ratingKey="90001" />
</MediaContainer>"""
        def fetch_xml_side_effect(url, settings):
            if "query=k-pop+demon+hunters" in url:
                return empty_album_xml
            if "query=kpop+demon+hunters" in url:
                return album_xml
            if "query=kpopdemonhunters" in url:
                return empty_album_xml
            if "/library/metadata/80793/children" in url:
                return track_xml
            raise AssertionError(f"Unexpected URL: {url}")

        mock_fetch_xml.side_effect = fetch_xml_side_effect

        intent = MusicIntent(
            intent="play",
            media_type="track",
            title="how it's done",
            artist=None,
            album="k-pop demon hunters",
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play how it's done from k-pop demon hunters",
        )
        settings = {"plex_base_url": "http://plex", "plex_token": "token", "plex_music_section_id": 4}

        with patch("oracle_app.music_runtime.client.get_music_settings", return_value=settings):
            matches = search_track_from_album_fallback(intent, settings)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["title"], "How It's Done")
        self.assertIn("query=k-pop+demon+hunters", mock_fetch_xml.call_args_list[0].args[0])
        self.assertIn("query=kpop+demon+hunters", mock_fetch_xml.call_args_list[1].args[0])

    @patch("oracle_app.provider_bridges.plex_music.PlexMusicBridge.fetch_xml")
    def test_search_track_from_artist_fallback_extracts_track_from_artist_children(self, mock_fetch_xml) -> None:
        empty_artist_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="0"></MediaContainer>"""
        artist_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="1">
  <Directory type="artist" title="AC/DC" key="/library/metadata/40078/children" ratingKey="40078" />
</MediaContainer>"""
        album_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="1">
  <Directory type="album" title="The Razors Edge" parentTitle="AC/DC" key="/library/metadata/50001/children" ratingKey="50001" />
</MediaContainer>"""
        track_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="1">
  <Track type="track" title="Thunderstruck" parentTitle="The Razors Edge" grandparentTitle="AC/DC" key="/library/metadata/90002" ratingKey="90002" />
</MediaContainer>"""

        def _fetch(endpoint: str, _settings: dict[str, object]) -> str:
            if "library/metadata/50001/children" in endpoint:
                return track_xml
            if "library/metadata/40078/children" in endpoint:
                return album_xml
            if "query=AC%2FDC" in endpoint:
                return artist_xml
            return empty_artist_xml

        mock_fetch_xml.side_effect = _fetch

        intent = MusicIntent(
            intent="play",
            media_type="track",
            title="thunderstruck",
            artist="ac dc",
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play thunderstruck ac dc",
        )
        settings = {"plex_base_url": "http://plex", "plex_token": "token", "plex_music_section_id": 4}

        with patch("oracle_app.music_runtime.client.get_music_settings", return_value=settings):
            matches = search_track_from_artist_fallback(intent, settings)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["title"], "Thunderstruck")
        called_urls = [call.args[0] for call in mock_fetch_xml.call_args_list]
        self.assertTrue(any("query=ac+dc" in url for url in called_urls))
        self.assertTrue(any("query=AC%2FDC" in url for url in called_urls))

    @patch("oracle_app.music_runtime.client.get_music_settings")
    @patch("oracle_app.provider_bridges.plex_music.PlexMusicBridge.fetch_xml")
    def test_build_native_queue_manifest_expands_artist_into_sorted_tracks(
        self,
        mock_fetch_xml,
        mock_get_music_settings,
    ) -> None:
        artist_children_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="2">
  <Directory type="album" title="Hunky Dory" parentTitle="David Bowie" key="/library/metadata/20001/children" ratingKey="20001" />
  <Directory type="album" title="Low" parentTitle="David Bowie" key="/library/metadata/20002/children" ratingKey="20002" />
</MediaContainer>"""
        low_tracks_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="2">
  <Track type="track" title="Breaking Glass" parentTitle="Low" grandparentTitle="David Bowie" key="/library/metadata/30002" ratingKey="30002" index="2" parentIndex="1" duration="111000" />
  <Track type="track" title="Speed of Life" parentTitle="Low" grandparentTitle="David Bowie" key="/library/metadata/30001" ratingKey="30001" index="1" parentIndex="1" duration="125000" />
</MediaContainer>"""
        hunky_dory_tracks_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="1">
  <Track type="track" title="Changes" parentTitle="Hunky Dory" grandparentTitle="David Bowie" key="/library/metadata/30003" ratingKey="30003" index="1" parentIndex="1" duration="217000" />
</MediaContainer>"""

        def _fetch(endpoint: str, _settings: dict[str, object]) -> str:
            if "library/metadata/artist-bowie/children" in endpoint:
                return artist_children_xml
            if "library/metadata/20001/children" in endpoint:
                return hunky_dory_tracks_xml
            if "library/metadata/20002/children" in endpoint:
                return low_tracks_xml
            raise AssertionError(f"Unexpected URL: {endpoint}")

        mock_fetch_xml.side_effect = _fetch
        mock_get_music_settings.return_value = {
            "plex_configured": True,
            "plex_base_url": "http://plex",
            "plex_token": "token",
            "plex_music_section_id": 4,
        }

        manifest = build_native_queue_manifest(
            {
                "type": "artist",
                "title": "David Bowie",
                "artist": "David Bowie",
                "plex_key": "/library/metadata/artist-bowie/children",
                "rating_key": "artist-bowie",
            }
        )

        assert manifest is not None
        self.assertEqual(manifest["collection_type"], "artist")
        self.assertEqual(manifest["queue_count"], 3)
        self.assertEqual(manifest["collection_title"], "David Bowie")
        self.assertEqual(manifest["tracks"][0]["title"], "Changes")
        self.assertEqual(manifest["tracks"][1]["title"], "Speed of Life")
        self.assertEqual(manifest["tracks"][2]["title"], "Breaking Glass")

    @patch("oracle_app.music_runtime.client.get_music_settings")
    @patch("oracle_app.provider_bridges.plex_music.PlexMusicBridge.fetch_xml")
    def test_build_native_queue_manifest_expands_playlist_into_tracks(
        self,
        mock_fetch_xml,
        mock_get_music_settings,
    ) -> None:
        playlist_tracks_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="2">
  <Track type="track" title="First Song" parentTitle="Road Trip Mix" grandparentTitle="Artist A" key="/library/metadata/40001" ratingKey="40001" index="1" duration="180000" />
  <Track type="track" title="Second Song" parentTitle="Road Trip Mix" grandparentTitle="Artist B" key="/library/metadata/40002" ratingKey="40002" index="2" duration="200000" />
</MediaContainer>"""

        mock_fetch_xml.return_value = playlist_tracks_xml
        mock_get_music_settings.return_value = {
            "plex_configured": True,
            "plex_base_url": "http://plex",
            "plex_token": "token",
            "plex_music_section_id": 4,
        }

        manifest = build_native_queue_manifest(
            {
                "type": "playlist",
                "title": "Road Trip Mix",
                "plex_key": "/playlists/123/items",
                "rating_key": "playlist-123",
            }
        )

        assert manifest is not None
        self.assertEqual(manifest["collection_type"], "playlist")
        self.assertEqual(manifest["queue_id"], "playlist-123")
        self.assertEqual(manifest["queue_count"], 2)
        self.assertEqual(manifest["tracks"][0]["title"], "First Song")
        self.assertEqual(manifest["tracks"][1]["title"], "Second Song")


if __name__ == "__main__":
    unittest.main()
