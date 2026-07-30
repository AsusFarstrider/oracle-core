from __future__ import annotations

import re
from typing import Any

from .parsing import MusicIntent

_MUSIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "all",
    "the",
    "too",
    "of",
    "feat",
    "featuring",
    "from",
}

_VERSION_MARKERS = {
    "acoustic",
    "bonus",
    "clean",
    "deluxe",
    "demo",
    "edit",
    "explicit",
    "instrumental",
    "karaoke",
    "live",
    "mono",
    "radio",
    "remaster",
    "remastered",
    "remix",
    "session",
    "soundtrack",
    "stereo",
    "version",
}

_ALIAS_SUBSTITUTIONS = {
    "rumours": "rumors",
}

_REVERSE_ALIAS_SUBSTITUTIONS = {target: source for source, target in _ALIAS_SUBSTITUTIONS.items()}


def score_music_candidates(intent: MusicIntent, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    title_target = normalize_match_text(intent.title or intent.album or intent.playlist or intent.artist or "")
    artist_target = normalize_match_text(intent.artist or "")
    album_target = normalize_match_text(intent.album or "")
    title_alias_target = normalize_music_alias_text(intent.title or intent.album or intent.playlist or intent.artist or "")
    album_alias_target = normalize_music_alias_text(intent.album or "")
    title_compact_target = normalize_music_compact_text(intent.title or intent.album or intent.playlist or intent.artist or "")
    artist_compact_target = normalize_music_compact_text(intent.artist or "")
    album_compact_target = normalize_music_compact_text(intent.album or "")
    media_type = intent.media_type
    target_word_count = len([token for token in title_target.split(" ") if token])
    title_tokens = {token for token in title_target.split(" ") if token}
    artist_tokens = {token for token in artist_target.split(" ") if token}
    meaningful_title_tokens = {token for token in title_tokens if token not in _MUSIC_STOPWORDS}
    candidate_title_tokens_list = []
    token_counts: dict[str, int] = {}

    for candidate in candidates:
        candidate_title = normalize_match_text(candidate.get("title", ""))
        candidate_tokens = {token for token in candidate_title.split(" ") if token}
        candidate_title_tokens_list.append(candidate_tokens)
        for token in candidate_tokens:
            token_counts[token] = token_counts.get(token, 0) + 1

    for candidate, candidate_title_tokens in zip(candidates, candidate_title_tokens_list):
        score = 0
        candidate_type = str(candidate.get("type", "")).strip().lower()
        candidate_title = normalize_match_text(candidate.get("title", ""))
        candidate_artist = normalize_match_text(candidate.get("artist", ""))
        candidate_album = normalize_match_text(candidate.get("album", ""))
        candidate_artist_tokens = {token for token in candidate_artist.split(" ") if token}
        candidate_title_alias = normalize_music_alias_text(candidate.get("title", ""))
        candidate_album_alias = normalize_music_alias_text(candidate.get("album", ""))
        candidate_title_compact = normalize_music_compact_text(candidate.get("title", ""))
        candidate_artist_compact = normalize_music_compact_text(candidate.get("artist", ""))
        candidate_album_compact = normalize_music_compact_text(candidate.get("album", ""))

        if media_type is None:
            score += _score_generic_candidate(
                title_target,
                title_alias_target,
                title_compact_target,
                candidate_type,
                candidate_title,
                candidate_title_alias,
                candidate_title_compact,
                target_word_count=target_word_count,
            )
        else:
            if candidate_type == media_type:
                score += 40
            if title_target and candidate_title == title_target:
                score += 40
            elif title_target and title_target in candidate_title:
                score += 20
            elif title_target and candidate_album == title_target:
                score += 18
            if title_alias_target and candidate_title_alias == title_alias_target:
                score += 26
            elif title_alias_target and title_alias_target in candidate_title_alias:
                score += 14
            if title_compact_target and candidate_title_compact == title_compact_target:
                score += 18
        if artist_target and candidate_artist == artist_target:
            score += 20
        elif artist_target and artist_target in candidate_artist:
            score += 10
        if artist_compact_target and candidate_artist_compact == artist_compact_target:
            score += 18
            if media_type == "artist":
                score += 22
        if artist_target and candidate_artist:
            artist_overlap = len(artist_tokens & candidate_artist_tokens)
            if media_type == "track":
                if artist_overlap == 0:
                    score -= 48
                elif artist_overlap < len(artist_tokens):
                    score -= 18
            elif media_type in {"album", "artist"} and artist_overlap == 0:
                score -= 28
        if album_target and candidate_album == album_target:
            score += 22
        elif album_target and album_target in candidate_album:
            score += 12
        if album_alias_target and candidate_album_alias == album_alias_target:
            score += 18
        elif album_alias_target and album_alias_target in candidate_album_alias:
            score += 10
        if album_compact_target and candidate_album_compact == album_compact_target:
            score += 14

        if title_tokens and candidate_title_tokens:
            overlap = len(title_tokens & candidate_title_tokens)
            score += overlap * 8
            if overlap == len(title_tokens):
                score += 10
        if title_alias_target and candidate_title_alias:
            alias_tokens = {token for token in title_alias_target.split(" ") if token}
            candidate_alias_tokens = {token for token in candidate_title_alias.split(" ") if token}
            alias_overlap = len(alias_tokens & candidate_alias_tokens)
            if alias_overlap:
                score += alias_overlap * 9
                if alias_overlap == len(alias_tokens):
                    score += 12
        if artist_tokens and candidate_artist_tokens:
            overlap = len(artist_tokens & candidate_artist_tokens)
            score += overlap * 6
            if overlap == len(artist_tokens):
                score += 6

        if meaningful_title_tokens and len(candidates) >= 3:
            semi_distinctive_title_tokens = {
                token
                for token in meaningful_title_tokens
                if token_counts.get(token, 0) < len(candidates)
            }
            overlap = len(semi_distinctive_title_tokens & candidate_title_tokens)
            missing = len(semi_distinctive_title_tokens - candidate_title_tokens)
            if overlap:
                score += overlap * 10
            if len(semi_distinctive_title_tokens) >= 2 and missing >= 2:
                score -= min(28, missing * 8)

        if candidate_type == "track":
            score += _score_track_variant_preference(candidate, title_alias_target)
        if candidate_type == "album":
            score += _score_album_variant_preference(candidate, album_alias_target or title_alias_target)

        scored.append({**candidate, "score": score})

    scored.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
    return scored


def choose_music_match(scored: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    if not scored:
        return "not_found", []
    if len(scored) == 1 and int(scored[0].get("score", 0)) >= 60:
        return "execute", scored[:1]
    top_score = int(scored[0].get("score", 0))
    second_score = int(scored[1].get("score", 0)) if len(scored) > 1 else -1
    if top_score >= 60 and second_score < 40:
        return "execute", scored[:1]
    if top_score >= 80 and top_score - second_score >= 20:
        return "execute", scored[:1]
    plausible = [item for item in scored[:5] if int(item.get("score", 0)) >= 40]
    if plausible:
        return "clarify", plausible
    return "not_found", []


def dedupe_music_candidates(
    candidates: list[dict[str, Any]],
    *,
    preserve_album_variants: bool = False,
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    canonical_seen: set[str] = set()
    soft_seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        canonical_key = _canonical_candidate_key(candidate)
        if canonical_key in canonical_seen:
            continue
        canonical_seen.add(canonical_key)
        soft_key = _soft_duplicate_track_key(candidate, preserve_album_variants=preserve_album_variants)
        if soft_key is not None:
            if soft_key in soft_seen:
                continue
            soft_seen.add(soft_key)
        deduped.append(candidate)
    return deduped


def build_search_queries(intent: MusicIntent) -> list[str]:
    primary = ""
    if intent.media_type == "artist":
        primary = intent.artist or ""
    elif intent.media_type == "album":
        primary = intent.album or ""
    elif intent.media_type == "playlist":
        primary = intent.playlist or ""
    else:
        primary = intent.title or ""

    queries: list[str] = []
    if primary.strip():
        queries.append(primary.strip())
    if intent.title and intent.album:
        queries.append(f"{intent.title} {intent.album}".strip())
    artist_variants = build_query_variants(intent.artist or "") if intent.artist else []
    if intent.media_type == "album" and intent.album and intent.artist:
        queries.append(f"{intent.album} {intent.artist}".strip())
        queries.append(f"{intent.artist} {intent.album}".strip())
        for artist_variant in artist_variants:
            if artist_variant:
                queries.append(f"{intent.album} {artist_variant}".strip())
                queries.append(f"{artist_variant} {intent.album}".strip())
    if intent.media_type == "track" and intent.title and intent.artist:
        queries.append(f"{intent.title} {intent.artist}".strip())
        queries.append(f"{intent.artist} {intent.title}".strip())
        for artist_variant in artist_variants:
            if artist_variant:
                queries.append(f"{intent.title} {artist_variant}".strip())
                queries.append(f"{artist_variant} {intent.title}".strip())
    for heuristic in _build_title_only_music_heuristic_queries(intent):
        if heuristic:
            queries.append(heuristic)
    for variant in build_query_variants(primary):
        if variant:
            queries.append(variant)
    if intent.media_type == "track" and intent.title and intent.artist:
        for variant in build_query_variants(f"{intent.title} {intent.artist}"):
            if variant:
                queries.append(variant)
        for variant in build_query_variants(f"{intent.artist} {intent.title}"):
            if variant:
                queries.append(variant)
    if intent.media_type == "album" and intent.album and intent.artist:
        for variant in build_query_variants(f"{intent.album} {intent.artist}"):
            if variant:
                queries.append(variant)
        for variant in build_query_variants(f"{intent.artist} {intent.album}"):
            if variant:
                queries.append(variant)

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    return deduped


def _build_title_only_music_heuristic_queries(intent: MusicIntent) -> list[str]:
    if intent.media_type is not None:
        return []
    if intent.artist or intent.album or intent.playlist or intent.genre:
        return []

    title = " ".join(str(intent.title or "").strip().split())
    if not title:
        return []

    queries: list[str] = []
    normalized_tokens = [token for token in normalize_match_text(title).split(" ") if token]
    if len(normalized_tokens) >= 3:
        leading_artist = normalized_tokens[:2]
        remainder = normalized_tokens[2:]
        if (
            len(remainder) >= 1
            and all(token.isalpha() for token in leading_artist)
            and not any(token in _MUSIC_STOPWORDS for token in leading_artist)
        ):
            queries.append(" ".join(remainder + leading_artist))

    if re.search(r"\btaylor'?s?\s+version\b", title, flags=re.IGNORECASE):
        stripped = re.sub(r"\btaylor'?s?\s+version\b", "", title, flags=re.IGNORECASE).strip(" ,-")
        if stripped:
            queries.append(stripped)
            queries.append(f"{stripped} Taylor Swift")

    return queries


def build_query_variants(value: str) -> list[str]:
    queries: list[str] = []
    cleaned = str(value).strip()
    if cleaned:
        queries.append(cleaned)

    normalized = normalize_match_text(cleaned)
    if normalized and normalized != cleaned.lower():
        queries.append(normalized)
    alias_normalized = normalize_music_alias_text(cleaned)
    if alias_normalized and alias_normalized not in {cleaned.lower(), normalized}:
        queries.append(alias_normalized)
    compact = normalize_music_compact_text(cleaned)
    if compact and compact not in {cleaned.lower(), normalized, alias_normalized}:
        queries.append(compact)
    punctuated = _build_punctuated_artist_alias(cleaned)
    if punctuated and punctuated.lower() not in {query.lower() for query in queries}:
        queries.append(punctuated)
    for artist_alias in _build_spoken_artist_aliases(cleaned):
        if artist_alias and artist_alias.lower() not in {query.lower() for query in queries}:
            queries.append(artist_alias)
    for spelling_alias in _build_spelling_aliases(cleaned):
        if spelling_alias and spelling_alias.lower() not in {query.lower() for query in queries}:
            queries.append(spelling_alias)

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    return deduped


def _build_punctuated_artist_alias(value: Any) -> str:
    tokens = [token for token in normalize_match_text(value).split(" ") if token]
    if len(tokens) != 2:
        return ""
    if not all(token.isalpha() and 1 <= len(token) <= 2 for token in tokens):
        return ""
    return "/".join(token.upper() for token in tokens)


def _build_spoken_artist_aliases(value: Any) -> list[str]:
    cleaned = " ".join(str(value).strip().split())
    normalized_tokens = [token for token in normalize_match_text(cleaned).split(" ") if token]
    if "and" not in normalized_tokens:
        return []

    aliases: list[str] = []
    ampersand = re.sub(r"\band\b", "&", cleaned, flags=re.IGNORECASE)
    if ampersand != cleaned:
        aliases.append(ampersand)

    n_apostrophe = re.sub(r"\band\b", "n’", cleaned, flags=re.IGNORECASE)
    if n_apostrophe != cleaned:
        aliases.append(n_apostrophe)
    straight_apostrophe = re.sub(r"\band\b", "n'", cleaned, flags=re.IGNORECASE)
    if straight_apostrophe != cleaned:
        aliases.append(straight_apostrophe)

    if normalized_tokens.count("and") == 1 and len(normalized_tokens) == 4 and normalized_tokens[2] == "and":
        aliases.append(f"{normalized_tokens[0]}, {normalized_tokens[1]} & {normalized_tokens[3]}")

    if cleaned.lower().startswith("the "):
        aliases.append(cleaned[4:].strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        key = alias.lower()
        if not alias or key in seen:
            continue
        seen.add(key)
        deduped.append(alias)
    return deduped


def _build_spelling_aliases(value: Any) -> list[str]:
    normalized = normalize_match_text(value)
    if not normalized:
        return []

    aliases: list[str] = []
    alias_value = normalized
    changed = False
    for source, target in _REVERSE_ALIAS_SUBSTITUTIONS.items():
        if source in alias_value:
            alias_value = alias_value.replace(source, target)
            changed = True
    if changed:
        aliases.append(alias_value)
    return aliases


def normalize_match_text(value: Any) -> str:
    text = " ".join(str(value).strip().lower().split())
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text


def normalize_music_alias_text(value: Any) -> str:
    text = normalize_match_text(value)
    if not text:
        return ""
    for source, target in _ALIAS_SUBSTITUTIONS.items():
        text = text.replace(source, target)
    text = re.sub(r"\bfeat(?:uring)?\b.*$", "", text).strip()
    text = re.sub(r"\bfrom\s+the\s+[^ ]+\s+(?:film|movie|soundtrack)\b", "", text).strip()
    text = re.sub(r"\bfrom\s+[^ ]+\s+(?:film|movie|soundtrack)\b", "", text).strip()
    tokens = [token for token in text.split(" ") if token]
    cleaned_tokens: list[str] = []
    for token in tokens:
        if token in _VERSION_MARKERS:
            continue
        cleaned_tokens.append(token)
    return " ".join(cleaned_tokens).strip()


def normalize_music_compact_text(value: Any) -> str:
    text = normalize_music_alias_text(value)
    if not text:
        return ""
    return "".join(token for token in text.split(" ") if token)


def _canonical_candidate_key(candidate: dict[str, Any]) -> str:
    candidate_type = normalize_match_text(candidate.get("type", ""))
    title = normalize_match_text(candidate.get("title", ""))
    artist = normalize_match_text(candidate.get("artist", ""))
    album = normalize_match_text(candidate.get("album", ""))
    if candidate_type == "track":
        return f"{candidate_type}|{title}|{artist}|{album}"
    if candidate_type == "album":
        return f"{candidate_type}|{title}|{artist}"
    return f"{candidate_type}|{title}|{artist}|{album}"


def _soft_duplicate_track_key(
    candidate: dict[str, Any],
    *,
    preserve_album_variants: bool,
) -> str | None:
    if preserve_album_variants:
        return None

    candidate_type = normalize_match_text(candidate.get("type", ""))
    if candidate_type != "track":
        return None

    title = normalize_match_text(candidate.get("title", ""))
    artist = normalize_match_text(candidate.get("artist", ""))
    album = normalize_match_text(candidate.get("album", ""))
    if not title or not artist:
        return None
    if _looks_like_distinct_track_version(title) or _looks_like_distinct_track_version(album):
        return None
    return f"{candidate_type}|{title}|{artist}"


def _looks_like_distinct_track_version(value: str) -> bool:
    markers = {
        "live",
        "acoustic",
        "instrumental",
        "karaoke",
        "remix",
        "remaster",
        "demo",
        "mono",
        "stereo",
        "edit",
        "version",
        "take",
        "session",
        "rehearsal",
    }
    tokens = {token for token in normalize_match_text(value).split(" ") if token}
    return bool(tokens & markers)


def _score_generic_candidate(
    title_target: str,
    title_alias_target: str,
    title_compact_target: str,
    candidate_type: str,
    candidate_title: str,
    candidate_title_alias: str,
    candidate_title_compact: str,
    *,
    target_word_count: int,
) -> int:
    if not title_target or not candidate_title:
        return 0

    exact_scores = {
        "track": 70,
        "album": 68,
        "playlist": 68,
        "artist": 64,
    }
    prefix_scores = {
        "track": 34,
        "album": 62,
        "playlist": 41,
        "artist": 30,
    }
    contains_scores = {
        "track": 18,
        "album": 26,
        "playlist": 24,
        "artist": 22,
    }

    if candidate_title == title_target:
        score = exact_scores.get(candidate_type, 0)
        if candidate_type == "artist" and target_word_count >= 2:
            score += 30
        return score
    if title_alias_target and candidate_title_alias == title_alias_target:
        return exact_scores.get(candidate_type, 0) + 18
    if title_compact_target and candidate_title_compact == title_compact_target:
        return exact_scores.get(candidate_type, 0) + 16
    if candidate_title.startswith(title_target):
        return prefix_scores.get(candidate_type, 0)
    if title_alias_target and candidate_title_alias.startswith(title_alias_target):
        return prefix_scores.get(candidate_type, 0) + 10
    if title_target in candidate_title:
        return contains_scores.get(candidate_type, 0)
    if title_alias_target and title_alias_target in candidate_title_alias:
        return contains_scores.get(candidate_type, 0) + 8
    return 0


def _score_track_variant_preference(candidate: dict[str, Any], title_alias_target: str) -> int:
    title = normalize_match_text(candidate.get("title", ""))
    if not title:
        return 0
    alias_title = normalize_music_alias_text(candidate.get("title", ""))
    raw_tokens = {token for token in title.split(" ") if token}
    if title_alias_target and alias_title == title_alias_target and raw_tokens & _VERSION_MARKERS:
        return -8
    if "remix" in raw_tokens or "karaoke" in raw_tokens:
        return -6
    return 0


def _score_album_variant_preference(candidate: dict[str, Any], album_alias_target: str) -> int:
    title = normalize_match_text(candidate.get("title", ""))
    if not title:
        return 0
    alias_title = normalize_music_alias_text(candidate.get("title", ""))
    raw_tokens = {token for token in title.split(" ") if token}
    if album_alias_target and alias_title == album_alias_target and raw_tokens & {"deluxe", "bonus", "edition", "remaster", "remastered"}:
        return -22
    if album_alias_target and alias_title.startswith(album_alias_target):
        target_tokens = {token for token in album_alias_target.split(" ") if token}
        extra_tokens = raw_tokens - target_tokens
        if extra_tokens and any(token.isdigit() for token in extra_tokens) and not any(
            token.isdigit() for token in target_tokens
        ):
            return -12
    return 0
