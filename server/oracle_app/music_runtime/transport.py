from __future__ import annotations

from typing import Any, Callable

from .control import build_control_plane_failure


def resolve_authority_transport_targets(
    action: str,
    authority_state: dict[str, Any],
) -> dict[str, bool]:
    music_match = False
    audiobook_match = False
    reply_audio_match = False
    for session in _authority_sessions(authority_state):
        if not isinstance(session, dict):
            continue
        media_kind = str(session.get("media_kind", "")).strip().lower()
        backend_type = str(session.get("backend_type", "")).strip().lower()
        if media_kind == "music" and _authority_session_matches(session, action):
            music_match = True
        elif backend_type == "oracle_audiobook" and _authority_session_matches(session, action):
            audiobook_match = True
        elif backend_type == "reply_audio" and _authority_session_matches(session, action):
            reply_audio_match = True
    return {
        "music": music_match,
        "audiobook": audiobook_match,
        "reply_audio": reply_audio_match,
        "ambiguous": action == "resume" and music_match and audiobook_match,
    }


def is_dual_active_music_audiobook_target(
    action: str,
    targets: dict[str, bool] | None,
) -> bool:
    if action not in {"pause", "stop"} or not isinstance(targets, dict):
        return False
    return bool(targets.get("music")) and bool(targets.get("audiobook"))


def execute_transport(
    *,
    source: str | None,
    action: str,
    args: dict[str, Any] | None,
    normalized_text: str,
    execute_satellite_command: Callable[[str | None, str, dict[str, Any] | None], dict[str, Any]],
    fetch_satellite_playback_authority: Callable[[str | None], dict[str, Any]],
    fetch_satellite_music_session: Callable[[str | None], dict[str, Any] | None],
    fetch_satellite_audiobook_session: Callable[[str | None], dict[str, Any] | None],
    fetch_satellite_reply_audio_session: Callable[[str | None], dict[str, Any] | None],
) -> tuple[str, dict[str, Any]]:
    authority_state = _safe_fetch_playback_authority(source, fetch_satellite_playback_authority)
    bare_transport = normalized_text == action
    authority_targets = (
        resolve_authority_transport_targets(action, authority_state)
        if bare_transport and authority_state is not None
        else None
    )
    if authority_targets is None and bare_transport:
        authority_targets = _resolve_fallback_transport_targets(
            source=source,
            action=action,
            fetch_satellite_music_session=fetch_satellite_music_session,
            fetch_satellite_audiobook_session=fetch_satellite_audiobook_session,
            fetch_satellite_reply_audio_session=fetch_satellite_reply_audio_session,
        )
    if authority_targets is not None and authority_targets.get("ambiguous"):
        return "failed", {
            "action": action,
            "error": "ambiguous_transport_target",
            "detail": "More than one resumable playback target is available.",
        }
    dual_active_degraded_state = _authority_dual_active_music_audiobook(authority_state)
    if not dual_active_degraded_state and authority_state is None:
        dual_active_degraded_state = is_dual_active_music_audiobook_target(action, authority_targets)
    should_send_music = should_send_music_transport(
        action=action,
        bare_transport=bare_transport,
        authority_state=authority_state,
        fetch_satellite_music_session=fetch_satellite_music_session,
        source=source,
    )

    result: dict[str, Any] | None = None
    if should_send_music:
        try:
            result = execute_satellite_command(source, action, args)
        except RuntimeError as exc:
            return "failed", build_control_plane_failure(
                action=action,
                exc=exc,
            )

    secondary_longform = maybe_transport_longform(
        source=source,
        action=action,
        normalized_text=normalized_text,
        authority_state=authority_state,
        authority_targets=authority_targets,
        execute_satellite_command=execute_satellite_command,
        fetch_satellite_audiobook_session=fetch_satellite_audiobook_session,
    )
    secondary_reply_audio = maybe_transport_reply_audio(
        source=source,
        action=action,
        normalized_text=normalized_text,
        authority_state=authority_state,
        execute_satellite_command=execute_satellite_command,
        fetch_satellite_reply_audio_session=fetch_satellite_reply_audio_session,
    )

    if result is None and secondary_longform is None and secondary_reply_audio is None:
        try:
            result = execute_satellite_command(source, action, args)
        except RuntimeError as exc:
            return "failed", build_control_plane_failure(
                action=action,
                exc=exc,
            )

    payload = {
        "action": action,
        "satellite": result or {"ok": True, "state": "skipped"},
    }
    if dual_active_degraded_state:
        payload["degraded_state_fallback"] = "dual_active_pause_all" if action == "pause" else "dual_active_stop_all"
    if secondary_longform is not None:
        payload["longform"] = secondary_longform
    if secondary_reply_audio is not None:
        payload["reply_audio"] = secondary_reply_audio
    return "executed", payload


