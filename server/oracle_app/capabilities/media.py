from __future__ import annotations

import re

from oracle_app.audiobook import is_audiobook_request
from oracle_app.music_runtime.parsing import is_music_request, parse_music_intent
from oracle_app.music_runtime.policy import is_generic_title_only_play_intent

from .base import CapabilityDecision


_AUDIOBOOK_TITLE_CUES = (
    "book ", " volume ", " chapter ", " full cast", " regular ",
    " standard ", " normal ", " unabridged", " abridged", " edition",
    " version of ", " narrated by ",
)
_EXPLICIT_MUSIC_CUES = (
    "soundtrack", "album ", "playlist", "artist ", "songs from ",
    "music from ", "songs by ", "music by ", "something by ",
)
_PROBABLE_AUDIOBOOK_PLAY_PREFIXES = (
    "play ", "start ", "queue up ", "cue up ", "put on ", "listen to ",
    "i want to hear ", "i wanna hear ",
)


def _looks_like_probable_audiobook_title(title: str) -> bool:
    normalized = " ".join(str(title).strip().lower().split())
    if not normalized:
        return False
    if any(cue in f" {normalized} " for cue in _AUDIOBOOK_TITLE_CUES):
        return True
    tokens = normalized.split()
    return len(tokens) >= 5 and re.search(r"\band the\b", normalized) is not None and re.search(r"\bof\b", normalized) is not None


def _has_explicit_music_cue(original_text: str) -> bool:
    normalized = f" {' '.join(str(original_text).strip().lower().split())} "
    return any(cue in normalized for cue in _EXPLICIT_MUSIC_CUES)


def _extract_probable_audiobook_title_from_play_request(text: str) -> str | None:
    normalized = " ".join(str(text).strip().lower().split())
    for prefix in _PROBABLE_AUDIOBOOK_PLAY_PREFIXES:
        if not normalized.startswith(prefix):
            continue
        remainder = normalized[len(prefix):].strip()
        if not remainder or remainder.startswith(("some ", "the song ", "song ", "the track ", "track ")):
            return None
        return remainder
    return None


class AudiobookCapability:
    name = "audiobook"
    priority = 79

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if not is_audiobook_request(normalized_text):
            return None
        return CapabilityDecision("audiobook", 0.9, "Matched audiobook request", normalized_text)


class ProbableAudiobookTitleCapability:
    name = "probable_audiobook_title"
    priority = 78.5

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        intent = parse_music_intent(normalized_text)
        if intent is not None and is_generic_title_only_play_intent(intent):
            if _has_explicit_music_cue(getattr(intent, "original_text", normalized_text)):
                return None
            title = str(intent.title or "").strip()
        else:
            title = _extract_probable_audiobook_title_from_play_request(normalized_text)
            if title is None or _has_explicit_music_cue(normalized_text):
                return None
        if not _looks_like_probable_audiobook_title(title):
            return None
        return CapabilityDecision("audiobook", 0.84, "Matched probable audiobook title request", f"play audiobook {title}")


class MusicCapability:
    name = "music"
    priority = 78

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if not is_music_request(normalized_text):
            return None
        return CapabilityDecision("music", 0.88, "Matched music control request", normalized_text)
