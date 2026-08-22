from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from .reply_audio import ReplyAudioStateStore


_OWNER_PRIORITY = {
    "reply_audio": 300,
    "oracle_audiobook": 200,
    "plexamp_external": 100,
    "oracle_native_music": 100,
}

_AUTHORITY_OWNER = "satellite.playback_authority"


def _authority_failure(
    *,
    failure_class: str,
    detail: str,
    error: str = "",
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "failure_class": failure_class,
        "owning_component": _AUTHORITY_OWNER,
        "detail": detail,
    }
    if error:
        payload["error"] = error
    payload.update(extra)
    return payload


class _InterruptionLedger:
    def __init__(self, *, stale_after_seconds: float = 300.0) -> None:
        self._stale_after_seconds = max(5.0, float(stale_after_seconds))
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], dict[str, Any]] = {}

    def register(self, session: dict[str, Any], *, default_interruption_token: str = "") -> None:
        backend_type = str(session.get("backend_type", "")).strip().lower()
        session_id = str(session.get("session_id", "")).strip()
        interruption_token = str(session.get("interruption_token", "")).strip() or str(default_interruption_token).strip()
        if not backend_type or not session_id or not interruption_token:
            return
        with self._lock:
            self._prune_locked()
            self._entries[(backend_type, session_id)] = {
                "interruption_token": interruption_token,
                "recorded_at": time.time(),
                "resume_action": str(session.get("resume_action", "")).strip(),
            }
        logging.info(
            "playback_authority_ledger_registered backend_type=%s session_id=%s interruption_token=%s resume_action=%s",
            backend_type,
            session_id,
            interruption_token,
            str(session.get("resume_action", "")).strip() or "-",
        )

    def consume_if_valid(self, *, backend_type: str, session_id: str, interruption_token: str) -> tuple[bool, str]:
        normalized_backend_type = str(backend_type).strip().lower()
        normalized_session_id = str(session_id).strip()
        normalized_token = str(interruption_token).strip()
        if not normalized_backend_type or not normalized_session_id:
            return True, "no_session_identity"
        with self._lock:
            self._prune_locked()
            entry = self._entries.get((normalized_backend_type, normalized_session_id))
            if not isinstance(entry, dict):
                return False, "missing_ledger_entry"
            expected_token = str(entry.get("interruption_token", "")).strip()
            if normalized_token and expected_token and normalized_token != expected_token:
                return False, "interruption_token_mismatch"
            self._entries.pop((normalized_backend_type, normalized_session_id), None)
            return True, "validated"

    def _prune_locked(self) -> None:
        now = time.time()
        stale_keys = [
            key
            for key, value in self._entries.items()
            if (now - float(value.get("recorded_at") or 0.0)) > self._stale_after_seconds
        ]
        for key in stale_keys:
            self._entries.pop(key, None)


_INTERRUPTION_LEDGER = _InterruptionLedger()


def build_playback_authority_state(
    *,
    adapter: Any,
    reply_audio: ReplyAudioStateStore,
    include_volume: bool = False,
) -> dict[str, Any]:
    music_backend_expectation = _adapter_music_backend_expectation(adapter)
    sessions: list[dict[str, Any]] = []
    sessions.extend(_build_reply_audio_sessions(reply_audio))
    sessions.extend(_build_longform_sessions(adapter, include_volume=include_volume))
    sessions.extend(
        _build_music_sessions(
            adapter,
            include_volume=include_volume,
            music_backend_expectation=music_backend_expectation,
        )
    )
    sessions.sort(key=lambda session: int(session.get("owner_priority") or 0), reverse=True)

    active_sessions = [session for session in sessions if _owns_output(session)]
    output_owner = active_sessions[0] if active_sessions else None
    degraded_reasons = _build_degraded_reasons(
        active_sessions,
        music_backend_expectation=music_backend_expectation,
    )
    _log_degraded_authority_state(
        degraded_reasons=degraded_reasons,
        active_sessions=active_sessions,
        output_owner=output_owner,
    )

    payload = {
        "ok": True,
        "sessions": sessions,
        "active_sessions": active_sessions,
        "output_owner": output_owner,
        "playback_active": bool(active_sessions),
        "degraded_state": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "music_backend_expectation": music_backend_expectation,
    }
    if degraded_reasons:
        payload.update(
            _authority_failure(
                failure_class="authority_mismatch",
                error="authority_state_degraded",
                detail="Playback authority detected a degraded local ownership state.",
            )
        )
    return payload