def should_send_music_transport(
    *,
    action: str,
    bare_transport: bool,
    authority_state: dict[str, Any] | None,
    fetch_satellite_music_session: Callable[[str | None], dict[str, Any] | None],
    source: str | None,
) -> bool:
    if not bare_transport:
        return True
    if action not in {"pause", "stop", "resume", "next", "previous", "restart"}:
        return True
    if authority_state is not None:
        return _authority_has_matching_session(
            authority_state,
            media_kind="music",
            action=action,
        )
    try:
        music_state = fetch_satellite_music_session(source)
    except RuntimeError:
        return False
    if not isinstance(music_state, dict):
        return False
    current_state = str(music_state.get("state", "")).strip().lower()
    playing = current_state in {"playing", "paused", "starting", "stopping"} or bool(music_state.get("resumable"))
    if action in {"pause", "stop"}:
        return current_state in {"playing", "paused", "buffering"} or playing
    if action == "resume":
        return current_state in {"paused", "buffering"} or playing
    if action in {"next", "previous", "restart"}:
        return current_state in {"playing", "paused", "buffering"} or playing
    return True


def maybe_transport_longform(
    *,
    source: str | None,
    action: str,
    normalized_text: str,
    authority_state: dict[str, Any] | None,
    authority_targets: dict[str, bool] | None,
    execute_satellite_command: Callable[[str | None, str, dict[str, Any] | None], dict[str, Any]],
    fetch_satellite_audiobook_session: Callable[[str | None], dict[str, Any] | None],
) -> dict[str, Any] | None:
    bare_transport = normalized_text == action
    if not bare_transport or action not in {"pause", "stop", "resume"}:
        return None

    if authority_state is not None:
        if authority_targets is None or not authority_targets.get("audiobook"):
            return None
    else:
        try:
            longform_state = fetch_satellite_audiobook_session(source)
        except RuntimeError:
            return None
        if not isinstance(longform_state, dict):
            return None

    if action == "pause":
        longform_action = "pause_longform_audio"
    elif action == "stop":
        longform_action = "stop_longform_audio"
    else:
        longform_action = "resume_longform_audio"
    try:
        return execute_satellite_command(source, longform_action, None)
    except RuntimeError:
        return None


def maybe_transport_reply_audio(
    *,
    source: str | None,
    action: str,
    normalized_text: str,
    authority_state: dict[str, Any] | None,
    execute_satellite_command: Callable[[str | None, str, dict[str, Any] | None], dict[str, Any]],
    fetch_satellite_reply_audio_session: Callable[[str | None], dict[str, Any] | None],
) -> dict[str, Any] | None:
    bare_transport = normalized_text in {"pause", "stop"}
    if not bare_transport or action not in {"pause", "stop"}:
        return None
    if authority_state is not None:
        if not _authority_has_matching_session(authority_state, backend_type="reply_audio", action=action):
            return None
    else:
        try:
            reply_state = fetch_satellite_reply_audio_session(source)
        except RuntimeError:
            return None
        if not isinstance(reply_state, dict):
            return None
    try:
        return execute_satellite_command(source, "stop_reply_audio", None)
    except RuntimeError:
        return None


