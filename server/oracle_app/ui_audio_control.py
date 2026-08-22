from __future__ import annotations

from fastapi import HTTPException

from . import audiobook_state
from .alerts import cancel_alerts, create_alert, format_duration, list_alerts
from .audiobook import (
    build_longform_payload,
    close_audiobook_session,
    fetch_audiobook_item,
    fetch_current_audiobook_progress,
    open_audiobook_playback_session,
    sync_audiobook_session,
)
from .audiobook_runtime.playback import (
    play_selected as play_selected_audiobook,
    sync_then_control as sync_then_control_audiobook,
)
from .audiobook_runtime.canonical import CanonicalAudiobookExecution
from .audiobook_runtime.policy import (
    cancel_sleep_timer as cancel_audiobook_sleep_timer,
    create_sleep_timer as create_audiobook_sleep_timer,
    set_sleep_timer as set_audiobook_sleep_timer,
    sleep_timer_status as audiobook_sleep_timer_status,
)
from .provider_bridges.audiobookshelf_audiobook import normalize_audiobook_progress
from .music_runtime.control import (
    ControlPlaneError,
    build_control_plane_failure,
    execute_satellite_command,
    fetch_satellite_playback_authority,
)
from .music_runtime.playback import music_playback_selection
from .music_runtime.canonical import CanonicalMusicExecution
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .schemas import UiAudioControlRequest, UiAudioPlayRequest, UiAudioSleepTimerRequest
from .ui_audio import (
    SLEEP_TIMER_KIND,
    build_ui_audio_users,
    resolve_ui_audio_user,
    summarize_ui_playback_session,
    ui_audio_target_session_id,
)


def _normalize_ui_client_id(client_id: str) -> str:
    normalized = str(client_id or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="client_id cannot be empty")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
    if normalized.lower() != normalized or any(char not in allowed for char in normalized):
        raise HTTPException(
            status_code=400,
            detail="client_id must be lowercase, hyphen-separated, and contain only letters, numbers, and hyphens",
        )
    return normalized


def _create_ui_audiobook_sleep_timer(*, source: str | None, session_id: str | None, duration_seconds: int) -> dict[str, object]:
    return create_audiobook_sleep_timer(
        source=source,
        session_id=session_id,
        duration_seconds=duration_seconds,
        cancel_alerts=cancel_alerts,
        create_alert=create_alert,
        format_duration=format_duration,
        kind=SLEEP_TIMER_KIND,
    )


