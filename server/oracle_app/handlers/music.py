from __future__ import annotations

import re
import time
from typing import Any

from fastapi import HTTPException

from oracle_app import audiobook_state, state
from oracle_app.audiobook_runtime.canonical import CanonicalAudiobookExecution
from oracle_app.config import get_satellite_music_backend_hint
from oracle_app.inference import InferenceClient
from oracle_app.music_runtime.policy import (
    apply_ultra_generic_single_word_music_guard as apply_ultra_generic_single_word_music_guard_runtime,
    build_best_guess_candidates as build_music_best_guess_candidates,
    build_best_guess_prompt as build_music_best_guess_prompt,
    choose_best_guess_fallback as choose_music_best_guess_fallback,
    is_generic_title_only_play_intent as is_generic_title_only_music_intent,
    is_ultra_generic_single_word_music_title as is_ultra_generic_single_word_music_title_runtime,
    load_audiobook_guess_candidates as load_audiobook_guess_candidates_for_music,
    music_intents_equivalent as music_intents_equivalent_runtime,
    music_media_type_was_explicit as music_media_type_was_explicit_runtime,
    normalize_simple_match_text as normalize_simple_music_match_text,
    normalized_optional_text as normalized_optional_music_text,
    resolve_alternate_music_intent as resolve_alternate_music_intent_runtime,
    should_downgrade_weak_single_music_clarification as should_downgrade_weak_single_music_clarification_runtime,
    should_try_audiobook_fallback as should_try_audiobook_fallback_runtime,
    trim_ultra_generic_single_word_clarification_candidates as trim_ultra_generic_single_word_clarification_candidates_runtime,
    try_audiobook_fallback as try_audiobook_fallback_runtime,
    try_ollama_best_guess as try_ollama_best_guess_runtime,
    try_prefer_strong_audiobook_match as try_prefer_strong_audiobook_match_runtime,
    value_present as music_value_present,
)
from oracle_app.music_runtime.pending import build_clarification_prompt as build_music_clarification_prompt
from oracle_app.music_runtime.transport import (
    execute_transport as execute_music_transport,
    maybe_transport_longform as maybe_music_transport_longform,
    maybe_transport_reply_audio as maybe_music_transport_reply_audio,
    should_send_music_transport as should_send_music_transport_runtime,
)
from oracle_app.music import search_music_catalog
from oracle_app.music_runtime.control import (
    build_control_plane_failure,
    fetch_satellite_audiobook_session,
    fetch_satellite_music_session,
    fetch_satellite_reply_audio_session,
    execute_satellite_command,
    fetch_satellite_playback_authority,
)
from oracle_app.music_runtime.matching import (
    choose_music_match,
    dedupe_music_candidates,
    score_music_candidates,
)
from oracle_app.music_runtime.ollama import (
    choose_best_guess_with_ollama,
    choose_music_match_with_ollama,
    resolve_with_ollama,
    parse_ollama_decision,
)
from oracle_app.music_runtime.parsing import parse_music_intent
from oracle_app.music_runtime.client import build_native_queue_manifest
from oracle_app.music_runtime.canonical import CanonicalMusicExecution
from oracle_app.music_runtime.playback import build_music_play_media_args, music_playback_selection
from oracle_app.music_runtime.pending import (
    match_pending_music_candidate,
)
from oracle_app.music_runtime.selection import (
    music_identity_key,
    music_pending_option,
    music_selection_id,
)
from oracle_app.schemas import DispatchPlan
from oracle_app.media_execution_context import (
    MediaExecutionContext,
    MediaExecutionContextError,
    fail_media_execution_context,
)
from oracle_app.tracing import log_fallback_event


MUSIC_INFO_ANSWER_SYSTEM_PROMPT = """You answer short music information questions for Oracle.

Return only valid JSON with this exact schema:
{
  "mode": "answer",
  "reply": "short spoken answer or short clarifying question",
  "command": "",
  "reason": "short explanation"
}

Rules:
- Always return mode answer.
- Answer directly if you know the answer.
- If you are not confident, say so briefly in reply.
- Keep reply concise and natural to speak aloud.
- Do not include markdown or any text outside the JSON object.
"""


