from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from oracle_app import audiobook_state
from oracle_app.music_runtime.control import build_control_plane_failure
from oracle_app.provider_bridges.audiobookshelf_audiobook import (
    normalize_audiobook_item,
    normalize_audiobook_playback_session,
)


logger = logging.getLogger(__name__)
_AUDIOBOOK_SYNC_RETRY_DELAYS_SECONDS = (0.0, 1.0, 4.0)
_AUDIOBOOK_SYNC_FAILURE_WINDOW_SECONDS = 10.0


def play_selected(
    *,
    source: str | None,
    session_id: str | None,
    user_id: str | None,
    selection: dict[str, Any],
    sleep_timer_seconds: int | None,
    fetch_audiobook_item: Callable[..., dict[str, Any]],
    open_audiobook_playback_session: Callable[..., dict[str, Any]],
    build_longform_payload: Callable[[dict[str, Any]], tuple[str, dict[str, Any], dict[str, Any]]],
    register_active_playback: Callable[[str, dict[str, Any]], None],
    clear_active_playback: Callable[[str], None],
    execute_satellite_command: Callable[[str | None, str, dict[str, Any] | None], dict[str, Any]],
    close_audiobook_session: Callable[..., None],
    create_sleep_timer: Callable[[str | None, str | None, int], dict[str, Any]],
    defer_audible_start: bool = False,
) -> tuple[str, dict[str, Any]]:
    library_item_id = str(selection.get("library_item_id", "")).strip()
    if not library_item_id:
        return "failed", {
            "action": "play",
            "error": "audiobook_not_found",
            "detail": "Audiobook selection was missing a library item id.",
        }

    try:
        item = normalize_audiobook_item(fetch_audiobook_item(library_item_id, user_id=user_id), fallback_id=library_item_id)
        session = normalize_audiobook_playback_session(
            open_audiobook_playback_session(library_item_id, user_id=user_id),
            fallback_library_item_id=library_item_id,
        )
        playback_id, longform_payload, active_payload = build_longform_payload(session)
        progress = item.get("progress") or {}
        if isinstance(progress, dict) and not bool(progress.get("finished")):
            progress_time = float(progress.get("current_time_seconds") or 0)
            if progress_time > longform_payload["start_position_seconds"]:
                longform_payload["start_position_seconds"] = progress_time
        register_active_playback(playback_id, active_payload)
        command_result = execute_satellite_command(source, "play_longform_audio", longform_payload)
        sleep_timer = None
        if sleep_timer_seconds is not None and sleep_timer_seconds > 0:
            try:
                sleep_timer = create_sleep_timer(source, session_id, sleep_timer_seconds)
            except Exception as exc:
                _cleanup_failed_playback_start(
                    source=source,
                    playback_id=playback_id,
                    session=session,
                    longform_payload=longform_payload,
                    user_id=user_id,
                    execute_satellite_command=execute_satellite_command,
                    clear_active_playback=clear_active_playback,
                    close_audiobook_session=close_audiobook_session,
                )
                return "failed", {
                    "action": "play",
                    "error": "audiobook_sleep_timer_failed",
                    "detail": str(exc),
                    "selected": selection,
                    "playback_stopped": True,
                }
    except RuntimeError as exc:
        _cleanup_failed_playback_start(
            source=source,
            playback_id=locals().get("playback_id"),
            session=locals().get("session"),
            longform_payload=locals().get("longform_payload"),
            user_id=user_id,
            execute_satellite_command=execute_satellite_command,
            clear_active_playback=clear_active_playback,
            close_audiobook_session=close_audiobook_session,
        )
        return "failed", build_control_plane_failure(
            action="play",
            exc=exc,
            selected=selection,
        )
    except Exception as exc:
        _cleanup_failed_playback_start(
            source=source,
            playback_id=locals().get("playback_id"),
            session=locals().get("session"),
            longform_payload=locals().get("longform_payload"),
            user_id=user_id,
            execute_satellite_command=execute_satellite_command,
            clear_active_playback=clear_active_playback,
            close_audiobook_session=close_audiobook_session,
        )
        return "failed", {
            "action": "play",
            "error": "audiobook_playback_failed",
            "detail": str(exc),
            "selected": selection,
        }

    result = {
        "action": "play",
        "selected": {
            "title": longform_payload.get("title"),
            "author": longform_payload.get("author"),
            "library_item_id": library_item_id,
            "start_position_seconds": longform_payload.get("start_position_seconds"),
        },
        "satellite": command_result,
    }
    if defer_audible_start:
        result["deferred_audible_start"] = True
        result["deferred_session"] = {
            "kind": "audiobook",
            "backend_type": "oracle_audiobook",
            "session_id": str(playback_id).strip(),
            "resume_action": "resume_longform_audio",
        }
    if sleep_timer is not None:
        result["sleep_timer"] = sleep_timer
    return "executed", result


