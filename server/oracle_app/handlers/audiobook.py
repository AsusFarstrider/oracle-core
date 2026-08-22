from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from fastapi import HTTPException

from oracle_app import audiobook_state, state
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.audiobook_runtime.canonical import CanonicalAudiobookExecution
from oracle_app.media_execution_context import (
    MediaExecutionContext,
    MediaExecutionContextError,
    fail_media_execution_context,
)
from oracle_app.alerts import cancel_alerts, create_alert, format_duration, list_alerts
from oracle_app.audiobook import (
    build_longform_payload,
    choose_audiobook_match,
    close_audiobook_session,
    fetch_audiobook_item,
    fetch_current_audiobook_progress,
    find_audiobook_series_entry,
    is_audiobook_request,
    open_audiobook_playback_session,
    parse_bare_audiobook_sleep_timer_intent,
    parse_audiobook_intent,
    score_audiobook_candidates,
    search_audiobooks,
    sync_audiobook_session,
)
from oracle_app.provider_bridges.audiobookshelf_audiobook import (
    AudiobookBridgeConfigurationError,
    normalize_audiobook_progress,
)
from oracle_app.audiobook_runtime.pending import (
    analyze_pending_candidate_reply as analyze_pending_audiobook_reply,
    build_clarification_prompt as build_audiobook_clarification_prompt,
    match_pending_candidate as match_pending_audiobook_candidate,
)
from oracle_app.audiobook_runtime.playback import (
    play_selected as play_selected_audiobook,
    sync_then_control as sync_then_control_audiobook,
)
from oracle_app.audiobook_runtime.policy import (
    cancel_sleep_timer as cancel_audiobook_sleep_timer,
    create_sleep_timer as create_audiobook_sleep_timer,
    now_plus as audiobook_now_plus,
    set_sleep_timer as set_audiobook_sleep_timer,
    sleep_timer_status as audiobook_sleep_timer_status,
)
from oracle_app.music_runtime.control import (
    build_control_plane_failure,
    execute_satellite_command,
    fetch_satellite_audiobook_session,
    fetch_satellite_music_session,
)
from oracle_app.schemas import DispatchPlan
from oracle_app.user_context import resolve_effective_user


logger = logging.getLogger(__name__)
SLEEP_TIMER_KIND = "sleep_timer"
_SERIES_NEIGHBOR_WINDOW = 3