def execute_music(
    dispatch: DispatchPlan,
    *,
    canonical_playback_target: bool = False,
    canonical_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    inference: InferenceClient | None = None,
    canonical_authority: bool = False,
) -> DispatchPlan:
    if canonical_authority and canonical_execution is None:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "music_failed",
            "error": "music_not_configured",
            "detail": "Music is not enabled in the selected canonical configuration.",
        }
        return dispatch
    search = search_music_catalog if canonical_execution is None else canonical_execution.search
    payload = dispatch.payload
    try:
        execution = MediaExecutionContext.from_dispatch(
            dispatch,
            canonical_playback_target=canonical_playback_target,
        )
    except MediaExecutionContextError as exc:
        return fail_media_execution_context(dispatch, exc)
    request_source = execution.request_source_id
    playback_source = execution.playback_target_source_id
    session_id = execution.session_id
    defer_audible_start = execution.defer_audible_start
    text = str(payload.get("text", "")).strip()
    normalized = str(payload.get("normalized_text", "")).strip() or text
    pending = state.load_pending_music_request(request_source, session_id)

    selected_candidate = match_pending_music_candidate(normalized, pending)
    if selected_candidate is not None:
        state.clear_pending_music_request(request_source, session_id)
        route_target = str(selected_candidate.get("route_target", "music")).strip().lower()
        if route_target == "audiobook":
            from oracle_app.handlers.audiobook import play_selected_dispatch

            if canonical_authority and audiobook_execution is None:
                dispatch.status = "failed"
                dispatch.result = {
                    "action": "play",
                    "error": "audiobook_not_configured",
                    "detail": "Audiobooks are not enabled in the selected canonical configuration.",
                }
                return dispatch

            return play_selected_dispatch(
                dispatch,
                source=playback_source,
                session_id=session_id,
                user_id=str(payload.get("effective_user_id") or "").strip() or None,
                selection=selected_candidate,
                defer_audible_start=defer_audible_start,
                canonical_execution=audiobook_execution,
            )
        selected_playback = music_playback_selection(selected_candidate)
        try:
            audiobook_interrupt = _interrupt_active_audiobook_for_music(
                playback_source,
                canonical_execution=canonical_execution,
                audiobook_execution=audiobook_execution,
            )
            if audiobook_interrupt is not None and audiobook_interrupt.get("status") != "executed":
                dispatch.status = "failed"
                dispatch.result = {
                    "action": "play",
                    "error": "audiobook_interrupt_failed",
                    "detail": str(audiobook_interrupt.get("detail") or "Failed to safely interrupt active audiobook playback."),
                    "audiobook": audiobook_interrupt,
                    "selected": selected_playback,
                }
                return dispatch
            play_media_args = _build_play_media_args(
                playback_source,
                selected_playback,
                canonical_execution=canonical_execution,
            )
            if defer_audible_start:
                command_result = {"ok": True, "state": "deferred"}
            else:
                command = execute_satellite_command if canonical_execution is None else canonical_execution.execute_satellite_command
                command_result = command(
                    playback_source,
                    "play_media",
                    play_media_args,
                )
        except RuntimeError as exc:
            dispatch.status = "failed"
            dispatch.result = build_control_plane_failure(
                action="play",
                exc=exc,
                selected=selected_playback,
            )
            return dispatch

        dispatch.status = "executed"
        dispatch.result = {
            "action": "play",
            "selected": selected_playback,
            "satellite": command_result,
        }
        if defer_audible_start:
            dispatch.result["deferred_audible_start"] = True
            dispatch.result["deferred_session"] = _build_deferred_music_session(play_media_args, selected_playback)
        return dispatch

    parsed_intent = parse_music_intent(normalized)
    intent = parsed_intent or resolve_with_ollama(normalized, inference=inference)
    if intent is None:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "music_failed",
            "error": "music_unrecognized",
            "detail": "Oracle could not parse that music request.",
        }
        return dispatch

    if intent.intent in {"pause", "resume", "stop", "next", "previous", "restart"}:
        return _execute_transport(
            dispatch,
            source=playback_source,
            action=intent.intent,
            normalized_text=normalized,
            canonical_execution=canonical_execution,
        )

    if intent.intent in {"volume_up", "volume_down"}:
        return _execute_transport(
            dispatch,
            source=playback_source,
            action=intent.intent,
            normalized_text=normalized,
            canonical_execution=canonical_execution,
        )

    if intent.intent == "set_volume":
        level = _parse_volume_level(intent.qualifiers)
        if level is None:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "music_failed",
                "error": "invalid_volume",
                "detail": "Volume must be between 0 and 100.",
            }
            return dispatch
        return _execute_transport(
            dispatch,
            source=playback_source,
            action="set_volume",
            args={"level": level},
            canonical_execution=canonical_execution,
        )

    if intent.intent == "what_is_playing":
        try:
            result = _fetch_music_now_playing(playback_source, canonical_execution=canonical_execution)
        except RuntimeError as exc:
            dispatch.status = "failed"
            dispatch.result = build_control_plane_failure(
                action="what_is_playing",
                exc=exc,
                error="satellite_query_failed",
            )
            return dispatch
        dispatch.status = "executed"
        dispatch.result = {
            "action": "what_is_playing",
            "intent": intent.to_payload(),
            "now_playing": result,
        }
        return dispatch

    if intent.intent == "lookup_album":
        return _lookup_track_album(dispatch, intent=intent, search_music=search, inference=inference)

    if intent.intent != "play":
        dispatch.status = "failed"
        dispatch.result = {
            "action": "music_failed",
            "error": "unsupported_music_intent",
            "detail": intent.intent,
        }
        return dispatch

    try:
        candidates = search(intent)
    except Exception as exc:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "play",
            "intent": intent.to_payload(),
            "error": "music_search_failed",
            "detail": str(exc),
        }
        return dispatch

    deduped_candidates = dedupe_music_candidates(candidates, preserve_album_variants=bool(intent.album))
    scored = score_music_candidates(intent, deduped_candidates)
    scored = _prefer_exact_album_artist_match(intent, scored)
    decision, selected = choose_music_match(scored)
    decision, selected = _apply_ultra_generic_single_word_music_guard(intent, decision, scored, selected)
    if _should_downgrade_weak_single_music_clarification(intent, decision, selected):
        decision = "not_found"
        selected = []

    audiobook_fallback = _try_audiobook_fallback(
        dispatch,
        intent=intent,
        decision=decision,
        selected=selected,
        canonical_playback_target=canonical_playback_target,
        audiobook_execution=audiobook_execution,
        canonical_authority=canonical_authority,
    )
    if audiobook_fallback is not None:
        log_fallback_event(
            source=request_source,
            session_id=session_id,
            from_target="music",
            to_target="audiobook",
            detail="generic_title_audiobook_fallback",
        )
        return audiobook_fallback

    if decision == "not_found":
        fallback_intent = _resolve_alternate_music_intent(normalized, intent, parsed_intent)
        if fallback_intent is not None:
            try:
                candidates = search(fallback_intent)
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "action": "play",
                    "intent": fallback_intent.to_payload(),
                    "error": "music_search_failed",
                    "detail": str(exc),
                }
                return dispatch
            deduped_candidates = dedupe_music_candidates(
                candidates,
                preserve_album_variants=bool(fallback_intent.album),
            )
            scored = score_music_candidates(fallback_intent, deduped_candidates)
            scored = _prefer_exact_album_artist_match(fallback_intent, scored)
            decision, selected = choose_music_match(scored)
            decision, selected = _apply_ultra_generic_single_word_music_guard(
                fallback_intent,
                decision,
                scored,
                selected,
            )
            if _should_downgrade_weak_single_music_clarification(fallback_intent, decision, selected):
                decision = "not_found"
                selected = []
            intent = fallback_intent

    corrected = _try_explicit_artist_album_fallback(intent, decision, selected, search_music=search)
    if corrected is not None:
        intent, decision, selected = corrected

    audiobook_fallback = _try_audiobook_fallback(
        dispatch,
        intent=intent,
        decision=decision,
        selected=selected,
        canonical_playback_target=canonical_playback_target,
        audiobook_execution=audiobook_execution,
        canonical_authority=canonical_authority,
    )
    if audiobook_fallback is not None:
        log_fallback_event(
            source=request_source,
            session_id=session_id,
            from_target="music",
            to_target="audiobook",
            detail="generic_title_audiobook_fallback",
        )
        return audiobook_fallback

    audiobook_preference = _try_prefer_strong_audiobook_match(
        dispatch,
        intent=intent,
        decision=decision,
        selected=selected,
        canonical_playback_target=canonical_playback_target,
        audiobook_execution=audiobook_execution,
        canonical_authority=canonical_authority,
    )
    if audiobook_preference is not None:
        log_fallback_event(
            source=request_source,
            session_id=session_id,
            from_target="music",
            to_target="audiobook",
            detail="prefer_strong_audiobook_match",
        )
        return audiobook_preference

    if decision == "not_found":
        ollama_guess = _try_ollama_best_guess(
            dispatch,
            normalized=normalized,
            intent=intent,
            source=request_source,
            session_id=session_id,
            music_candidates=scored[:5],
            audiobook_execution=audiobook_execution,
            canonical_authority=canonical_authority,
            inference=inference,
        )
        if ollama_guess is not None:
            log_fallback_event(
                source=request_source,
                session_id=session_id,
                from_target="music",
                to_target="music",
                detail="ollama_best_guess_prompt",
            )
            return ollama_guess

    if decision == "not_found":
        state.clear_pending_music_request(request_source, session_id)
        dispatch.status = "failed"
        dispatch.result = {
            "action": "play",
            "intent": intent.to_payload(),
            "error": "music_not_found",
            "detail": "No strong Plex matches were found.",
        }
        return dispatch

    if decision == "clarify" and not _is_ultra_generic_single_word_music_title(intent):
        ollama_selection = choose_music_match_with_ollama(intent, selected[:5], inference=inference)
        if ollama_selection is not None:
            decision = "execute"
            selected = [ollama_selection]

    if decision == "clarify":
        selected = _trim_ultra_generic_single_word_clarification_candidates(intent, selected)
        options = [
            music_pending_option(item)
            for item in selected[:3]
        ]
        stored = state.store_pending_music_request(
            request_source,
            session_id,
            {
                "intent": intent.to_payload(),
                "candidates": options,
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
            "prompt": _build_clarification_prompt(options),
            "candidates": options,
        }
        return dispatch

    selection = selected[0]
    selected_playback = music_playback_selection(selection)
    state.clear_pending_music_request(request_source, session_id)
    try:
        audiobook_interrupt = _interrupt_active_audiobook_for_music(
            playback_source,
            canonical_execution=canonical_execution,
            audiobook_execution=audiobook_execution,
        )
        if audiobook_interrupt is not None and audiobook_interrupt.get("status") != "executed":
            dispatch.status = "failed"
            dispatch.result = {
                "action": "play",
                "intent": intent.to_payload(),
                "error": "audiobook_interrupt_failed",
                "detail": str(audiobook_interrupt.get("detail") or "Failed to safely interrupt active audiobook playback."),
                "audiobook": audiobook_interrupt,
                "selected": selected_playback,
            }
            return dispatch
        play_media_args = _build_play_media_args(
            playback_source,
            selected_playback,
            canonical_execution=canonical_execution,
        )
        if defer_audible_start:
            command_result = {"ok": True, "state": "deferred"}
        else:
            command = execute_satellite_command if canonical_execution is None else canonical_execution.execute_satellite_command
            command_result = command(
                playback_source,
                "play_media",
                play_media_args,
            )
    except RuntimeError as exc:
        dispatch.status = "failed"
        dispatch.result = build_control_plane_failure(
            action="play",
            exc=exc,
            intent=intent.to_payload(),
            selected=selected_playback,
        )
        return dispatch

    dispatch.status = "executed"
    dispatch.result = {
        "action": "play",
        "intent": intent.to_payload(),
        "selected": selected_playback,
        "satellite": (
            command_result
            if defer_audible_start
            else _refresh_playback_ack(
                playback_source,
                selected_playback,
                command_result,
                canonical_execution=canonical_execution,
            )
        ),
    }
    if defer_audible_start:
        dispatch.result["deferred_audible_start"] = True
        dispatch.result["deferred_session"] = _build_deferred_music_session(play_media_args, selected_playback)
    return dispatch


def _lookup_track_album(
    dispatch: DispatchPlan,
    *,
    intent,
    search_music=search_music_catalog,
    inference: InferenceClient | None = None,
) -> DispatchPlan:
    try:
        candidates = search_music(intent)
    except Exception as exc:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "lookup_album",
            "intent": intent.to_payload(),
            "error": "music_search_failed",
            "detail": str(exc),
        }
        return dispatch

    deduped_candidates = dedupe_music_candidates(candidates, preserve_album_variants=True)
    scored = score_music_candidates(intent, deduped_candidates)
    scored = _prefer_exact_album_artist_match(intent, scored)
    decision, selected = choose_music_match(scored)

    if decision == "not_found" or not selected:
        ollama_reply = _lookup_music_info_with_ollama(intent.original_text, inference=inference)
        if ollama_reply:
            log_fallback_event(
                source=dispatch.payload.get("source"),
                session_id=dispatch.payload.get("session_id"),
                from_target="music",
                to_target="ollama",
                detail="music_info_lookup_answer_fallback",
            )
            dispatch.status = "executed"
            dispatch.result = {
                "action": "lookup_album",
                "intent": intent.to_payload(),
                "reply": ollama_reply,
                "source": "ollama_fallback",
            }
            return dispatch
        dispatch.status = "failed"
        dispatch.result = {
            "action": "lookup_album",
            "intent": intent.to_payload(),
            "error": "music_not_found",
            "detail": "No strong Plex matches were found.",
        }
        return dispatch

    top = selected[0]
    dispatch.status = "executed"
    dispatch.result = {
        "action": "lookup_album",
        "intent": intent.to_payload(),
        "selected": {
            "title": top.get("title"),
            "artist": top.get("artist"),
            "album": top.get("album"),
            "type": top.get("type"),
            "score": top.get("score"),
        },
    }
    return dispatch