def _safe_fetch_playback_authority(
    source: str | None,
    fetch_satellite_playback_authority: Callable[[str | None], dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        authority_state = fetch_satellite_playback_authority(source)
    except RuntimeError:
        return None
    return authority_state if isinstance(authority_state, dict) else None


def _authority_has_matching_session(
    authority_state: dict[str, Any],
    *,
    backend_type: str | None = None,
    action: str,
    media_kind: str | None = None,
) -> bool:
    for session in _authority_sessions(authority_state):
        if not isinstance(session, dict):
            continue
        if backend_type is not None and str(session.get("backend_type", "")).strip().lower() != backend_type:
            continue
        if media_kind is not None and str(session.get("media_kind", "")).strip().lower() != media_kind:
            continue
        if _authority_session_matches(session, action):
            return True
    return False


def _authority_sessions(authority_state: dict[str, Any]) -> list[dict[str, Any]]:
    sessions = authority_state.get("sessions")
    if isinstance(sessions, list):
        return [session for session in sessions if isinstance(session, dict)]
    active_sessions = authority_state.get("active_sessions")
    if isinstance(active_sessions, list):
        return [session for session in active_sessions if isinstance(session, dict)]
    return []


def _authority_session_matches(session: dict[str, Any], action: str) -> bool:
    state = str(session.get("state", "")).strip().lower()
    resumable = bool(session.get("resumable"))
    if action in {"pause", "stop"}:
        return state in {"playing", "paused", "starting", "stopping"} or resumable
    if action == "resume":
        return resumable or state in {"paused", "buffering"}
    if action in {"next", "previous", "restart"}:
        return state in {"playing", "paused", "starting", "stopping"} or resumable
    return False


def _resolve_fallback_transport_targets(
    *,
    source: str | None,
    action: str,
    fetch_satellite_music_session: Callable[[str | None], dict[str, Any] | None],
    fetch_satellite_audiobook_session: Callable[[str | None], dict[str, Any] | None],
    fetch_satellite_reply_audio_session: Callable[[str | None], dict[str, Any] | None],
) -> dict[str, bool] | None:
    try:
        music_state = fetch_satellite_music_session(source)
    except RuntimeError:
        music_state = None
    try:
        audiobook_state = fetch_satellite_audiobook_session(source)
    except RuntimeError:
        audiobook_state = None
    try:
        reply_audio_state = fetch_satellite_reply_audio_session(source)
    except RuntimeError:
        reply_audio_state = None
    return {
        "music": _fallback_session_matches(music_state, action=action, media_kind="music"),
        "audiobook": _fallback_session_matches(audiobook_state, action=action, media_kind="audiobook"),
        "reply_audio": _fallback_session_matches(reply_audio_state, action=action, media_kind="reply_audio"),
        "ambiguous": action == "resume"
        and _fallback_session_matches(music_state, action=action, media_kind="music")
        and _fallback_session_matches(audiobook_state, action=action, media_kind="audiobook"),
    }


def _fallback_session_matches(
    session: dict[str, Any] | None,
    *,
    action: str,
    media_kind: str,
) -> bool:
    if not isinstance(session, dict):
        return False
    state = str(session.get("state", "")).strip().lower()
    playing = bool(session.get("playing")) or bool(session.get("resumable"))
    if media_kind == "reply_audio":
        return action in {"pause", "stop"} and bool(session.get("playing"))
    if action in {"pause", "stop"}:
        return state in {"playing", "paused", "buffering", "starting", "stopping"} or playing
    if action == "resume":
        return state in {"paused", "buffering"} or playing
    if action in {"next", "previous", "restart"} and media_kind == "music":
        return state in {"playing", "paused", "buffering", "starting", "stopping"} or playing
    return False


def _authority_dual_active_music_audiobook(authority_state: dict[str, Any] | None) -> bool:
    if not isinstance(authority_state, dict):
        return False
    if not bool(authority_state.get("degraded_state")):
        return False
    reasons = authority_state.get("degraded_reasons")
    if not isinstance(reasons, list):
        return False
    return "dual_active_music_audiobook" in [str(reason).strip() for reason in reasons]