def execute_audiobook(
    dispatch: DispatchPlan,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
    canonical_playback_target: bool = False,
    canonical_execution: CanonicalAudiobookExecution | None = None,
    canonical_authority: bool = False,
) -> DispatchPlan:
    if canonical_authority and canonical_execution is None:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "audiobook_failed",
            "error": "audiobook_not_configured",
            "detail": "Audiobooks are not enabled in the selected canonical configuration.",
        }
        return dispatch
    alert_target_error = str(
        dispatch.payload.get("alert_delivery_target_error") or ""
    ).strip()
    if alert_target_error:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "sleep_timer",
            "error": alert_target_error,
            "detail": "A durable sleep timer requires an authorized managed alert destination.",
        }
        return dispatch
    payload = dispatch.payload
    try:
        execution = MediaExecutionContext.from_dispatch(
            dispatch,
            canonical_playback_target=canonical_playback_target,
        )
    except MediaExecutionContextError as exc:
        return fail_media_execution_context(dispatch, exc)
    request_source = execution.request_source_id
    source = execution.playback_target_source_id
    session_id = execution.session_id
    defer_audible_start = execution.defer_audible_start
    effective_user_id = str(payload.get("effective_user_id") or "").strip() or None
    requested_user_name = str(payload.get("requested_user_name") or "").strip() or None
    user_resolution_error = str(payload.get("user_resolution_error") or "").strip()
    text = str(payload.get("text", "")).strip()
    normalized = str(payload.get("normalized_text", "")).strip() or text
    search = search_audiobooks if canonical_execution is None else canonical_execution.search_audiobooks
    find_series = (
        find_audiobook_series_entry
        if canonical_execution is None
        else canonical_execution.find_series_entry
    )
    fetch_progress = (
        fetch_current_audiobook_progress
        if canonical_execution is None
        else canonical_execution.fetch_current_progress
    )
    if effective_user_id is None and not user_resolution_error:
        resolved_user = resolve_effective_user(
            source=request_source,
            session_id=session_id,
            household_settings=household_settings,
        )
        if resolved_user.get("ok"):
            effective_user_id = str(resolved_user.get("user_id") or "").strip() or None
        else:
            user_resolution_error = str(resolved_user.get("error") or "")
    if user_resolution_error == "unknown_user":
        dispatch.status = "failed"
        dispatch.result = {
            "action": "play",
            "error": "unknown_user",
            "requested_user_name": requested_user_name,
        }
        return dispatch
    pending = state.load_pending_audiobook_request(request_source, session_id)

    pending_outcome = analyze_pending_audiobook_reply(normalized, pending)
    if pending_outcome["action"] == "resolve":
        selected_candidate = pending_outcome.get("candidate")
        if not isinstance(selected_candidate, dict):
            selected_candidate = None
    else:
        selected_candidate = None
    if selected_candidate is not None:
        state.clear_pending_audiobook_request(request_source, session_id)
        pending_intent = pending.get("intent") if isinstance(pending, dict) else None
        sleep_timer_seconds = None
        if isinstance(pending_intent, dict):
            raw_sleep_timer = pending_intent.get("sleep_timer_seconds")
            if isinstance(raw_sleep_timer, int):
                sleep_timer_seconds = raw_sleep_timer
        return _play_selected(
            dispatch,
            source=source,
            session_id=session_id,
            user_id=effective_user_id,
            selection=selected_candidate,
            sleep_timer_seconds=sleep_timer_seconds,
            defer_audible_start=defer_audible_start,
            canonical_execution=canonical_execution,
        )
    if pending_outcome["action"] == "narrow":
        narrowed_candidates = pending_outcome.get("remaining")
        if isinstance(pending, dict) and isinstance(narrowed_candidates, list) and narrowed_candidates:
            narrowed_payload = _build_narrowed_pending_payload(
                pending,
                narrowed_candidates=narrowed_candidates,
                narrowing=pending_outcome,
            )
            stored = state.narrow_pending_audiobook_request(
                request_source,
                session_id,
                narrowed_payload,
            )
            if not stored:
                dispatch.status = "failed"
                dispatch.result = {
                    "action": "play",
                    "error": "pending_state_requires_context",
                    "detail": "Pending audiobook clarification requires both source and session_id.",
                }
                return dispatch
            dispatch.status = "pending_clarification"
            dispatch.result = {
                "action": "play",
                "intent": narrowed_payload.get("intent"),
                "prompt": build_audiobook_clarification_prompt(narrowed_candidates),
                "candidates": narrowed_candidates,
                "narrowed": True,
            }
            return dispatch

    intent = parse_audiobook_intent(normalized)
    if intent is None:
        intent = parse_bare_audiobook_sleep_timer_intent(normalized)
    if intent is None:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "audiobook_failed",
            "error": "audiobook_unrecognized",
            "detail": "Oracle could not parse that audiobook request.",
        }
        return dispatch

    if intent.intent == "pause":
        return _pause_audiobook(dispatch, source=source, canonical_execution=canonical_execution)
    if intent.intent == "resume":
        return _resume_active_audiobook(
            dispatch,
            source=source,
            session_id=session_id,
            user_id=effective_user_id,
            defer_audible_start=defer_audible_start,
            canonical_execution=canonical_execution,
        )
    if intent.intent == "stop":
        return _stop_audiobook(dispatch, source=source, canonical_execution=canonical_execution)
    if intent.intent == "what_is_playing":
        return _what_is_playing(dispatch, source=source, canonical_execution=canonical_execution)
    if intent.intent == "sleep_timer":
        return _set_sleep_timer(dispatch, source=source, session_id=session_id, duration_seconds=intent.sleep_timer_seconds)
    if intent.intent == "sleep_timer_cancel":
        return _cancel_sleep_timer(dispatch, source=source)
    if intent.intent == "sleep_timer_status":
        return _sleep_timer_status(dispatch, source=source)
    if intent.intent == "resume_current":
        progress = normalize_audiobook_progress(fetch_progress(user_id=effective_user_id))
        if progress is None:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "resume_current",
                "error": "audiobook_not_found",
                "detail": "No in-progress audiobook was found.",
            }
            return dispatch
        selection = {"library_item_id": str(progress.get("library_item_id", "")).strip()}
        return _play_selected(
            dispatch,
            source=source,
            session_id=session_id,
            user_id=effective_user_id,
            selection=selection,
            sleep_timer_seconds=intent.sleep_timer_seconds,
            defer_audible_start=defer_audible_start,
            canonical_execution=canonical_execution,
        )
    if intent.intent == "series_lookup":
        return _lookup_series_entry(
            dispatch,
            series=intent.series or intent.title,
            ordinal=intent.ordinal,
            user_id=effective_user_id,
            find_series=find_series,
        )

    if intent.intent != "play" or not intent.title:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "audiobook_failed",
            "error": "unsupported_audiobook_intent",
            "detail": intent.intent,
        }
        return dispatch

    if intent.series and intent.ordinal:
        try:
            match = find_series(intent.series, intent.ordinal, user_id=effective_user_id)
        except Exception as exc:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "play",
                "intent": intent.to_payload(),
                "error": "audiobook_search_failed",
                "detail": str(exc),
            }
            return dispatch
        if match is None:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "play",
                "intent": intent.to_payload(),
                "error": "audiobook_not_found",
                "detail": "No matching series entry was found in Audiobookshelf.",
            }
            return dispatch
        state.clear_pending_audiobook_request(request_source, session_id)
        return _play_selected(
            dispatch,
            source=source,
            session_id=session_id,
            user_id=effective_user_id,
            selection=match,
            sleep_timer_seconds=intent.sleep_timer_seconds,
            defer_audible_start=defer_audible_start,
            canonical_execution=canonical_execution,
        )

    try:
        candidates = search(intent.title, intent.narrator_preference, user_id=effective_user_id)
    except AudiobookBridgeConfigurationError as exc:
        if effective_user_id:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "play",
                "intent": intent.to_payload(),
                "error": "audiobook_user_not_configured",
                "detail": exc.detail,
                "requested_user_name": requested_user_name,
                "user_id": effective_user_id,
            }
            return dispatch
        raise
    except Exception as exc:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "play",
            "intent": intent.to_payload(),
            "error": "audiobook_search_failed",
            "detail": str(exc),
        }
        return dispatch

    candidates = _maybe_expand_series_neighbors(
        intent,
        candidates,
        search_audiobooks=search,
        user_id=effective_user_id,
    )

    scored = score_audiobook_candidates(
        intent.title,
        candidates,
        narrator_preference=intent.narrator_preference,
    )
    series_neighbor_clarification = _series_neighbor_clarification_candidates(
        intent,
        scored,
    )
    if series_neighbor_clarification:
        decision = "clarify"
        selected = series_neighbor_clarification
        skip_initial_clarification_autoselect = True
    else:
        decision, selected = choose_audiobook_match(scored)
        skip_initial_clarification_autoselect = False

    if decision == "not_found":
        state.clear_pending_audiobook_request(request_source, session_id)
        dispatch.status = "failed"
        dispatch.result = {
            "action": "play",
            "intent": intent.to_payload(),
            "error": "audiobook_not_found",
            "detail": "No strong Audiobookshelf matches were found.",
        }
        return dispatch

    if decision == "clarify":
        narrator_selected = _match_candidate_by_narrator_preference(
            intent.narrator_preference,
            selected,
        )
        if narrator_selected is not None:
            state.clear_pending_audiobook_request(request_source, session_id)
            return _play_selected(
                dispatch,
                source=source,
                session_id=session_id,
                user_id=effective_user_id,
                selection=narrator_selected,
                sleep_timer_seconds=intent.sleep_timer_seconds,
                defer_audible_start=defer_audible_start,
                canonical_execution=canonical_execution,
            )
        options = [
            {
                "library_item_id": item.get("library_item_id"),
                "title": item.get("title"),
                "author": item.get("author"),
                "subtitle": item.get("subtitle"),
                "narrator": item.get("narrator"),
                "series": item.get("series"),
                "score": item.get("score"),
            }
            for item in selected[:5]
        ]
        initial_selection = None
        if not skip_initial_clarification_autoselect:
            initial_selection = match_pending_audiobook_candidate(intent.title, {"candidates": options})
        if initial_selection is not None:
            state.clear_pending_audiobook_request(request_source, session_id)
            return _play_selected(
                dispatch,
                source=source,
                session_id=session_id,
                user_id=effective_user_id,
                selection=initial_selection,
                sleep_timer_seconds=intent.sleep_timer_seconds,
                defer_audible_start=defer_audible_start,
                canonical_execution=canonical_execution,
            )
        stored = state.store_pending_audiobook_request(
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
                "detail": "Pending audiobook clarification requires both source and session_id.",
            }
            return dispatch
        dispatch.status = "pending_clarification"
        dispatch.result = {
            "action": "play",
            "intent": intent.to_payload(),
            "prompt": build_audiobook_clarification_prompt(options),
            "candidates": options,
        }
        return dispatch

    state.clear_pending_audiobook_request(request_source, session_id)
    return _play_selected(
        dispatch,
        source=source,
        session_id=session_id,
        user_id=effective_user_id,
        selection=selected[0],
        sleep_timer_seconds=intent.sleep_timer_seconds,
        defer_audible_start=defer_audible_start,
        canonical_execution=canonical_execution,
    )


