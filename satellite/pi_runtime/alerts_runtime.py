from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time

import requests

from .audio import play_ack_tone, play_wav_bytes, resolve_output_device
from .local_control import (
    begin_foreground_handoff,
    finalize_foreground_handoff,
)
from .models import ForegroundAudioRequest
from .oracle_client import acknowledge_alert, claim_due_alerts, fetch_alert_state, request_tts
from .session import get_active_session_id
from .wake import clear_audio_queue
from .host_tools import set_alarm_display_attention


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
    if kind in {"timer", "alarm", "reminder"}:
        return ForegroundAudioRequest(
            kind=kind,
            handoff_mode="borrow",
            interrupt_policy="pause_or_stronger",
            resume_policy="resume_previous",
            correlation_id=str(alert.get("occurrence_id") or alert.get("alert_id", "")).strip(),
        )
    return ForegroundAudioRequest(
        kind=kind,
        handoff_mode="replace",
        interrupt_policy="pause_or_stronger",
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


def _build_alarm_followup_text(alert: dict) -> str:
    message = str(alert.get("message", "")).strip()
    metadata = alert.get("metadata") if isinstance(alert.get("metadata"), dict) else {}
    intended_local_raw = str(metadata.get("intended_local") or "").strip()
    display_time_raw = intended_local_raw or str(alert.get("due_at", "")).strip()
    if display_time_raw:
        try:
            due_at = datetime.fromisoformat(display_time_raw)
            hour = due_at.strftime("%I").lstrip("0") or "0"
            time_text = f"It's {hour}:{due_at.strftime('%M %p')}."
            return f"{message} {time_text}" if message else time_text
        except ValueError:
            pass
    if message:
        return message
    return "Your alarm is going off now."


def _play_alert_audio(*, args, logger, alert: dict) -> None:
    kind = str(alert.get("kind", "")).strip()
    message = str(alert.get("message", "")).strip()
    if kind == "reminder":
        play_ack_tone(
            resolve_output_device(args),
            min(1.0, max(0.0, float(getattr(args, "playback_gain", 1.0)))),
            playback_handoff_active=True,
        )
        late_seconds = int(alert.get("late_seconds") or (alert.get("metadata") or {}).get("late_seconds") or 0)
        spoken = message or "You have a reminder."
        if late_seconds > 0:
            late_minutes = max(1, round(late_seconds / 60))
            spoken = f"{spoken} It was delayed about {late_minutes} {_plural_word('minute', late_minutes)} because this satellite was unavailable."
        _play_tts_alert(args=args, message=spoken, reply_audio_kind="reminder")
        return
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
                late_seconds = int(
                    alert.get("late_seconds")
                    or (alert.get("metadata") or {}).get("late_seconds")
                    or 0
                )
                spoken = message or "Your timer is finished."
                if late_seconds > 0:
                    late_label = (
                        f"{late_seconds} seconds"
                        if late_seconds < 60
                        else f"{max(1, round(late_seconds / 60))} minutes"
                    )
                    spoken = f"{spoken} It expired {late_label} ago while this satellite was unavailable."
                _play_tts_alert(args=args, message=spoken, reply_audio_kind="timer")
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
                late_seconds = int(alert.get("late_seconds") or (alert.get("metadata") or {}).get("late_seconds") or 0)
                if late_seconds > 0:
                    late_minutes = max(1, round(late_seconds / 60))
                    followup = f"{followup} It was delayed about {late_minutes} {_plural_word('minute', late_minutes)} because this satellite was unavailable."
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


def _plural_word(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


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
    active_timers = getattr(runtime_state, "active_timer_alerts", None)
    if not isinstance(active_timers, dict):
        active_timers = {}
        runtime_state.active_timer_alerts = active_timers
    try:
        alerts = claim_due_alerts(
            args.oracle_url,
            args.source,
            credential=getattr(args, "brain_api_key", ""),
        )
        for alert in alerts:
            if str(alert.get("kind") or "") == "reminder":
                alert["occurrence_id"] = str((alert.get("metadata") or {}).get("occurrence_id") or "").strip()
                handoff = _begin_alert_handoff(args=args, logger=logger, alert=alert)
                if not set_alarm_display_attention(True):
                    logger.warning("Reminder display attention request failed occurrence_id=%s", alert["occurrence_id"])
                try:
                    _play_alert_audio(args=args, logger=logger, alert=alert)
                    clear_audio_queue(frame_queue, pre_roll)
                    runtime_state.next_wake_time = max(runtime_state.next_wake_time, time.time() + args.post_playback_block_seconds)
                finally:
                    set_alarm_display_attention(False)
                    finalize_foreground_handoff(
                        control_url=args.music_control_url,
                        api_key=str(getattr(args, "music_control_api_key", "") or "").strip(),
                        handoff=handoff,
                        logger=logger,
                    )
                runtime_state.active_session_id, runtime_state.last_conversation_activity_at = get_active_session_id(
                    args.source,
                    getattr(runtime_state, "active_session_id", None),
                    getattr(runtime_state, "last_conversation_activity_at", None),
                    float(getattr(args, "conversation_timeout_seconds", 90.0)),
                )
                acknowledge_alert(
                    args.oracle_url, args.source, str(alert.get("alert_id") or ""),
                    str(alert.get("lease_id") or ""),
                    credential=getattr(args, "brain_api_key", ""), status="completed",
                    session_id=runtime_state.active_session_id,
                )
                continue
            if str(alert.get("kind") or "") in {"timer", "alarm"}:
                kind = str(alert.get("kind") or "")
                occurrence_id = str((alert.get("metadata") or {}).get("occurrence_id") or "").strip()
                alert["occurrence_id"] = occurrence_id
                if kind == "alarm" and not set_alarm_display_attention(True):
                    logger.warning("Alarm display attention request failed occurrence_id=%s", occurrence_id)
                if getattr(runtime_state, "active_timer_handoff", None) is None:
                    runtime_state.active_timer_handoff = _begin_alert_handoff(
                        args=args, logger=logger, alert=alert
                    )
                _play_alert_audio(args=args, logger=logger, alert=alert)
                active_timers[occurrence_id or str(alert.get("alert_id") or "")] = {
                    **dict(alert),
                    "next_reassert_at": time.time() + (60.0 if kind == "alarm" else 30.0),
                }
                clear_audio_queue(frame_queue, pre_roll)
                runtime_state.next_wake_time = max(
                    runtime_state.next_wake_time,
                    time.time() + args.post_playback_block_seconds,
                )
                acknowledge_alert(
                    args.oracle_url,
                    args.source,
                    str(alert.get("alert_id") or ""),
                    str(alert.get("lease_id") or ""),
                    credential=getattr(args, "brain_api_key", ""),
                    status="acknowledged",
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
            acknowledge_alert(
                args.oracle_url,
                args.source,
                str(alert.get("alert_id") or ""),
                str(alert.get("lease_id") or ""),
                credential=getattr(args, "brain_api_key", ""),
            )

        state = fetch_alert_state(
            args.oracle_url,
            args.source,
            credential=getattr(args, "brain_api_key", ""),
        )
        ringing = {
            str(item.get("occurrence_id") or ""): dict(item)
            for item in list(state.get("ringing") or [])
            if isinstance(item, dict) and item.get("occurrence_id")
        }
        for occurrence_id in list(active_timers):
            if occurrence_id not in ringing:
                active_timers.pop(occurrence_id, None)
        for occurrence_id, timer in ringing.items():
            current = active_timers.get(occurrence_id)
            if current is None:
                timer["occurrence_id"] = occurrence_id
                if getattr(runtime_state, "active_timer_handoff", None) is None:
                    runtime_state.active_timer_handoff = _begin_alert_handoff(
                        args=args, logger=logger, alert=timer
                    )
                _play_alert_audio(args=args, logger=logger, alert=timer)
                if str(timer.get("kind") or "") == "alarm" and not set_alarm_display_attention(True):
                    logger.warning("Alarm display attention request failed occurrence_id=%s", occurrence_id)
                timer["next_reassert_at"] = time.time() + (60.0 if str(timer.get("kind") or "") == "alarm" else 30.0)
                active_timers[occurrence_id] = timer
            elif time.time() >= float(current.get("next_reassert_at") or 0.0):
                _play_alert_audio(args=args, logger=logger, alert=current)
                current["next_reassert_at"] = time.time() + (60.0 if str(current.get("kind") or "") == "alarm" else 30.0)

        if not active_timers and getattr(runtime_state, "active_timer_handoff", None) is not None:
            finalize_foreground_handoff(
                control_url=args.music_control_url,
                api_key=str(getattr(args, "music_control_api_key", "") or "").strip(),
                handoff=runtime_state.active_timer_handoff,
                logger=logger,
            )
            runtime_state.active_timer_handoff = None
            set_alarm_display_attention(False)
    except requests.RequestException as exc:
        logger.error("Alert poll failed: %s", exc)
