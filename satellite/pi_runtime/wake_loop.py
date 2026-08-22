from __future__ import annotations

import queue
import time

import numpy as np

from .models import RuntimeState
from .wake import capture_utterance_after_wake


def enqueue_input_frame(frame_queue: queue.Queue[bytes], indata: bytes) -> None:
    try:
        frame_queue.put_nowait(bytes(indata))
    except queue.Full:
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            pass
        frame_queue.put_nowait(bytes(indata))


def normalize_input_frame(frame_bytes: bytes, input_gain: float):
    frame = np.frombuffer(frame_bytes, dtype=np.int16)
    if input_gain != 1.0:
        frame = np.clip(frame.astype(np.float32) * input_gain, -32768, 32767).astype(np.int16)
    return frame


def should_log_wake_score(score: float, *, wake_log_threshold: float, last_score_log: float, now: float) -> bool:
    return score >= wake_log_threshold and (now - last_score_log) > 1.0


def wake_window_open(*, runtime_state: RuntimeState, now: float) -> bool:
    return now >= runtime_state.next_wake_time


def capture_after_wake(*, args, frame_queue, pre_roll):
    return capture_utterance_after_wake(
        frame_queue=frame_queue,
        pre_roll_frames=list(pre_roll),
        vad_threshold=args.vad_threshold,
        vad_noise_multiplier=args.vad_noise_multiplier,
        vad_noise_offset=args.vad_noise_offset,
        vad_release_multiplier=args.vad_release_multiplier,
        vad_release_offset=args.vad_release_offset,
        vad_max_speech_threshold=args.vad_max_speech_threshold,
        vad_max_silence_threshold=args.vad_max_silence_threshold,
        silence_seconds=args.silence_seconds,
        max_record_seconds=args.max_record_seconds,
        min_speech_seconds=args.min_speech_seconds,
        input_gain=args.input_gain,
        speech_start_timeout_seconds=args.speech_start_timeout_seconds,
        false_start_silence_seconds=args.false_start_silence_seconds,
    )
