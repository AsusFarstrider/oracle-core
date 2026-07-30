from __future__ import annotations

import importlib.util
import io
import queue
import wave
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np

from .models import CaptureOutcome

if TYPE_CHECKING:
    from openwakeword.model import Model


SAMPLE_RATE = 16000
FRAME_LENGTH = 1280


def frame_rms(frame: np.ndarray) -> float:
    audio = frame.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(audio * audio) + 1e-12))


def pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


def clear_audio_queue(frame_queue: queue.Queue[bytes], pre_roll: deque[np.ndarray]) -> None:
    while True:
        try:
            frame_queue.get_nowait()
        except queue.Empty:
            break
    pre_roll.clear()


def collect_followup_pre_roll_frames(
    frame_queue: queue.Queue[bytes],
    *,
    max_frames: int = 3,
    timeout_seconds: float = 0.24,
) -> List[np.ndarray]:
    import time

    frames: List[np.ndarray] = []
    deadline = time.time() + max(0.0, timeout_seconds)
    while len(frames) < max_frames:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            frame_bytes = frame_queue.get(timeout=min(0.08, remaining))
        except queue.Empty:
            break
        frame = np.frombuffer(frame_bytes, dtype=np.int16)
        if frame.size == 0:
            continue
        frames.append(frame.copy())
    return frames


def capture_utterance_after_wake(
    frame_queue: queue.Queue[bytes],
    pre_roll_frames: List[np.ndarray],
    vad_threshold: float,
    vad_noise_multiplier: float,
    vad_noise_offset: float,
    vad_release_multiplier: float,
    vad_release_offset: float,
    vad_max_speech_threshold: float,
    vad_max_silence_threshold: float,
    silence_seconds: float,
    max_record_seconds: float,
    min_speech_seconds: float,
    input_gain: float,
    speech_start_timeout_seconds: float | None = None,
    false_start_silence_seconds: float | None = None,
) -> CaptureOutcome:
    frame_duration = FRAME_LENGTH / SAMPLE_RATE
    max_frames = int(max_record_seconds / frame_duration)
    silence_frames_needed = max(1, int(silence_seconds / frame_duration))
    min_speech_frames = max(1, int(min_speech_seconds / frame_duration))
    false_start_silence_frames = (
        max(1, int(false_start_silence_seconds / frame_duration))
        if false_start_silence_seconds is not None and false_start_silence_seconds > 0
        else silence_frames_needed
    )
    speech_start_timeout_frames = (
        max(1, int(speech_start_timeout_seconds / frame_duration))
        if speech_start_timeout_seconds is not None and speech_start_timeout_seconds > 0
        else None
    )

    normalized_pre_roll: List[np.ndarray] = []
    for frame in pre_roll_frames:
        if frame.size == 0:
            continue
        normalized = frame
        if input_gain != 1.0:
            normalized = np.clip(frame.astype(np.float32) * input_gain, -32768, 32767).astype(np.int16)
        normalized_pre_roll.append(normalized.copy())

    collected: List[np.ndarray] = [frame.copy() for frame in normalized_pre_roll]
    voiced_frames = 0
    silence_frames = 0
    heard_voice = False
    total_frames = 0
    max_energy = 0.0
    stop_reason = "timeout"
    noise_reference_frames = normalized_pre_roll[:-3] if len(normalized_pre_roll) > 3 else normalized_pre_roll
    pre_roll_energies = [frame_rms(frame) for frame in noise_reference_frames if frame.size > 0]
    noise_floor = float(np.percentile(pre_roll_energies, 10)) if pre_roll_energies else 0.0
    speech_threshold = max(vad_threshold, noise_floor * vad_noise_multiplier + vad_noise_offset)
    silence_threshold = max(vad_threshold * 0.5, noise_floor * vad_release_multiplier + vad_release_offset)
    speech_threshold = min(speech_threshold, max(vad_threshold, vad_max_speech_threshold))
    silence_threshold = min(silence_threshold, max(vad_threshold * 0.5, vad_max_silence_threshold))

    for _ in range(max_frames):
        try:
            frame_bytes = frame_queue.get(timeout=1.0)
        except queue.Empty:
            stop_reason = "queue_empty"
            break

        frame = np.frombuffer(frame_bytes, dtype=np.int16)
        if frame.size == 0:
            continue
        if input_gain != 1.0:
            frame = np.clip(frame.astype(np.float32) * input_gain, -32768, 32767).astype(np.int16)
        collected.append(frame.copy())
        total_frames += 1

        energy = frame_rms(frame)
        max_energy = max(max_energy, energy)
        if energy >= speech_threshold:
            heard_voice = True
            voiced_frames += 1
            silence_frames = 0
        elif heard_voice:
            has_minimum_speech = voiced_frames >= min_speech_frames
            if energy <= silence_threshold or (has_minimum_speech and energy < speech_threshold):
                silence_frames += 1
            else:
                silence_frames = 0
            if silence_frames >= silence_frames_needed and has_minimum_speech:
                stop_reason = "silence"
                break
            if silence_frames >= false_start_silence_frames and voiced_frames < min_speech_frames:
                stop_reason = "false_start_silence"
                break
        elif speech_start_timeout_frames is not None and total_frames >= speech_start_timeout_frames:
            stop_reason = "speech_start_timeout"
            break

    if not heard_voice or voiced_frames < min_speech_frames:
        return CaptureOutcome(
            pcm_bytes=None,
            stop_reason="insufficient_speech",
            total_frames=total_frames,
            voiced_frames=voiced_frames,
            silence_frames=silence_frames,
            max_energy=max_energy,
            noise_floor=noise_floor,
            speech_threshold=speech_threshold,
            silence_threshold=silence_threshold,
        )

    return CaptureOutcome(
        pcm_bytes=np.concatenate(collected).astype(np.int16).tobytes(),
        stop_reason=stop_reason,
        total_frames=total_frames,
        voiced_frames=voiced_frames,
        silence_frames=silence_frames,
        max_energy=max_energy,
        noise_floor=noise_floor,
        speech_threshold=speech_threshold,
        silence_threshold=silence_threshold,
    )


def build_wake_model(model_path: Path) -> Tuple["Model", str]:
    from openwakeword.model import Model

    suffix = model_path.suffix.lower()
    if suffix == ".onnx":
        return (
            Model(
                wakeword_models=[str(model_path)],
                inference_framework="onnx",
            ),
            model_path.stem,
        )
    if suffix == ".tflite":
        if importlib.util.find_spec("tflite_runtime") is None:
            raise RuntimeError(
                "TFLite wake model selected but tflite_runtime is not installed in the satellite venv."
            )
        return (
            Model(
                wakeword_models=[str(model_path)],
                inference_framework="tflite",
            ),
            model_path.stem,
        )
    raise RuntimeError(f"Unsupported wake model type: {model_path}")
