from __future__ import annotations

import queue
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from .audio import (
    play_followup_listen_cue,
    play_wav_bytes,
    play_wav_bytes_with_wake_interrupt,
    resolve_output_device,
)
from .local_control import (
    begin_foreground_handoff,
    finalize_foreground_handoff,
    should_listen_for_followup_reply,
)
from .models import CaptureOutcome, CommandOutcome, ForegroundAudioRequest, ForegroundHandoff, InterruptedPlayback, RuntimeState
from .oracle_client import report_satellite_activity
from .wake import (
    capture_utterance_after_wake,
    clear_audio_queue,
    collect_followup_pre_roll_frames,
)


@dataclass
class ReplyPlaybackResult:
    interrupted_playback: Optional[list[InterruptedPlayback]]
    playback_elapsed_ms: float
    foreground_final_state: str = ""
    foreground_reason: str = ""


@dataclass
class TimedCaptureOutcome:
    capture: CaptureOutcome
    elapsed_ms: float


class ReplyRuntime:
    def __init__(
        self,
        *,
        args,
        logger,
        frame_queue: queue.Queue[bytes],
        pre_roll: deque,
        wake_model,
        wake_key: str,
        runtime_state: RuntimeState,
    ) -> None:
        self._args = args
        self._logger = logger
        self._frame_queue = frame_queue
        self._pre_roll = pre_roll
        self._wake_model = wake_model
        self._wake_key = wake_key
        self._state = runtime_state

    def _build_followup_cue_foreground_request(self) -> ForegroundAudioRequest:
        return ForegroundAudioRequest(
            kind="followup_cue",
            handoff_mode="borrow",
            interrupt_policy="none",
            resume_policy="no_resume",
            correlation_id=uuid.uuid4().hex,
        )

    def _begin_followup_cue_handoff(self) -> ForegroundHandoff:
        return begin_foreground_handoff(
            control_url=self._args.music_control_url,
            api_key=self._args.music_control_api_key,
            request=self._build_followup_cue_foreground_request(),
            settle_seconds=0.0,
            logger=self._logger,
        )

    def play_reply(
        self,
        *,
        tts_wav: bytes,
        outcome: CommandOutcome,
        foreground_handoff: ForegroundHandoff | None,
        interrupted_playback: Optional[list[InterruptedPlayback]],
        process_capture: Callable[..., None],
        correlation_id: str | None = None,
    ) -> ReplyPlaybackResult:
        playback_started_at = time.perf_counter()
        reply_interrupted = False
        playback_failed = False
        authority_final_state = "completed"
        authority_reason = ""
        playback_handoff_active = bool(interrupted_playback) or (
            self._state.reply_output_handoff_until > time.time()
        )
        output_device = resolve_output_device(self._args)
        settle_seconds = self._reply_output_settle_seconds(
            outcome=outcome,
            interrupted_playback=interrupted_playback,
        )
        self._logger.info(
            "reply_playback_request source=%s session_id=%s output_device_index=%s playback_gain=%.3f wav_bytes=%d interrupt_replies=%s playback_handoff_active=%s settle_ms=%.1f authority_session_id=%s authority_correlation_id=%s",
            self._args.source or "-",
            self._state.active_session_id or "-",
            str(output_device),
            float(self._args.playback_gain),
            len(tts_wav or b""),
            str(bool(self._args.interrupt_replies)).lower(),
            str(bool(playback_handoff_active)).lower(),
            settle_seconds * 1000.0,
            (foreground_handoff.foreground_session_id if foreground_handoff is not None else "") or "-",
            (foreground_handoff.authority_correlation_id if foreground_handoff is not None else "") or "-",
        )
        self._log_reply_event("reply_playback_started", outcome=outcome)
        try:
            if settle_seconds > 0.0:
                time.sleep(settle_seconds)
            if self._args.interrupt_replies:
                reply_interrupted = play_wav_bytes_with_wake_interrupt(
                    tts_wav,
                    output_device,
                    self._args.playback_gain,
                    reply_audio_state_path=self._args.reply_audio_state_path,
                    reply_audio_stop_path=self._args.reply_audio_stop_path,
                    frame_queue=self._frame_queue,
                    pre_roll=self._pre_roll,
                    wake_model=self._wake_model,
                    wake_key=self._wake_key,
                    wake_threshold=self._args.wake_threshold,
                    input_gain=self._args.input_gain,
                    playback_handoff_active=playback_handoff_active,
                    reply_audio_session_id=(foreground_handoff.foreground_session_id if foreground_handoff is not None else ""),
                    reply_audio_correlation_id=(foreground_handoff.authority_correlation_id if foreground_handoff is not None else ""),
                    interrupt_grace_seconds=float(getattr(self._args, "reply_interrupt_grace_seconds", 0.35) or 0.35),
                    logger=self._logger,
                )
            else:
                play_wav_bytes(
                    tts_wav,
                    output_device,
                    self._args.playback_gain,
                    reply_audio_state_path=self._args.reply_audio_state_path,
                    reply_audio_stop_path=self._args.reply_audio_stop_path,
                    playback_handoff_active=playback_handoff_active,
                    reply_audio_session_id=(foreground_handoff.foreground_session_id if foreground_handoff is not None else ""),
                    reply_audio_correlation_id=(foreground_handoff.authority_correlation_id if foreground_handoff is not None else ""),
                    logger=self._logger,
                )
        except Exception as exc:
            playback_failed = True
            authority_final_state = "failed"
            authority_reason = str(exc)
            report_satellite_activity(
                getattr(self._args, "oracle_url", ""),
                source_id=getattr(self._args, "source", "") or "unknown-satellite",
                event_type="tts_playback_failed",
                status="degraded",
                correlation_id=correlation_id,
                payload={
                    "detail": str(exc),
                    "wav_bytes": len(tts_wav or b""),
                },
                snapshot={"last_error": str(exc)},
                timeout=0.05,
                credential=getattr(self._args, "brain_api_key", ""),
            )
            self._logger.warning("Reply playback failed: %s", exc)
        if reply_interrupted and not playback_failed:
            authority_final_state = "interrupted"
            authority_reason = "wake_or_stop_request"
        playback_elapsed_ms = (time.perf_counter() - playback_started_at) * 1000.0
        self._log_reply_event(
            "reply_playback_finished",
            outcome=outcome,
            detail="failed" if playback_failed else ("interrupted" if reply_interrupted else "completed"),
            playback_elapsed_ms=playback_elapsed_ms,
        )
        if playback_failed:
            return ReplyPlaybackResult(
                interrupted_playback=interrupted_playback,
                playback_elapsed_ms=playback_elapsed_ms,
                foreground_final_state=authority_final_state,
                foreground_reason=authority_reason,
            )
        if reply_interrupted:
            self._handle_reply_interrupted(process_capture, outcome)
            return ReplyPlaybackResult(
                interrupted_playback=interrupted_playback,
                playback_elapsed_ms=playback_elapsed_ms,
                foreground_final_state=authority_final_state,
                foreground_reason=authority_reason,
            )
        return ReplyPlaybackResult(
            interrupted_playback=self._handle_followup_or_post_playback(outcome, interrupted_playback),
            playback_elapsed_ms=playback_elapsed_ms,
            foreground_final_state=authority_final_state,
            foreground_reason=authority_reason,
        )

    def _reply_output_settle_seconds(
        self,
        *,
        outcome: CommandOutcome,
        interrupted_playback: Optional[list[InterruptedPlayback]],
    ) -> float:
        if not interrupted_playback:
            return 0.0
        raw_response = outcome.raw_response if isinstance(outcome.raw_response, dict) else {}
        dispatch = raw_response.get("dispatch") if isinstance(raw_response.get("dispatch"), dict) else {}
        result = dispatch.get("result") if isinstance(dispatch.get("result"), dict) else {}
        action = str(result.get("action", "")).strip().lower()
        if action in {"stop", "pause"}:
            return float(getattr(self._args, "reply_output_settle_seconds", 1.0) or 1.0)
        return 0.0

    def _handle_reply_interrupted(self, process_capture: Callable[..., None], outcome: CommandOutcome) -> None:
        self._logger.info("Reply playback interrupted by wake word or local stop request.")
        self._state.next_wake_time = time.time() + self._args.wake_cooldown_seconds
        interrupt_capture_started = time.perf_counter()
        self._log_reply_event("followup_capture_started", outcome=outcome, detail="reply_interrupted")
        interrupt_capture = self._capture_after_reply_interrupt()
        interrupt_capture_elapsed_ms = (time.perf_counter() - interrupt_capture_started) * 1000.0
        if interrupt_capture.pcm_bytes:
            self._log_reply_event(
                "followup_capture_finished",
                outcome=outcome,
                detail="reply_interrupted",
                capture_elapsed_ms=interrupt_capture_elapsed_ms,
                capture_has_audio=True,
            )
            process_capture(
                interrupt_capture.pcm_bytes,
                capture_elapsed_ms=interrupt_capture_elapsed_ms,
                interrupted_playback=None,
            )
            return
        self._log_reply_event(
            "followup_capture_finished",
            outcome=outcome,
            detail="reply_interrupted",
            capture_elapsed_ms=interrupt_capture_elapsed_ms,
            capture_has_audio=False,
        )
        self._log_capture_result("No usable speech captured after reply interruption.", interrupt_capture)

    def _handle_followup_or_post_playback(
        self,
        outcome: CommandOutcome,
        interrupted_playback: Optional[list[InterruptedPlayback]],
    ) -> Optional[list[InterruptedPlayback]]:
        if should_listen_for_followup_reply(outcome):
            clear_audio_queue(self._frame_queue, self._pre_roll)
            if self._args.ack_tone_enabled:
                cue_handoff = self._begin_followup_cue_handoff()
                try:
                    play_followup_listen_cue(
                        resolve_output_device(self._args),
                        min(1.0, max(self._args.ack_tone_gain * 1.75, 0.24)),
                        playback_handoff_active=self._state.reply_output_handoff_until > time.time(),
                    )
                except Exception as exc:
                    self._logger.warning("Follow-up listen cue failed: %s", exc)
                finally:
                    finalize_foreground_handoff(
                        control_url=self._args.music_control_url,
                        api_key=self._args.music_control_api_key,
                        handoff=cue_handoff,
                        logger=self._logger,
                    )
            time.sleep(min(0.18, max(0.0, self._args.playback_interrupt_settle_seconds)))
            clear_audio_queue(self._frame_queue, self._pre_roll)
            self._logger.info("Waiting for same-session follow-up reply without wake word.")
            self._log_reply_event("followup_capture_started", outcome=outcome, detail="same_session")
            followup_capture = self._capture_followup_reply()
            if followup_capture.capture.pcm_bytes:
                self._log_reply_event(
                    "followup_capture_finished",
                    outcome=outcome,
                    detail="same_session",
                    capture_elapsed_ms=followup_capture.elapsed_ms,
                    capture_has_audio=True,
                )
                self._log_capture_result("Captured follow-up reply without wake word.", followup_capture.capture)
                self._process_followup_capture(followup_capture, interrupted_playback)
                return None
            self._log_reply_event(
                "followup_capture_finished",
                outcome=outcome,
                detail="same_session",
                capture_elapsed_ms=followup_capture.elapsed_ms,
                capture_has_audio=False,
            )
            self._log_capture_result("No follow-up reply captured.", followup_capture.capture)
        clear_audio_queue(self._frame_queue, self._pre_roll)
        self._state.next_wake_time = max(
            self._state.next_wake_time,
            time.time() + self._args.post_playback_block_seconds,
        )
        return interrupted_playback

    def _process_followup_capture(
        self,
        followup_capture: TimedCaptureOutcome,
        interrupted_playback: Optional[list[InterruptedPlayback]],
    ) -> None:
        raise NotImplementedError

    def _capture_after_reply_interrupt(self):
        return capture_utterance_after_wake(
            frame_queue=self._frame_queue,
            pre_roll_frames=list(self._pre_roll),
            vad_threshold=self._args.vad_threshold,
            vad_noise_multiplier=self._args.vad_noise_multiplier,
            vad_noise_offset=self._args.vad_noise_offset,
            vad_release_multiplier=self._args.vad_release_multiplier,
            vad_release_offset=self._args.vad_release_offset,
            vad_max_speech_threshold=self._args.vad_max_speech_threshold,
            vad_max_silence_threshold=self._args.vad_max_silence_threshold,
            silence_seconds=self._args.silence_seconds,
            max_record_seconds=self._args.max_record_seconds,
            min_speech_seconds=self._args.min_speech_seconds,
            input_gain=self._args.input_gain,
            speech_start_timeout_seconds=self._args.speech_start_timeout_seconds,
            false_start_silence_seconds=self._args.false_start_silence_seconds,
        )

    def _capture_followup_reply(self) -> TimedCaptureOutcome:
        followup_pre_roll = collect_followup_pre_roll_frames(self._frame_queue)
        followup_capture_started = time.perf_counter()
        capture_outcome = capture_utterance_after_wake(
            frame_queue=self._frame_queue,
            pre_roll_frames=followup_pre_roll,
            vad_threshold=self._args.vad_threshold,
            vad_noise_multiplier=self._args.vad_noise_multiplier,
            vad_noise_offset=self._args.vad_noise_offset,
            vad_release_multiplier=self._args.vad_release_multiplier,
            vad_release_offset=self._args.vad_release_offset,
            vad_max_speech_threshold=self._args.vad_max_speech_threshold,
            vad_max_silence_threshold=self._args.vad_max_silence_threshold,
            silence_seconds=self._args.followup_silence_seconds,
            max_record_seconds=self._args.followup_max_record_seconds,
            min_speech_seconds=self._args.min_speech_seconds,
            input_gain=self._args.input_gain,
            speech_start_timeout_seconds=self._args.followup_speech_start_timeout_seconds,
            false_start_silence_seconds=self._args.false_start_silence_seconds,
        )
        return TimedCaptureOutcome(
            capture=capture_outcome,
            elapsed_ms=(time.perf_counter() - followup_capture_started) * 1000.0,
        )

    def _log_capture_result(self, prefix: str, capture_outcome: CaptureOutcome) -> None:
        self._logger.info(
            "%s stop_reason=%s total_frames=%d voiced_frames=%d silence_frames=%d max_energy=%.4f noise_floor=%.4f speech_threshold=%.4f silence_threshold=%.4f",
            prefix,
            capture_outcome.stop_reason,
            capture_outcome.total_frames,
            capture_outcome.voiced_frames,
            capture_outcome.silence_frames,
            capture_outcome.max_energy,
            capture_outcome.noise_floor,
            capture_outcome.speech_threshold,
            capture_outcome.silence_threshold,
        )

    def _log_reply_event(
        self,
        event: str,
        *,
        outcome: CommandOutcome | None = None,
        detail: str | None = None,
        playback_elapsed_ms: float | None = None,
        capture_elapsed_ms: float | None = None,
        capture_has_audio: bool | None = None,
    ) -> None:
        raw_response = outcome.raw_response if outcome and isinstance(outcome.raw_response, dict) else {}
        dispatch = raw_response.get("dispatch") if isinstance(raw_response.get("dispatch"), dict) else {}
        route = raw_response.get("route") if isinstance(raw_response.get("route"), dict) else {}
        result = dispatch.get("result") if isinstance(dispatch.get("result"), dict) else {}
        self._logger.info(
            "%s source=%s session_id=%s route_target=%s dispatch_hook=%s status=%s action=%s detail=%s reply_chars=%d playback_ms=%s capture_ms=%s capture_has_audio=%s",
            event,
            self._args.source or "-",
            self._state.active_session_id or "-",
            str(route.get("target") or dispatch.get("target") or "-"),
            str(dispatch.get("hook") or "-"),
            str(dispatch.get("status") or "-"),
            str(result.get("action") or "-"),
            detail or "-",
            len(outcome.spoken_reply) if outcome is not None else 0,
            f"{playback_elapsed_ms:.1f}" if playback_elapsed_ms is not None else "-",
            f"{capture_elapsed_ms:.1f}" if capture_elapsed_ms is not None else "-",
            str(bool(capture_has_audio)).lower() if capture_has_audio is not None else "-",
        )
