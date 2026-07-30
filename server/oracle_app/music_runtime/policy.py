from __future__ import annotations

from typing import Any, Callable

from oracle_app.media_rescue_policy import (
    apply_ultra_generic_single_word_music_guard as apply_ultra_generic_single_word_music_guard_policy,
    audiobook_is_clearly_stronger_than_music as audiobook_is_clearly_stronger_than_music_policy,
    is_generic_title_only_play_intent as is_generic_title_only_play_intent_policy,
    is_ultra_generic_single_word_music_title as is_ultra_generic_single_word_music_title_policy,
    music_media_type_was_explicit as music_media_type_was_explicit_policy,
    normalize_simple_match_text as normalize_simple_match_text_policy,
    normalized_optional_text as normalized_optional_text_policy,
    should_downgrade_weak_single_music_clarification as should_downgrade_weak_single_music_clarification_policy,
    should_try_audiobook_fallback as should_try_audiobook_fallback_policy,
    trim_ultra_generic_single_word_clarification_candidates as trim_ultra_generic_single_word_clarification_candidates_policy,
    value_present as value_present_policy,
)
from oracle_app.music_runtime.selection import music_pending_option
from oracle_app.schemas import DispatchPlan


def try_audiobook_fallback(
    dispatch: DispatchPlan,
    *,
    intent,
    decision: str,
    selected: list[dict[str, Any]],
    execute_audiobook: Callable[[DispatchPlan], DispatchPlan],
) -> DispatchPlan | None:
    if not should_try_audiobook_fallback(intent, decision, selected):
        return None

    title = str(intent.title or "").strip()
    if not title:
        return None

    fallback_text = f"play audiobook {title}"
    fallback_payload = dict(dispatch.payload)
    fallback_payload["text"] = fallback_text
    fallback_payload["normalized_text"] = fallback_text
    fallback_dispatch = DispatchPlan(
        target="audiobook",
        hook="audiobook.execute",
        payload=fallback_payload,
        status="planned",
    )
    result = execute_audiobook(fallback_dispatch)
    if result.status in {"executed", "pending_clarification"}:
        return result
    return None


def try_ollama_best_guess(
    dispatch: DispatchPlan,
    *,
    normalized: str,
    intent,
    source: str | None,
    session_id: str | None,
    music_candidates: list[dict[str, Any]],
    load_audiobook_guess_candidates: Callable[[str], list[dict[str, Any]]],
    choose_best_guess_with_ollama: Callable[[str, list[dict[str, Any]]], dict[str, Any] | None],
    store_pending_music_request: Callable[[str | None, str | None, dict[str, Any]], bool],
) -> DispatchPlan | None:
    if not should_try_audiobook_fallback(intent, "not_found", []):
        return None

    title = str(intent.title or "").strip()
    if not title:
        return None

    audiobook_candidates = load_audiobook_guess_candidates(title)
    guess_candidates = build_best_guess_candidates(music_candidates, audiobook_candidates)
    if not guess_candidates:
        return None

    guess = choose_best_guess_with_ollama(normalized, guess_candidates)
    if guess is None:
        guess = choose_best_guess_fallback(guess_candidates)
    if guess is None:
        return None

    stored = store_pending_music_request(
        source,
        session_id,
        {
            "intent": intent.to_payload(),
            "candidates": [guess],
        },
    )
    if not stored:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "play",
            "intent": intent.to_payload(),
            "error": "pending_state_requires_context",
            "detail": "Pending music clarification requires both source and session_id.",
        }
        return dispatch
    dispatch.status = "pending_clarification"
    dispatch.result = {
        "action": "play",
        "intent": intent.to_payload(),
        "prompt": build_best_guess_prompt(guess),
        "candidates": [guess],
    }
    return dispatch


