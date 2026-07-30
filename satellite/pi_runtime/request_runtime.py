from __future__ import annotations

import time
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from .audio import play_ack_tone, play_wav_bytes, resolve_output_device
from .local_control import begin_foreground_handoff, finalize_foreground_handoff
from .models import CommandOutcome, ForegroundAudioRequest, RuntimeState
from .oracle_client import fetch_command_events, report_satellite_activity, request_tts, send_command, send_stt
from .session import get_active_session_id
from .wake import SAMPLE_RATE, pcm_to_wav_bytes


@dataclass
class RequestPipelineResult:
    transcript: str
    outcome: CommandOutcome
    tts_wav: bytes
    stt_elapsed_ms: float
    command_elapsed_ms: float
    tts_elapsed_ms: float


@dataclass
class RequestPipelineError(RuntimeError):
    kind: str
    detail: str
    should_play_error_tone: bool
    capture_context: dict[str, Any] = field(default_factory=dict)
    reply_text: str = ""

    def __str__(self) -> str:
        return self.detail


def _log_satellite_command_event(
    logger,
    event: str,
    *,
    source: str,
    session_id: str | None = None,
    route_target: str | None = None,
    dispatch_hook: str | None = None,
    dispatch_status: str | None = None,
    action: str | None = None,
    transcript: str | None = None,
    reply_text: str | None = None,
) -> None:
    logger.info(
        "%s source=%s session_id=%s route_target=%s dispatch_hook=%s status=%s action=%s transcript_chars=%d reply_chars=%d",
        event,
        source or "-",
        session_id or "-",
        route_target or "-",
        dispatch_hook or "-",
        dispatch_status or "-",
        action or "-",
        len(transcript or ""),
        len(reply_text or ""),
    )


def _build_ack_foreground_request() -> ForegroundAudioRequest:
    return ForegroundAudioRequest(
        kind="ack",
        handoff_mode="borrow",
        interrupt_policy="none",
        resume_policy="no_resume",
        correlation_id=uuid.uuid4().hex,
    )


def _interim_ack_enabled(args) -> bool:
    return bool(getattr(args, "interim_ack_enabled", False))


def _interim_ack_poll_interval_seconds(args) -> float:
    return max(0.05, float(getattr(args, "interim_ack_poll_interval_seconds", 0.15) or 0.15))


def _interim_ack_request_timeout_seconds(args) -> float:
    return max(0.05, float(getattr(args, "interim_ack_request_timeout_seconds", 0.75) or 0.75))


def _play_interim_ack_message(
    *,
    args,
    logger,
    message: str,
    session_id: str,
    correlation_id: str,
) -> None:
    clean_message = str(message or "").strip()
    if not clean_message:
        return
    tts_wav = request_tts(
        args.oracle_url,
        clean_message,
        credential=getattr(args, "brain_api_key", ""),
    )
    handoff = begin_foreground_handoff(
        control_url=args.music_control_url,
        api_key=args.music_control_api_key,
        request=_build_ack_foreground_request(),
        settle_seconds=0.0,
        logger=logger,
    )
    try:
        play_wav_bytes(
            tts_wav,
            resolve_output_device(args),
            float(getattr(args, "playback_gain", 1.0) or 1.0),
            reply_audio_state_path=getattr(args, "reply_audio_state_path", None),
            reply_audio_stop_path=getattr(args, "reply_audio_stop_path", None),
            reply_audio_kind="ack",
            playback_handoff_active=False,
            reply_audio_session_id=session_id,
            reply_audio_correlation_id=correlation_id,
            logger=logger,
        )
    finally:
        finalize_foreground_handoff(
            control_url=args.music_control_url,
            api_key=args.music_control_api_key,
            handoff=handoff,
            logger=logger,
        )