def interrupt_for_oracle(*, adapter: Any, reply_audio: ReplyAudioStateStore) -> dict[str, Any]:
    authority = build_playback_authority_state(adapter=adapter, reply_audio=reply_audio, include_volume=True)
    active_sessions = authority.get("active_sessions")
    if not isinstance(active_sessions, list):
        return {
            "ok": True,
            "interrupted_sessions": [],
            "interrupted_any": False,
            "active_session_count": 0,
            "degraded_state": bool(authority.get("degraded_state")),
            "degraded_reasons": list(authority.get("degraded_reasons") or []),
        }

    interrupted_sessions: list[dict[str, Any]] = []
    interruption_token = uuid.uuid4().hex
    active_backend_types = [
        str(session.get("backend_type", "")).strip() or str(session.get("media_kind", "")).strip() or "unknown"
        for session in active_sessions
        if isinstance(session, dict)
    ]
    logging.info(
        "playback_authority_interrupt_started interruption_token=%s active_session_count=%d active_backends=%s degraded_state=%s degraded_reasons=%s",
        interruption_token,
        len(active_sessions),
        ",".join(active_backend_types) or "-",
        str(bool(authority.get("degraded_state"))).lower(),
        ",".join(authority.get("degraded_reasons") or []) or "-",
    )
    for session in active_sessions:
        if not isinstance(session, dict):
            continue
        backend_type = str(session.get("backend_type", "")).strip().lower()
        if backend_type == "reply_audio":
            result = reply_audio.request_stop()
            interrupted_session = {
                "kind": "reply",
                "backend_type": backend_type,
                "media_kind": "reply",
                "session_id": session.get("session_id"),
                "interruption_token": interruption_token,
                "interrupted_by_session_id": "",
                "superseded_by_session_id": "",
                "interrupt_action": "stop_reply_audio",
                "resume_action": None,
                "result": result,
            }
            interrupted_sessions.append(interrupted_session)
            _log_interrupt_decision(interrupted_session)
            if bool(interrupted_session.get("result")):
                _INTERRUPTION_LEDGER.register(interrupted_session, default_interruption_token=interruption_token)
            continue

        if bool(session.get("can_duck")):
            duck_result = _try_duck_session(
                session=session,
                adapter=adapter,
                interruption_token=interruption_token,
            )
            if duck_result is not None:
                interrupted_sessions.append(duck_result)
                _log_interrupt_decision(duck_result)
                _INTERRUPTION_LEDGER.register(duck_result, default_interruption_token=interruption_token)
                continue

        if backend_type == "oracle_audiobook":
            interrupt_action = "pause_longform_audio" if bool(session.get("can_pause")) else "stop_longform_audio"
            command_result = (
                adapter.pause_longform_audio() if interrupt_action == "pause_longform_audio" else adapter.stop_longform_audio()
            )
            fallback_action = "stop_longform_audio"
            fallback_command = adapter.stop_longform_audio
        else:
            interrupt_action = "pause" if bool(session.get("can_pause")) else "stop"
            command_result = adapter.pause() if interrupt_action == "pause" else adapter.stop()
            fallback_action = "stop"
            fallback_command = adapter.stop

        if not getattr(command_result, "ok", False) and interrupt_action.startswith("pause"):
            fallback_result = fallback_command()
            if getattr(fallback_result, "ok", False):
                interrupt_action = fallback_action
                command_result = fallback_result

        interrupted_session = {
            "kind": _session_kind(session),
            "backend_type": backend_type,
            "media_kind": session.get("media_kind"),
            "session_id": session.get("session_id"),
            "interruption_token": interruption_token,
            "interrupted_by_session_id": "",
            "superseded_by_session_id": "",
            "interrupt_action": interrupt_action,
            "resume_action": (
                session.get("resume_action")
                if (
                    getattr(command_result, "ok", False)
                    and (
                        interrupt_action.startswith("pause")
                        or (backend_type == "oracle_audiobook" and interrupt_action == "stop_longform_audio")
                    )
                )
                else None
            ),
            "result": command_result.to_dict("interrupt_for_oracle"),
        }
        interrupted_sessions.append(interrupted_session)
        _log_interrupt_decision(interrupted_session)
        if bool((interrupted_session.get("result") or {}).get("ok")):
            _INTERRUPTION_LEDGER.register(interrupted_session, default_interruption_token=interruption_token)

    payload = {
        "ok": True,
        "interruption_token": interruption_token,
        "interrupted_sessions": interrupted_sessions,
        "interrupted_any": any(bool((item.get("result") or {}).get("ok")) for item in interrupted_sessions),
        "active_session_count": len(active_sessions),
        "degraded_state": bool(authority.get("degraded_state")),
        "degraded_reasons": list(authority.get("degraded_reasons") or []),
        "owning_component": _AUTHORITY_OWNER,
    }
    if payload["degraded_state"]:
        payload["failure_class"] = "authority_mismatch"
        payload["error"] = "authority_state_degraded"
    elif payload["active_session_count"] > 0 and not payload["interrupted_any"]:
        payload.update(
            _authority_failure(
                failure_class="authority_mismatch",
                error="authority_interrupt_failed",
                detail="Playback authority found active sessions but could not interrupt them.",
            )
        )
    logging.info(
        "playback_authority_interrupt_finished interruption_token=%s interrupted_any=%s interrupted_count=%d active_session_count=%d degraded_state=%s degraded_reasons=%s",
        interruption_token,
        str(bool(payload.get("interrupted_any"))).lower(),
        len(interrupted_sessions),
        len(active_sessions),
        str(bool(payload.get("degraded_state"))).lower(),
        ",".join(payload.get("degraded_reasons") or []) or "-",
    )
    return payload


