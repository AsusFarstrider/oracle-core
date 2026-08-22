from __future__ import annotations

import re
from typing import Any


def build_clarification_prompt(options: list[dict[str, Any]]) -> str:
    spoken_candidates = _select_spoken_candidates(options)
    normalized_titles = [
        " ".join(str(item.get("title", "")).strip().lower().split())
        for item in spoken_candidates
        if isinstance(item, dict)
    ]
    repeated_titles = {title for title in normalized_titles if title and normalized_titles.count(title) > 1}
    spoken = []
    for item in spoken_candidates:
        title = str(item.get("title", "")).strip()
        author = str(item.get("author", "")).strip()
        subtitle = str(item.get("subtitle", "")).strip()
        include_subtitle = bool(subtitle and " ".join(title.lower().split()) in repeated_titles)
        if title and author and include_subtitle:
            spoken.append(f"{title} by {author}, {subtitle}")
        elif title and author:
            spoken.append(f"{title} by {author}")
        elif title and include_subtitle:
            spoken.append(f"{title}, {subtitle}")
        elif title:
            spoken.append(title)
    duplicate_title_prompt = _build_duplicate_title_prompt(spoken_candidates)
    if duplicate_title_prompt:
        return duplicate_title_prompt
    if not spoken:
        return "I found multiple audiobooks. Which one did you want?"
    if len(spoken) == 1:
        return f"Did you want {spoken[0]}?"
    if len(spoken) == 2:
        return f"I found {spoken[0]} and {spoken[1]}. Which one did you want?"
    return f"I found {spoken[0]}, {spoken[1]}, and {spoken[2]}. Which one did you want?"


