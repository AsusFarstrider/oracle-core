from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
import uuid

import requests

from .audio import play_wav_bytes, resolve_output_device
from .local_control import (
    begin_foreground_handoff,
    finalize_foreground_handoff,
    send_local_control_command,
)
from .models import ForegroundAudioRequest
from .oracle_client import fetch_pending_alerts, request_tts, send_silent_audiobook_stop
from .wake import clear_audio_queue


def _play_tts_alert(*, args, message: str, reply_audio_kind: str) -> None:
    tts_wav = request_tts(
        args.oracle_url,
        message,
        credential=getattr(args, "brain_api_key", ""),
    )
    play_wav_bytes(
        tts_wav,
        resolve_output_device(args),
        args.playback_gain,
        reply_audio_state_path=args.reply_audio_state_path,
        reply_audio_stop_path=args.reply_audio_stop_path,
        reply_audio_kind=reply_audio_kind,
    )


def _build_alert_foreground_request(*, alert: dict) -> ForegroundAudioRequest:
    kind = str(alert.get("kind", "")).strip() or "alert"
    if kind == "notification":
        return ForegroundAudioRequest(
            kind=kind,
            handoff_mode="borrow",
            interrupt_policy="pause_or_stronger",
            resume_policy="resume_previous",
            correlation_id=str(alert.get("alert_id", "")).strip(),
        )
    return ForegroundAudioRequest(
        kind=kind,
        handoff_mode="replace",
        interrupt_policy="pause_or_stronger",
        resume_policy="no_resume",
        correlation_id=str(alert.get("alert_id", "")).strip(),
    )


def _build_sleep_expiry_foreground_request(*, alert: dict) -> ForegroundAudioRequest:
    return ForegroundAudioRequest(
        kind="sleep_expiry",
        handoff_mode="replace",
        interrupt_policy="stop_required",
        resume_policy="no_resume",
        correlation_id=str(alert.get("alert_id", "")).strip(),
    )


def _begin_alert_handoff(*, args, logger, alert: dict):
    return begin_foreground_handoff(
        control_url=args.music_control_url,
        api_key=str(getattr(args, "music_control_api_key", "") or "").strip(),
        request=_build_alert_foreground_request(alert=alert),
        settle_seconds=getattr(args, "playback_interrupt_settle_seconds", 0.0),
        logger=logger,
    )


def _begin_sleep_expiry_handoff(*, args, logger, alert: dict):
    return begin_foreground_handoff(
        control_url=args.music_control_url,
        api_key=str(getattr(args, "music_control_api_key", "") or "").strip(),
        request=_build_sleep_expiry_foreground_request(alert=alert),
        settle_seconds=getattr(args, "playback_interrupt_settle_seconds", 0.0),
        logger=logger,
    )


def _build_alarm_followup_text(alert: dict) -> str:
    due_at_raw = str(alert.get("due_at", "")).strip()
    if due_at_raw:
        try:
            due_at = datetime.fromisoformat(due_at_raw)
            return f"It's {due_at.strftime('%-I:%M %p')}."
        except ValueError:
            pass
    message = str(alert.get("message", "")).strip()
    if message:
        return message
    return "Your alarm is going off now."


def _play_alert_audio(*, args, logger, alert: dict) -> None:
    kind = str(alert.get("kind", "")).strip()
    message = str(alert.get("message", "")).strip()
    if kind == "timer":
        timer_sound_path = Path(str(getattr(args, "timer_sound_path", "") or "").strip())
        if timer_sound_path.exists():
            try:
                wav_bytes = timer_sound_path.read_bytes()
                logger.info("Timer due: playing local sound %s", timer_sound_path)
                play_wav_bytes(
                    wav_bytes,
                    resolve_output_device(args),
                    args.playback_gain,
                    reply_audio_state_path=args.reply_audio_state_path,
                    reply_audio_stop_path=args.reply_audio_stop_path,
                    reply_audio_kind="timer",
                )
                return
            except Exception as exc:
                logger.warning("Timer sound playback failed; falling back to TTS: %s", exc)
        else:
            logger.warning("Timer sound file not found; falling back to TTS: %s", timer_sound_path)

    if kind == "alarm":
        alarm_sound_path = Path(str(getattr(args, "alarm_sound_path", "") or "").strip())
        if alarm_sound_path.exists():
            try:
                wav_bytes = alarm_sound_path.read_bytes()
                logger.info("Alarm due: playing local sound %s", alarm_sound_path)
                play_wav_bytes(
                    wav_bytes,
                    resolve_output_device(args),
                    args.playback_gain,
                    reply_audio_state_path=args.reply_audio_state_path,
                    reply_audio_stop_path=args.reply_audio_stop_path,
                    reply_audio_kind="alarm",
                )
                followup = _build_alarm_followup_text(alert)
                logger.info("Alarm due: speaking follow-up %s", followup)
                _play_tts_alert(args=args, message=followup, reply_audio_kind="alarm")
                return
            except Exception as exc:
                logger.warning("Alarm sound playback failed; falling back to TTS: %s", exc)
        else:
            logger.warning("Alarm sound file not found; falling back to TTS: %s", alarm_sound_path)

    if not message:
        return
    logger.info("Alert due: %s", message)
    _play_tts_alert(args=args, message=message, reply_audio_kind="alert")


