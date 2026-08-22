from __future__ import annotations

import logging
import time
import uuid
from argparse import Namespace
from typing import Any, Dict, List, Optional

import requests

from .models import CommandOutcome, ForegroundAudioRequest, ForegroundHandoff, InterruptedPlayback


REQUEST_EXCEPTION = getattr(requests, "RequestException", RuntimeError)


def _log_failure_selection(
    logger: logging.Logger,
    *,
    failure_class: str,
    owning_component: str,
    detail: str,
) -> None:
    logger.warning(
        "failure_path_selected failure_class=%s owning_component=%s detail=%s",
        failure_class,
        owning_component,
        detail,
    )


def _validate_local_control_command_response(action: str, payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("local control returned a non-object response")
    if not isinstance(payload.get("ok"), bool):
        raise RuntimeError("local control response omitted boolean ok")
    state = str(payload.get("state", "")).strip()
    if not state:
        raise RuntimeError("local control response omitted state")
    if not str(payload.get("command_id", "")).strip():
        raise RuntimeError("local control response omitted command_id")
    if action == "begin_reply_audio" and not str(payload.get("session_id", "")).strip():
        raise RuntimeError("begin_reply_audio did not return session_id")
    return payload


def _validate_local_authority_response(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("playback authority returned a non-object response")
    sessions = payload.get("sessions")
    active_sessions = payload.get("active_sessions")
    if sessions is not None and not isinstance(sessions, list):
        raise RuntimeError("playback authority sessions must be a list")
    if active_sessions is not None and not isinstance(active_sessions, list):
        raise RuntimeError("playback authority active_sessions must be a list")
    return payload


def _lineage_ledger_key(item: InterruptedPlayback) -> str:
    backend_type = (item.backend_type or "-").strip().lower() or "-"
    session_id = (item.session_id or "-").strip() or "-"
    interruption_token = (item.interruption_token or "-").strip() or "-"
    return f"{backend_type}:{session_id}:{interruption_token}"


def _log_lineage(
    logger: logging.Logger,
    *,
    source: str,
    item: InterruptedPlayback,
) -> None:
    logger.info(
        "interruption_lineage source=%s kind=%s backend_type=%s session_id=%s interruption_token=%s ledger_key=%s interrupt_action=%s resume_action=%s playback_state=%s",
        source,
        item.kind or "-",
        item.backend_type or "-",
        item.session_id or "-",
        item.interruption_token or "-",
        _lineage_ledger_key(item),
        item.interrupt_action or "-",
        item.resume_action or "-",
        item.playback_state or "-",
    )


def _log_foreground_handoff(
    logger: logging.Logger,
    *,
    handoff: ForegroundHandoff,
    resume_outcome: str,
) -> None:
    interrupted = [
        _lineage_ledger_key(item)
        for item in handoff.interrupted_sessions
    ]
    deferred_key = _lineage_ledger_key(handoff.deferred_resume) if handoff.deferred_resume is not None else "-"
    logger.info(
        "foreground_handoff foreground_kind=%s handoff_mode=%s foreground_session_id=%s interrupted_sessions=%s resume_outcome=%s deferred_resume=%s authority_correlation_id=%s",
        handoff.foreground_kind or "-",
        handoff.handoff_mode or "-",
        handoff.foreground_session_id or "-",
        ",".join(interrupted) or "-",
        resume_outcome or "-",
        deferred_key,
        handoff.authority_correlation_id or "-",
    )


def _validate_foreground_handoff_finalization(
    *,
    handoff: ForegroundHandoff,
    deferred_resume: InterruptedPlayback | None,
) -> None:
    if handoff.resume_policy == "replace_with_deferred" and deferred_resume is None:
        raise ValueError("replace_with_deferred handoff requires deferred_resume")
    if handoff.resume_policy != "replace_with_deferred" and deferred_resume is not None:
        raise ValueError(f"{handoff.resume_policy} handoff cannot carry deferred_resume")


def fetch_local_control_state(control_url: str, api_key: str, path: str) -> Optional[Dict[str, Any]]:
    if not api_key:
        return None
    if not hasattr(requests, "get"):
        raise REQUEST_EXCEPTION("requests.get unavailable")
    response = requests.get(
        f"{control_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=2.0,
    )
    response.raise_for_status()
    data = response.json()
    if path == "/playback-authority":
        return _validate_local_authority_response(data)
    return data if isinstance(data, dict) else None


def begin_foreground_handoff(
    *,
    control_url: str,
    api_key: str,
    request: ForegroundAudioRequest,
    settle_seconds: float,
    logger: logging.Logger,
) -> ForegroundHandoff:
    foreground_session_id = ""
    authority_correlation_id = request.correlation_id
    if not api_key:
        return ForegroundHandoff(
            foreground_kind=request.kind,
            handoff_mode=request.handoff_mode,
            interrupted_sessions=[],
            resume_policy=request.resume_policy,
            authority_correlation_id=authority_correlation_id,
            foreground_session_id=foreground_session_id,
        )
    if request.kind == "reply":
        payload = begin_reply_audio(
            control_url,
            api_key,
            kind="tts",
            correlation_id=request.correlation_id,
        )
        payload = _validate_local_control_command_response("begin_reply_audio", payload)
        if not bool(payload.get("ok")):
            raise RuntimeError("reply authority begin failed")
        foreground_session_id = str(payload.get("session_id", "")).strip()
        authority_correlation_id = str(payload.get("correlation_id", "")).strip() or authority_correlation_id
    interrupted_sessions: List[InterruptedPlayback] = []
    if request.interrupt_policy != "none":
        interrupted_sessions = interrupt_local_playback(
            control_url=control_url,
            api_key=api_key,
            settle_seconds=settle_seconds,
            logger=logger,
        )
        if interrupted_sessions and request.interrupt_policy in {"pause_or_stronger", "stop_required"}:
            interrupted_sessions = prepare_interrupted_playback_for_reply(
                control_url=control_url,
                api_key=api_key,
                interrupted=interrupted_sessions,
                settle_seconds=settle_seconds,
                logger=logger,
            )
    return ForegroundHandoff(
        foreground_kind=request.kind,
        handoff_mode=request.handoff_mode,
        interrupted_sessions=interrupted_sessions,
        resume_policy=request.resume_policy,
        authority_correlation_id=authority_correlation_id,
        foreground_session_id=foreground_session_id,
    )


def finalize_foreground_handoff(
    *,
    control_url: str,
    api_key: str,
    handoff: ForegroundHandoff,
    logger: logging.Logger,
    deferred_resume: InterruptedPlayback | None = None,
    foreground_final_state: str = "",
    foreground_reason: str = "",
) -> None:
    _validate_foreground_handoff_finalization(
        handoff=handoff,
        deferred_resume=deferred_resume,
    )
    handoff.deferred_resume = deferred_resume
    if handoff.foreground_kind == "reply" and handoff.foreground_session_id:
        finalize_result = finalize_reply_audio(
            control_url,
            api_key,
            session_id=handoff.foreground_session_id,
            correlation_id=handoff.authority_correlation_id,
            final_state=foreground_final_state or "completed",
            reason=foreground_reason,
        )
        validated_finalize_result = _validate_local_control_command_response("finalize_reply_audio", finalize_result)
        if not bool(validated_finalize_result.get("ok")):
            _log_failure_selection(
                logger,
                failure_class=str(validated_finalize_result.get("failure_class") or "control_service_failure"),
                owning_component=str(validated_finalize_result.get("owning_component") or "satellite.control_service"),
                detail=str(validated_finalize_result.get("detail") or "reply authority finalize failed"),
            )
    if handoff.resume_policy == "replace_with_deferred":
        if deferred_resume is not None:
            resume_deferred_transport_after_reply(
                control_url=control_url,
                api_key=api_key,
                deferred=deferred_resume,
                logger=logger,
            )
        _log_foreground_handoff(logger, handoff=handoff, resume_outcome="replace_with_deferred")
        return
    if handoff.resume_policy == "resume_previous":
        if handoff.interrupted_sessions:
            resume_interrupted_local_playback(
                control_url=control_url,
                api_key=api_key,
                interrupted=handoff.interrupted_sessions,
                logger=logger,
            )
        _log_foreground_handoff(logger, handoff=handoff, resume_outcome="resume_previous")
        return
    _log_foreground_handoff(logger, handoff=handoff, resume_outcome="no_resume")
def fetch_local_playback_authority(control_url: str, api_key: str) -> Optional[Dict[str, Any]]:
    return fetch_local_control_state(control_url, api_key, "/playback-authority")


def fetch_local_music_state(control_url: str, api_key: str) -> Optional[Dict[str, Any]]:
    authority = fetch_local_playback_authority(control_url, api_key)
    if not isinstance(authority, dict):
        return None
    sessions = authority.get("active_sessions")
    if not isinstance(sessions, list):
        return None
    for session in sessions:
        if not isinstance(session, dict):
            continue
        if str(session.get("media_kind", "")).strip().lower() != "music":
            continue
        return session
    return None


def fetch_local_longform_state(control_url: str, api_key: str) -> Optional[Dict[str, Any]]:
    authority = fetch_local_playback_authority(control_url, api_key)
    if not isinstance(authority, dict):
        return None
    sessions = authority.get("sessions")
    if not isinstance(sessions, list):
        return None
    for session in sessions:
        if not isinstance(session, dict):
            continue
        if str(session.get("backend_type", "")).strip().lower() != "oracle_audiobook":
            continue
        return session
    return None


def is_state_playing(state: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state, dict):
        return False
    if state.get("state") == "playing":
        return True
    return bool(state.get("playing"))


def fetch_local_playback_active(control_url: str, api_key: str, logger: logging.Logger) -> bool:
    if not api_key:
        return False
    try:
        authority = fetch_local_playback_authority(control_url, api_key)
    except REQUEST_EXCEPTION as exc:
        logger.warning("Failed to query local playback authority for wake tuning: %s", exc)
        authority = None
    if isinstance(authority, dict):
        return bool(authority.get("playback_active"))
    return False


def send_local_control_command(
    control_url: str,
    api_key: str,
    action: str,
    args: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not api_key:
        return None
    if not hasattr(requests, "post"):
        raise REQUEST_EXCEPTION("requests.post unavailable")
    response = requests.post(
        f"{control_url.rstrip('/')}/control",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "command_id": uuid.uuid4().hex,
            "action": action,
            "args": args or {},
        },
        timeout=3.0,
    )
    response.raise_for_status()
    data = response.json()
    return _validate_local_control_command_response(action, data)


def send_local_music_command(
    control_url: str,
    api_key: str,
    action: str,
    args: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    return send_local_control_command(control_url, api_key, action, args)


def begin_reply_audio(
    control_url: str,
    api_key: str,
    *,
    kind: str = "tts",
    correlation_id: str = "",
) -> Optional[Dict[str, Any]]:
    return send_local_control_command(
        control_url,
        api_key,
        "begin_reply_audio",
        {
            "kind": kind,
            "correlation_id": correlation_id or uuid.uuid4().hex,
        },
    )


def finalize_reply_audio(
    control_url: str,
    api_key: str,
    *,
    session_id: str,
    correlation_id: str = "",
    final_state: str,
    reason: str = "",
) -> Optional[Dict[str, Any]]:
    return send_local_control_command(
        control_url,
        api_key,
        "finalize_reply_audio",
        {
            "session_id": session_id,
            "correlation_id": correlation_id,
            "final_state": final_state,
            "reason": reason,
        },
    )


def interrupt_local_playback(
    *,
    control_url: str,
    api_key: str,
    settle_seconds: float,
    logger: logging.Logger,
) -> List[InterruptedPlayback]:
    if not api_key:
        return []

    authority_ok = False
    authority_interrupted_any = False
    authority_interruption_token = ""
    authority_session_count = 0
    authority_backend_types: list[str] = []
    degraded_state = False
    degraded_reasons: list[str] = []
    try:
        command_result = send_local_control_command(control_url, api_key, "interrupt_for_oracle")
    except REQUEST_EXCEPTION as exc:
        logger.warning("Failed to interrupt local playback through playback authority: %s", exc)
        command_result = None
        logger.info(
            "fallback_invoked reason=wake_capture authority_ok=false authority_interrupted_any=false authority_session_count=0 authority_backends=- degraded_state=false degraded_reasons=- fallback_used=true fallback_trigger=authority_exception"
        )
    else:
        command_payload = command_result if isinstance(command_result, dict) else {}
        authority_ok = bool(command_payload.get("ok"))
        authority_interrupted_any = bool(command_payload.get("interrupted_any"))
        authority_interruption_token = str(command_payload.get("interruption_token", "")).strip()
        authority_session_count = int(command_payload.get("active_session_count") or 0)
        degraded_state = bool(command_payload.get("degraded_state"))
        degraded_reasons = [str(reason).strip() for reason in (command_payload.get("degraded_reasons") or []) if str(reason).strip()]
        sessions_payload = command_result.get("interrupted_sessions") if isinstance(command_result, dict) else None
        if authority_ok and authority_interrupted_any and authority_session_count > 0:
            if not isinstance(sessions_payload, list) or not sessions_payload:
                _log_failure_selection(
                    logger,
                    failure_class="authority_mismatch",
                    owning_component="satellite.playback_authority",
                    detail="interrupt_for_oracle reported interrupted_any without interrupted session lineage",
                )
                authority_interrupted_any = False
        if isinstance(sessions_payload, list):
            authority_backend_types = [
                str(item.get("backend_type", "")).strip() or str(item.get("media_kind", "")).strip() or "unknown"
                for item in sessions_payload
                if isinstance(item, dict)
            ]
        logger.info(
            "authority_interrupt_result authority_ok=%s authority_interrupted_any=%s authority_session_count=%d interruption_token=%s authority_backends=%s degraded_state=%s degraded_reasons=%s",
            str(authority_ok).lower(),
            str(authority_interrupted_any).lower(),
            authority_session_count,
            authority_interruption_token or "-",
            ",".join(authority_backend_types) or "-",
            str(degraded_state).lower(),
            ",".join(degraded_reasons) or "-",
        )

    interrupted: List[InterruptedPlayback] = []
    sessions = command_result.get("interrupted_sessions") if isinstance(command_result, dict) else None
    if isinstance(sessions, list):
        for session in sessions:
            if not isinstance(session, dict):
                continue
            backend_type = str(session.get("backend_type", "")).strip().lower()
            if backend_type == "reply_audio":
                continue
            session_kind = str(session.get("kind", "")).strip().lower()
            resume_action = str(session.get("resume_action", "") or "").strip()
            item = InterruptedPlayback(
                kind=session_kind or ("audiobook" if backend_type == "oracle_audiobook" else "music"),
                backend_type=backend_type,
                session_id=str(session.get("session_id", "") or "").strip(),
                resume_action=resume_action,
                restore_volume_level=(
                    int(session.get("restore_volume_level"))
                    if session.get("restore_volume_level") not in (None, "")
                    else None
                ),
                interruption_token=(
                    str(session.get("interruption_token", "") or "").strip() or authority_interruption_token
                ),
                interrupted_by_session_id=str(session.get("interrupted_by_session_id", "") or "").strip(),
                superseded_by_session_id=str(session.get("superseded_by_session_id", "") or "").strip(),
                interrupt_action=str(session.get("interrupt_action", "") or "").strip(),
                playback_state=str(session.get("state", "") or "").strip(),
            )
            interrupted.append(item)
            _log_lineage(logger, source="interrupt_local_playback", item=item)
        if interrupted and settle_seconds > 0:
            time.sleep(settle_seconds)
        if bool(command_result.get("interrupted_any")):
            return interrupted

    fallback_trigger = "authority_empty"
    if authority_ok and authority_session_count > 0 and not authority_interrupted_any:
        fallback_trigger = "authority_no_interrupted_any"
    elif authority_ok and authority_session_count == 0:
        fallback_trigger = "authority_empty"
    elif authority_ok and authority_interrupted_any and not interrupted:
        fallback_trigger = "authority_partial"

    logger.info(
        "fallback_invoked reason=wake_capture authority_ok=%s authority_interrupted_any=%s authority_session_count=%d authority_backends=%s degraded_state=%s degraded_reasons=%s fallback_used=true fallback_trigger=%s",
        str(authority_ok).lower(),
        str(authority_interrupted_any).lower(),
        authority_session_count,
        ",".join(authority_backend_types) or "-",
        str(degraded_state).lower(),
        ",".join(degraded_reasons) or "-",
        fallback_trigger,
    )

    try:
        longform_state = fetch_local_longform_state(control_url, api_key)
    except REQUEST_EXCEPTION as exc:
        logger.warning("Failed to query local audiobook state before wake capture: %s", exc)
    else:
        if is_state_playing(longform_state):
            try:
                send_local_control_command(control_url, api_key, "pause_longform_audio")
            except REQUEST_EXCEPTION as exc:
                logger.warning("Failed to pause local audiobook playback for wake capture: %s", exc)
                logger.info(
                    "fallback_result reason=wake_capture fallback_target=audiobook fallback_ok=false fallback_action=pause_longform_audio fallback_trigger=%s",
                    fallback_trigger,
                )
            else:
                logger.info(
                    "fallback_result reason=wake_capture fallback_target=audiobook fallback_ok=true fallback_action=pause_longform_audio fallback_trigger=%s",
                    fallback_trigger,
                )
                interrupted.append(
                    InterruptedPlayback(
                        kind="audiobook",
                        backend_type="oracle_audiobook",
                        session_id=(
                            str((longform_state or {}).get("session_id", "") or "").strip()
                            or str((longform_state or {}).get("playback_id", "") or "").strip()
                        ),
                        resume_action="resume_longform_audio",
                        interruption_token=authority_interruption_token,
                        interrupt_action="pause_longform_audio",
                        playback_state="paused",
                    )
                )
                _log_lineage(logger, source="interrupt_local_playback_fallback", item=interrupted[-1])

    try:
        music_state = fetch_local_music_state(control_url, api_key)
    except REQUEST_EXCEPTION as exc:
        logger.warning("Failed to query local music state before wake capture: %s", exc)
    else:
        if is_state_playing(music_state):
            try:
                send_local_control_command(control_url, api_key, "pause")
            except REQUEST_EXCEPTION as exc:
                logger.warning("Failed to pause local music playback for wake capture: %s", exc)
                logger.info(
                    "fallback_result reason=wake_capture fallback_target=music fallback_ok=false fallback_action=pause fallback_trigger=%s",
                    fallback_trigger,
                )
            else:
                logger.info(
                    "fallback_result reason=wake_capture fallback_target=music fallback_ok=true fallback_action=pause fallback_trigger=%s",
                    fallback_trigger,
                )
                interrupted.append(
                    InterruptedPlayback(
                        kind="music",
                        backend_type=str((music_state or {}).get("backend_type", "") or "").strip().lower(),
                        session_id=(
                            str((music_state or {}).get("session_id", "") or "").strip()
                            or str((music_state or {}).get("plex_key", "") or "").strip()
                        ),
                        resume_action="resume",
                        interruption_token=authority_interruption_token,
                        interrupt_action="pause",
                        playback_state="paused",
                    )
                )
                _log_lineage(logger, source="interrupt_local_playback_fallback", item=interrupted[-1])

    if interrupted and settle_seconds > 0:
        time.sleep(settle_seconds)
    return interrupted


def is_transport_playback_command(outcome: Optional[CommandOutcome]) -> bool:
    if outcome is None:
        return False
    playback = (outcome.effects or {}).get("satellite_playback")
    return isinstance(playback, dict) and str(playback.get("disposition") or "") in {
        "started", "updated", "stopped", "failed"
    }


def should_resume_after_reply_for_transport_command(outcome: Optional[CommandOutcome]) -> bool:
    if outcome is None:
        return False
    playback = (outcome.effects or {}).get("satellite_playback")
    return isinstance(playback, dict) and str(playback.get("disposition") or "") == "started"


def extract_deferred_transport_resume(
    outcome: Optional[CommandOutcome],
    *,
    oracle_url: str,
    source: str,
    credential: str,
) -> Optional[InterruptedPlayback]:
    if outcome is None:
        return None
    deferred = (outcome.effects or {}).get("deferred_satellite_playback")
    if not isinstance(deferred, dict):
        return None
    token = str(deferred.get("continuation_token") or "").strip()
    if not token:
        return None
    return InterruptedPlayback(
        kind="deferred_satellite_playback",
        backend_type="oracle",
        session_id=outcome.session_id,
        resume_action="oracle_deferred_resume",
        resume_args={
            "oracle_url": oracle_url,
            "source": source,
            "credential": credential,
            "continuation_token": token,
        },
    )


def resume_deferred_transport_after_reply(
    *,
    control_url: str,
    api_key: str,
    deferred: InterruptedPlayback,
    logger: logging.Logger,
) -> None:
    if not deferred.resume_action:
        return
    if deferred.resume_action == "oracle_deferred_resume":
        from .oracle_client import resume_deferred_playback

        args = deferred.resume_args or {}
        try:
            result = resume_deferred_playback(
                str(args.get("oracle_url") or ""),
                str(args.get("source") or ""),
                str(args.get("continuation_token") or ""),
                credential=str(args.get("credential") or ""),
            )
        except REQUEST_EXCEPTION as exc:
            logger.warning("Failed to resume deferred Oracle playback after reply: %s", exc)
            return
        logger.info(
            "deferred_resume_result kind=%s backend_type=oracle session_id=%s resume_action=oracle_deferred_resume ok=%s state=%s",
            deferred.kind or "-",
            deferred.session_id or "-",
            str(bool((result or {}).get("ok"))).lower(),
            str((result or {}).get("state") or "-"),
        )
        return
    try:
        result = send_local_control_command(control_url, api_key, deferred.resume_action, deferred.resume_args)
    except REQUEST_EXCEPTION as exc:
        logger.warning(
            "Failed to resume deferred %s playback after reply: %s",
            deferred.kind or deferred.backend_type or "transport",
            exc,
        )
        return
    logger.info(
        "deferred_resume_result kind=%s backend_type=%s session_id=%s resume_action=%s ok=%s state=%s",
        deferred.kind or "-",
        deferred.backend_type or "-",
        deferred.session_id or "-",
        deferred.resume_action,
        str(bool((result or {}).get("ok"))).lower(),
        str((result or {}).get("state") or "-"),
    )


def should_listen_for_followup_reply(outcome: Optional[CommandOutcome]) -> bool:
    if outcome is None:
        return False
    follow_up = (outcome.effects or {}).get("follow_up")
    return isinstance(follow_up, dict) and bool(follow_up.get("expected"))


def resume_interrupted_local_playback(
    *,
    control_url: str,
    api_key: str,
    interrupted: List[InterruptedPlayback],
    logger: logging.Logger,
) -> None:
    for item in interrupted:
        if item.resume_action:
            _log_lineage(logger, source="resume_interrupted_local_playback_out", item=item)
    payload = [
        {
            "kind": item.kind,
            "backend_type": item.backend_type,
            "session_id": item.session_id,
            "resume_action": item.resume_action,
            "restore_volume_level": item.restore_volume_level,
            "interruption_token": item.interruption_token,
            "interrupted_by_session_id": item.interrupted_by_session_id,
            "superseded_by_session_id": item.superseded_by_session_id,
        }
        for item in interrupted
        if item.resume_action
    ]
    if payload:
        try:
            command_result = send_local_control_command(
                control_url,
                api_key,
                "resume_after_oracle",
                {"interrupted_sessions": payload},
            )
            if isinstance(command_result, dict) and "resumed_sessions" not in command_result and "skipped_sessions" not in command_result:
                logger.info("authority_resume_result resumed_any=- resumed_count=- skipped_count=-")
                return
            resumed_sessions = command_result.get("resumed_sessions") if isinstance(command_result, dict) else []
            skipped_sessions = command_result.get("skipped_sessions") if isinstance(command_result, dict) else []
            logger.info(
                "authority_resume_result resumed_any=%s resumed_count=%d skipped_count=%d",
                str(bool((command_result or {}).get("resumed_any"))).lower(),
                len(resumed_sessions) if isinstance(resumed_sessions, list) else 0,
                len(skipped_sessions) if isinstance(skipped_sessions, list) else 0,
            )
            resumed_lookup: set[tuple[str, str, str]] = set()
            if isinstance(resumed_sessions, list):
                for session in resumed_sessions:
                    if not isinstance(session, dict):
                        continue
                    resumed_lookup.add(
                        (
                            str(session.get("backend_type", "")).strip(),
                            str(session.get("session_id", "")).strip(),
                            str(session.get("resume_action", "")).strip(),
                        )
                    )
            authority_rejected_resume = {
                "missing_ledger_entry",
                "interruption_token_mismatch",
                "authority_resume_rejected",
            }
            if isinstance(skipped_sessions, list):
                fallback_candidates = [
                    item
                    for item in interrupted
                    if item.resume_action
                    and not item.superseded_by_session_id
                    and (
                        item.backend_type,
                        item.session_id,
                        item.resume_action,
                    )
                    not in resumed_lookup
                ]
                rejected_count = sum(
                    1
                    for session in skipped_sessions
                    if isinstance(session, dict)
                    and str(session.get("skip_reason", "")).strip() in authority_rejected_resume
                )
                if fallback_candidates and rejected_count:
                    logger.warning(
                        "Playback authority rejected %d resume request(s); falling back to direct local resume.",
                        rejected_count,
                    )
                else:
                    return
        except REQUEST_EXCEPTION as exc:
            logger.warning("Failed to resume interrupted playback through playback authority: %s", exc)

    for item in interrupted:
        if not item.resume_action:
            continue
        if item.superseded_by_session_id:
            logger.info(
                "local_resume_skipped backend_type=%s session_id=%s resume_action=%s skip_reason=superseded superseded_by_session_id=%s",
                item.backend_type or "-",
                item.session_id or "-",
                item.resume_action,
                item.superseded_by_session_id,
            )
            continue
        try:
            send_local_control_command(control_url, api_key, item.resume_action)
        except REQUEST_EXCEPTION as exc:
            logger.warning("Failed to resume interrupted %s playback: %s", item.kind, exc)


def prepare_interrupted_playback_for_reply(
    *,
    control_url: str,
    api_key: str,
    interrupted: List[InterruptedPlayback],
    settle_seconds: float,
    logger: logging.Logger,
) -> List[InterruptedPlayback]:
    prepared: List[InterruptedPlayback] = []
    for item in interrupted:
        _log_lineage(logger, source="prepare_interrupted_playback_for_reply_in", item=item)
        if item.resume_action != "restore_volume":
            prepared.append(item)
            _log_lineage(logger, source="prepare_interrupted_playback_for_reply_passthrough", item=item)
            continue
        if item.restore_volume_level is not None:
            try:
                send_local_control_command(
                    control_url,
                    api_key,
                    "set_volume",
                    {"level": item.restore_volume_level},
                )
            except REQUEST_EXCEPTION as exc:
                logger.warning("Failed to restore ducked %s volume before reply pause: %s", item.kind, exc)
        action = "pause_longform_audio" if item.backend_type == "oracle_audiobook" else "pause"
        fallback_resume_action = "resume_longform_audio" if item.backend_type == "oracle_audiobook" else "resume"
        fallback_action = "stop_longform_audio" if item.backend_type == "oracle_audiobook" else "stop"
        # Duck-to-pause is still the same interrupted session. Only the immediate transport
        # action changes for reply output; the ledger lookup tuple must survive intact.
        try:
            result = send_local_control_command(control_url, api_key, action)
        except REQUEST_EXCEPTION as exc:
            logger.warning("Failed to promote ducked %s playback to pause for reply output: %s", item.kind, exc)
            prepared.append(item)
            _log_lineage(logger, source="prepare_interrupted_playback_for_reply_pause_failed", item=item)
            continue
        if not bool((result or {}).get("ok")):
            try:
                fallback_result = send_local_control_command(control_url, api_key, fallback_action)
            except REQUEST_EXCEPTION as exc:
                logger.warning("Failed to stop %s playback after pause failed for reply output: %s", item.kind, exc)
                prepared.append(item)
                _log_lineage(logger, source="prepare_interrupted_playback_for_reply_fallback_failed", item=item)
                continue
            if not bool((fallback_result or {}).get("ok")):
                logger.warning("Failed to interrupt %s playback for reply output: %s", item.kind, fallback_result)
                prepared.append(item)
                _log_lineage(logger, source="prepare_interrupted_playback_for_reply_fallback_rejected", item=item)
                continue
            promoted = InterruptedPlayback(
                kind=item.kind,
                backend_type=item.backend_type,
                session_id=item.session_id,
                resume_action=fallback_resume_action if item.backend_type == "oracle_audiobook" else "",
                interruption_token=item.interruption_token,
                interrupted_by_session_id=item.interrupted_by_session_id,
                superseded_by_session_id=item.superseded_by_session_id,
                interrupt_action=fallback_action,
                playback_state=str((fallback_result or {}).get("state") or "stopped"),
            )
            prepared.append(promoted)
            _log_lineage(logger, source="prepare_interrupted_playback_for_reply_promoted", item=promoted)
            continue
        promoted = InterruptedPlayback(
            kind=item.kind,
            backend_type=item.backend_type,
            session_id=item.session_id,
            resume_action=fallback_resume_action,
            interruption_token=item.interruption_token,
            interrupted_by_session_id=item.interrupted_by_session_id,
            superseded_by_session_id=item.superseded_by_session_id,
            interrupt_action=action,
            playback_state=str((result or {}).get("state") or "paused"),
        )
        prepared.append(promoted)
        _log_lineage(logger, source="prepare_interrupted_playback_for_reply_promoted", item=promoted)
    if prepared != interrupted and settle_seconds > 0:
        time.sleep(settle_seconds)
    return prepared


class DuckedMusicController:
    def __init__(self, args: Namespace, logger: logging.Logger) -> None:
        self._args = args
        self._logger = logger
        self._active_duck_volume: Optional[int] = None
        self._duck_restore_deadline = 0.0
        self._interrupted_playback: List[InterruptedPlayback] = []

    def maybe_restore(self, *, force: bool = False) -> None:
        if not self._interrupted_playback:
            return
        if not force and time.time() < self._duck_restore_deadline:
            return
        resume_interrupted_local_playback(
            control_url=self._args.music_control_url,
            api_key=self._args.music_control_api_key,
            interrupted=self._interrupted_playback,
            logger=self._logger,
        )
        self._interrupted_playback = []
        self._active_duck_volume = None
        self._duck_restore_deadline = 0.0

    def take_interrupted_playback(self) -> List[InterruptedPlayback]:
        interrupted = list(self._interrupted_playback)
        self._interrupted_playback = []
        self._active_duck_volume = None
        self._duck_restore_deadline = 0.0
        return interrupted

    def _stage_target_volume(self, stage: int) -> int:
        if stage >= 3:
            raw_value = self._args.music_duck_stage_three_volume
        elif stage == 2:
            raw_value = self._args.music_duck_stage_two_volume
        else:
            raw_value = self._args.music_duck_stage_one_volume
        return max(0, min(100, int(raw_value)))

    def apply_duck_stage(self, stage: int) -> None:
        if stage <= 0:
            self.maybe_restore()
            return
        if self._interrupted_playback:
            self._duck_restore_deadline = time.time() + self._args.music_duck_max_seconds
            target_volume = self._stage_target_volume(stage)
            if self._active_duck_volume is None or target_volume >= self._active_duck_volume:
                return
            try:
                send_local_music_command(
                    self._args.music_control_url,
                    self._args.music_control_api_key,
                    "set_volume",
                    {"level": target_volume},
                )
            except REQUEST_EXCEPTION as exc:
                self._logger.warning("Failed to deepen local music duck volume: %s", exc)
                return
            self._active_duck_volume = target_volume
            return
        if not self._args.music_control_api_key:
            return
        interrupted = interrupt_local_playback(
            control_url=self._args.music_control_url,
            api_key=self._args.music_control_api_key,
            settle_seconds=0.0,
            logger=self._logger,
        )
        if not interrupted:
            return
        self._interrupted_playback = interrupted
        target_volume = self._stage_target_volume(stage)
        restore_volume_level = next(
            (
                item.restore_volume_level
                for item in interrupted
                if item.resume_action == "restore_volume" and item.restore_volume_level is not None
            ),
            None,
        )
        if restore_volume_level is None or restore_volume_level <= target_volume:
            self._duck_restore_deadline = time.time() + self._args.music_duck_max_seconds
            return
        try:
            send_local_music_command(
                self._args.music_control_url,
                self._args.music_control_api_key,
                "set_volume",
                {"level": target_volume},
            )
        except REQUEST_EXCEPTION as exc:
            self._logger.warning("Failed to duck local music volume: %s", exc)
            return
        self._active_duck_volume = target_volume
        self._duck_restore_deadline = time.time() + self._args.music_duck_max_seconds

    def maybe_duck(self) -> None:
        self.apply_duck_stage(3)