def _cleanup_failed_playback_start(
    *,
    source: str | None,
    playback_id: Any,
    session: Any,
    longform_payload: Any,
    user_id: str | None,
    execute_satellite_command: Callable[[str | None, str, dict[str, Any] | None], dict[str, Any]],
    clear_active_playback: Callable[[str], None],
    close_audiobook_session: Callable[..., None],
) -> None:
    normalized_playback_id = str(playback_id or "").strip()
    if normalized_playback_id:
        try:
            execute_satellite_command(source, "stop_longform_audio", None)
        except Exception:
            pass
        clear_active_playback(normalized_playback_id)
    if not isinstance(session, dict):
        return
    session = normalize_audiobook_playback_session(session)
    provider_session_id = str(session.get("provider_session_id", "")).strip()
    if not provider_session_id:
        return
    if isinstance(longform_payload, dict):
        position_seconds = _safe_float(longform_payload.get("start_position_seconds"), default=0.0)
        duration_seconds = _safe_float(longform_payload.get("duration_seconds"), default=0.0)
    else:
        position_seconds = _safe_float(session.get("current_time_seconds"), default=0.0)
        duration_seconds = _safe_float(session.get("duration_seconds"), default=0.0)
    try:
        close_audiobook_session(
            provider_session_id,
            current_time=position_seconds,
            time_listened=0.0,
            duration=duration_seconds,
            user_id=user_id,
        )
    except Exception:
        pass


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sync_then_control(
    *,
    source: str | None,
    action: str,
    close_session: bool,
    require_sync_success: bool = False,
    defer_sync: bool = False,
    get_active_playback_for_source: Callable[[str | None], dict[str, Any] | None],
    execute_satellite_command: Callable[[str | None, str, dict[str, Any] | None], dict[str, Any]],
    close_audiobook_session: Callable[..., None],
    sync_audiobook_session: Callable[..., None],
    clear_active_playback: Callable[[str], None],
) -> tuple[str, dict[str, Any]]:
    active = get_active_playback_for_source(source)
    position_seconds = 0.0
    time_listened = 0.0
    duration_seconds = 0.0
    if active is not None:
        duration_seconds = float(active.get("duration_seconds") or 0)
        fallback_position_seconds = _safe_float(active.get("start_position_seconds"), default=0.0)
        position_seconds = fallback_position_seconds
        time_listened = 0.0
        try:
            status = execute_satellite_command(source, "get_longform_state", None)
            status_position = _safe_float(status.get("position_seconds"), default=fallback_position_seconds)
            if status_position > 0 or fallback_position_seconds <= 0:
                position_seconds = status_position
            time_listened = max(0.0, position_seconds - fallback_position_seconds)
        except RuntimeError:
            position_seconds = fallback_position_seconds
            time_listened = 0.0

    try:
        result = execute_satellite_command(source, action, None)
        if active is not None:
            provider_session_id = str(active.get("provider_session_id") or active.get("abs_session_id") or "").strip()
            if provider_session_id:
                try:
                    if close_session:
                        close_audiobook_session(
                            provider_session_id,
                            current_time=position_seconds,
                            time_listened=time_listened,
                            duration=duration_seconds,
                            user_id=str(active.get("user_id") or "").strip() or None,
                        )
                        clear_active_playback(str(active.get("playback_id", "")).strip())
                    else:
                        if defer_sync:
                            _queue_deferred_audiobook_sync(
                                sync_id=_build_audiobook_sync_id(active, provider_session_id),
                                source=source,
                                active=active,
                                provider_session_id=provider_session_id,
                                position_seconds=position_seconds,
                                time_listened=time_listened,
                                duration_seconds=duration_seconds,
                                sync_audiobook_session=sync_audiobook_session,
                            )
                        else:
                            sync_audiobook_session(
                                provider_session_id,
                                current_time=position_seconds,
                                time_listened=time_listened,
                                duration=duration_seconds,
                                user_id=str(active.get("user_id") or "").strip() or None,
                            )
                except Exception as exc:
                    if require_sync_success:
                        return "failed", {
                            "action": action,
                            "error": "audiobook_sync_failed",
                            "detail": f"Audiobookshelf sync failed after {action}: {exc}",
                            "position_seconds": position_seconds,
                            "duration_seconds": duration_seconds,
                        }
                    logger.warning("Audiobook session sync failed after %s: %s", action, exc)
    except RuntimeError as exc:
        return "failed", build_control_plane_failure(
            action=action,
            exc=exc,
        )

    return "executed", {
        "action": "pause" if action == "pause_longform_audio" else "stop",
        "satellite": result,
        "audiobook_sync": (
            {
                "status": "pending",
                "failure_window_seconds": _AUDIOBOOK_SYNC_FAILURE_WINDOW_SECONDS,
            }
            if defer_sync
            and not close_session
            and active is not None
            and str(active.get("provider_session_id") or active.get("abs_session_id") or "").strip()
            else None
        ),
    }