def resume_after_oracle(*, adapter: Any, interrupted_sessions: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not isinstance(interrupted_sessions, list):
        interrupted_sessions = []
    resumed_sessions: list[dict[str, Any]] = []
    skipped_sessions: list[dict[str, Any]] = []
    for session in interrupted_sessions:
        if not isinstance(session, dict):
            continue
        resume_action = str(session.get("resume_action", "")).strip()
        if not resume_action:
            continue
        backend_type = str(session.get("backend_type", "")).strip().lower()
        superseded_by_session_id = str(session.get("superseded_by_session_id", "") or "").strip()
        if superseded_by_session_id:
            skipped_sessions.append(
                {
                    "kind": str(session.get("kind", "")).strip() or ("audiobook" if backend_type == "oracle_audiobook" else "music"),
                    "backend_type": str(session.get("backend_type", "")).strip().lower(),
                    "media_kind": session.get("media_kind"),
                    "session_id": session.get("session_id"),
                    "resume_action": resume_action,
                    "skip_reason": "superseded",
                    "superseded_by_session_id": superseded_by_session_id,
                    "owning_component": _AUTHORITY_OWNER,
                }
            )
            logging.info(
                "playback_authority_resume_skipped backend_type=%s session_id=%s resume_action=%s skip_reason=superseded superseded_by_session_id=%s",
                str(session.get("backend_type", "")).strip().lower() or "-",
                str(session.get("session_id", "")).strip() or "-",
                resume_action,
                superseded_by_session_id,
            )
            continue
        ledger_valid, ledger_reason = _INTERRUPTION_LEDGER.consume_if_valid(
            backend_type=backend_type,
            session_id=str(session.get("session_id", "")).strip(),
            interruption_token=str(session.get("interruption_token", "") or "").strip(),
        )
        logging.info(
            "playback_authority_resume_lookup backend_type=%s session_id=%s interruption_token=%s resume_action=%s lookup_result=%s",
            backend_type or "-",
            str(session.get("session_id", "")).strip() or "-",
            str(session.get("interruption_token", "") or "").strip() or "-",
            resume_action,
            ledger_reason,
        )
        if not ledger_valid:
            skipped_sessions.append(
                {
                    "kind": str(session.get("kind", "")).strip() or ("audiobook" if backend_type == "oracle_audiobook" else "music"),
                    "backend_type": backend_type,
                    "media_kind": session.get("media_kind"),
                    "session_id": session.get("session_id"),
                    "resume_action": resume_action,
                    "skip_reason": ledger_reason,
                    **(
                        _authority_failure(
                            failure_class="authority_mismatch",
                            error="authority_resume_rejected",
                            detail="Playback authority rejected resume because interruption lineage did not validate.",
                        )
                        if ledger_reason in {"missing_ledger_entry", "interruption_token_mismatch"}
                        else {"owning_component": _AUTHORITY_OWNER}
                    ),
                }
            )
            logging.info(
                "playback_authority_resume_skipped backend_type=%s session_id=%s resume_action=%s skip_reason=%s",
                backend_type or "-",
                str(session.get("session_id", "")).strip() or "-",
                resume_action,
                ledger_reason,
            )
            continue
        if resume_action == "restore_volume":
            restore_level = session.get("restore_volume_level")
            try:
                level = int(restore_level)
            except (TypeError, ValueError):
                command_result = None
            else:
                command_result = adapter.set_volume(level)
        elif backend_type == "oracle_audiobook":
            command_result = adapter.resume_longform_audio() if resume_action == "resume_longform_audio" else None
        else:
            command_result = adapter.resume() if resume_action == "resume" else None
        if command_result is None:
            continue
        resumed_session = {
            "kind": str(session.get("kind", "")).strip() or ("audiobook" if backend_type == "oracle_audiobook" else "music"),
            "backend_type": backend_type,
            "media_kind": session.get("media_kind"),
            "session_id": session.get("session_id"),
            "resume_action": resume_action,
            "result": command_result.to_dict("resume_after_oracle"),
        }
        resumed_sessions.append(resumed_session)
        logging.info(
            "playback_authority_resume_decision backend_type=%s session_id=%s resume_action=%s result_ok=%s result_state=%s",
            backend_type or "-",
            str(session.get("session_id", "")).strip() or "-",
            resume_action,
            str(bool((resumed_session.get("result") or {}).get("ok"))).lower(),
            str((resumed_session.get("result") or {}).get("state") or "-"),
        )
    payload = {
        "ok": True,
        "resumed_sessions": resumed_sessions,
        "resumed_any": bool(resumed_sessions),
        "skipped_sessions": skipped_sessions,
        "owning_component": _AUTHORITY_OWNER,
    }
    if not payload["resumed_any"]:
        skip_reasons = {str(item.get("skip_reason", "")).strip() for item in skipped_sessions if isinstance(item, dict)}
        if skip_reasons & {"missing_ledger_entry", "interruption_token_mismatch"}:
            payload.update(
                _authority_failure(
                    failure_class="authority_mismatch",
                    error="authority_resume_rejected",
                    detail="Playback authority rejected one or more resume requests because lineage validation failed.",
                )
            )
    return payload


def _log_interrupt_decision(interrupted_session: dict[str, Any]) -> None:
    result = interrupted_session.get("result")
    result_ok = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
    result_state = str(result.get("state") or "-") if isinstance(result, dict) else ("requested" if result_ok else "-")
    logging.info(
        "playback_authority_interrupt_decision backend_type=%s session_id=%s interruption_token=%s interrupt_action=%s resume_action=%s result_ok=%s result_state=%s",
        str(interrupted_session.get("backend_type", "")).strip().lower() or "-",
        str(interrupted_session.get("session_id", "")).strip() or "-",
        str(interrupted_session.get("interruption_token", "")).strip() or "-",
        str(interrupted_session.get("interrupt_action", "")).strip() or "-",
        str(interrupted_session.get("resume_action", "")).strip() or "-",
        str(result_ok).lower(),
        result_state,
    )


def _build_reply_audio_sessions(reply_audio: ReplyAudioStateStore) -> list[dict[str, Any]]:
    state = reply_audio.get_state()
    if not bool(state.get("playing")):
        return []
    updated_at = _coerce_float(state.get("updated_at"))
    return [
        {
            "session_id": str(state.get("session_id", "")).strip() or "reply_audio",
            "backend_type": "reply_audio",
            "media_kind": "reply",
            "state": str(state.get("state", "")).strip() or "playing",
            "resumable": False,
            "owner_priority": _OWNER_PRIORITY["reply_audio"],
            "can_duck": False,
            "can_pause": False,
            "can_stop": True,
            "can_resume": False,
            "resume_action": None,
            "title": str(state.get("kind", "tts")).strip() or "tts",
            "artist_or_author": "",
            "position_seconds": None,
            "duration_seconds": None,
            "updated_at": updated_at,
            "correlation_id": str(state.get("correlation_id", "")).strip(),
            "state_source": str(state.get("state_source", "")).strip(),
        }
    ]


def _build_longform_sessions(adapter: Any, *, include_volume: bool) -> list[dict[str, Any]]:
    try:
        state = adapter.get_longform_state()
    except RuntimeError:
        return []
    current_state = _normalize_state(state.get("state"))
    if current_state == "stopped" and not bool(state.get("playing")):
        return []
    updated_at = _coerce_float(state.get("updated_at"))
    output_volume = _adapter_output_volume(adapter) if include_volume else None
    resumable = current_state in {"playing", "paused", "starting"} or bool(state.get("playing"))
    return [
        {
            "session_id": str(state.get("playback_id", "")).strip() or "oracle_audiobook",
            "backend_type": "oracle_audiobook",
            "media_kind": "audiobook",
            "content_type": "audiobook",
            "state": current_state,
            "resumable": resumable,
            "owner_priority": _OWNER_PRIORITY["oracle_audiobook"],
            "can_duck": output_volume is not None,
            "can_pause": True,
            "can_stop": True,
            "can_resume": resumable,
            "resume_action": "resume_longform_audio" if resumable else None,
            "title": str(state.get("title", "")).strip(),
            "artist_or_author": str(state.get("author", "")).strip(),
            "position_seconds": _coerce_float(state.get("position_seconds")),
            "duration_seconds": _coerce_float(state.get("duration_seconds")),
            "updated_at": updated_at,
            "volume": output_volume,
            "queue_id": str(state.get("playback_id", "")).strip() or "oracle_audiobook",
            "queue_position": 1,
            "queue_count": 1,
            "collection_title": str(state.get("title", "")).strip(),
            "collection_type": "audiobook",
        }
    ]


def _build_music_sessions(
    adapter: Any,
    *,
    include_volume: bool,
    music_backend_expectation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    try:
        state = adapter.get_now_playing()
    except RuntimeError:
        return []
    current_state = _normalize_state(state.get("state"))
    if current_state == "stopped" and not bool(state.get("playing")):
        return []
    updated_at = _coerce_float(state.get("updated_at"))
    resumable = current_state in {"playing", "paused", "starting"} or bool(state.get("playing"))
    backend_type = str(state.get("backend_type", "plexamp_external")).strip() or "plexamp_external"
    output_volume = safe_int(state.get("volume")) if include_volume else None
    return [
        {
            "session_id": str(state.get("plex_key", "")).strip() or "plexamp_external",
            "backend_type": backend_type,
            "expected_backend": str((music_backend_expectation or {}).get("default_backend", "")).strip(),
            "media_kind": "music",
            "content_type": str(state.get("type", "")).strip() or "track",
            "state": current_state,
            "resumable": resumable,
            "owner_priority": _OWNER_PRIORITY.get(backend_type, _OWNER_PRIORITY["plexamp_external"]),
            "can_duck": output_volume is not None,
            "can_pause": True,
            "can_stop": True,
            "can_resume": resumable,
            "resume_action": "resume" if resumable else None,
            "title": str(state.get("title", "")).strip(),
            "artist_or_author": str(state.get("artist", "")).strip(),
            "position_seconds": _coerce_float(state.get("position_seconds")),
            "duration_seconds": _coerce_float(state.get("duration_seconds")),
            "updated_at": updated_at,
            "album": str(state.get("album", "")).strip(),
            "volume": output_volume,
            "queue_id": str(state.get("queue_id", "")).strip(),
            "queue_position": _coerce_int(state.get("queue_position")),
            "queue_count": _coerce_int(state.get("queue_count")),
            "collection_title": str(state.get("collection_title", "")).strip(),
            "collection_type": str(state.get("collection_type", "")).strip(),
        }
    ]


def _owns_output(session: dict[str, Any]) -> bool:
    return str(session.get("state", "")).strip().lower() in {"playing", "starting", "stopping"}


def _build_degraded_reasons(
    active_sessions: list[dict[str, Any]],
    *,
    music_backend_expectation: dict[str, Any] | None = None,
) -> list[str]:
    degraded_reasons: list[str] = []
    if _has_dual_active_music_audiobook(active_sessions):
        degraded_reasons.append("dual_active_music_audiobook")
    if _has_music_backend_default_mismatch(active_sessions, music_backend_expectation=music_backend_expectation):
        degraded_reasons.append("music_backend_default_mismatch")
    return degraded_reasons


def _has_music_backend_default_mismatch(
    active_sessions: list[dict[str, Any]],
    *,
    music_backend_expectation: dict[str, Any] | None = None,
) -> bool:
    expected_backend = str((music_backend_expectation or {}).get("default_backend", "")).strip().lower()
    for session in active_sessions:
        if not isinstance(session, dict):
            continue
        if str(session.get("media_kind", "")).strip().lower() != "music":
            continue
        actual_backend = str(session.get("backend_type", "")).strip().lower()
        session_expected = str(session.get("expected_backend", "")).strip().lower()
        effective_expected = session_expected or expected_backend
        if effective_expected and actual_backend and actual_backend != effective_expected:
            return True
    return False


def _has_dual_active_music_audiobook(active_sessions: list[dict[str, Any]]) -> bool:
    music_active = False
    audiobook_active = False
    for session in active_sessions:
        if not isinstance(session, dict):
            continue
        backend_type = str(session.get("backend_type", "")).strip().lower()
        media_kind = str(session.get("media_kind", "")).strip().lower()
        if media_kind == "music":
            music_active = True
        elif backend_type == "oracle_audiobook":
            audiobook_active = True
    return music_active and audiobook_active


def _log_degraded_authority_state(
    *,
    degraded_reasons: list[str],
    active_sessions: list[dict[str, Any]],
    output_owner: dict[str, Any] | None,
) -> None:
    if not degraded_reasons:
        return
    active_backends = [
        str(session.get("backend_type", "")).strip() or str(session.get("media_kind", "")).strip() or "unknown"
        for session in active_sessions
        if isinstance(session, dict)
    ]
    owner_backend = ""
    if isinstance(output_owner, dict):
        owner_backend = str(output_owner.get("backend_type", "")).strip()
    logging.warning(
        "playback_authority_degraded degraded_reasons=%s output_owner=%s active_backends=%s",
        ",".join(degraded_reasons),
        owner_backend or "-",
        ",".join(active_backends) or "-",
    )


def _adapter_music_backend_expectation(adapter: Any) -> dict[str, Any]:
    getter = getattr(adapter, "get_music_backend_expectation", None)
    if not callable(getter):
        return {}
    try:
        expectation = getter()
    except RuntimeError:
        return {}
    return dict(expectation) if isinstance(expectation, dict) else {}


def _try_duck_session(
    *,
    session: dict[str, Any],
    adapter: Any,
    interruption_token: str,
) -> dict[str, Any] | None:
    current_volume = session.get("volume")
    try:
        current_level = int(current_volume)
    except (TypeError, ValueError):
        return None
    target_level = _target_duck_volume(current_level)
    if target_level >= current_level:
        return None
    command_result = adapter.set_volume(target_level)
    if not getattr(command_result, "ok", False):
        return None
    confirmed_level = None
    payload = getattr(command_result, "payload", None)
    if isinstance(payload, dict):
        confirmed_level = _coerce_int(payload.get("volume_level"))
    if confirmed_level is None or confirmed_level >= current_level:
        return None
    return {
        "kind": _session_kind(session),
        "backend_type": str(session.get("backend_type", "")).strip().lower(),
        "media_kind": session.get("media_kind"),
        "session_id": session.get("session_id"),
        "interruption_token": interruption_token,
        "interrupted_by_session_id": "",
        "superseded_by_session_id": "",
        "interrupt_action": "duck",
        "resume_action": "restore_volume",
        "restore_volume_level": current_level,
        "result": command_result.to_dict("interrupt_for_oracle"),
    }


def _target_duck_volume(current_level: int) -> int:
    normalized = max(0, min(100, int(current_level)))
    if normalized <= 12:
        return 0
    return max(0, min(18, normalized // 2))


def _session_kind(session: dict[str, Any]) -> str:
    backend_type = str(session.get("backend_type", "")).strip().lower()
    media_kind = str(session.get("media_kind", "")).strip().lower()
    if backend_type == "oracle_audiobook" or media_kind == "audiobook":
        return "audiobook"
    if backend_type == "reply_audio" or media_kind == "reply":
        return "reply"
    return "music"


def _normalize_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    if state in {"playing", "paused", "stopped", "starting", "stopping"}:
        return state
    if state == "buffering":
        return "starting"
    return "unknown" if state else "stopped"


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    return _coerce_int(value)


def _adapter_output_volume(adapter: Any) -> int | None:
    getter = getattr(adapter, "get_output_volume", None)
    if not callable(getter):
        return None
    try:
        return _coerce_int(getter())
    except RuntimeError:
        return None