def _start_interim_ack_poller(
    *,
    args,
    logger,
    source: str,
    session_id: str,
    correlation_id: str,
) -> tuple[threading.Event, threading.Thread] | None:
    if not _interim_ack_enabled(args):
        return None
    stop_event = threading.Event()

    def _poll() -> None:
        after_event_id = 0
        played = False
        while not stop_event.is_set() and not played:
            try:
                events = fetch_command_events(
                    args.oracle_url,
                    source=source,
                    session_id=session_id,
                    after_event_id=after_event_id,
                    timeout=_interim_ack_request_timeout_seconds(args),
                    credential=getattr(args, "brain_api_key", ""),
                )
            except Exception as exc:
                logger.debug("interim_ack_poll_failed source=%s session_id=%s detail=%s", source, session_id, exc)
                stop_event.wait(_interim_ack_poll_interval_seconds(args))
                continue
            for event in events:
                if not isinstance(event, dict):
                    continue
                try:
                    after_event_id = max(after_event_id, int(event.get("event_id") or 0))
                except (TypeError, ValueError):
                    pass
                if str(event.get("event_type") or "") != "facts_summarizer_ack":
                    continue
                message = str(event.get("message") or "").strip()
                if not message:
                    continue
                try:
                    logger.info(
                        "interim_ack_playback_start source=%s session_id=%s event_id=%s message_chars=%d",
                        source,
                        session_id,
                        str(event.get("event_id") or "-"),
                        len(message),
                    )
                    _play_interim_ack_message(
                        args=args,
                        logger=logger,
                        message=message,
                        session_id=session_id,
                        correlation_id=correlation_id,
                    )
                    logger.info("interim_ack_playback_done source=%s session_id=%s", source, session_id)
                except Exception as exc:
                    logger.warning("interim_ack_playback_failed source=%s session_id=%s detail=%s", source, session_id, exc)
                played = True
                break
            if not played:
                stop_event.wait(_interim_ack_poll_interval_seconds(args))

    thread = threading.Thread(target=_poll, name="oracle-interim-ack-poller", daemon=True)
    thread.start()
    return stop_event, thread


def _begin_ack_handoff(*, args, logger):
    return begin_foreground_handoff(
        control_url=args.music_control_url,
        api_key=args.music_control_api_key,
        request=_build_ack_foreground_request(),
        settle_seconds=0.0,
        logger=logger,
    )