def _lookup_music_info_with_ollama(
    question: str,
    *,
    inference: InferenceClient | None = None,
) -> str | None:
    prompt = str(question or "").strip()
    if not prompt or inference is None:
        return None
    try:
        result = inference.generate(
            prompt,
            system=MUSIC_INFO_ANSWER_SYSTEM_PROMPT,
            format="json",
        )
    except Exception:
        return None
    decision = parse_ollama_decision(str(result.get("response", "")).strip())
    if decision.get("mode") != "answer":
        return None
    reply = str(decision.get("reply", "")).strip()
    return reply or None


def _execute_transport(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    action: str,
    args: dict[str, Any] | None = None,
    normalized_text: str = "",
    canonical_execution: CanonicalMusicExecution | None = None,
) -> DispatchPlan:
    command = execute_satellite_command if canonical_execution is None else canonical_execution.execute_satellite_command
    authority = fetch_satellite_playback_authority if canonical_execution is None else canonical_execution.fetch_playback_authority
    music_session = fetch_satellite_music_session if canonical_execution is None else canonical_execution.fetch_satellite_music_session
    audiobook_session = fetch_satellite_audiobook_session if canonical_execution is None else canonical_execution.fetch_satellite_audiobook_session
    reply_session = fetch_satellite_reply_audio_session if canonical_execution is None else canonical_execution.fetch_satellite_reply_audio_session
    status, result = execute_music_transport(
        source=source,
        action=action,
        args=args,
        normalized_text=normalized_text,
        execute_satellite_command=command,
        fetch_satellite_playback_authority=authority,
        fetch_satellite_music_session=music_session,
        fetch_satellite_audiobook_session=audiobook_session,
        fetch_satellite_reply_audio_session=reply_session,
    )
    dispatch.status = status
    dispatch.result = result
    return dispatch