def try_prefer_strong_audiobook_match(
    dispatch: DispatchPlan,
    *,
    intent,
    decision: str,
    selected: list[dict[str, Any]],
    load_audiobook_guess_candidates: Callable[[str], list[dict[str, Any]]],
    execute_audiobook: Callable[[DispatchPlan], DispatchPlan],
) -> DispatchPlan | None:
    if decision not in {"execute", "clarify"}:
        return None
    if not selected:
        return None
    if not is_generic_title_only_play_intent(intent):
        return None

    requested_title = str(getattr(intent, "title", "") or "").strip()
    top_music = selected[0]
    music_title = str(top_music.get("title", "")).strip()
    if not requested_title or not music_title:
        return None

    audiobook_candidates = load_audiobook_guess_candidates(requested_title)
    if not audiobook_candidates:
        return None
    top_audiobook = audiobook_candidates[0]
    if not audiobook_is_clearly_stronger_than_music(
        requested_title=requested_title,
        top_music=top_music,
        top_audiobook=top_audiobook,
    ):
        return None

    fallback_text = f"play audiobook {requested_title}"
    fallback_payload = dict(dispatch.payload)
    fallback_payload["text"] = fallback_text
    fallback_payload["normalized_text"] = fallback_text
    fallback_dispatch = DispatchPlan(
        target="audiobook",
        hook="audiobook.execute",
        payload=fallback_payload,
        status="planned",
    )
    result = execute_audiobook(fallback_dispatch)
    if result.status in {"executed", "pending_clarification"}:
        return result
    return None


def should_try_audiobook_fallback(
    intent,
    decision: str,
    selected: list[dict[str, Any]],
) -> bool:
    return should_try_audiobook_fallback_policy(intent, decision, selected)


def audiobook_is_clearly_stronger_than_music(
    *,
    requested_title: str,
    top_music: dict[str, Any],
    top_audiobook: dict[str, Any],
) -> bool:
    return audiobook_is_clearly_stronger_than_music_policy(
        requested_title=requested_title,
        top_music=top_music,
        top_audiobook=top_audiobook,
    )


def should_downgrade_weak_single_music_clarification(
    intent,
    decision: str,
    selected: list[dict[str, Any]],
) -> bool:
    return should_downgrade_weak_single_music_clarification_policy(intent, decision, selected)


