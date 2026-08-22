from __future__ import annotations

import io
import json
import os
import platform
import queue
import threading
import time
import wave
from collections import deque
from typing import TYPE_CHECKING, Optional, Tuple

import numpy as np
import sounddevice as sd

if TYPE_CHECKING:
    from openwakeword.model import Model


_OUTPUT_DEVICE_RETRY_DELAY_SECONDS = 0.2
_OUTPUT_DEVICE_RETRY_ATTEMPTS = 3
_OUTPUT_DEVICE_HANDOFF_RETRY_DELAY_SECONDS = 0.35
_OUTPUT_DEVICE_HANDOFF_RETRY_ATTEMPTS = 6
_HOST_SAFE_OUTPUT_SAMPLE_RATE = 48000
_REPLY_WAKE_INTERRUPT_GRACE_SECONDS = 0.35
_LOCAL_OUTPUT_GATE = threading.RLock()


def _is_device_unavailable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "device unavailable" in message or "-9985" in message


def _play_with_retry(
    play_fn,
    *,
    attempts: int = _OUTPUT_DEVICE_RETRY_ATTEMPTS,
    retry_delay_seconds: float = _OUTPUT_DEVICE_RETRY_DELAY_SECONDS,
) -> None:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            play_fn()
            return
        except Exception as exc:
            last_exc = exc
            if not _is_device_unavailable_error(exc) or attempt == (attempts - 1):
                raise
            time.sleep(retry_delay_seconds)
    if last_exc is not None:
        raise last_exc


def _wait_for_output_device_ready(
    *,
    sample_rate: int,
    channel_count: int,
    output_device_index: Optional[int],
    attempts: int,
    retry_delay_seconds: float,
) -> None:
    if not hasattr(sd, "OutputStream"):
        return
    last_exc: Exception | None = None
    channels = max(1, int(channel_count or 1))
    for attempt in range(attempts):
        try:
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                device=output_device_index,
                dtype="float32",
            )
            stream.close()
            return
        except Exception as exc:
            last_exc = exc
            if not _is_device_unavailable_error(exc) or attempt == (attempts - 1):
                raise
            time.sleep(retry_delay_seconds)
    if last_exc is not None:
        raise last_exc


def _call_stream_method(stream: object, method_name: str) -> None:
    method = getattr(stream, method_name, None)
    if method is None:
        return
    try:
        method(ignore_errors=True)
    except TypeError:
        method()


def _release_output_stream() -> None:
    if not hasattr(sd, "get_stream"):
        return
    try:
        stream = sd.get_stream()
    except Exception:
        return
    if stream is None:
        return
    try:
        _call_stream_method(stream, "stop")
    except Exception:
        pass
    try:
        _call_stream_method(stream, "close")
    except Exception:
        pass


def _reply_retry_profile(*, playback_handoff_active: bool) -> tuple[int, float]:
    if playback_handoff_active:
        return (_OUTPUT_DEVICE_HANDOFF_RETRY_ATTEMPTS, _OUTPUT_DEVICE_HANDOFF_RETRY_DELAY_SECONDS)
    return (_OUTPUT_DEVICE_RETRY_ATTEMPTS, _OUTPUT_DEVICE_RETRY_DELAY_SECONDS)


def _with_local_output_gate(play_fn):
    with _LOCAL_OUTPUT_GATE:
        return play_fn()


def _resolve_short_tone_output_device(output_device_index: Optional[int]) -> Optional[int]:
    if output_device_index is not None:
        return output_device_index
    if platform.system().lower() != "windows":
        return None
    if not hasattr(sd, "query_hostapis"):
        return None
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        return None
    for hostapi in hostapis or ():
        if not isinstance(hostapi, dict):
            continue
        if str(hostapi.get("name", "")).strip().lower() != "windows wasapi":
            continue
        default_output = hostapi.get("default_output_device")
        try:
            device_index = int(default_output)
        except (TypeError, ValueError):
            return None
        return device_index if device_index >= 0 else None
    return None