def _try_audiobook_fallback(
    dispatch: DispatchPlan,
    *,
    intent,
    decision: str,
    selected: list[dict[str, Any]],
    canonical_playback_target: bool = False,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    canonical_authority: bool = False,
) -> DispatchPlan | None:
    from oracle_app.handlers.audiobook import execute_audiobook
    return try_audiobook_fallback_runtime(
        dispatch,
        intent=intent,
        decision=decision,
        selected=selected,
        execute_audiobook=lambda next_dispatch: execute_audiobook(
            next_dispatch,
            canonical_playback_target=canonical_playback_target,
            canonical_execution=audiobook_execution,
            canonical_authority=canonical_authority,
        ),
    )


def _try_ollama_best_guess(
    dispatch: DispatchPlan,
    *,
    normalized: str,
    intent,
    source: str | None,
    session_id: str | None,
    music_candidates: list[dict[str, Any]],
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    canonical_authority: bool = False,
    inference: InferenceClient | None = None,
) -> DispatchPlan | None:
    return try_ollama_best_guess_runtime(
        dispatch,
        normalized=normalized,
        intent=intent,
        source=source,
        session_id=session_id,
        music_candidates=music_candidates,
        load_audiobook_guess_candidates=lambda title: _load_audiobook_guess_candidates(
            title,
            audiobook_execution=audiobook_execution,
            canonical_authority=canonical_authority,
        ),
        choose_best_guess_with_ollama=lambda text, candidates: choose_best_guess_with_ollama(
            text,
            candidates,
            inference=inference,
        ),
        store_pending_music_request=state.store_pending_music_request,
    )


