from __future__ import annotations

import logging
import queue
import time
import uuid
from collections import deque
from pathlib import Path

import numpy as np
from oracle_runtime_config import build_config_report_payload, findings_have_errors, render_config_report_text

from .audio import (
    clear_reply_audio_stop_request,
    open_input_stream,
    resolve_audio_input_config,
    write_reply_audio_state,
)
from .alerts_runtime import poll_due_alerts_if_needed
from .host_tools import list_devices
from .config_http import start_config_http_server
from .config_runtime import build_satellite_runtime_report
from .local_control import DuckedMusicController, fetch_local_playback_active, interrupt_local_playback
from .models import RuntimeState
from .oracle_client import report_satellite_activity
from .pipeline_runtime import CapturePipeline
from .wake import FRAME_LENGTH, SAMPLE_RATE, build_wake_model, frame_rms
from .wake_arbitration import WAKE_STATE_IDLE, arbitrate_provisional_capture
from .wake_tuning import classify_duck_stage, get_wake_profile, resolve_effective_playback_active
from .wake_loop import (
    capture_after_wake,
    enqueue_input_frame,
    normalize_input_frame,
    should_log_wake_score,
    wake_window_open,
)
from satellite.wake_capture import build_wake_capture_collector


def run(args) -> int:
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("oracle-satellite")

    if args.list_devices:
        list_devices()
        return 0

    report_sections = [
        ("Pi satellite config check:", build_satellite_runtime_report(args, probe_audio_input=True, logger=logger))
    ]
    findings = report_sections[0][1]
    for finding in findings:
        severity = str(finding.get("severity") or "warning").lower()
        if severity == "error":
            log_method = logger.error
        elif severity == "info":
            log_method = logger.info
        else:
            log_method = logger.warning
        log_method(
            "config_%s subsystem=%s setting=%s status=%s source=%s message=%s",
            severity,
            finding.get("subsystem") or "-",
            finding.get("setting") or "-",
            finding.get("status") or "-",
            finding.get("effective_source") or "-",
            finding.get("message") or "",
        )
    if findings_have_errors(findings):
        return 2

    start_config_http_server(
        bind_host=str(args.config_bind_host),
        bind_port=int(args.config_bind_port),
        build_config_report_payload=lambda: build_config_report_payload(
            service="oracle-pi-satellite",
            report_sections=report_sections,
        ),
        render_config_report_text=lambda: render_config_report_text(report_sections),
        logger=logger,
    )

    model_path = Path(args.model_path)
    logger.info("Starting wake-word satellite against %s", args.oracle_url)
    input_config = resolve_audio_input_config(args)
    logger.info(
        "Audio input config backend=%s device=%s explicit=%s source=%s",
        input_config.backend,
        input_config.label,
        input_config.explicitly_configured,
        args.source,
    )
    wake_model, wake_key = build_wake_model(model_path)

    clear_reply_audio_stop_request(args.reply_audio_stop_path)
    write_reply_audio_state(args.reply_audio_state_path, playing=False, kind="tts")
    frame_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
    pre_roll = deque(maxlen=8)
    inference_frames: list = []
    last_score_log = 0.0
    runtime_state = RuntimeState()
    ducked_music = DuckedMusicController(args, logger)
    wake_capture = build_wake_capture_collector(args=args, logger=logger)
    pipeline = CapturePipeline(
        args=args,
        logger=logger,
        frame_queue=frame_queue,
        pre_roll=pre_roll,
        wake_model=wake_model,
        wake_key=wake_key,
        ducked_music=ducked_music,
        runtime_state=runtime_state,
    )
    report_satellite_activity(
        args.oracle_url,
        source_id=args.source,
        event_type="satellite_started",
        status="available",
        payload={"model_path": model_path.name},
        timeout=0.5,
        credential=getattr(args, "brain_api_key", ""),
    )

    def input_callback(indata: bytes, frames: int | None = None, time_info: object | None = None, status=None) -> None:
        if status:
            logger.warning("Input audio status: %s", status)
        if wake_capture is not None:
            wake_capture.append_frame_bytes(bytes(indata))
        enqueue_input_frame(frame_queue, indata)

    try:
        with open_input_stream(
            sample_rate=SAMPLE_RATE,
            frame_length=FRAME_LENGTH,
            callback=input_callback,
            args=args,
            logger=logger,
        ):
            logger.info("Listening for wake word model: %s", model_path.name)
            while True:
                try:
                    frame_bytes = frame_queue.get(timeout=1.0)
                except queue.Empty:
                    ducked_music.maybe_restore()
                    poll_due_alerts_if_needed(
                        args=args,
                        logger=logger,
                        frame_queue=frame_queue,
                        pre_roll=pre_roll,
                        runtime_state=runtime_state,
                    )
                    continue
                poll_due_alerts_if_needed(
                    args=args,
                    logger=logger,
                    frame_queue=frame_queue,
                    pre_roll=pre_roll,
                    runtime_state=runtime_state,
                )
                frame = normalize_input_frame(frame_bytes, args.input_gain)
                normalized_frame = frame.copy()
                pre_roll.append(normalized_frame)
                # Batch two 80 ms frames so wake inference runs every 160 ms without dropping audio.
                inference_frames.append(normalized_frame)
                if len(inference_frames) < 2:
                    continue
                batched_frame_count = len(inference_frames)
                inference_input = np.concatenate(inference_frames)
                inference_frames.clear()
                now = time.time()
                ducked_music.maybe_restore()
                if (
                    runtime_state.wake_playback_state_checked_at <= 0.0
                    or (now - runtime_state.wake_playback_state_checked_at) >= args.wake_playback_poll_seconds
                ):
                    runtime_state.wake_playback_state_raw_active = fetch_local_playback_active(
                        args.music_control_url,
                        args.music_control_api_key,
                        logger,
                    )
                    runtime_state.wake_playback_state_checked_at = now
                effective_playback_active, hold_until = resolve_effective_playback_active(
                    raw_playback_active=runtime_state.wake_playback_state_raw_active,
                    previous_effective_playback_active=runtime_state.wake_playback_mode_active,
                    previous_hold_until=runtime_state.wake_playback_mode_hold_until,
                    now=now,
                    hold_seconds=args.wake_playback_hold_seconds,
                )
                runtime_state.wake_playback_mode_active = effective_playback_active
                runtime_state.wake_playback_mode_hold_until = hold_until
                profile = get_wake_profile(args, playback_active=effective_playback_active)
                if runtime_state.wake_last_mode != profile.mode:
                    runtime_state.wake_above_threshold_frames = 0
                    runtime_state.wake_last_mode = profile.mode
                prediction = wake_model.predict(inference_input)
                score = float(prediction.get(wake_key, 0.0))
                if not wake_window_open(runtime_state=runtime_state, now=now):
                    runtime_state.wake_above_threshold_frames = 0
                    continue
                duck_stage = classify_duck_stage(score, trigger_threshold=args.music_duck_trigger_threshold)
                if duck_stage is not None:
                    ducked_music.apply_duck_stage(duck_stage)
                if should_log_wake_score(
                    score,
                    wake_log_threshold=profile.wake_log_threshold,
                    last_score_log=last_score_log,
                    now=now,
                ):
                    logger.info("Wake score %.3f", score)
                    last_score_log = now
                if wake_capture is not None:
                    wake_capture.observe_score(
                        score=score,
                        active_threshold=profile.wake_threshold,
                        playback_active=runtime_state.wake_playback_state_raw_active,
                        ducking_triggered=duck_stage is not None,
                        now=now,
                    )
                if score < profile.wake_threshold:
                    runtime_state.wake_above_threshold_frames = 0
                    continue
                runtime_state.wake_above_threshold_frames += batched_frame_count
                if runtime_state.wake_above_threshold_frames < profile.required_consecutive_frames:
                    continue
                runtime_state.wake_above_threshold_frames = 0

                logger.info("Wake word detected (score %.3f mode=%s).", score, profile.mode)
                interaction_correlation_id = f"corr_{uuid.uuid4().hex}"
                wake_audio_level = frame_rms(inference_input)
                interrupted_playback = None

                def _commit_winning_listener() -> None:
                    nonlocal interrupted_playback
                    interrupted_playback = ducked_music.take_interrupted_playback()
                    if not interrupted_playback:
                        interrupted_playback = interrupt_local_playback(
                            control_url=args.music_control_url,
                            api_key=args.music_control_api_key,
                            settle_seconds=args.playback_interrupt_settle_seconds,
                            logger=logger,
                        )
                    elif args.playback_interrupt_settle_seconds > 0:
                        time.sleep(args.playback_interrupt_settle_seconds)
                    runtime_state.reply_output_handoff_until = time.time() + 2.5 if interrupted_playback else 0.0

                if wake_capture is not None:
                    wake_capture.record_activation(
                        score=score,
                        playback_active=runtime_state.wake_playback_state_raw_active,
                        ducking_triggered=duck_stage is not None,
                        now=now,
                    )
                runtime_state.next_wake_time = now + args.wake_cooldown_seconds
                arbitration_result = arbitrate_provisional_capture(
                    args=args,
                    logger=logger,
                    runtime_state=runtime_state,
                    capture_func=lambda: capture_after_wake(args=args, frame_queue=frame_queue, pre_roll=pre_roll),
                    satellite_id=args.satellite_id,
                    wake_confidence=score,
                    audio_level=wake_audio_level,
                    correlation_id=interaction_correlation_id,
                    on_proceed=_commit_winning_listener,
                )
                if not arbitration_result.proceeded:
                    ducked_music.maybe_restore(force=True)
                    continue
                capture_outcome = arbitration_result.capture_outcome
                if capture_outcome is None:
                    runtime_state.wake_state = WAKE_STATE_IDLE
                    continue
                report_satellite_activity(
                    args.oracle_url,
                    source_id=args.source,
                    event_type="wake_detected",
                    status="available",
                    correlation_id=interaction_correlation_id,
                    payload={
                        "wake_score": score,
                        "wake_mode": profile.mode,
                        "audio_level": wake_audio_level,
                        "wake_arbitration_decision": (
                            arbitration_result.decision.decision if arbitration_result.decision is not None else "fail_open"
                        ),
                        "playback_active": bool(runtime_state.wake_playback_state_raw_active),
                    },
                    timeout=0.05,
                    credential=getattr(args, "brain_api_key", ""),
                )
                if not capture_outcome.pcm_bytes:
                    report_satellite_activity(
                        args.oracle_url,
                        source_id=args.source,
                        event_type="audio_capture_failed",
                        status="degraded",
                        correlation_id=interaction_correlation_id,
                        payload={
                            "stop_reason": capture_outcome.stop_reason,
                            "total_frames": capture_outcome.total_frames,
                            "voiced_frames": capture_outcome.voiced_frames,
                            "silence_frames": capture_outcome.silence_frames,
                            "max_energy": capture_outcome.max_energy,
                            "noise_floor": capture_outcome.noise_floor,
                            "speech_threshold": capture_outcome.speech_threshold,
                            "silence_threshold": capture_outcome.silence_threshold,
                            "vad_threshold": args.vad_threshold,
                        },
                        snapshot={"last_error": capture_outcome.stop_reason},
                        timeout=0.05,
                        credential=getattr(args, "brain_api_key", ""),
                    )
                    logger.info(
                        "No usable speech captured after wake word. stop_reason=%s total_frames=%d voiced_frames=%d silence_frames=%d max_energy=%.4f noise_floor=%.4f speech_threshold=%.4f silence_threshold=%.4f vad_threshold=%.4f",
                        capture_outcome.stop_reason,
                        capture_outcome.total_frames,
                        capture_outcome.voiced_frames,
                        capture_outcome.silence_frames,
                        capture_outcome.max_energy,
                        capture_outcome.noise_floor,
                        capture_outcome.speech_threshold,
                        capture_outcome.silence_threshold,
                        args.vad_threshold,
                    )
                    runtime_state.next_wake_time = min(
                        runtime_state.next_wake_time,
                        time.time() + max(0.0, args.wake_retry_cooldown_seconds),
                    )
                    runtime_state.wake_state = WAKE_STATE_IDLE
                    continue
                capture_elapsed_ms = arbitration_result.capture_elapsed_ms
                logger.info(
                    "Capture ended. stop_reason=%s total_frames=%d voiced_frames=%d silence_frames=%d max_energy=%.4f noise_floor=%.4f speech_threshold=%.4f silence_threshold=%.4f vad_threshold=%.4f",
                    capture_outcome.stop_reason,
                    capture_outcome.total_frames,
                    capture_outcome.voiced_frames,
                    capture_outcome.silence_frames,
                    capture_outcome.max_energy,
                    capture_outcome.noise_floor,
                    capture_outcome.speech_threshold,
                    capture_outcome.silence_threshold,
                    args.vad_threshold,
                )

                pipeline.process_capture(
                    capture_outcome.pcm_bytes,
                    capture_elapsed_ms=capture_elapsed_ms,
                    interrupted_playback=interrupted_playback,
                    correlation_id=interaction_correlation_id,
                )
                runtime_state.wake_state = WAKE_STATE_IDLE
    except KeyboardInterrupt:
        report_satellite_activity(
            args.oracle_url,
            source_id=args.source,
            event_type="satellite_stopped",
            status="unavailable",
            timeout=0.5,
            credential=getattr(args, "brain_api_key", ""),
        )
        logger.info("Stopping satellite.")
        return 0

    return 0
