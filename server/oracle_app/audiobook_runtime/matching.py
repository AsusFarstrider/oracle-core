from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from .parsing import AudiobookIntent

_MATCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "of",
    "book",
    "audiobook",
    "edition",
}


def find_audiobook_series_entry(
    series: str,
    ordinal: int,
    *,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not series or ordinal <= 0:
        return None
    matches: list[dict[str, Any]] = []
    normalized_series = _normalize_match_text(series)
    for candidate in candidates:
        candidate_ordinal = _extract_book_number(candidate, normalized_series=normalized_series)
        if candidate_ordinal != ordinal:
            continue
        title = str(candidate.get("title", "")).strip()
        subtitle = str(candidate.get("subtitle", "")).strip()
        searchable = _normalize_match_text(" ".join(part for part in (title, subtitle) if part))
        series_score = 0
        if normalized_series and normalized_series in searchable:
            series_score += 40
        if "(" not in title:
            series_score += 12
        if "full-cast" not in searchable and "dramati" not in searchable:
            series_score += 8
        matches.append({**candidate, "series_score": series_score})

    if not matches:
        return None

    matches.sort(
        key=lambda item: (
            int(item.get("series_score", 0)),
            -len(str(item.get("title", ""))),
        ),
        reverse=True,
    )
    return matches[0]


def score_audiobook_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    narrator_preference: str | None = None,
) -> list[dict[str, Any]]:
    normalized_query = _normalize_match_text(query)
    normalized_narrator_preference = _normalize_match_text(narrator_preference)
    query_words = len([token for token in normalized_query.split(" ") if token])
    query_tokens = set(token for token in normalized_query.split(" ") if token)
    meaningful_query_tokens = {token for token in query_tokens if token not in _MATCH_STOPWORDS}
    title_token_sets = []
    title_token_counts: dict[str, int] = {}
    for candidate in candidates:
        title = _normalize_match_text(candidate.get("title", ""))
        title_tokens = set(token for token in title.split(" ") if token)
        title_token_sets.append(title_tokens)
        for token in title_tokens:
            title_token_counts[token] = title_token_counts.get(token, 0) + 1
    scored: list[dict[str, Any]] = []
    for candidate, title_tokens in zip(candidates, title_token_sets):
        title = _normalize_match_text(candidate.get("title", ""))
        author = _normalize_match_text(candidate.get("author", ""))
        narrator = _normalize_match_text(candidate.get("narrator", ""))
        subtitle = _normalize_match_text(candidate.get("subtitle", ""))
        score = 0
        if title == normalized_query:
            score += 90
        elif title.startswith(normalized_query):
            score += 70
        elif normalized_query and normalized_query in title:
            score += 50
        elif subtitle and normalized_query and normalized_query in subtitle:
            score += 25

        similarity = SequenceMatcher(None, normalized_query, title).ratio() if normalized_query and title else 0.0
        if similarity >= 0.92:
            score += 42
        elif similarity >= 0.84:
            score += 30
        elif similarity >= 0.74:
            score += 18

        if author and normalized_query and normalized_query in author:
            score += 15
        if normalized_narrator_preference:
            if narrator and (
                narrator == normalized_narrator_preference
                or normalized_narrator_preference in narrator
                or narrator in normalized_narrator_preference
            ):
                score += 85
            elif narrator:
                score -= 20
        if query_words >= 2 and title:
            overlap = len(query_tokens & title_tokens)
            score += overlap * 10
            if query_tokens and overlap:
                coverage = overlap / max(1, len(query_tokens))
                score += int(coverage * 24)
            if query_words >= 3 and overlap >= max(3, query_words - 1):
                score += 24
            if query_tokens and query_tokens.issubset(title_tokens):
                score += 16

        if meaningful_query_tokens and len(candidates) >= 3:
            semi_distinctive_query_tokens = {
                token
                for token in meaningful_query_tokens
                if title_token_counts.get(token, 0) < len(candidates)
            }
            semi_distinctive_overlap = len(semi_distinctive_query_tokens & title_tokens)
            missing_semi_distinctive = len(semi_distinctive_query_tokens - title_tokens)
            if semi_distinctive_overlap:
                score += semi_distinctive_overlap * 12
            if len(semi_distinctive_query_tokens) >= 2 and missing_semi_distinctive >= 2:
                score -= min(36, missing_semi_distinctive * 12)
        scored.append({**candidate, "score": score})

    scored.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
    return scored