def _resample_audio(audio: np.ndarray, *, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate:
        return audio
    if audio.ndim == 1:
        source_positions = np.arange(audio.shape[0], dtype=np.float32)
        target_length = max(1, int(round(audio.shape[0] * (target_rate / float(source_rate)))))
        target_positions = np.linspace(0.0, max(float(audio.shape[0] - 1), 0.0), target_length, dtype=np.float32)
        return np.interp(target_positions, source_positions, audio.astype(np.float32)).astype(np.float32)

    channels: list[np.ndarray] = []
    for channel_index in range(audio.shape[1]):
        channel = _resample_audio(audio[:, channel_index], source_rate=source_rate, target_rate=target_rate)
        channels.append(channel.astype(np.float32))
    return np.stack(channels, axis=1).astype(np.float32)


def decode_wav_bytes(wav_bytes: bytes, playback_gain: float) -> Tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        frames = wav_file.readframes(wav_file.getnframes())
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()

    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map.get(sample_width)
    if dtype is None:
        raise RuntimeError(f"Unsupported WAV sample width: {sample_width}")

    audio = np.frombuffer(frames, dtype=dtype)
    if channels > 1:
        audio = audio.reshape(-1, channels)

    if np.issubdtype(audio.dtype, np.integer):
        info = np.iinfo(audio.dtype)
        normalized = audio.astype(np.float32) / max(abs(float(info.min)), float(info.max))
    else:
        normalized = audio.astype(np.float32)
    scaled = np.clip(normalized * playback_gain, -1.0, 1.0).astype(np.float32)
    resampled = _resample_audio(scaled, source_rate=sample_rate, target_rate=_HOST_SAFE_OUTPUT_SAMPLE_RATE)
    return resampled.astype(np.float32), _HOST_SAFE_OUTPUT_SAMPLE_RATE


def write_reply_audio_state(
    state_path: str,
    *,
    playing: bool,
    kind: str = "tts",
    session_id: str = "",
    correlation_id: str = "",
    state_source: str = "mirror",
) -> None:
    payload = {
        "ok": True,
        "playing": playing,
        "kind": kind,
        "updated_at": time.time(),
        "state_source": state_source,
    }
    if session_id:
        payload["session_id"] = session_id
    if correlation_id:
        payload["correlation_id"] = correlation_id
    payload["state"] = "playing" if playing else "stopped"
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def clear_reply_audio_stop_request(stop_path: str) -> None:
    try:
        os.remove(stop_path)
    except FileNotFoundError:
        pass


def reply_audio_stop_requested(stop_path: str) -> bool:
    return os.path.exists(stop_path)


def play_ack_tone(
    output_device_index: Optional[int],
    ack_tone_gain: float,
    *,
    playback_handoff_active: bool = False,
) -> None:
    sample_rate = _HOST_SAFE_OUTPUT_SAMPLE_RATE
    segments = []
    for frequency_hz, duration_seconds in ((740.0, 0.045), (880.0, 0.065)):
        frame_count = max(1, int(sample_rate * duration_seconds))
        times = np.arange(frame_count, dtype=np.float32) / sample_rate
        segment = 0.5 * np.sin(2.0 * np.pi * frequency_hz * times)
        envelope = np.linspace(0.0, 1.0, frame_count, dtype=np.float32)
        envelope = np.minimum(envelope, envelope[::-1])
        segments.append(segment * envelope)
        segments.append(np.zeros(int(sample_rate * 0.015), dtype=np.float32))

    tone = np.concatenate(segments)
    tone = np.clip(tone * max(0.0, ack_tone_gain), -1.0, 1.0)
    attempts, retry_delay_seconds = _reply_retry_profile(playback_handoff_active=playback_handoff_active)
    resolved_output_device = _resolve_short_tone_output_device(output_device_index)
    _with_local_output_gate(
        lambda: _play_with_retry(
            lambda: _play_array(tone, sample_rate=sample_rate, output_device_index=resolved_output_device),
            attempts=attempts,
            retry_delay_seconds=retry_delay_seconds,
        )
    )


def play_followup_listen_cue(
    output_device_index: Optional[int],
    gain: float,
    *,
    playback_handoff_active: bool = False,
) -> None:
    sample_rate = _HOST_SAFE_OUTPUT_SAMPLE_RATE
    segments = []
    for frequency_hz, duration_seconds in ((660.0, 0.08), (660.0, 0.08), (990.0, 0.12)):
        frame_count = max(1, int(sample_rate * duration_seconds))
        times = np.arange(frame_count, dtype=np.float32) / sample_rate
        segment = 0.5 * np.sin(2.0 * np.pi * frequency_hz * times)
        envelope = np.linspace(0.0, 1.0, frame_count, dtype=np.float32)
        envelope = np.minimum(envelope, envelope[::-1])
        segments.append(segment * envelope)
        segments.append(np.zeros(int(sample_rate * 0.04), dtype=np.float32))

    tone = np.concatenate(segments)
    tone = np.clip(tone * max(0.0, gain), -1.0, 1.0)
    attempts, retry_delay_seconds = _reply_retry_profile(playback_handoff_active=playback_handoff_active)
    _with_local_output_gate(
        lambda: _play_with_retry(
            lambda: _play_array(tone, sample_rate=sample_rate, output_device_index=output_device_index),
            attempts=attempts,
            retry_delay_seconds=retry_delay_seconds,
        )
    )


def play_error_tone(output_device_index: Optional[int], gain: float = 0.18) -> None:
    sample_rate = _HOST_SAFE_OUTPUT_SAMPLE_RATE
    segments = []
    for frequency_hz, duration_seconds in ((440.0, 0.08), (330.0, 0.12)):
        frame_count = max(1, int(sample_rate * duration_seconds))
        times = np.arange(frame_count, dtype=np.float32) / sample_rate
        segment = 0.5 * np.sin(2.0 * np.pi * frequency_hz * times)
        envelope = np.linspace(0.0, 1.0, frame_count, dtype=np.float32)
        envelope = np.minimum(envelope, envelope[::-1])
        segments.append(segment * envelope)
        segments.append(np.zeros(int(sample_rate * 0.02), dtype=np.float32))

    tone = np.concatenate(segments)
    tone = np.clip(tone * max(0.0, gain), -1.0, 1.0)
    _with_local_output_gate(
        lambda: _play_with_retry(lambda: _play_array(tone, sample_rate=sample_rate, output_device_index=output_device_index))
    )


def _play_array(audio, *, sample_rate: int, output_device_index: Optional[int]) -> None:
    try:
        sd.play(audio, samplerate=sample_rate, device=output_device_index)
        sd.wait()
    finally:
        _release_output_stream()


def play_wav_bytes(
    wav_bytes: bytes,
    output_device_index: Optional[int],
    playback_gain: float,
    *,
    reply_audio_state_path: Optional[str] = None,
    reply_audio_stop_path: Optional[str] = None,
    reply_audio_kind: str = "tts",
    playback_handoff_active: bool = False,
    reply_audio_session_id: str = "",
    reply_audio_correlation_id: str = "",
    logger=None,
) -> bool:
    scaled, sample_rate = decode_wav_bytes(wav_bytes, playback_gain)
    if logger is not None:
        logger.info(
            "reply_audio_stream_start mode=plain output_device_index=%s playback_gain=%.3f sample_rate=%d frame_count=%d playback_handoff_active=%s session_id=%s correlation_id=%s",
            str(output_device_index),
            float(playback_gain),
            sample_rate,
            int(len(scaled)),
            str(bool(playback_handoff_active)).lower(),
            reply_audio_session_id or "-",
            reply_audio_correlation_id or "-",
        )

    if reply_audio_stop_path:
        clear_reply_audio_stop_request(reply_audio_stop_path)
    if reply_audio_state_path:
        write_reply_audio_state(
            reply_audio_state_path,
            playing=True,
            kind=reply_audio_kind,
            session_id=reply_audio_session_id,
            correlation_id=reply_audio_correlation_id,
        )

    interrupted = False
    try:
        def _play_reply() -> None:
            nonlocal interrupted
            attempts, retry_delay_seconds = _reply_retry_profile(playback_handoff_active=playback_handoff_active)
            _wait_for_output_device_ready(
                sample_rate=sample_rate,
                channel_count=(scaled.shape[1] if getattr(scaled, "ndim", 1) > 1 else 1),
                output_device_index=output_device_index,
                attempts=attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            _play_with_retry(
                lambda: sd.play(scaled, samplerate=sample_rate, device=output_device_index),
                attempts=attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            while True:
                stream = sd.get_stream()
                if stream is None or not getattr(stream, "active", False):
                    break
                if reply_audio_stop_path and reply_audio_stop_requested(reply_audio_stop_path):
                    interrupted = True
                    sd.stop()
                    break
                time.sleep(0.05)
            try:
                sd.wait()
            finally:
                _release_output_stream()

        _with_local_output_gate(_play_reply)
    finally:
        if reply_audio_state_path:
            write_reply_audio_state(
                reply_audio_state_path,
                playing=False,
                kind=reply_audio_kind,
                session_id=reply_audio_session_id,
                correlation_id=reply_audio_correlation_id,
            )
        if reply_audio_stop_path:
            clear_reply_audio_stop_request(reply_audio_stop_path)
    return not interrupted


def play_wav_bytes_with_wake_interrupt(
    wav_bytes: bytes,
    output_device_index: Optional[int],
    playback_gain: float,
    *,
    reply_audio_state_path: str,
    reply_audio_stop_path: str,
    frame_queue: queue.Queue[bytes],
    pre_roll: deque[np.ndarray],
    wake_model: "Model",
    wake_key: str,
    wake_threshold: float,
    input_gain: float,
    playback_handoff_active: bool = False,
    reply_audio_session_id: str = "",
    reply_audio_correlation_id: str = "",
    interrupt_grace_seconds: float = _REPLY_WAKE_INTERRUPT_GRACE_SECONDS,
    logger=None,
) -> bool:
    scaled, sample_rate = decode_wav_bytes(wav_bytes, playback_gain)
    if logger is not None:
        logger.info(
            "reply_audio_stream_start mode=wake_interrupt output_device_index=%s playback_gain=%.3f sample_rate=%d frame_count=%d playback_handoff_active=%s interrupt_grace_ms=%.1f session_id=%s correlation_id=%s",
            str(output_device_index),
            float(playback_gain),
            sample_rate,
            int(len(scaled)),
            str(bool(playback_handoff_active)).lower(),
            max(0.0, float(interrupt_grace_seconds)) * 1000.0,
            reply_audio_session_id or "-",
            reply_audio_correlation_id or "-",
        )
    clear_reply_audio_stop_request(reply_audio_stop_path)
    write_reply_audio_state(
        reply_audio_state_path,
        playing=True,
        kind="tts",
        session_id=reply_audio_session_id,
        correlation_id=reply_audio_correlation_id,
    )

    interrupted = False
    try:
        def _play_interruptible_reply() -> None:
            nonlocal interrupted
            attempts, retry_delay_seconds = _reply_retry_profile(playback_handoff_active=playback_handoff_active)
            _wait_for_output_device_ready(
                sample_rate=sample_rate,
                channel_count=(scaled.shape[1] if getattr(scaled, "ndim", 1) > 1 else 1),
                output_device_index=output_device_index,
                attempts=attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            _play_with_retry(
                lambda: sd.play(scaled, samplerate=sample_rate, device=output_device_index),
                attempts=attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            playback_started_at = time.monotonic()
            while True:
                stream = sd.get_stream()
                if stream is None or not getattr(stream, "active", False):
                    break
                try:
                    frame_bytes = frame_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                frame = np.frombuffer(frame_bytes, dtype=np.int16)
                if frame.size == 0:
                    continue
                if input_gain != 1.0:
                    frame = np.clip(frame.astype(np.float32) * input_gain, -32768, 32767).astype(np.int16)
                pre_roll.append(frame.copy())
                prediction = wake_model.predict(frame)
                score = float(prediction.get(wake_key, 0.0))
                if reply_audio_stop_requested(reply_audio_stop_path):
                    interrupted = True
                    if logger is not None:
                        logger.info("reply_playback_interrupt cause=stop_request elapsed_ms=%.1f", (time.monotonic() - playback_started_at) * 1000.0)
                    sd.stop()
                    break
                if score >= wake_threshold and (time.monotonic() - playback_started_at) >= max(0.0, interrupt_grace_seconds):
                    interrupted = True
                    if logger is not None:
                        logger.info(
                            "reply_playback_interrupt cause=wake_score score=%.3f threshold=%.3f elapsed_ms=%.1f grace_ms=%.1f",
                            score,
                            wake_threshold,
                            (time.monotonic() - playback_started_at) * 1000.0,
                            max(0.0, interrupt_grace_seconds) * 1000.0,
                        )
                    sd.stop()
                    break
            try:
                sd.wait()
            finally:
                _release_output_stream()

        _with_local_output_gate(_play_interruptible_reply)
    finally:
        write_reply_audio_state(
            reply_audio_state_path,
            playing=False,
            kind="tts",
            session_id=reply_audio_session_id,
            correlation_id=reply_audio_correlation_id,
        )
        clear_reply_audio_stop_request(reply_audio_stop_path)
    return interrupted
