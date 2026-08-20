from __future__ import annotations

import queue
import time
import uuid
from collections import deque
from typing import Optional

import requests

from .audio import play_error_tone, resolve_output_device
from .local_control import (
    begin_foreground_handoff,
    extract_deferred_transport_resume,
    finalize_foreground_handoff,
    is_transport_playback_command,
    interrupt_local_playback,
    prepare_interrupted_playback_for_reply,
    resume_deferred_transport_after_reply,
    resume_interrupted_local_playback,
    should_resume_after_reply_for_transport_command,
)
from .models import ForegroundAudioRequest, ForegroundHandoff, InterruptedPlayback, RuntimeState
from .reply_runtime import ReplyRuntime, TimedCaptureOutcome
from .request_runtime import RequestPipelineError, run_request_pipeline
from .wake import clear_audio_queue


REQUEST_EXCEPTION = getattr(requests, "RequestException", RuntimeError)


class CapturePipeline:
    def __init__(
        self,
        *,
        args,
        logger,
        frame_queue: queue.Queue[bytes],
        pre_roll: deque,
        wake_model,
        wake_key: str,
        ducked_music,
        runtime_state: RuntimeState,
    ) -> None:
        self._args = args
        self._logger = logger
        self._frame_queue = frame_queue
        self._pre_roll = pre_roll
        self._wake_model = wake_model
        self._wake_key = wake_key
        self._ducked_music = ducked_music
        self._state = runtime_state
        self._reply_runtime = ReplyRuntime(
            args=args,
            logger=logger,
            frame_queue=frame_queue,
            pre_roll=pre_roll,
            wake_model=wake_model,
            wake_key=wake_key,
            runtime_state=runtime_state,
        )
        self._reply_runtime._process_followup_capture = self._process_followup_capture

    def _build_reply_foreground_request(
        self,
        *,
        outcome: CommandOutcome,
        interrupted_playback: Optional[list[InterruptedPlayback]],
        deferred_transport_resume: InterruptedPlayback | None,
    ) -> ForegroundAudioRequest:
        if deferred_transport_resume is not None:
            return ForegroundAudioRequest(
                kind="reply",
                handoff_mode="replace",
                interrupt_policy="none",
                resume_policy="replace_with_deferred",
            )
        if interrupted_playback:
            return ForegroundAudioRequest(
                kind="reply",
                handoff_mode="borrow",
                interrupt_policy="none",
                resume_policy=(
                    "resume_previous"
                    if (
                        not is_transport_playback_command(outcome)
                        or should_resume_after_reply_for_transport_command(outcome)
                    )
                    else "no_resume"
                ),
            )
        if should_resume_after_reply_for_transport_command(outcome):
            return ForegroundAudioRequest(
                kind="reply",
                handoff_mode="borrow",
                interrupt_policy="pause_or_stronger",
                resume_policy="resume_previous",
            )
        return ForegroundAudioRequest(
            kind="reply",
            handoff_mode="borrow",
            interrupt_policy="none",
            resume_policy="no_resume",
        )

    def _begin_reply_foreground_handoff(
        self,
        *,
        outcome: CommandOutcome,
        interrupted_playback: Optional[list[InterruptedPlayback]],
        deferred_transport_resume: InterruptedPlayback | None,
    ) -> ForegroundHandoff:
        foreground_handoff = begin_foreground_handoff(
            control_url=self._args.music_control_url,
            api_key=self._args.music_control_api_key,
            request=self._build_reply_foreground_request(
                outcome=outcome,
                interrupted_playback=interrupted_playback,
                deferred_transport_resume=deferred_transport_resume,
            ),
            settle_seconds=self._args.playback_interrupt_settle_seconds,
            logger=self._logger,
        )
        if interrupted_playback and deferred_transport_resume is None:
            foreground_handoff.interrupted_sessions = interrupted_playback
        return foreground_handoff

    def _play_error_tone_if_due(self) -> None:
        if not getattr(self._args, "error_tone_enabled", True):
            return
        now = time.time()
        if now < self._state.next_error_tone_at:
            return
        try:
            play_error_tone(resolve_output_device(self._args))
            self._state.next_error_tone_at = now + float(self._args.error_tone_cooldown_seconds)
        except Exception as exc:
            self._logger.warning("Error tone playback failed: %s", exc)

    def process_capture(
        self,
        pcm_bytes: bytes,
        *,
        capture_elapsed_ms: float,
        interrupted_playback: Optional[list[InterruptedPlayback]] = None,
        correlation_id: str | None = None,
    ) -> None:
        outcome = None
        playback_elapsed_ms = 0.0
        request_result = None
        deferred_transport_resume = None
        foreground_handoff: ForegroundHandoff | None = None
        foreground_final_state = ""
        foreground_reason = ""
        interaction_correlation_id = str(correlation_id or "").strip() or f"corr_{uuid.uuid4().hex}"
        try:
            if interrupted_playback and bool(getattr(self._args, "ack_tone_enabled", False)):
                interrupted_playback = prepare_interrupted_playback_for_reply(
                    control_url=self._args.music_control_url,
                    api_key=self._args.music_control_api_key,
                    interrupted=interrupted_playback,
                    settle_seconds=self._args.playback_interrupt_settle_seconds,
                    logger=self._logger,
                )
            request_result = run_request_pipeline(
                args=self._args,
                logger=self._logger,
                runtime_state=self._state,
                pcm_bytes=pcm_bytes,
                suppress_ack_tone=bool(interrupted_playback),
                correlation_id=interaction_correlation_id,
            )
            outcome = request_result.outcome
            if request_result.tts_wav:
                deferred_transport_resume = extract_deferred_transport_resume(
                    outcome,
                    oracle_url=getattr(self._args, "oracle_url", ""),
                    source=getattr(self._args, "source", ""),
                    credential=getattr(self._args, "brain_api_key", ""),
                )
                foreground_handoff = self._begin_reply_foreground_handoff(
                    outcome=outcome,
                    interrupted_playback=interrupted_playback,
                    deferred_transport_resume=deferred_transport_resume,
                )
                if foreground_handoff is not None:
                    interrupted_playback = foreground_handoff.interrupted_sessions
                reply_result = self._reply_runtime.play_reply(
                    tts_wav=request_result.tts_wav,
                    outcome=outcome,
                    foreground_handoff=foreground_handoff,
                    interrupted_playback=interrupted_playback,
                    process_capture=self.process_capture,
                    correlation_id=interaction_correlation_id,
                )
                interrupted_playback = reply_result.interrupted_playback
                playback_elapsed_ms = reply_result.playback_elapsed_ms
                foreground_final_state = reply_result.foreground_final_state
                foreground_reason = reply_result.foreground_reason
            self._logger.info(
                "Pipeline timing capture=%.1fms stt=%.1fms command=%.1fms tts=%.1fms playback=%.1fms transcript_chars=%d reply_chars=%d",
                capture_elapsed_ms,
                request_result.stt_elapsed_ms,
                request_result.command_elapsed_ms,
                request_result.tts_elapsed_ms,
                playback_elapsed_ms,
                len(request_result.transcript),
                len(outcome.spoken_reply),
            )
        except RequestPipelineError as exc:
            if exc.should_play_error_tone:
                self._play_error_tone_if_due()
            time.sleep(0.5)
        except REQUEST_EXCEPTION as exc:
            self._logger.error("Request failed: %s", exc)
            if foreground_handoff is not None and not foreground_final_state:
                foreground_final_state = "failed"
                foreground_reason = str(exc)
            self._play_error_tone_if_due()
            time.sleep(0.5)
        except Exception as exc:
            self._logger.error("Pipeline failed: %s", exc)
            if foreground_handoff is not None and not foreground_final_state:
                foreground_final_state = "failed"
                foreground_reason = str(exc)
            time.sleep(0.5)
        finally:
            if foreground_handoff is not None:
                finalize_foreground_handoff(
                    control_url=self._args.music_control_url,
                    api_key=self._args.music_control_api_key,
                    handoff=foreground_handoff,
                    logger=self._logger,
                    deferred_resume=deferred_transport_resume,
                    foreground_final_state=foreground_final_state,
                    foreground_reason=foreground_reason,
                )
            else:
                if deferred_transport_resume is not None:
                    resume_deferred_transport_after_reply(
                        control_url=self._args.music_control_url,
                        api_key=self._args.music_control_api_key,
                        deferred=deferred_transport_resume,
                        logger=self._logger,
                    )
                should_resume = bool(interrupted_playback) and (
                    not is_transport_playback_command(outcome) or should_resume_after_reply_for_transport_command(outcome)
                )
                if should_resume:
                    resume_interrupted_local_playback(
                        control_url=self._args.music_control_url,
                        api_key=self._args.music_control_api_key,
                        interrupted=interrupted_playback,
                        logger=self._logger,
                    )
            self._ducked_music.maybe_restore(force=True)
            clear_audio_queue(self._frame_queue, self._pre_roll)

    def _process_followup_capture(
        self,
        followup_capture: TimedCaptureOutcome,
        interrupted_playback: Optional[list[InterruptedPlayback]],
    ) -> None:
        self.process_capture(
            followup_capture.capture.pcm_bytes,
            capture_elapsed_ms=followup_capture.elapsed_ms,
            interrupted_playback=interrupted_playback,
        )