def choose_audiobook_match(scored: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    if not scored:
        return "not_found", []
    if len(scored) == 1 and int(scored[0].get("score", 0)) >= 48:
        return "execute", scored[:1]
    top_score = int(scored[0].get("score", 0))
    second_score = int(scored[1].get("score", 0)) if len(scored) > 1 else -1
    if top_score >= 58 and second_score < 42:
        return "execute", scored[:1]
    plausible = [item for item in scored[:5] if int(item.get("score", 0)) >= 24]
    if len(plausible) >= 3:
        focused = plausible[:2]
        focused_second_score = int(focused[1].get("score", 0))
        for item in plausible[2:]:
            score = int(item.get("score", 0))
            if score >= top_score - 45 and score >= focused_second_score - 35:
                focused.append(item)
        plausible = focused
    if plausible:
        return "clarify", plausible
    return "not_found", []


def build_search_queries(query: str, narrator_preference: str | None = None) -> list[str]:
    normalized = " ".join(str(query).strip().split())
    compact = _normalize_match_text(query)
    normalized_narrator = " ".join(str(narrator_preference or "").strip().split())
    compact_narrator = _normalize_match_text(narrator_preference or "")
    queries: list[str] = []
    if normalized:
        queries.append(normalized)
        if normalized_narrator:
            queries.append(f"{normalized} {normalized_narrator}")
    if compact and compact not in {normalized.lower(), normalized}:
        queries.append(compact)
        if compact_narrator:
            queries.append(f"{compact} {compact_narrator}")
    article_stripped = re.sub(r"^(?:a|an|the)\s+", "", normalized, flags=re.IGNORECASE).strip()
    if article_stripped and article_stripped.lower() not in {normalized.lower(), compact}:
        queries.append(article_stripped)
        if normalized_narrator:
            queries.append(f"{article_stripped} {normalized_narrator}")

    words = [token for token in compact.split(" ") if token]
    if len(words) >= 2:
        queries.append(" ".join(words[:2]))
    if len(words) >= 3:
        queries.append(" ".join(words[:3]))
    if len(words) >= 4:
        queries.append(" ".join(words[-3:]))
    if words:
        queries.append(words[0])
    meaningful_words = [token for token in words if token not in _MATCH_STOPWORDS]
    if meaningful_words:
        strongest_token = max(meaningful_words, key=len)
        if len(strongest_token) >= 5:
            queries.append(strongest_token)
        trailing_token = meaningful_words[-1]
        if len(trailing_token) >= 5:
            queries.append(trailing_token)
    if len(meaningful_words) >= 2:
        queries.append(" ".join(meaningful_words[-2:]))
    if compact_narrator:
        queries.append(compact_narrator)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in queries:
        cleaned = " ".join(item.strip().split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _extract_book_number(candidate: dict[str, Any], *, normalized_series: str | None = None) -> int | None:
    series_entries = candidate.get("series")
    if isinstance(series_entries, list):
        for entry in series_entries:
            if not isinstance(entry, dict):
                continue
            name = _normalize_match_text(entry.get("name", ""))
            sequence = str(entry.get("sequence", "")).strip()
            if normalized_series and name and name != normalized_series:
                continue
            if not re.fullmatch(r"\d+", sequence):
                continue
            parsed_sequence = int(sequence)
            if parsed_sequence > 0:
                return parsed_sequence
    text = " ".join(
        str(candidate.get(key, "")).strip().lower()
        for key in ("title", "subtitle")
        if str(candidate.get(key, "")).strip()
    )
    if not text:
        return None
    match = re.search(r"\bbook\s+(\d+)\b", text)
    if match is not None:
        return int(match.group(1))
    match = re.search(r"\b#\s*(\d+)\b", text)
    if match is not None:
        return int(match.group(1))
    return None


def _normalize_match_text(value: Any) -> str:
    text = " ".join(str(value).strip().lower().split())
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    text = re.sub(r"^(?:a|an|the)\s+", "", text)
    return text.strip()