def _try_prefer_strong_audiobook_match(
    dispatch: DispatchPlan,
    *,
    intent,
    decision: str,
    selected: list[dict[str, Any]],
    canonical_playback_target: bool = False,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    canonical_authority: bool = False,
) -> DispatchPlan | None:
    from oracle_app.handlers.audiobook import execute_audiobook

    return try_prefer_strong_audiobook_match_runtime(
        dispatch,
        intent=intent,
        decision=decision,
        selected=selected,
        load_audiobook_guess_candidates=lambda title: _load_audiobook_guess_candidates(
            title,
            audiobook_execution=audiobook_execution,
            canonical_authority=canonical_authority,
        ),
        execute_audiobook=lambda next_dispatch: execute_audiobook(
            next_dispatch,
            canonical_playback_target=canonical_playback_target,
            canonical_execution=audiobook_execution,
            canonical_authority=canonical_authority,
        ),
    )


def _should_try_audiobook_fallback(
    intent,
    decision: str,
    selected: list[dict[str, Any]],
) -> bool:
    return should_try_audiobook_fallback_runtime(intent, decision, selected)


def _should_downgrade_weak_single_music_clarification(
    intent,
    decision: str,
    selected: list[dict[str, Any]],
) -> bool:
    return should_downgrade_weak_single_music_clarification_runtime(intent, decision, selected)


