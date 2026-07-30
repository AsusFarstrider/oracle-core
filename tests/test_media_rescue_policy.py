from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.media_rescue_policy import (
    apply_ultra_generic_single_word_music_guard,
    audiobook_is_clearly_stronger_than_music,
    is_generic_title_only_play_intent,
    should_downgrade_weak_single_music_clarification,
    should_try_audiobook_fallback,
)
from oracle_app.music import MusicIntent


class MediaRescuePolicyTests(unittest.TestCase):
    def test_should_try_audiobook_fallback_accepts_generic_title_only_not_found(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="dune",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play dune",
        )

        self.assertTrue(should_try_audiobook_fallback(intent, "not_found", []))

    def test_should_try_audiobook_fallback_rejects_explicit_album_request(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type="album",
            title=None,
            artist="the beatles",
            album="abbey road",
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play the abbey road album by the beatles",
        )

        self.assertFalse(should_try_audiobook_fallback(intent, "not_found", []))

    def test_audiobook_is_clearly_stronger_than_music_uses_normal_threshold(self) -> None:
        self.assertTrue(
            audiobook_is_clearly_stronger_than_music(
                requested_title="dune",
                top_music={"title": "Dune Buggy", "score": 52},
                top_audiobook={"title": "Dune", "score": 70},
            )
        )

    def test_audiobook_is_clearly_stronger_than_music_uses_strict_title_match_threshold(self) -> None:
        self.assertFalse(
            audiobook_is_clearly_stronger_than_music(
                requested_title="dune",
                top_music={"title": "Dune", "score": 54},
                top_audiobook={"title": "Dune", "score": 78},
            )
        )
        self.assertTrue(
            audiobook_is_clearly_stronger_than_music(
                requested_title="dune",
                top_music={"title": "Dune", "score": 54},
                top_audiobook={"title": "Dune", "score": 80},
            )
        )

    def test_should_downgrade_weak_single_music_clarification_requires_generic_title_only(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="dooon",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play dooon",
        )

        self.assertTrue(
            should_downgrade_weak_single_music_clarification(intent, "clarify", [{"score": 40}])
        )

    def test_is_generic_title_only_play_intent_rejects_artist_specific_play(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type="artist",
            title=None,
            artist="david bowie",
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play songs by david bowie",
        )

        self.assertFalse(is_generic_title_only_play_intent(intent))

    def test_ultra_generic_single_word_guard_blocks_ordinary_autoplay_and_clarifies(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="one",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play one",
        )
        scored = [
            {"title": "One", "artist": "U2", "score": 119},
            {"title": "One", "artist": "Metallica", "score": 96},
        ]

        decision, selected = apply_ultra_generic_single_word_music_guard(intent, "execute", scored, [scored[0]])

        self.assertEqual(decision, "clarify")
        self.assertEqual(selected, scored)

    def test_ultra_generic_single_word_guard_trims_substring_spillover_from_clarification(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="one",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play one",
        )
        scored = [
            {"title": "One", "artist": "U2", "album": "U218 Singles", "score": 119},
            {"title": "One", "artist": "The Beatles", "album": "One", "score": 90},
            {"title": "One of Them Girls", "artist": "Lee Brice", "album": "One of Them Girls", "score": 88},
        ]

        decision, selected = apply_ultra_generic_single_word_music_guard(intent, "execute", scored, [scored[0]])

        self.assertEqual(decision, "clarify")
        self.assertEqual([item["title"] for item in selected], ["One", "One"])

    def test_ultra_generic_single_word_guard_allows_very_strong_autoplay(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="hello",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play hello",
        )
        scored = [
            {"title": "Hello", "artist": "Adele", "score": 145},
            {"title": "Hello", "artist": "Lionel Richie", "score": 109},
        ]

        decision, selected = apply_ultra_generic_single_word_music_guard(intent, "execute", scored, [scored[0]])

        self.assertEqual(decision, "execute")
        self.assertEqual(selected, [scored[0]])

    def test_ultra_generic_single_word_guard_can_fall_through_to_not_found(self) -> None:
        intent = MusicIntent(
            intent="play",
            media_type=None,
            title="stay",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="play stay",
        )
        scored = [
            {"title": "Stay", "artist": "Artist A", "score": 32},
            {"title": "Stay", "artist": "Artist B", "score": 28},
        ]

        decision, selected = apply_ultra_generic_single_word_music_guard(intent, "execute", scored, [scored[0]])

        self.assertEqual(decision, "not_found")
        self.assertEqual(selected, [])

    def test_ultra_generic_single_word_guard_does_not_affect_non_bucket_single_word(self) -> None:
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
        scored = [
            {"title": "Fortnight", "artist": "Taylor Swift", "score": 119},
            {"title": "Fortnight", "artist": "Other Artist", "score": 96},
        ]

        decision, selected = apply_ultra_generic_single_word_music_guard(intent, "execute", scored, [scored[0]])

        self.assertEqual(decision, "execute")
        self.assertEqual(selected, [scored[0]])


if __name__ == "__main__":
    unittest.main()