def run_request_pipeline(
    *,
    args,
    logger,
    runtime_state: RuntimeState,
    pcm_bytes: bytes,
    suppress_ack_tone: bool = False,
    correlation_id: str | None = None,
) -> RequestPipelineResult:
    interaction_correlation_id = str(correlation_id or "").strip() or f"corr_{uuid.uuid4().hex}"
    wav_bytes = pcm_to_wav_bytes(pcm_bytes, SAMPLE_RATE)
    capture_duration_ms = (len(pcm_bytes) / 2 / SAMPLE_RATE) * 1000.0 if pcm_bytes else 0.0
    playback_handoff_active = float(getattr(runtime_state, "reply_output_handoff_until", 0.0) or 0.0) > time.time()
    ack_allowed = bool(args.ack_tone_enabled) and not suppress_ack_tone and not playback_handoff_active
    capture_context = {
        "capture_succeeded": bool(pcm_bytes),
        "capture_bytes": len(pcm_bytes),
        "capture_duration_ms": round(capture_duration_ms, 1),
    }
    output_device = resolve_output_device(args)
    logger.info(
        "ack_tone_decision source=%s session_id=%s ack_enabled=%s suppress_ack_tone=%s playback_handoff_active=%s ack_allowed=%s output_device_index=%s ack_tone_gain=%.3f capture_bytes=%d",
        args.source,
        getattr(runtime_state, "active_session_id", None) or "-",
        str(bool(getattr(args, "ack_tone_enabled", False))).lower(),
        str(bool(suppress_ack_tone)).lower(),
        str(bool(playback_handoff_active)).lower(),
        str(bool(ack_allowed)).lower(),
        str(output_device),
        float(getattr(args, "ack_tone_gain", 0.0) or 0.0),
        len(wav_bytes),
    )

    def _play_ack_through_handoff() -> None:
        handoff = _begin_ack_handoff(args=args, logger=logger)
        try:
            play_ack_tone(
                output_device,
                args.ack_tone_gain,
                playback_handoff_active=playback_handoff_active,
            )
        finally:
            finalize_foreground_handoff(
                control_url=args.music_control_url,
                api_key=args.music_control_api_key,
                handoff=handoff,
                logger=logger,
            )

    stt_started_at = time.perf_counter()
    try:
        transcript = send_stt(
            args.oracle_url,
            wav_bytes,
            correlation_id=interaction_correlation_id,
            source=args.source,
            credential=getattr(args, "brain_api_key", ""),
            on_upload_complete=(
                _play_ack_through_handoff
            )
            if ack_allowed
            else None,
            on_upload_complete_error=(lambda exc: logger.warning("Ack tone playback failed: %s", exc))
            if ack_allowed
            else None,
        )
    except Exception as exc:
        report_satellite_activity(
            args.oracle_url,
            source_id=args.source,
            event_type="stt_upload_failed",
            status="degraded",
            correlation_id=interaction_correlation_id,
            payload={
                **capture_context,
                "detail": str(exc),
            },
            snapshot={"last_error": str(exc)},
            timeout=0.05,
            credential=getattr(args, "brain_api_key", ""),
        )
        logger.error(
            "stt_failed source=%s session_id=%s capture_succeeded=%s capture_bytes=%d capture_duration_ms=%.1f detail=%s",
            args.source,
            runtime_state.active_session_id or "-",
            str(capture_context["capture_succeeded"]).lower(),
            capture_context["capture_bytes"],
            capture_context["capture_duration_ms"],
            exc,
        )
        raise RequestPipelineError(
            kind="stt_failed",
            detail=str(exc),
            should_play_error_tone=True,
            capture_context=capture_context,
        ) from exc
    stt_elapsed_ms = (time.perf_counter() - stt_started_at) * 1000.0
    logger.info("Transcript: %s", transcript)
    runtime_state.active_session_id, runtime_state.last_conversation_activity_at = get_active_session_id(
        args.source,
        runtime_state.active_session_id,
        runtime_state.last_conversation_activity_at,
        args.conversation_timeout_seconds,
    )
    _log_satellite_command_event(
        logger,
        "transcript_obtained",
        source=args.source,
        session_id=runtime_state.active_session_id,
        transcript=transcript,
    )
    command_started_at = time.perf_counter()
    interim_ack_poller = _start_interim_ack_poller(
        args=args,
        logger=logger,
        source=args.source,
        session_id=runtime_state.active_session_id,
        correlation_id=interaction_correlation_id,
    )
    try:
        outcome = send_command(
            args.oracle_url,
            args.source,
            transcript,
            runtime_state.active_session_id,
            correlation_id=interaction_correlation_id,
            credential=getattr(args, "brain_api_key", ""),
        )
    except Exception as exc:
        logger.error(
            "brain_request_failed source=%s session_id=%s transcript_chars=%d detail=%s",
            args.source,
            runtime_state.active_session_id or "-",
            len(transcript),
            exc,
        )
        raise RequestPipelineError(
            kind="brain_request_failed",
            detail=str(exc),
            should_play_error_tone=True,
            capture_context=capture_context,
        ) from exc
    finally:
        if interim_ack_poller is not None:
            stop_event, thread = interim_ack_poller
            stop_event.set()
            thread.join(timeout=1.0)
    command_elapsed_ms = (time.perf_counter() - command_started_at) * 1000.0
    logger.info("Reply: %s", outcome.spoken_reply)
    raw_response = outcome.raw_response if isinstance(outcome.raw_response, dict) else {}
    dispatch = raw_response.get("dispatch") if isinstance(raw_response.get("dispatch"), dict) else {}
    route = raw_response.get("route") if isinstance(raw_response.get("route"), dict) else {}
    result = dispatch.get("result") if isinstance(dispatch.get("result"), dict) else {}
    _log_satellite_command_event(
        logger,
        "command_response_received",
        source=args.source,
        session_id=runtime_state.active_session_id,
        route_target=str(route.get("target") or dispatch.get("target") or ""),
        dispatch_hook=str(dispatch.get("hook") or ""),
        dispatch_status=str(dispatch.get("status") or ""),
        action=str(result.get("action") or ""),
        transcript=transcript,
        reply_text=outcome.spoken_reply,
    )
    if str(dispatch.get("status") or "") == "failed":
        logger.warning(
            "brain_dispatch_failed source=%s session_id=%s route_target=%s dispatch_hook=%s action=%s reply_chars=%d detail=%s",
            args.source,
            runtime_state.active_session_id or "-",
            str(route.get("target") or dispatch.get("target") or "-"),
            str(dispatch.get("hook") or "-"),
            str(result.get("action") or "-"),
            len(outcome.spoken_reply),
            str(result.get("error") or result.get("detail") or "dispatch_failed"),
        )
    if not outcome.spoken_reply:
        return RequestPipelineResult(
            transcript=transcript,
            outcome=outcome,
            tts_wav=b"",
            stt_elapsed_ms=stt_elapsed_ms,
            command_elapsed_ms=command_elapsed_ms,
            tts_elapsed_ms=0.0,
        )

    tts_started_at = time.perf_counter()
    try:
        tts_wav = request_tts(
            args.oracle_url,
            outcome.spoken_reply,
            credential=getattr(args, "brain_api_key", ""),
        )
    except Exception as exc:
        logger.error(
            "tts_failed source=%s session_id=%s reply_chars=%d reply_text=%r detail=%s",
            args.source,
            runtime_state.active_session_id or "-",
            len(outcome.spoken_reply),
            outcome.spoken_reply,
            exc,
        )
        raise RequestPipelineError(
            kind="tts_failed",
            detail=str(exc),
            should_play_error_tone=True,
            capture_context=capture_context,
            reply_text=outcome.spoken_reply,
        ) from exc
    tts_elapsed_ms = (time.perf_counter() - tts_started_at) * 1000.0
    return RequestPipelineResult(
        transcript=transcript,
        outcome=outcome,
        tts_wav=tts_wav,
        stt_elapsed_ms=stt_elapsed_ms,
        command_elapsed_ms=command_elapsed_ms,
        tts_elapsed_ms=tts_elapsed_ms,
    )