def apply_ultra_generic_single_word_music_guard(
    intent,
    decision: str,
    scored: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    return apply_ultra_generic_single_word_music_guard_policy(intent, decision, scored, selected)


def is_generic_title_only_play_intent(intent) -> bool:
    return is_generic_title_only_play_intent_policy(intent)


def is_ultra_generic_single_word_music_title(intent) -> bool:
    return is_ultra_generic_single_word_music_title_policy(intent)


def trim_ultra_generic_single_word_clarification_candidates(intent, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return trim_ultra_generic_single_word_clarification_candidates_policy(intent, candidates)


def normalize_simple_match_text(text: str) -> str:
    return normalize_simple_match_text_policy(text)


def music_media_type_was_explicit(intent) -> bool:
    return music_media_type_was_explicit_policy(intent)


def load_audiobook_guess_candidates(
    title: str,
    search_audiobooks: Callable[[str], list[dict[str, Any]]],
    score_audiobook_candidates: Callable[[str, list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    try:
        candidates = search_audiobooks(title)
    except Exception:
        return []
    scored = score_audiobook_candidates(title, candidates)
    result: list[dict[str, Any]] = []
    for item in scored[:5]:
        result.append(
            {
                "route_target": "audiobook",
                "media_type": "audiobook",
                "library_item_id": item.get("library_item_id"),
                "title": item.get("title"),
                "author": item.get("author"),
                "score": item.get("score"),
            }
        )
    return result


def build_best_guess_candidates(
    music_candidates: list[dict[str, Any]],
    audiobook_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in music_candidates[:5]:
        if not isinstance(item, dict):
            continue
        candidate = music_pending_option(item)
        candidate["route_target"] = "music"
        candidates.append(candidate)
    candidates.extend(audiobook_candidates[:5])
    return candidates


def choose_best_guess_fallback(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda item: int(item.get("score", 0)), reverse=True)
    top = ranked[0]
    top_score = int(top.get("score", 0))
    second_score = int(ranked[1].get("score", 0)) if len(ranked) > 1 else -1
    if len(ranked) == 1 and top_score >= 20:
        return top
    if top_score >= 20 and top_score - second_score >= 15:
        return top
    return None


def normalized_optional_text(value: Any) -> str:
    return normalized_optional_text_policy(value)


def value_present(value: Any) -> bool:
    return value_present_policy(value)


def build_best_guess_prompt(candidate: dict[str, Any]) -> str:
    route_target = str(candidate.get("route_target", "")).strip().lower()
    title = str(candidate.get("title", "")).strip()
    artist = str(candidate.get("artist", "")).strip()
    album = str(candidate.get("album", "")).strip()
    author = str(candidate.get("author", "")).strip()

    if route_target == "audiobook":
        if title and author:
            return f"I couldn't find a strong direct match. Did you mean the audiobook {title} by {author}?"
        if title:
            return f"I couldn't find a strong direct match. Did you mean the audiobook {title}?"
    if title and artist and album:
        return f"I couldn't find a strong direct match. Did you mean {title} by {artist} from {album}?"
    if title and artist:
        return f"I couldn't find a strong direct match. Did you mean {title} by {artist}?"
    if title:
        return f"I couldn't find a strong direct match. Did you mean {title}?"
    return "I couldn't find a strong direct match. Is this what you meant?"


def resolve_alternate_music_intent(normalized: str, intent, parsed_intent, resolve_with_ollama: Callable[[str], Any]):
    some_artist_intent = _build_some_artist_intent(parsed_intent)
    if some_artist_intent is not None and not music_intents_equivalent(some_artist_intent, intent):
        return some_artist_intent

    trailing_artist_intent = _build_trailing_short_artist_intent(parsed_intent)
    if trailing_artist_intent is not None and not music_intents_equivalent(trailing_artist_intent, intent):
        return trailing_artist_intent

    heuristic_intent = _build_artist_leading_title_intent(parsed_intent)
    if heuristic_intent is not None and not music_intents_equivalent(heuristic_intent, intent):
        return heuristic_intent

    ollama_intent = resolve_with_ollama(normalized)
    if ollama_intent is None:
        return None
    if parsed_intent is not None and music_media_type_was_explicit(parsed_intent):
        if normalized_optional_text(getattr(ollama_intent, "media_type", None)) != normalized_optional_text(
            getattr(parsed_intent, "media_type", None)
        ):
            return None
    if music_intents_equivalent(ollama_intent, intent):
        return None
    if parsed_intent is not None and music_intents_equivalent(ollama_intent, parsed_intent):
        return None
    if not _alternate_title_is_grounded_in_request(ollama_intent, normalized, parsed_intent):
        return None
    return ollama_intent


def music_intents_equivalent(left, right) -> bool:
    if left is None or right is None:
        return False
    return left.to_payload() == right.to_payload()


def _build_artist_leading_title_intent(parsed_intent):
    if parsed_intent is None:
        return None
    if getattr(parsed_intent, "intent", None) != "play":
        return None
    if normalized_optional_text(getattr(parsed_intent, "media_type", None)):
        return None
    if any(
        normalized_optional_text(getattr(parsed_intent, field, None))
        for field in ("artist", "album", "playlist", "genre")
    ):
        return None

    title = " ".join(str(getattr(parsed_intent, "title", "") or "").strip().split())
    tokens = [token for token in normalize_simple_match_text(title).split(" ") if token]
    if len(tokens) < 3:
        return None
    leading = tokens[:2]
    remainder = tokens[2:]
    if not remainder:
        return None
    if len(remainder) == 1 and remainder[0] in {"title", "song", "track", "album", "playlist", "music"}:
        return None
    if any(token in {"the", "a", "an", "all", "too", "my", "of", "to", "from", "for", "in", "on", "by"} for token in leading):
        return None
    if any(token in {"and", "n", "feat", "featuring", "with"} for token in leading):
        return None

    return parsed_intent.__class__(
        intent="play",
        media_type="track",
        title=" ".join(remainder),
        artist=" ".join(leading),
        album=None,
        playlist=None,
        genre=None,
        qualifiers=[],
        mode="replace",
        original_text=getattr(parsed_intent, "original_text", ""),
    )


def _build_trailing_short_artist_intent(parsed_intent):
    if parsed_intent is None:
        return None
    if getattr(parsed_intent, "intent", None) != "play":
        return None
    if normalized_optional_text(getattr(parsed_intent, "media_type", None)):
        return None
    if any(
        normalized_optional_text(getattr(parsed_intent, field, None))
        for field in ("artist", "album", "playlist", "genre")
    ):
        return None

    title = " ".join(str(getattr(parsed_intent, "title", "") or "").strip().split())
    tokens = [token for token in normalize_simple_match_text(title).split(" ") if token]
    if len(tokens) < 3:
        return None

    for artist_token_count in (2, 1):
        if len(tokens) <= artist_token_count:
            continue
        trailing = tokens[-artist_token_count:]
        leading = tokens[:-artist_token_count]
        if not leading:
            continue
        if not all(token.isalpha() and 1 <= len(token) <= 2 for token in trailing):
            continue
        return parsed_intent.__class__(
            intent="play",
            media_type="track",
            title=" ".join(leading),
            artist=" ".join(trailing),
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text=getattr(parsed_intent, "original_text", ""),
        )
    return None


def _build_some_artist_intent(parsed_intent):
    if parsed_intent is None:
        return None
    if getattr(parsed_intent, "intent", None) != "play":
        return None
    if normalized_optional_text(getattr(parsed_intent, "media_type", None)):
        return None
    if any(
        normalized_optional_text(getattr(parsed_intent, field, None))
        for field in ("artist", "album", "playlist", "genre")
    ):
        return None

    original_text = str(getattr(parsed_intent, "original_text", "") or "").strip().lower()
    if not any(original_text.startswith(prefix) for prefix in ("play some ", "put on some ", "throw on some ")):
        return None

    title = " ".join(str(getattr(parsed_intent, "title", "") or "").strip().split())
    tokens = [token for token in normalize_simple_match_text(title).split(" ") if token]
    if not 2 <= len(tokens) <= 4:
        return None
    if any(token in {"song", "track", "album", "playlist", "music"} for token in tokens):
        return None

    return parsed_intent.__class__(
        intent="play",
        media_type="artist",
        title=None,
        artist=title,
        album=None,
        playlist=None,
        genre=None,
        qualifiers=[],
        mode="replace",
        original_text=getattr(parsed_intent, "original_text", ""),
    )


def _alternate_title_is_grounded_in_request(ollama_intent, normalized: str, parsed_intent) -> bool:
    alternate_title = normalize_simple_match_text(getattr(ollama_intent, "title", "") or "")
    if not alternate_title:
        return True

    normalized_request = normalize_simple_match_text(normalized)
    if alternate_title and alternate_title in normalized_request:
        return True

    parsed_title = normalize_simple_match_text(getattr(parsed_intent, "title", "") or "")
    if not parsed_title:
        return True

    alternate_tokens = {
        token
        for token in alternate_title.split(" ")
        if token and token not in {"the", "a", "an", "version", "taylors", "taylor", "feat", "featuring"}
    }
    parsed_tokens = {
        token
        for token in parsed_title.split(" ")
        if token and token not in {"the", "a", "an", "version", "taylors", "taylor", "feat", "featuring"}
    }
    if not alternate_tokens or not parsed_tokens:
        return True
    overlap = alternate_tokens & parsed_tokens
    return bool(overlap) and len(overlap) >= min(len(alternate_tokens), 2)