def _select_spoken_candidates(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [item for item in options[:5] if isinstance(item, dict)]
    if len(candidates) <= 2:
        return candidates

    top_score = _coerce_score(candidates[0].get("score"))
    second_score = _coerce_score(candidates[1].get("score"))
    third_score = _coerce_score(candidates[2].get("score"))
    if top_score is None or second_score is None or third_score is None:
        return candidates[:3]

    spoken = candidates[:2]
    if third_score >= 24 and third_score >= top_score - 45 and third_score >= second_score - 35:
        spoken.append(candidates[2])
    return spoken


def _coerce_score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _build_duplicate_title_prompt(options: list[dict[str, Any]]) -> str | None:
    candidates = [item for item in options if isinstance(item, dict)]
    if len(candidates) < 2:
        return None
    titles = {
        " ".join(_extract_base_title(item).lower().split())
        for item in candidates
        if _extract_base_title(item)
    }
    if len(titles) != 1:
        return None

    anchor_title = _extract_base_title(candidates[0])
    if not anchor_title:
        return None

    authors = {
        " ".join(str(item.get("author", "")).strip().lower().split())
        for item in candidates
        if str(item.get("author", "")).strip()
    }
    all_same_author = len(authors) <= 1
    any_subtitle = any(str(item.get("subtitle", "")).strip() for item in candidates)

    labels = []
    for item in candidates:
        label = _build_duplicate_title_label(
            item,
            all_same_author=all_same_author,
            any_subtitle=any_subtitle,
        )
        if not label:
            return None
        labels.append(label)

    if len(labels) == 1:
        return f"Did you want {anchor_title}: {labels[0]}?"
    if len(labels) == 2:
        return f"I found {anchor_title}: {labels[0]} and {labels[1]}. Which one did you want?"
    return f"I found {anchor_title}: {labels[0]}, {labels[1]}, and {labels[2]}. Which one did you want?"


def _build_duplicate_title_label(
    item: dict[str, Any],
    *,
    all_same_author: bool,
    any_subtitle: bool,
) -> str:
    subtitle = _extract_variant_subtitle(item)
    narrator = str(item.get("narrator", "")).strip()
    author = str(item.get("author", "")).strip()
    if subtitle:
        normalized_subtitle = _normalize_pending_text(subtitle).replace("-", " ")
        if "full cast" in normalized_subtitle:
            return "the full-cast edition"
        if re.fullmatch(r"book\s+\d+", normalized_subtitle):
            return subtitle
        if any(keyword in normalized_subtitle for keyword in ("edition", "unabridged", "abridged")):
            return f"the {subtitle.lower()}"
        return subtitle
    if any_subtitle:
        normalized_narrator = _normalize_pending_text(narrator).replace("-", " ")
        if narrator and "full cast" not in normalized_narrator:
            return f"the {narrator} edition"
        return "the regular edition"
    if author and not all_same_author:
        return f"by {author}"
    return str(item.get("title", "")).strip()


def _extract_base_title(item: dict[str, Any]) -> str:
    title = _clean_optional_text(item.get("title", ""))
    if not title:
        return ""
    title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    title = re.sub(r",\s*book\s+\d+\s*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s+book\s+\d+\s*$", "", title, flags=re.IGNORECASE).strip()
    return title


def _extract_variant_subtitle(item: dict[str, Any]) -> str:
    subtitle = _clean_optional_text(item.get("subtitle", ""))
    if subtitle and not _looks_like_book_marker(subtitle):
        return subtitle

    title = _clean_optional_text(item.get("title", ""))
    if not title:
        return ""

    paren_match = re.search(r"\(([^)]+)\)\s*$", title)
    if paren_match is not None:
        return paren_match.group(1).strip()

    comma_book_match = re.search(r",\s*(book\s+\d+)\s*$", title, flags=re.IGNORECASE)
    if comma_book_match is not None:
        return ""

    trailing_book_match = re.search(r"\b(book\s+\d+)\s*$", title, flags=re.IGNORECASE)
    if trailing_book_match is not None:
        return ""

    return subtitle


def _looks_like_book_marker(value: str) -> bool:
    normalized = _normalize_pending_text(value)
    return bool(re.fullmatch(r"(?:bk\.?|book)\s+\d+", normalized))


def _clean_optional_text(value: Any) -> str:
    text = " ".join(str(value).strip().split())
    if text.lower() in {"", "none", "null"}:
        return ""
    return text


def _match_regular_candidate(normalized: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_text = f" {normalized} "
    if not any(
        cue in normalized_text
        for cue in (
            " regular ",
            " standard ",
            " normal ",
        )
    ):
        return None
    regular_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and _is_regular_edition_candidate(candidate)
    ]
    if len(regular_candidates) == 1:
        return regular_candidates[0]
    return None


def _match_negated_candidate(normalized: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(candidates) != 2 or not normalized.startswith("not "):
        return None
    positive_text = normalized[4:].strip()
    if not positive_text:
        return None
    matched = _match_candidate_against_candidates(positive_text, candidates)
    if matched is None:
        return None
    for candidate in candidates:
        if candidate is not matched:
            return candidate if isinstance(candidate, dict) else None
    return None


def _match_candidate_by_book_marker(normalized: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    marker = _extract_requested_book_marker(normalized)
    if not marker:
        return None
    matched = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and _candidate_has_book_marker(candidate, marker)
    ]
    if len(matched) == 1:
        return matched[0]
    return None


def _extract_requested_book_marker(normalized: str) -> str | None:
    digit_match = re.search(r"\bbook\s+(\d+)\b", normalized)
    if digit_match is not None:
        return f"book {digit_match.group(1)}"
    word_to_number = {
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "ten": "10",
    }
    word_match = re.search(r"\bbook\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b", normalized)
    if word_match is not None:
        return f"book {word_to_number[word_match.group(1)]}"
    return None


def _candidate_has_book_marker(candidate: dict[str, Any], marker: str) -> bool:
    target = marker.strip().lower()
    replacements = {
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "ten": "10",
    }
    for value in (_clean_optional_text(candidate.get("title", "")), _clean_optional_text(candidate.get("subtitle", ""))):
        normalized_value = _normalize_pending_text(value)
        if not normalized_value:
            continue
        normalized_value = re.sub(r"\bbk\.?\s+(\d+)\b", r"book \1", normalized_value)
        for word, number in replacements.items():
            normalized_value = re.sub(rf"\bbook\s+{word}\b", f"book {number}", normalized_value)
        if target in normalized_value:
            return True
    return False


def _is_regular_edition_candidate(candidate: dict[str, Any]) -> bool:
    subtitle = _normalize_pending_text(_extract_variant_subtitle(candidate)).replace("-", " ")
    if not subtitle:
        return True
    return not any(
        keyword in subtitle
        for keyword in ("full cast", "edition", "unabridged", "abridged", "dramati")
    )


def resolve_safe_pronoun_candidate(normalized: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if normalized in {"that one", "this one", "the one", "sounds right"}:
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        return candidate if isinstance(candidate, dict) else None
    if normalized == "the other one":
        if len(candidates) != 2:
            return None
        candidate = candidates[1]
        return candidate if isinstance(candidate, dict) else None
    return None


def analyze_negative_candidate_elimination(normalized: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not normalized.startswith("not "):
        return {"action": "none"}

    if normalized in {"not that one", "not this one", "not the one"}:
        if len(candidates) != 2:
            return {"action": "none"}
        candidate = candidates[1]
        return (
            {"action": "resolve", "candidate": candidate, "normalized_text": normalized}
            if isinstance(candidate, dict)
            else {"action": "none"}
        )

    ordinal_index = _parse_negative_ordinal_index(normalized)
    if ordinal_index is not None:
        if ordinal_index >= len(candidates):
            return {"action": "none"}
        remaining = [
            candidate
            for index, candidate in enumerate(candidates)
            if isinstance(candidate, dict) and index != ordinal_index
        ]
        result = _negative_elimination_result(remaining)
        if result["action"] != "none":
            result["normalized_text"] = normalized
        return result

    exclusion_text = normalized[4:].strip()
    if not exclusion_text:
        return {"action": "none"}
    excluded = _match_candidate_against_candidates(exclusion_text, candidates)
    if excluded is None:
        return {"action": "none"}
    remaining = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate is not excluded
    ]
    result = _negative_elimination_result(remaining)
    if result["action"] != "none":
        result["normalized_text"] = normalized
    return result


def _negative_elimination_result(remaining: list[dict[str, Any]]) -> dict[str, Any]:
    if len(remaining) == 1:
        return {"action": "resolve", "candidate": remaining[0]}
    if len(remaining) >= 2:
        return {"action": "narrow", "remaining": remaining}
    return {"action": "none"}


def _parse_negative_ordinal_index(normalized: str) -> int | None:
    ordinal_map = {
        "not the first one": 0,
        "not first": 0,
        "not first one": 0,
        "not the second one": 1,
        "not second": 1,
        "not second one": 1,
        "not the third one": 2,
        "not third": 2,
        "not third one": 2,
    }
    return ordinal_map.get(normalized)


def analyze_pending_candidate_reply(text: str, pending: dict[str, Any] | None) -> dict[str, Any]:
    if pending is None:
        return {"action": "none"}
    candidates = pending.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return {"action": "none"}

    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return {"action": "none"}

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
        return {"action": "resolve", "candidate": candidate} if isinstance(candidate, dict) else {"action": "none"}

    pronoun_candidate = resolve_safe_pronoun_candidate(normalized, candidates)
    if pronoun_candidate is not None:
        return {"action": "resolve", "candidate": pronoun_candidate}

    regular_candidate = _match_regular_candidate(normalized, candidates)
    if regular_candidate is not None:
        return {"action": "resolve", "candidate": regular_candidate}

    negative_resolution = analyze_negative_candidate_elimination(normalized, candidates)
    if negative_resolution["action"] != "none":
        return negative_resolution

    book_marker_candidate = _match_candidate_by_book_marker(normalized, candidates)
    if book_marker_candidate is not None:
        return {"action": "resolve", "candidate": book_marker_candidate}

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
        if isinstance(candidate, dict):
            return {"action": "resolve", "candidate": candidate}

    scored: list[tuple[int, dict[str, Any]]] = []
    normalized_tokens = {_normalize_pending_text(token) for token in normalized.split(" ") if _normalize_pending_text(token)}
    distinctive_tokens_by_candidate = _build_distinctive_candidate_tokens(candidates)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        score = _score_pending_candidate_match(
            normalized,
            normalized_tokens,
            candidate,
            distinctive_tokens=distinctive_tokens_by_candidate.get(id(candidate), set()),
        )
        if score > 0:
            scored.append((score, candidate))

    if not scored:
        return {"action": "none"}

    scored.sort(key=lambda item: item[0], reverse=True)
    top_score, top_candidate = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else -1
    if top_score >= 70 and second_score < top_score:
        return {"action": "resolve", "candidate": top_candidate}
    if top_score >= 45 and top_score - second_score >= 15:
        return {"action": "resolve", "candidate": top_candidate}
    if top_score >= 24 and top_score - second_score >= 12:
        return {"action": "resolve", "candidate": top_candidate}
    return {"action": "none"}


def looks_like_pending_audiobook_clarification(normalized_text: str, pending: dict[str, Any]) -> bool:
    if analyze_pending_candidate_reply(normalized_text, pending)["action"] != "none":
        return True

    compact = " ".join(normalized_text.strip().lower().split())
    if compact in {
        "",
        "yes",
        "yeah",
        "yep",
        "no",
        "nope",
        "okay",
        "ok",
        "sure",
        "that",
    }:
        return False

    candidates = pending.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return False

    normalized_tokens = {
        _normalize_pending_text(token)
        for token in compact.split(" ")
        if _normalize_pending_text(token)
    }
    if not normalized_tokens:
        return False

    distinctive_tokens_by_candidate = _build_distinctive_candidate_tokens(candidates)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        score = _score_pending_candidate_match(
            compact,
            normalized_tokens,
            candidate,
            distinctive_tokens=distinctive_tokens_by_candidate.get(id(candidate), set()),
        )
        if score >= 45:
            return True
    return False


def match_pending_candidate(text: str, pending: dict[str, Any] | None) -> dict[str, Any] | None:
    outcome = analyze_pending_candidate_reply(text, pending)
    if outcome["action"] != "resolve":
        return None
    candidate = outcome.get("candidate")
    return candidate if isinstance(candidate, dict) else None


def _normalize_pending_text(value: Any) -> str:
    return " ".join(str(value).strip().lower().split())


def _tokenize_pending_text(value: Any) -> set[str]:
    normalized = _normalize_pending_text(value)
    return {
        token
        for token in "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in normalized).split()
        if token and token not in {"the", "a", "an", "and", "of", "book", "audiobook", "one"}
    }


def _build_distinctive_candidate_tokens(candidates: list[dict[str, Any]]) -> dict[int, set[str]]:
    token_counts: dict[str, int] = {}
    candidate_tokens: dict[int, set[str]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        tokens = (
            _tokenize_pending_text(candidate.get("title", ""))
            | _tokenize_pending_text(candidate.get("author", ""))
            | _tokenize_pending_text(candidate.get("subtitle", ""))
            | _tokenize_pending_text(candidate.get("narrator", ""))
            | _tokenize_pending_series(candidate.get("series"))
        )
        candidate_tokens[id(candidate)] = tokens
        for token in tokens:
            token_counts[token] = token_counts.get(token, 0) + 1
    return {
        key: {token for token in tokens if token_counts.get(token, 0) == 1}
        for key, tokens in candidate_tokens.items()
    }


def _score_pending_candidate_match(
    normalized: str,
    normalized_tokens: set[str],
    candidate: dict[str, Any],
    *,
    distinctive_tokens: set[str],
) -> int:
    title = _normalize_pending_text(candidate.get("title", ""))
    author = _normalize_pending_text(candidate.get("author", ""))
    subtitle = _normalize_pending_text(candidate.get("subtitle", ""))
    narrator = _normalize_pending_text(candidate.get("narrator", ""))
    series_tokens = _tokenize_pending_series(candidate.get("series"))

    score = 0
    if title and (normalized == title or title in normalized):
        score += 60
    if author and (normalized == author or author in normalized):
        score += 40
    if subtitle and (normalized == subtitle or subtitle in normalized):
        score += 55
    if narrator and (normalized == narrator or narrator in normalized):
        score += 55

    for value, weight in ((title, 10), (author, 8), (subtitle, 12), (narrator, 12)):
        tokens = _tokenize_pending_text(value)
        overlap = len(tokens & normalized_tokens)
        score += overlap * weight
    if series_tokens:
        score += len(series_tokens & normalized_tokens) * 8

    if subtitle:
        subtitle_tokens = _tokenize_pending_text(subtitle)
        if subtitle_tokens and subtitle_tokens.issubset(normalized_tokens):
            score += 24
    distinctive_overlap = len(distinctive_tokens & normalized_tokens)
    if distinctive_overlap:
        score += distinctive_overlap * 18

    return score


def _tokenize_pending_series(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    tokens: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            continue
        tokens |= _tokenize_pending_text(entry.get("name", ""))
        sequence = str(entry.get("sequence", "")).strip()
        if sequence.isdigit():
            tokens.add(sequence)
    return tokens


def _match_candidate_against_candidates(normalized: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_tokens = {_normalize_pending_text(token) for token in normalized.split(" ") if _normalize_pending_text(token)}
    distinctive_tokens_by_candidate = _build_distinctive_candidate_tokens(candidates)
    scored: list[tuple[int, dict[str, Any]]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        score = _score_pending_candidate_match(
            normalized,
            normalized_tokens,
            candidate,
            distinctive_tokens=distinctive_tokens_by_candidate.get(id(candidate), set()),
        )
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
    if top_score >= 24 and top_score - second_score >= 12:
        return top_candidate
    return None