def _queue_deferred_audiobook_sync(
    *,
    sync_id: str,
    source: str | None,
    active: dict[str, Any],
    provider_session_id: str,
    position_seconds: float,
    time_listened: float,
    duration_seconds: float,
    sync_audiobook_session: Callable[..., None],
) -> None:
    now = time.time()
    audiobook_state.upsert_pending_audiobook_sync(
        sync_id,
        {
            "sync_id": sync_id,
            "source": str(source or "").strip(),
            "playback_id": str(active.get("playback_id", "")).strip(),
            "provider_session_id": provider_session_id,
            "position_seconds": float(position_seconds),
            "time_listened": float(time_listened),
            "duration_seconds": float(duration_seconds),
            "status": "pending",
            "attempt_count": 0,
            "created_at": now,
            "deadline_at": now + _AUDIOBOOK_SYNC_FAILURE_WINDOW_SECONDS,
            "last_error": "",
        },
    )
    thread = threading.Thread(
        target=_run_deferred_audiobook_sync,
        kwargs={
            "sync_id": sync_id,
            "provider_session_id": provider_session_id,
            "position_seconds": position_seconds,
            "time_listened": time_listened,
            "duration_seconds": duration_seconds,
            "sync_audiobook_session": sync_audiobook_session,
        },
        daemon=True,
        name=f"audiobook-sync-{sync_id[:12]}",
    )
    thread.start()


def _run_deferred_audiobook_sync(
    *,
    sync_id: str,
    provider_session_id: str,
    position_seconds: float,
    time_listened: float,
    duration_seconds: float,
    sync_audiobook_session: Callable[..., None],
) -> None:
    start = time.time()
    last_error = ""
    for attempt_index, delay_seconds in enumerate(_AUDIOBOOK_SYNC_RETRY_DELAYS_SECONDS, start=1):
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            sync_audiobook_session(
                provider_session_id,
                current_time=position_seconds,
                time_listened=time_listened,
                duration=duration_seconds,
            )
        except Exception as exc:
            last_error = str(exc)
            audiobook_state.mark_pending_audiobook_sync_status(
                sync_id,
                status="pending",
                attempt_count=attempt_index,
                last_error=last_error,
            )
            if time.time() - start >= _AUDIOBOOK_SYNC_FAILURE_WINDOW_SECONDS:
                break
            continue
        audiobook_state.mark_pending_audiobook_sync_status(
            sync_id,
            status="synced",
            attempt_count=attempt_index,
            last_error="",
            synced_at=time.time(),
        )
        logger.info("Deferred Audiobookshelf sync succeeded sync_id=%s attempts=%s", sync_id, attempt_index)
        return

    audiobook_state.mark_pending_audiobook_sync_status(
        sync_id,
        status="failed",
        attempt_count=len(_AUDIOBOOK_SYNC_RETRY_DELAYS_SECONDS),
        last_error=last_error,
        failed_at=time.time(),
    )
    logger.warning("Deferred Audiobookshelf sync failed sync_id=%s detail=%s", sync_id, last_error or "unknown")


def _build_audiobook_sync_id(active: dict[str, Any], provider_session_id: str) -> str:
    playback_id = str(active.get("playback_id", "")).strip()
    if playback_id:
        return playback_id
    return provider_session_id