def _maybe_expand_series_neighbors(
    intent,
    candidates: list[dict[str, Any]],
    *,
    search_audiobooks,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    if not _should_expand_series_neighbors(intent, candidates):
        return candidates

    top_candidate = candidates[0]
    series_name = _first_series_name(top_candidate)
    if not series_name:
        return candidates

    try:
        series_candidates = search_audiobooks(series_name, user_id=user_id)
    except Exception:
        return candidates

    return _merge_series_neighbors(
        candidates,
        series_candidates,
        series_name=series_name,
        max_sequence=_SERIES_NEIGHBOR_WINDOW,
    )


def _match_candidate_by_narrator_preference(
    narrator_preference: str | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_preference = _normalize_simple_audiobook_text(narrator_preference)
    if not normalized_preference:
        return None

    matched = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        narrator = _normalize_simple_audiobook_text(candidate.get("narrator", ""))
        if not narrator:
            continue
        if (
            narrator == normalized_preference
            or normalized_preference in narrator
            or narrator in normalized_preference
        ):
            matched.append(candidate)
    if len(matched) == 1:
        return matched[0]
    return None


def _series_neighbor_clarification_candidates(
    intent,
    scored: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not _should_expand_series_neighbors(intent, scored):
        return []

    top_candidate = scored[0]
    series_name = _first_series_name(top_candidate)
    if not series_name:
        return []

    normalized_series = _normalize_simple_audiobook_text(series_name)
    series_candidates = [
        candidate
        for candidate in scored
        if isinstance(candidate, dict)
        and (sequence := _extract_series_sequence(candidate, normalized_series=normalized_series)) is not None
        and 0 < sequence <= _SERIES_NEIGHBOR_WINDOW
    ]
    if len(series_candidates) < 2:
        return []

    series_candidates.sort(
        key=lambda item: (
            _extract_series_sequence(item, normalized_series=normalized_series) or 0,
            -int(item.get("score", 0)),
        )
    )
    return series_candidates


def _should_expand_series_neighbors(intent, candidates: list[dict[str, Any]]) -> bool:
    if not candidates:
        return False
    if getattr(intent, "series", None) or getattr(intent, "ordinal", None):
        return False
    if getattr(intent, "narrator_preference", None):
        return False

    requested_title = _normalize_simple_audiobook_text(getattr(intent, "title", "") or "")
    if not requested_title:
        return False

    top_candidate = candidates[0]
    top_title = _normalize_simple_audiobook_text(top_candidate.get("title", "") or "")
    if top_title != requested_title:
        return False
    if _extract_series_sequence(top_candidate) != 1:
        return False
    return bool(_first_series_name(top_candidate))


def _merge_series_neighbors(
    candidates: list[dict[str, Any]],
    series_candidates: list[dict[str, Any]],
    *,
    series_name: str,
    max_sequence: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    normalized_series = _normalize_simple_audiobook_text(series_name)

    def _append(item: dict[str, Any]) -> None:
        library_item_id = str(item.get("library_item_id", "")).strip()
        if not library_item_id or library_item_id in seen_ids:
            return
        seen_ids.add(library_item_id)
        merged.append(item)

    for item in candidates:
        if isinstance(item, dict):
            _append(item)

    for item in series_candidates:
        if not isinstance(item, dict):
            continue
        sequence = _extract_series_sequence(item, normalized_series=normalized_series)
        if sequence is None or sequence <= 0 or sequence > max_sequence:
            continue
        _append(item)

    return merged


def _first_series_name(candidate: dict[str, Any]) -> str:
    series_entries = candidate.get("series")
    if not isinstance(series_entries, list):
        return ""
    for entry in series_entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        sequence = str(entry.get("sequence", "")).strip()
        if name and sequence:
            return name
    return ""


def _extract_series_sequence(candidate: dict[str, Any], *, normalized_series: str | None = None) -> int | None:
    series_entries = candidate.get("series")
    if not isinstance(series_entries, list):
        return None
    for entry in series_entries:
        if not isinstance(entry, dict):
            continue
        name = _normalize_simple_audiobook_text(entry.get("name", ""))
        sequence = str(entry.get("sequence", "")).strip()
        if normalized_series and name and name != normalized_series:
            continue
        if re.fullmatch(r"\d+", sequence):
            parsed = int(sequence)
            if parsed > 0:
                return parsed
    return None


def _normalize_simple_audiobook_text(value: Any) -> str:
    text = " ".join(str(value).strip().lower().split())
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text.strip()


def _lookup_series_entry(
    dispatch: DispatchPlan,
    *,
    series: str | None,
    ordinal: int | None,
    user_id: str | None = None,
    find_series=find_audiobook_series_entry,
) -> DispatchPlan:
    if not series or ordinal is None or ordinal <= 0:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "series_lookup",
            "error": "audiobook_unrecognized",
            "detail": "Oracle could not parse that audiobook series request.",
        }
        return dispatch

    try:
        match = find_series(series, ordinal, user_id=user_id)
    except Exception as exc:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "series_lookup",
            "series": series,
            "ordinal": ordinal,
            "error": "audiobook_search_failed",
            "detail": str(exc),
        }
        return dispatch

    if match is None:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "series_lookup",
            "series": series,
            "ordinal": ordinal,
            "error": "audiobook_not_found",
            "detail": "No matching series entry was found in Audiobookshelf.",
        }
        return dispatch

    dispatch.status = "executed"
    dispatch.result = {
        "action": "series_lookup",
        "series": series,
        "ordinal": ordinal,
        "match": {
            "title": match.get("title"),
            "author": match.get("author"),
            "subtitle": match.get("subtitle"),
            "library_item_id": match.get("library_item_id"),
        },
    }
    return dispatch


def _play_selected(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    session_id: str | None,
    user_id: str | None = None,
    selection: dict[str, Any],
    sleep_timer_seconds: int | None = None,
    defer_audible_start: bool | None = None,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> DispatchPlan:
    should_defer = bool(source) if defer_audible_start is None else defer_audible_start
    fetch_item = fetch_audiobook_item if canonical_execution is None else canonical_execution.fetch_item
    open_session = (
        open_audiobook_playback_session
        if canonical_execution is None
        else canonical_execution.open_playback_session
    )
    payload_builder = build_longform_payload if canonical_execution is None else canonical_execution.build_longform_payload
    command = execute_satellite_command if canonical_execution is None else canonical_execution.execute_satellite_command
    close_session = close_audiobook_session if canonical_execution is None else canonical_execution.close_session
    music_interrupt = _interrupt_active_music_for_audiobook(
        source,
        canonical_execution=canonical_execution,
    )
    if music_interrupt is not None and music_interrupt.get("status") != "executed":
        dispatch.status = "failed"
        dispatch.result = {
            "action": "play",
            "error": "music_interrupt_failed",
            "detail": str(music_interrupt.get("detail") or "Failed to safely stop active music playback."),
            "music": music_interrupt,
            "selected": selection,
        }
        return dispatch
    status, result = play_selected_audiobook(
        source=source,
        session_id=session_id,
        user_id=user_id,
        selection=selection,
        sleep_timer_seconds=sleep_timer_seconds,
        defer_audible_start=should_defer,
        fetch_audiobook_item=fetch_item,
        open_audiobook_playback_session=open_session,
        build_longform_payload=lambda session: payload_builder(
            session,
            source=str(source or ""),
            user_id=user_id,
            start_paused=should_defer,
        ),
        register_active_playback=audiobook_state.register_active_audiobook_playback,
        clear_active_playback=audiobook_state.clear_active_audiobook_playback,
        execute_satellite_command=command,
        close_audiobook_session=close_session,
        create_sleep_timer=lambda current_source, current_session_id, duration: _create_sleep_timer(
            source=current_source,
            session_id=current_session_id,
            duration_seconds=duration,
        ),
    )
    dispatch.status = status
    dispatch.result = result
    return dispatch


def _interrupt_active_music_for_audiobook(
    source: str | None,
    *,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> dict[str, Any] | None:
    if not source:
        return None
    try:
        music_session = (
            fetch_satellite_music_session(source)
            if canonical_execution is None
            else canonical_execution.fetch_satellite_music_session(source)
        )
    except HTTPException:
        return None
    except RuntimeError:
        return None
    if not isinstance(music_session, dict):
        return None
    music_state = str(music_session.get("state", "")).strip().lower()
    if music_state not in {"playing", "starting", "buffering", "stopping"}:
        return None
    try:
        stop_result = (
            execute_satellite_command(source, "stop", None)
            if canonical_execution is None
            else canonical_execution.execute_satellite_command(source, "stop", None)
        )
    except RuntimeError as exc:
        return build_control_plane_failure(
            action="stop",
            exc=exc,
            status="failed",
            music=music_session,
        )
    return {
        "status": "executed",
        "action": "stop",
        "satellite": stop_result,
        "music": music_session,
    }


def play_selected_dispatch(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    session_id: str | None,
    user_id: str | None,
    selection: dict[str, Any],
    sleep_timer_seconds: int | None = None,
    defer_audible_start: bool | None = None,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> DispatchPlan:
    dispatch.target = "audiobook"
    dispatch.hook = "audiobook.execute"
    return _play_selected(
        dispatch,
        source=source,
        session_id=session_id,
        user_id=user_id,
        selection=selection,
        sleep_timer_seconds=sleep_timer_seconds,
        defer_audible_start=defer_audible_start,
        canonical_execution=canonical_execution,
    )


def _pause_audiobook(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> DispatchPlan:
    return _sync_then_control(
        dispatch,
        source=source,
        action="pause_longform_audio",
        close_session=False,
        canonical_execution=canonical_execution,
    )


def _resume_active_audiobook(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    session_id: str | None,
    user_id: str | None = None,
    defer_audible_start: bool | None = None,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> DispatchPlan:
    fetch_progress = (
        fetch_current_audiobook_progress
        if canonical_execution is None
        else canonical_execution.fetch_current_progress
    )
    progress = normalize_audiobook_progress(fetch_progress(user_id=user_id))
    library_item_id = str((progress or {}).get("library_item_id", "")).strip()
    if not library_item_id:
        dispatch.status = "failed"
        dispatch.result = {
            "action": "resume",
            "error": "audiobook_not_found",
            "detail": "No in-progress audiobook was found.",
        }
        return dispatch

    return _play_selected(
        dispatch,
        source=source,
        session_id=session_id,
        user_id=user_id,
        selection={"library_item_id": library_item_id},
        sleep_timer_seconds=None,
        defer_audible_start=defer_audible_start,
        canonical_execution=canonical_execution,
    )


def _stop_audiobook(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> DispatchPlan:
    result = _sync_then_control(
        dispatch,
        source=source,
        action="stop_longform_audio",
        close_session=True,
        canonical_execution=canonical_execution,
    )
    if result.status == "executed":
        canceled = cancel_alerts(source, SLEEP_TIMER_KIND, all_matches=True)
        if canceled:
            (result.result or {})["sleep_timer_canceled"] = canceled
    return result


def _what_is_playing(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> DispatchPlan:
    try:
        result = _fetch_audiobook_now_playing(source, canonical_execution=canonical_execution)
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
        "now_playing": result,
    }
    return dispatch


def _fetch_audiobook_now_playing(
    source: str | None,
    *,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> dict[str, Any]:
    try:
        session = (
            fetch_satellite_audiobook_session(source)
            if canonical_execution is None
            else canonical_execution.fetch_satellite_audiobook_session(source)
        )
    except RuntimeError:
        session = None
    if isinstance(session, dict):
        return {
            "ok": True,
            "playing": str(session.get("state", "")).strip().lower() in {"playing", "paused", "starting", "stopping"},
            "state": session.get("state"),
            "playback_id": session.get("session_id"),
            "title": session.get("title", ""),
            "author": session.get("artist_or_author", ""),
            "position_seconds": session.get("position_seconds"),
            "duration_seconds": session.get("duration_seconds"),
        }
    if canonical_execution is None:
        return execute_satellite_command(source, "get_longform_state")
    return canonical_execution.execute_satellite_command(source, "get_longform_state")


def _set_sleep_timer(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    session_id: str | None,
    duration_seconds: int | None,
) -> DispatchPlan:
    status, result = set_audiobook_sleep_timer(
        source=source,
        session_id=session_id,
        duration_seconds=duration_seconds,
        get_active_playback_for_source=audiobook_state.get_active_audiobook_playback_for_source,
        create_sleep_timer=lambda current_source, current_session_id, duration: _create_sleep_timer(
            source=current_source,
            session_id=current_session_id,
            duration_seconds=duration,
        ),
    )
    dispatch.status = status
    dispatch.result = result
    return dispatch


def _cancel_sleep_timer(dispatch: DispatchPlan, *, source: str | None) -> DispatchPlan:
    status, result = cancel_audiobook_sleep_timer(
        source=source,
        cancel_alerts=cancel_alerts,
        kind=SLEEP_TIMER_KIND,
    )
    dispatch.status = status
    dispatch.result = result
    return dispatch


def _sleep_timer_status(dispatch: DispatchPlan, *, source: str | None) -> DispatchPlan:
    status, result = audiobook_sleep_timer_status(
        source=source,
        list_alerts=list_alerts,
        kind=SLEEP_TIMER_KIND,
    )
    dispatch.status = status
    dispatch.result = result
    return dispatch


def _create_sleep_timer(*, source: str | None, session_id: str | None, duration_seconds: int) -> dict[str, Any]:
    return create_audiobook_sleep_timer(
        source=source,
        session_id=session_id,
        duration_seconds=duration_seconds,
        cancel_alerts=cancel_alerts,
        create_alert=create_alert,
        format_duration=format_duration,
        kind=SLEEP_TIMER_KIND,
    )


def _now_plus(duration_seconds: int):
    return audiobook_now_plus(duration_seconds)


def _sync_then_control(
    dispatch: DispatchPlan,
    *,
    source: str | None,
    action: str,
    close_session: bool,
    canonical_execution: CanonicalAudiobookExecution | None = None,
) -> DispatchPlan:
    command = execute_satellite_command if canonical_execution is None else canonical_execution.execute_satellite_command
    close_provider_session = close_audiobook_session if canonical_execution is None else canonical_execution.close_session
    sync_provider_session = sync_audiobook_session if canonical_execution is None else canonical_execution.sync_session
    status, result = sync_then_control_audiobook(
        source=source,
        action=action,
        close_session=close_session,
        get_active_playback_for_source=audiobook_state.get_active_audiobook_playback_for_source,
        execute_satellite_command=command,
        close_audiobook_session=close_provider_session,
        sync_audiobook_session=sync_provider_session,
        clear_active_playback=audiobook_state.clear_active_audiobook_playback,
    )
    dispatch.status = status
    dispatch.result = result
    return dispatch


def _build_clarification_prompt(options: list[dict[str, Any]]) -> str:
    return build_audiobook_clarification_prompt(options)


def _match_pending_candidate(text: str, pending: dict[str, Any] | None) -> dict[str, Any] | None:
    return match_pending_audiobook_candidate(text, pending)


def _build_narrowed_pending_payload(
    pending: dict[str, Any],
    *,
    narrowed_candidates: list[dict[str, Any]],
    narrowing: dict[str, Any],
) -> dict[str, Any]:
    previous_candidates = pending.get("candidates")
    previous_candidate_count = len(previous_candidates) if isinstance(previous_candidates, list) else 0
    excluded_candidate_ids = []
    if isinstance(previous_candidates, list):
        remaining_ids = {
            str(item.get("library_item_id", "")).strip()
            for item in narrowed_candidates
            if isinstance(item, dict)
        }
        for item in previous_candidates:
            if not isinstance(item, dict):
                continue
            library_item_id = str(item.get("library_item_id", "")).strip()
            if library_item_id and library_item_id not in remaining_ids:
                excluded_candidate_ids.append(library_item_id)

    narrowing_kind = "unknown"
    normalized_text = str(narrowing.get("normalized_text", "")).strip().lower()
    if normalized_text in {"not that one", "not this one", "not the one"}:
        narrowing_kind = "negative_pronoun"
    elif normalized_text.startswith("not the ") or normalized_text.startswith("not first") or normalized_text.startswith("not second") or normalized_text.startswith("not third"):
        narrowing_kind = "negative_ordinal"
    elif normalized_text.startswith("not "):
        narrowing_kind = "negative_phrase"

    return {
        "intent": pending.get("intent"),
        "candidates": narrowed_candidates,
        "narrowed": True,
        "previous_candidate_count": previous_candidate_count,
        "narrowing": {
            "kind": narrowing_kind,
            "excluded_candidate_ids": excluded_candidate_ids,
        },
    }


class AudiobookHandler:
    target = "audiobook"

    def __init__(
        self,
        household_settings: HouseholdRuntimeSettings | None = None,
        *,
        canonical_playback_target: bool = False,
        canonical_execution: CanonicalAudiobookExecution | None = None,
        canonical_authority: bool = False,
    ) -> None:
        self.household_settings = household_settings
        self.canonical_playback_target = canonical_playback_target
        self.canonical_execution = canonical_execution
        self.canonical_authority = canonical_authority

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        return execute_audiobook(
            dispatch,
            household_settings=self.household_settings,
            canonical_playback_target=self.canonical_playback_target,
            canonical_execution=self.canonical_execution,
            canonical_authority=self.canonical_authority,
        )
