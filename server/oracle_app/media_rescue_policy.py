from __future__ import annotations

import re
from typing import Any

_ULTRA_GENERIC_SINGLE_WORD_TITLES = {"one", "hello", "stay"}


def should_try_audiobook_fallback(
    intent,
    decision: str,
    selected: list[dict[str, Any]],
) -> bool:
    if getattr(intent, "intent", None) != "play":
        return False
    if value_present(getattr(intent, "media_type", None)):
        return False
    if any(
        value_present(getattr(intent, field, None))
        for field in ("artist", "album", "playlist", "genre")
    ):
        return False
    if not value_present(getattr(intent, "title", "")):
        return False
    return decision == "not_found"


def audiobook_is_clearly_stronger_than_music(
    *,
    requested_title: str,
    top_music: dict[str, Any],
    top_audiobook: dict[str, Any],
) -> bool:
    normalized_request = normalize_simple_match_text(requested_title)
    normalized_music_title = normalize_simple_match_text(top_music.get("title", ""))
    audiobook_score = int(top_audiobook.get("score", 0))
    music_score = int(top_music.get("score", 0))
    advantage = audiobook_score - music_score

    if normalized_music_title == normalized_request:
        return audiobook_score >= 80 and advantage >= 25
    return audiobook_score >= 70 and advantage >= 15


def should_downgrade_weak_single_music_clarification(
    intent,
    decision: str,
    selected: list[dict[str, Any]],
) -> bool:
    if decision != "clarify":
        return False
    if len(selected) != 1:
        return False
    if not is_generic_title_only_play_intent(intent):
        return False
    top_score = int(selected[0].get("score", 0))
    return top_score < 55


def apply_ultra_generic_single_word_music_guard(
    intent,
    decision: str,
    scored: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    if decision != "execute":
        return decision, selected
    if not selected:
        return decision, selected
    if not is_ultra_generic_single_word_music_title(intent):
        return decision, selected

    top_score = int(selected[0].get("score", 0))
    second_score = int(scored[1].get("score", 0)) if len(scored) > 1 else -1
    if top_score >= 140 and top_score - second_score >= 35:
        return decision, selected

    plausible = [item for item in scored[:5] if int(item.get("score", 0)) >= 40]
    plausible = _trim_ultra_generic_single_word_clarification_candidates(intent, plausible)
    if plausible:
        return "clarify", plausible
    return "not_found", []


def is_generic_title_only_play_intent(intent) -> bool:
    if getattr(intent, "intent", None) != "play":
        return False
    media_type = getattr(intent, "media_type", None)
    if value_present(media_type) and music_media_type_was_explicit(intent):
        return False
    if any(
        value_present(getattr(intent, field, None))
        for field in ("artist", "album", "playlist", "genre")
    ):
        return False
    return value_present(getattr(intent, "title", ""))


def is_ultra_generic_single_word_music_title(intent) -> bool:
    if not is_generic_title_only_play_intent(intent):
        return False
    title = normalize_simple_match_text(getattr(intent, "title", ""))
    if not title:
        return False
    tokens = [token for token in title.split(" ") if token]
    return len(tokens) == 1 and tokens[0] in _ULTRA_GENERIC_SINGLE_WORD_TITLES


def _trim_ultra_generic_single_word_clarification_candidates(
    intent,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requested_title = normalize_simple_match_text(getattr(intent, "title", ""))
    if not requested_title:
        return []
    trimmed = [
        item
        for item in candidates
        if normalize_simple_match_text(item.get("title", "")) == requested_title
    ]
    return trimmed[:5]


def trim_ultra_generic_single_word_clarification_candidates(
    intent,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not is_ultra_generic_single_word_music_title(intent):
        return list(candidates)
    return _trim_ultra_generic_single_word_clarification_candidates(intent, candidates)


def normalize_simple_match_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def music_media_type_was_explicit(intent) -> bool:
    original_text = str(getattr(intent, "original_text", "") or "").strip().lower()
    media_type = normalized_optional_text(getattr(intent, "media_type", None))
    if media_type == "track":
        return any(token in original_text for token in (" track ", " song ", " songs "))
    if media_type == "album":
        return (
            "album " in original_text
            or "soundtrack" in original_text
            or "music from " in original_text
            or "songs from " in original_text
        )
    if media_type == "artist":
        return any(token in original_text for token in ("artist ", "songs by ", "music by ", "something by "))
    if media_type == "playlist":
        return "playlist" in original_text
    return False


def normalized_optional_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "none", "null"}:
        return ""
    return text


def value_present(value: Any) -> bool:
    return bool(normalized_optional_text(value))