def _execute_ui_audiobook_play(
    *,
    client_id: str,
    target: str,
    result: dict[str, object],
    user_id: str | None,
    sleep_timer_minutes: int | None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> tuple[str, dict[str, object]]:
    library_item_id = str(result.get("library_item_id") or "").strip()
    if not library_item_id:
        raise HTTPException(status_code=400, detail="Audiobook result is missing library_item_id")
    sleep_timer_seconds = None
    if sleep_timer_minutes is not None and sleep_timer_minutes > 0:
        sleep_timer_seconds = int(sleep_timer_minutes) * 60
    return play_selected_audiobook(
        source=target,
        session_id=ui_audio_target_session_id(client_id, target),
        user_id=user_id,
        selection={"library_item_id": library_item_id},
        sleep_timer_seconds=sleep_timer_seconds,
        fetch_audiobook_item=fetch_audiobook_item if audiobook_execution is None else audiobook_execution.fetch_item,
        open_audiobook_playback_session=open_audiobook_playback_session if audiobook_execution is None else audiobook_execution.open_playback_session,
        build_longform_payload=lambda session: (build_longform_payload if audiobook_execution is None else audiobook_execution.build_longform_payload)(
            session,
            source=target,
            user_id=user_id,
            start_paused=False,
        ),
        register_active_playback=audiobook_state.register_active_audiobook_playback,
        clear_active_playback=audiobook_state.clear_active_audiobook_playback,
        execute_satellite_command=execute_satellite_command if audiobook_execution is None else audiobook_execution.execute_satellite_command,
        close_audiobook_session=close_audiobook_session if audiobook_execution is None else audiobook_execution.close_session,
        create_sleep_timer=lambda current_source, current_session_id, duration: _create_ui_audiobook_sleep_timer(
            source=current_source,
            session_id=current_session_id,
            duration_seconds=duration,
        ),
        defer_audible_start=False,
    )


def _stop_active_audiobook_before_ui_music(
    target: str,
    *,
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> tuple[str, dict[str, object]] | None:
    active = audiobook_state.get_active_audiobook_playback_for_source(target)
    if active is None:
        return None
    if music_execution is not None and audiobook_execution is None:
        try:
            satellite = music_execution.execute_satellite_command(target, "stop_longform_audio", None)
        except ControlPlaneError as exc:
            return "failed", build_control_plane_failure(action="stop", exc=exc)
        audiobook_state.clear_active_audiobook_playback(target)
        return "executed", {
            "action": "stop",
            "satellite": satellite,
            "warning": "audiobook_provider_disabled",
        }
    return sync_then_control_audiobook(
        source=target,
        action="stop_longform_audio",
        close_session=True,
        get_active_playback_for_source=audiobook_state.get_active_audiobook_playback_for_source,
        execute_satellite_command=execute_satellite_command if music_execution is None else music_execution.execute_satellite_command,
        close_audiobook_session=close_audiobook_session if audiobook_execution is None else audiobook_execution.close_session,
        sync_audiobook_session=sync_audiobook_session if audiobook_execution is None else audiobook_execution.sync_session,
        clear_active_playback=audiobook_state.clear_active_audiobook_playback,
    )


def _execute_ui_music_play(
    *,
    target: str,
    result: dict[str, object],
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> tuple[str, dict[str, object]]:
    media_type = str(result.get("media_type") or "").strip().lower()
    if media_type not in {"track", "album"}:
        raise HTTPException(status_code=400, detail="Music result must be a track or album")
    selection = music_playback_selection({**result, "media_type": media_type, "type": media_type})
    from oracle_app.handlers.music import _build_play_media_args

    args = _build_play_media_args(target, selection, canonical_execution=music_execution)
    audiobook_stop_result = _stop_active_audiobook_before_ui_music(target, music_execution=music_execution, audiobook_execution=audiobook_execution)
    if audiobook_stop_result is not None:
        stop_status, stop_payload = audiobook_stop_result
        if stop_status != "executed":
            return "failed", {
                "action": "play",
                "error": "active_audiobook_stop_failed",
                "detail": "Oracle could not stop the active audiobook before starting music.",
                "selected": selection,
                "audiobook_stop": stop_payload,
            }
    try:
        command = execute_satellite_command if music_execution is None else music_execution.execute_satellite_command
        satellite = command(target, "play_media", args)
    except ControlPlaneError as exc:
        return "failed", build_control_plane_failure(action="play", exc=exc, selected=selection)
    return "executed", {
        "action": "play",
        "selected": {
            "title": selection.get("title"),
            "artist": selection.get("artist"),
            "album": selection.get("album"),
            "media_type": media_type,
        },
        "satellite": satellite,
        "audiobook_stop": audiobook_stop_result[1] if audiobook_stop_result is not None else None,
    }


def _execute_ui_audio_control(
    *,
    target: str,
    operation: str,
    media_kind: str | None = None,
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> tuple[str, dict[str, object]]:
    normalized_operation = str(operation or "").strip().lower()
    if normalized_operation not in {"pause", "resume", "stop", "volume_up", "volume_down"}:
        raise HTTPException(status_code=400, detail="Audio control operation must be pause, resume, stop, volume_up, or volume_down")

    normalized_kind = str(media_kind or "").strip().lower()
    if normalized_kind not in {"audiobook", "music"}:
        try:
            fetch_authority = fetch_satellite_playback_authority
            if music_execution is not None and music_execution.settings.playback_target(target) is not None:
                fetch_authority = music_execution.fetch_playback_authority
            elif audiobook_execution is not None:
                fetch_authority = audiobook_execution.fetch_playback_authority
            authority = fetch_authority(target)
        except ControlPlaneError as exc:
            return "failed", build_control_plane_failure(action=normalized_operation, exc=exc)
        owner = summarize_ui_playback_session(authority.get("output_owner"))
        normalized_kind = str((owner or {}).get("media_kind") or "").strip().lower()

    if normalized_kind == "audiobook":
        if normalized_operation in {"volume_up", "volume_down"}:
            try:
                command = audiobook_execution.execute_satellite_command if audiobook_execution is not None else execute_satellite_command
                satellite = command(target, normalized_operation, None)
            except ControlPlaneError as exc:
                return "failed", build_control_plane_failure(action=normalized_operation, exc=exc)
            return "executed", {
                "action": normalized_operation,
                "media_kind": "audiobook",
                "satellite": satellite,
            }
        if normalized_operation == "resume":
            try:
                command = audiobook_execution.execute_satellite_command if audiobook_execution is not None else execute_satellite_command
                satellite = command(target, "resume_longform_audio", None)
            except ControlPlaneError as exc:
                return "failed", build_control_plane_failure(action=normalized_operation, exc=exc)
            return "executed", {
                "action": normalized_operation,
                "media_kind": "audiobook",
                "satellite": satellite,
            }
        action = "pause_longform_audio" if normalized_operation == "pause" else "stop_longform_audio"
        status, result = sync_then_control_audiobook(
            source=target,
            action=action,
            close_session=normalized_operation == "stop",
            get_active_playback_for_source=audiobook_state.get_active_audiobook_playback_for_source,
            execute_satellite_command=audiobook_execution.execute_satellite_command if audiobook_execution is not None else execute_satellite_command,
            close_audiobook_session=audiobook_execution.close_session if audiobook_execution is not None else close_audiobook_session,
            sync_audiobook_session=audiobook_execution.sync_session if audiobook_execution is not None else sync_audiobook_session,
            clear_active_playback=audiobook_state.clear_active_audiobook_playback,
        )
        if status == "executed" and normalized_operation == "stop":
            canceled = cancel_alerts(target, SLEEP_TIMER_KIND, all_matches=True)
            if canceled:
                result["sleep_timer_canceled"] = canceled
        return status, result

    if normalized_kind == "music":
        try:
            command = music_execution.execute_satellite_command if music_execution is not None else execute_satellite_command
            satellite = command(target, normalized_operation, None)
        except ControlPlaneError as exc:
            return "failed", build_control_plane_failure(action=normalized_operation, exc=exc)
        return "executed", {
            "action": normalized_operation,
            "media_kind": "music",
            "satellite": satellite,
        }

    return "failed", {
        "action": normalized_operation,
        "error": "no_active_audio",
        "detail": "No active music or audiobook playback was found on the selected target.",
    }


def _serialize_ui_audio_operation_response(
    *,
    status: str,
    result: dict[str, object],
    target: str,
    user_id: str | None = None,
) -> dict[str, object]:
    ok = status == "executed"
    detail = ""
    error = ""
    if isinstance(result, dict):
        detail = str(result.get("detail") or result.get("failure_detail") or "").strip()
        error = str(result.get("error") or result.get("failure_code") or "").strip()
    return {
        "ok": ok,
        "status": status,
        "target": target,
        "selected_user": user_id,
        "detail": detail or None,
        "error": error or None,
        "result": result,
        "refresh": {
            "refresh_pages": ["audio", "music", "audiobooks", "home"],
            "refresh_after_ms": 250,
        },
    }


def _validate_ui_action_source(
    source: str | None,
    *,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    music_execution: CanonicalMusicExecution | None = None,
) -> str:
    if source is None:
        raise HTTPException(status_code=400, detail="A playback-capable source is required for this action")
    admitted = bool(
        (audiobook_execution is not None and audiobook_execution.settings.playback_target(source) is not None)
        or (music_execution is not None and music_execution.settings.playback_target(source) is not None)
    )
    if not admitted:
        raise HTTPException(status_code=400, detail="Source is not an admitted canonical audio target")
    return str(source).strip()


def ui_audio_play_impl(
    payload: UiAudioPlayRequest,
    *,
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    household_settings: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    result = payload.result if isinstance(payload.result, dict) else {}
    result_type = str(result.get("type") or "").strip().lower()
    if result_type == "audiobook":
        if audiobook_execution is None:
            raise HTTPException(status_code=409, detail="Audiobooks are disabled in canonical configuration.")
        target = _validate_ui_action_source(payload.target, audiobook_execution=audiobook_execution)
        users, default_user_id = build_ui_audio_users(target, household_settings=household_settings)
        selected_user = resolve_ui_audio_user(payload.user_id, users, default_user_id)
        status, operation_result = _execute_ui_audiobook_play(
            client_id=client_id,
            target=target,
            result=result,
            user_id=selected_user,
            sleep_timer_minutes=payload.sleep_timer_minutes,
            audiobook_execution=audiobook_execution,
        )
    elif result_type == "music":
        if music_execution is None:
            raise HTTPException(status_code=409, detail="Music is disabled in canonical configuration.")
        target = _validate_ui_action_source(payload.target, music_execution=music_execution)
        users, default_user_id = build_ui_audio_users(target, household_settings=household_settings)
        selected_user = resolve_ui_audio_user(payload.user_id, users, default_user_id)
        if payload.sleep_timer_minutes:
            raise HTTPException(status_code=400, detail="Sleep timer is only supported for audiobook playback in this pass")
        status, operation_result = _execute_ui_music_play(target=target, result=result, music_execution=music_execution, audiobook_execution=audiobook_execution)
    else:
        raise HTTPException(status_code=400, detail="Audio play result must be audiobook or music")
    return _serialize_ui_audio_operation_response(
        status=status,
        result=operation_result,
        target=target,
        user_id=selected_user,
    )


def ui_audio_control_impl(
    payload: UiAudioControlRequest,
    *,
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> dict[str, object]:
    _client_id = _normalize_ui_client_id(payload.client_id)
    requested_kind = str(payload.media_kind or "").strip().lower()
    target = _validate_ui_action_source(
        payload.target,
        music_execution=music_execution if requested_kind != "audiobook" else None,
        audiobook_execution=audiobook_execution if requested_kind != "music" else None,
    )
    status, result = _execute_ui_audio_control(
        target=target,
        operation=payload.operation,
        media_kind=payload.media_kind,
        music_execution=music_execution,
        audiobook_execution=audiobook_execution,
    )
    return _serialize_ui_audio_operation_response(
        status=status,
        result=result,
        target=target,
    )


def ui_audio_sleep_timer_impl(
    payload: UiAudioSleepTimerRequest,
    *,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    if audiobook_execution is None:
        raise HTTPException(status_code=409, detail="Audiobooks are disabled in canonical configuration.")
    target = _validate_ui_action_source(payload.target, audiobook_execution=audiobook_execution)
    if payload.operation == "set":
        if payload.minutes is None or payload.minutes <= 0:
            raise HTTPException(status_code=400, detail="Sleep timer set operation requires positive minutes")
        status, result = set_audiobook_sleep_timer(
            source=target,
            session_id=ui_audio_target_session_id(client_id, target),
            duration_seconds=int(payload.minutes) * 60,
            get_active_playback_for_source=audiobook_state.get_active_audiobook_playback_for_source,
            create_sleep_timer=lambda current_source, current_session_id, duration: _create_ui_audiobook_sleep_timer(
                source=current_source,
                session_id=current_session_id,
                duration_seconds=duration,
            ),
        )
    elif payload.operation == "cancel":
        status, result = cancel_audiobook_sleep_timer(
            source=target,
            cancel_alerts=cancel_alerts,
            kind=SLEEP_TIMER_KIND,
        )
    else:
        status, result = audiobook_sleep_timer_status(
            source=target,
            list_alerts=list_alerts,
            kind=SLEEP_TIMER_KIND,
        )
    return _serialize_ui_audio_operation_response(
        status=status,
        result=result,
        target=target,
    )


def start_current_audiobook_for_user(
    *,
    client_id: str,
    source_id: str,
    user_id: str,
    defer_audible_start: bool = False,
    sleep_timer_seconds: int | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> dict[str, object]:
    if audiobook_execution is None:
        raise HTTPException(status_code=503, detail="Audiobooks are disabled in canonical configuration.")
    fetch_progress = audiobook_execution.fetch_current_progress
    fetch_item = audiobook_execution.fetch_item
    open_session = audiobook_execution.open_playback_session
    payload_builder = audiobook_execution.build_longform_payload
    command = audiobook_execution.execute_satellite_command
    close_session = audiobook_execution.close_session
    progress = normalize_audiobook_progress(fetch_progress(user_id=user_id))
    library_item_id = str((progress or {}).get("library_item_id") or "").strip()
    if not library_item_id:
        return {
            "ok": False,
            "status": "failed",
            "target": source_id,
            "selected_user": user_id,
            "error": "audiobook_not_found",
            "detail": "No in-progress audiobook was found.",
            "result": {},
        }
    target = _validate_ui_action_source(
        source_id,
        audiobook_execution=audiobook_execution,
    )
    status, result = play_selected_audiobook(
        source=target,
        session_id=ui_audio_target_session_id(_normalize_ui_client_id(client_id), target),
        user_id=user_id,
        selection={"library_item_id": library_item_id},
        sleep_timer_seconds=sleep_timer_seconds,
        fetch_audiobook_item=fetch_item,
        open_audiobook_playback_session=open_session,
        build_longform_payload=lambda session: payload_builder(
            session,
            source=target,
            user_id=user_id,
            start_paused=defer_audible_start,
        ),
        register_active_playback=audiobook_state.register_active_audiobook_playback,
        clear_active_playback=audiobook_state.clear_active_audiobook_playback,
        execute_satellite_command=command,
        close_audiobook_session=close_session,
        create_sleep_timer=lambda current_source, current_session_id, duration: _create_ui_audiobook_sleep_timer(
            source=current_source,
            session_id=current_session_id,
            duration_seconds=duration,
        ),
        defer_audible_start=defer_audible_start,
    )
    return _serialize_ui_audio_operation_response(
        status=status,
        result=result,
        target=target,
        user_id=user_id,
    )


def set_audiobook_sleep_timer_seconds(
    *,
    client_id: str,
    source_id: str,
    duration_seconds: int,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> dict[str, object]:
    target = _validate_ui_action_source(
        source_id,
        audiobook_execution=audiobook_execution,
    )
    if duration_seconds <= 0:
        raise HTTPException(status_code=400, detail="Sleep timer duration must be positive.")
    status, result = set_audiobook_sleep_timer(
        source=target,
        session_id=ui_audio_target_session_id(_normalize_ui_client_id(client_id), target),
        duration_seconds=int(duration_seconds),
        get_active_playback_for_source=audiobook_state.get_active_audiobook_playback_for_source,
        create_sleep_timer=lambda current_source, current_session_id, duration: _create_ui_audiobook_sleep_timer(
            source=current_source,
            session_id=current_session_id,
            duration_seconds=duration,
        ),
    )
    return _serialize_ui_audio_operation_response(
        status=status,
        result=result,
        target=target,
    )
