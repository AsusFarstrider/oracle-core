from __future__ import annotations

from typing import Any

from .matching import normalize_match_text


def build_clarification_prompt(options: list[dict[str, Any]]) -> str:
    spoken = []
    for item in options[:3]:
        title = str(item.get("title", "")).strip()
        artist = str(item.get("artist", "")).strip()
        album = str(item.get("album", "")).strip()
        if title and artist and album:
            spoken.append(f"{title} by {artist} from {album}")
        elif title and artist:
            spoken.append(f"{title} by {artist}")
        elif title:
            spoken.append(title)
    if not spoken:
        return "I found multiple music matches. Which one did you want?"
    if len(spoken) == 1:
        return f"Did you want {spoken[0]}?"
    if len(spoken) == 2:
        return f"I found {spoken[0]} and {spoken[1]}. Which one did you want?"
    return f"I found {spoken[0]}, {spoken[1]}, and {spoken[2]}. Which one did you want?"


def looks_like_pending_music_clarification(normalized_text: str, pending: dict[str, object]) -> bool:
    if match_pending_music_candidate(normalized_text, pending) is not None:
        return True

    compact = " ".join(normalized_text.strip().lower().split())
    if compact in {
        "the song",
        "that song",
        "the track",
        "that track",
        "the album",
        "that album",
        "the artist",
        "that artist",
        "the playlist",
        "that playlist",
    }:
        return True

    candidates = pending.get("candidates")
    if not isinstance(candidates, list):
        return False
    tokens = {token for token in normalize_match_text(compact).split(" ") if len(token) >= 3}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_tokens = {
            token
            for token in normalize_match_text(
                " ".join(
                    [
                        str(candidate.get("title", "")),
                        str(candidate.get("artist", "")),
                        str(candidate.get("album", "")),
                    ]
                )
            ).split(" ")
            if len(token) >= 3
        }
        if tokens & candidate_tokens:
            return True
    return False


def match_pending_music_candidate(text: str, pending: dict[str, Any] | None) -> dict[str, Any] | None:
    if pending is None:
        return None
    candidates = pending.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None

    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return None

    if len(candidates) == 1 and normalized in {
        "yes",
        "yeah",
        "yep",
        "sure",
        "okay",
        "ok",
        "that one",
        "the one",
        "sounds right",
    }:
        candidate = candidates[0]
        return candidate if isinstance(candidate, dict) else None

    ordinal_map = {
        "first": 0,
        "first one": 0,
        "the first one": 0,
        "second": 1,
        "second one": 1,
        "the second one": 1,
        "third": 2,
        "third one": 2,
        "the third one": 2,
    }
    ordinal_index = ordinal_map.get(normalized)
    if ordinal_index is not None and ordinal_index < len(candidates):
        candidate = candidates[ordinal_index]
        return candidate if isinstance(candidate, dict) else None

    unique_type_candidate = _match_unique_candidate_type(normalized, candidates)
    if unique_type_candidate is not None:
        return unique_type_candidate

    scored: list[tuple[int, dict[str, Any]]] = []
    normalized_tokens = {token for token in normalize_match_text(normalized).split(" ") if token}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        score = _score_pending_candidate_match(normalized, normalized_tokens, candidate)
        if score > 0:
            scored.append((score, candidate))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    top_score, top_candidate = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -1
    if top_score >= 70 and second_score < top_score:
        return top_candidate
    if top_score >= 45 and top_score - second_score >= 15:
        return top_candidate
    return None


def _match_unique_candidate_type(normalized: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    type_aliases = {
        "track": {"track", "song", "the track", "the song", "that track", "that song"},
        "album": {"album", "the album", "that album"},
        "artist": {"artist", "the artist", "that artist"},
        "playlist": {"playlist", "the playlist", "that playlist"},
    }
    for media_type, aliases in type_aliases.items():
        if normalized not in aliases:
            continue
        matches = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and normalize_match_text(candidate.get("media_type", "")) == media_type
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _score_pending_candidate_match(normalized: str, normalized_tokens: set[str], candidate: dict[str, Any]) -> int:
    title = " ".join(str(candidate.get("title", "")).strip().lower().split())
    artist = " ".join(str(candidate.get("artist", "")).strip().lower().split())
    album = " ".join(str(candidate.get("album", "")).strip().lower().split())
    media_type = normalize_match_text(candidate.get("media_type", ""))

    score = 0
    if title and (normalized == title or title in normalized):
        score += 60
    if artist and (normalized == artist or artist in normalized):
        score += 40
    if album and (normalized == album or album in normalized):
        score += 35

    if media_type and media_type in normalized:
        score += 8

    for value, weight in ((title, 10), (artist, 8), (album, 6)):
        tokens = {token for token in normalize_match_text(value).split(" ") if token}
        overlap = len(tokens & normalized_tokens)
        score += overlap * weight
    return score