def _stop_audiobook_for_sleep_timer(*, args, logger, alert: dict) -> None:
    api_key = str(getattr(args, "music_control_api_key", "") or "").strip()
    alert_id = str(alert.get("alert_id", "")).strip() or uuid.uuid4().hex[:12]
    handoff = None
    try:
        brain_stop_error: requests.RequestException | None = None
        try:
            send_silent_audiobook_stop(
                args.oracle_url,
                args.source,
                alert_id,
                credential=getattr(args, "brain_api_key", ""),
            )
        except requests.RequestException as exc:
            brain_stop_error = exc
            logger.warning("Sleep timer brain stop failed; falling back to local longform stop: %s", exc)
        else:
            logger.info("Audiobook sleep timer expired; stopped playback silently through brain.")
            return

        handoff = _begin_sleep_expiry_handoff(args=args, logger=logger, alert=alert)
        if api_key:
            result = send_local_control_command(
                args.music_control_url,
                api_key,
                "stop_longform_audio",
            )
            if bool((result or {}).get("ok")):
                logger.info(
                    "Audiobook sleep timer expired; stopped playback through local control session_id=%s state=%s",
                    str((result or {}).get("playback_id") or (result or {}).get("session_id") or "-"),
                    str((result or {}).get("state") or "-"),
                )
                return
            logger.warning("Sleep timer local stop returned non-ok result: %s", result)
        if brain_stop_error is not None:
            raise brain_stop_error
    finally:
        if handoff is not None:
            finalize_foreground_handoff(
                control_url=args.music_control_url,
                api_key=api_key,
                handoff=handoff,
                logger=logger,
            )


def poll_due_alerts_if_needed(
    *,
    args,
    logger,
    frame_queue,
    pre_roll,
    runtime_state,
) -> None:
    now = time.time()
    if now < runtime_state.next_alert_poll_at:
        return

    runtime_state.next_alert_poll_at = now + args.alerts_poll_seconds
    try:
        alerts = fetch_pending_alerts(
            args.oracle_url,
            args.source,
            credential=getattr(args, "brain_api_key", ""),
        )
        for alert in alerts:
            if str(alert.get("kind", "")).strip() == "sleep_timer":
                try:
                    _stop_audiobook_for_sleep_timer(args=args, logger=logger, alert=alert)
                except requests.RequestException as exc:
                    logger.error("Sleep timer stop failed: %s", exc)
                clear_audio_queue(frame_queue, pre_roll)
                runtime_state.next_wake_time = max(
                    runtime_state.next_wake_time,
                    time.time() + args.post_playback_block_seconds,
                )
                continue

            handoff = _begin_alert_handoff(args=args, logger=logger, alert=alert)
            try:
                _play_alert_audio(args=args, logger=logger, alert=alert)
                clear_audio_queue(frame_queue, pre_roll)
                runtime_state.next_wake_time = max(
                    runtime_state.next_wake_time,
                    time.time() + args.post_playback_block_seconds,
                )
            finally:
                finalize_foreground_handoff(
                    control_url=args.music_control_url,
                    api_key=str(getattr(args, "music_control_api_key", "") or "").strip(),
                    handoff=handoff,
                    logger=logger,
                )
    except requests.RequestException as exc:
        logger.error("Alert poll failed: %s", exc)