def _apply_ultra_generic_single_word_music_guard(
    intent,
    decision: str,
    scored: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    return apply_ultra_generic_single_word_music_guard_runtime(intent, decision, scored, selected)


def _is_generic_title_only_play_intent(intent) -> bool:
    return is_generic_title_only_music_intent(intent)


def _is_ultra_generic_single_word_music_title(intent) -> bool:
    return is_ultra_generic_single_word_music_title_runtime(intent)


def _trim_ultra_generic_single_word_clarification_candidates(intent, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return trim_ultra_generic_single_word_clarification_candidates_runtime(intent, candidates)


def _normalize_simple_match_text(text: str) -> str:
    return normalize_simple_music_match_text(text)


def _music_media_type_was_explicit(intent) -> bool:
    return music_media_type_was_explicit_runtime(intent)


def _load_audiobook_guess_candidates(
    title: str,
    *,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    canonical_authority: bool = False,
) -> list[dict[str, Any]]:
    from oracle_app.audiobook import score_audiobook_candidates, search_audiobooks
    if canonical_authority and audiobook_execution is None:
        return []
    search = search_audiobooks if audiobook_execution is None else audiobook_execution.search_audiobooks
    return load_audiobook_guess_candidates_for_music(title, search, score_audiobook_candidates)


def _build_best_guess_candidates(
    music_candidates: list[dict[str, Any]],
    audiobook_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return build_music_best_guess_candidates(music_candidates, audiobook_candidates)


def _choose_best_guess_fallback(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    return choose_music_best_guess_fallback(candidates)


def _normalized_optional_text(value: Any) -> str:
    return normalized_optional_music_text(value)


def _value_present(value: Any) -> bool:
    return music_value_present(value)


def _build_best_guess_prompt(candidate: dict[str, Any]) -> str:
    return build_music_best_guess_prompt(candidate)


def _build_deferred_music_session(play_media_args: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "music",
        "backend_type": str(play_media_args.get("backend_hint") or "").strip() or "music",
        "session_id": music_selection_id(selection),
        "resume_action": "play_media",
        "resume_args": play_media_args,
    }


def _resolve_alternate_music_intent(
    normalized: str,
    intent,
    parsed_intent,
):
    return resolve_alternate_music_intent_runtime(normalized, intent, parsed_intent, resolve_with_ollama)


def _music_intents_equivalent(left, right) -> bool:
    return music_intents_equivalent_runtime(left, right)


def _should_send_music_transport(*, source: str | None, action: str, bare_transport: bool) -> bool:
    return should_send_music_transport_runtime(
        action=action,
        bare_transport=bare_transport,
        authority_state=None,
        fetch_satellite_music_session=fetch_satellite_music_session,
        source=source,
    )


def _maybe_transport_longform(
    *,
    source: str | None,
    action: str,
    normalized_text: str,
) -> dict[str, Any] | None:
    return maybe_music_transport_longform(
        source=source,
        action=action,
        normalized_text=normalized_text,
        authority_state=None,
        execute_satellite_command=execute_satellite_command,
        fetch_satellite_audiobook_session=fetch_satellite_audiobook_session,
    )


def _maybe_transport_reply_audio(
    *,
    source: str | None,
    action: str,
    normalized_text: str,
) -> dict[str, Any] | None:
    return maybe_music_transport_reply_audio(
        source=source,
        action=action,
        normalized_text=normalized_text,
        authority_state=None,
        execute_satellite_command=execute_satellite_command,
        fetch_satellite_reply_audio_session=fetch_satellite_reply_audio_session,
    )


def _build_clarification_prompt(options: list[dict[str, Any]]) -> str:
    return build_music_clarification_prompt(options)


def _try_explicit_artist_album_fallback(
    intent,
    decision: str,
    selected: list[dict[str, Any]],
    *,
    search_music=search_music_catalog,
):
    if getattr(intent, "intent", None) != "play":
        return None
    if _normalized_optional_text(getattr(intent, "media_type", None)) != "track":
        return None

    requested_title = str(getattr(intent, "title", "") or "").strip()
    requested_artist = str(getattr(intent, "artist", "") or "").strip()
    if not requested_title or not requested_artist:
        return None
    if decision not in {"execute", "clarify", "not_found"}:
        return None
    if selected and not _candidate_conflicts_with_explicit_artist(selected[0], requested_artist):
        return None

    album_intent = intent.__class__(
        intent="play",
        media_type="album",
        title=None,
        artist=requested_artist,
        album=requested_title,
        playlist=None,
        genre=None,
        qualifiers=[],
        mode="replace",
        original_text=getattr(intent, "original_text", ""),
    )
    try:
        album_candidates = search_music(album_intent)
    except Exception:
        return None
    deduped_album_candidates = dedupe_music_candidates(
        album_candidates,
        preserve_album_variants=True,
    )
    scored_album_candidates = score_music_candidates(album_intent, deduped_album_candidates)
    album_decision, album_selected = choose_music_match(scored_album_candidates)
    if album_decision == "not_found" or not album_selected:
        return None
    top_album = album_selected[0]
    if str(top_album.get("type", "")).strip().lower() != "album":
        return None
    if _candidate_conflicts_with_explicit_artist(top_album, requested_artist):
        return None
    if int(top_album.get("score", 0)) < 50:
        return None
    return album_intent, album_decision, album_selected


def _prefer_exact_album_artist_match(intent, scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if getattr(intent, "intent", None) != "play":
        return scored
    if _normalized_optional_text(getattr(intent, "media_type", None)) != "album":
        return scored
    requested_album = _normalize_simple_match_text(getattr(intent, "album", "") or "")
    requested_artist = str(getattr(intent, "artist", "") or "").strip()
    if not requested_album or not requested_artist:
        return scored

    exact_matches = [
        item
        for item in scored
        if str(item.get("type", "")).strip().lower() == "album"
        and _normalize_simple_match_text(item.get("album", "") or item.get("title", "")) == requested_album
        and not _candidate_conflicts_with_explicit_artist(item, requested_artist)
    ]
    if not exact_matches:
        return scored

    exact_keys = {music_identity_key(item) for item in exact_matches}
    preferred = sorted(exact_matches, key=lambda item: int(item.get("score", 0)), reverse=True)
    remainder = [
        item
        for item in scored
        if music_identity_key(item) not in exact_keys
    ]
    return preferred + remainder


def _candidate_conflicts_with_explicit_artist(candidate: dict[str, Any], requested_artist: str) -> bool:
    requested_tokens = {
        token
        for token in _normalize_simple_match_text(requested_artist).split(" ")
        if token and token not in {"the", "a", "an"}
    }
    candidate_artist = str(candidate.get("artist", "") or "").strip()
    candidate_tokens = {
        token
        for token in _normalize_simple_match_text(candidate_artist).split(" ")
        if token and token not in {"the", "a", "an"}
    }
    if not requested_tokens or not candidate_tokens:
        return False
    return not bool(requested_tokens & candidate_tokens)


def _refresh_playback_ack(
    source: str | None,
    selection: dict[str, Any],
    command_result: dict[str, Any],
    *,
    canonical_execution: CanonicalMusicExecution | None = None,
) -> dict[str, Any]:
    expected_title = normalize_simple_music_match_text(selection.get("title"))
    expected_artist = normalize_simple_music_match_text(selection.get("artist"))
    refreshed: dict[str, Any] | None = None

    for attempt in range(3):
        try:
            now_playing = _fetch_music_now_playing(source, canonical_execution=canonical_execution)
        except Exception:
            break
        refreshed = _merge_playback_snapshot(command_result, now_playing)
        if _playback_snapshot_matches_expected(now_playing, expected_title, expected_artist):
            return refreshed
        if attempt < 2:
            time.sleep(0.2)

    if refreshed is not None:
        return refreshed
    return command_result


def _fetch_music_now_playing(
    source: str | None,
    *,
    canonical_execution: CanonicalMusicExecution | None = None,
) -> dict[str, Any]:
    fetch_session = (
        fetch_satellite_music_session
        if canonical_execution is None
        else canonical_execution.fetch_satellite_music_session
    )
    session = fetch_session(source)
    if not isinstance(session, dict):
        return {"ok": True, "playing": False, "state": "stopped"}
    return {
        "ok": True,
        "playing": str(session.get("state", "")).strip().lower() in {"playing", "paused", "starting", "stopping"},
        "state": session.get("state"),
        "title": session.get("title", ""),
        "artist": session.get("artist_or_author", ""),
        "album": session.get("album", ""),
        "volume": session.get("volume"),
    }


def _build_play_media_args(
    source: str | None,
    selection: dict[str, Any],
    *,
    canonical_execution: CanonicalMusicExecution | None = None,
) -> dict[str, Any]:
    return build_music_play_media_args(
        source,
        selection,
        get_backend_hint=(
            get_satellite_music_backend_hint
            if canonical_execution is None
            else canonical_execution.backend_hint
        ),
        build_manifest=(
            build_native_queue_manifest
            if canonical_execution is None
            else canonical_execution.build_native_queue_manifest
        ),
    )


def _interrupt_active_audiobook_for_music(
    source: str | None,
    *,
    canonical_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> dict[str, Any] | None:
    if not source:
        return None
    try:
        fetch_session = (
            fetch_satellite_audiobook_session
            if canonical_execution is None
            else canonical_execution.fetch_satellite_audiobook_session
        )
        authority_session = fetch_session(source)
    except HTTPException:
        return None
    except RuntimeError as exc:
        return {
            "status": "failed",
            "error": "audiobook_state_query_failed",
            "detail": str(exc),
        }
    active = audiobook_state.get_active_audiobook_playback_for_source(source)
    if authority_session is None and active is None:
        return None
    authority_state = str((authority_session or {}).get("state", "")).strip().lower()
    if authority_state not in {"playing", "starting", "buffering", "stopping"}:
        return None
    if active is None or (canonical_execution is not None and audiobook_execution is None):
        try:
            command = execute_satellite_command if canonical_execution is None else canonical_execution.execute_satellite_command
            pause_result = command(source, "pause_longform_audio", None)
        except RuntimeError as exc:
            return build_control_plane_failure(
                action="pause",
                exc=exc,
                audiobook=authority_session,
                status="failed",
            )
        return {
            "status": "executed",
            "action": "pause",
            "satellite": pause_result,
            "warning": "audiobook_sync_context_missing",
            "detail": "Audiobook playback was paused for music, but Oracle had no Audiobookshelf session context to sync progress.",
            "audiobook": authority_session,
        }
    if active is not None:
        from oracle_app.audiobook import close_audiobook_session, sync_audiobook_session
        from oracle_app.audiobook_runtime.playback import sync_then_control as sync_then_control_audiobook

        command = execute_satellite_command if canonical_execution is None else canonical_execution.execute_satellite_command
        close_session = close_audiobook_session if audiobook_execution is None else audiobook_execution.close_session
        sync_session = sync_audiobook_session if audiobook_execution is None else audiobook_execution.sync_session
        status, result = sync_then_control_audiobook(
            source=source,
            action="pause_longform_audio",
            close_session=False,
            require_sync_success=False,
            defer_sync=True,
            get_active_playback_for_source=audiobook_state.get_active_audiobook_playback_for_source,
            execute_satellite_command=command,
            close_audiobook_session=close_session,
            sync_audiobook_session=sync_session,
            clear_active_playback=audiobook_state.clear_active_audiobook_playback,
        )
        merged = {"status": status, **result}
        if authority_session is not None:
            merged["audiobook"] = {
                "session_id": authority_session.get("session_id"),
                "title": authority_session.get("title"),
                "author": authority_session.get("artist_or_author"),
                "position_seconds": authority_session.get("position_seconds"),
                "duration_seconds": authority_session.get("duration_seconds"),
            }
        return merged
    return None


def _merge_playback_snapshot(
    command_result: dict[str, Any],
    now_playing: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(command_result)
    for key in ("playing", "state", "title", "artist", "album", "volume"):
        if key in now_playing:
            merged[key] = now_playing.get(key)
    if "state" in now_playing and "playback_state" not in merged:
        merged["playback_state"] = now_playing.get("state")
    elif "state" in now_playing:
        merged["playback_state"] = now_playing.get("state")
    return merged


def _playback_snapshot_matches_expected(
    now_playing: dict[str, Any],
    expected_title: str,
    expected_artist: str,
) -> bool:
    if not now_playing.get("playing"):
        return False
    title = normalize_simple_music_match_text(now_playing.get("title"))
    artist = normalize_simple_music_match_text(now_playing.get("artist"))
    if expected_title and title != expected_title:
        return False
    if expected_artist and artist and artist != expected_artist:
        return False
    return bool(title)


def _parse_volume_level(values: list[str]) -> int | None:
    if not values:
        return None
    try:
        level = int(values[0])
    except ValueError:
        return None
    if not 0 <= level <= 100:
        return None
    return level


class MusicHandler:
    target = "music"

    def __init__(
        self,
        *,
        canonical_playback_target: bool = False,
        canonical_execution: CanonicalMusicExecution | None = None,
        audiobook_execution: CanonicalAudiobookExecution | None = None,
        inference: InferenceClient | None = None,
        canonical_authority: bool = False,
    ) -> None:
        self.canonical_playback_target = canonical_playback_target
        self.canonical_execution = canonical_execution
        self.audiobook_execution = audiobook_execution
        self.inference = inference
        self.canonical_authority = canonical_authority

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        return execute_music(
            dispatch,
            canonical_playback_target=self.canonical_playback_target,
            canonical_execution=self.canonical_execution,
            audiobook_execution=self.audiobook_execution,
            inference=self.inference,
            canonical_authority=self.canonical_authority,
        )
