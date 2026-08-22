from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from .config import build_wake_capture_config
from .models import (
    EVENT_TYPE_ACTIVATION,
    EVENT_TYPE_NEAR_THRESHOLD,
    CaptureEvent,
    NearThresholdPeak,
    PendingClip,
    WakeCaptureConfig,
)
from .ring_buffer import AudioRingBuffer
from .storage import write_pending_clip

SAMPLE_RATE = 16000
FRAME_LENGTH = 1280


def _ms_to_frames(milliseconds: int) -> int:
    samples = int((milliseconds / 1000.0) * SAMPLE_RATE)
    return max(1, int(np.ceil(samples / FRAME_LENGTH)))


def _normalize_frame(frame_bytes: bytes, input_gain: float) -> np.ndarray:
    frame = np.frombuffer(frame_bytes, dtype=np.int16)
    if input_gain != 1.0:
        frame = np.clip(frame.astype(np.float32) * input_gain, -32768, 32767).astype(np.int16)
    return frame


class WakeCaptureCollector:
    def __init__(self, *, config: WakeCaptureConfig, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._ring = AudioRingBuffer(max_frames=_ms_to_frames(config.pre_roll_ms))
        self._post_roll_frames = _ms_to_frames(config.post_roll_ms)
        self._lock = threading.Lock()
        self._pending: list[PendingClip] = []
        self._last_activation_capture_at = 0.0
        self._last_near_capture_at = 0.0
        self._near_peak: Optional[NearThresholdPeak] = None

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def append_frame_bytes(self, frame_bytes: bytes) -> None:
        if not self._config.enabled:
            return
        frame = _normalize_frame(frame_bytes, self._config.input_gain)
        if frame.size == 0:
            return
        completed: list[PendingClip] = []
        with self._lock:
            self._ring.append(frame)
            for clip in self._pending:
                clip.post_frames.append(frame.copy())
                clip.remaining_post_frames -= 1
            still_pending: list[PendingClip] = []
            for clip in self._pending:
                if clip.remaining_post_frames <= 0:
                    completed.append(clip)
                else:
                    still_pending.append(clip)
            self._pending = still_pending
        for clip in completed:
            try:
                write_pending_clip(
                    local_storage_path=self._config.local_storage_path,
                    clip=clip,
                    sample_rate=SAMPLE_RATE,
                )
            except Exception as exc:
                self._logger.warning("Wake capture write failed for %s: %s", clip.event.event_type, exc)

    def record_activation(
        self,
        *,
        score: float,
        playback_active: bool,
        ducking_triggered: bool,
        now: float,
    ) -> None:
        if not (self._config.enabled and self._config.capture_activation):
            return
        with self._lock:
            self._near_peak = None
            if (now - self._last_activation_capture_at) < self._config.event_cooldown_seconds:
                return
            self._last_activation_capture_at = now
            self._pending.append(
                PendingClip(
                    event=CaptureEvent(
                        event_type=EVENT_TYPE_ACTIVATION,
                        timestamp=now,
                        source_id=self._config.source_id,
                        score=score,
                        playback_active=playback_active,
                        ducking_triggered=ducking_triggered,
                    ),
                    pre_frames=self._ring.snapshot(),
                    remaining_post_frames=self._post_roll_frames,
                )
            )

    def observe_score(
        self,
        *,
        score: float,
        active_threshold: float,
        playback_active: bool,
        ducking_triggered: bool,
        now: float,
    ) -> None:
        if not (self._config.enabled and self._config.capture_near_threshold):
            return
        band_threshold = active_threshold * self._config.near_threshold_fraction
        completed_event: Optional[CaptureEvent] = None
        with self._lock:
            if score >= active_threshold:
                self._near_peak = None
                return
            if score >= band_threshold:
                if self._near_peak is None or score >= self._near_peak.score:
                    self._near_peak = NearThresholdPeak(
                        timestamp=now,
                        score=score,
                        playback_active=playback_active,
                        pre_frames=self._ring.snapshot(),
                        ducking_triggered=ducking_triggered,
                    )
                return
            if self._near_peak is None:
                return
            if (now - self._last_near_capture_at) < self._config.event_cooldown_seconds:
                self._near_peak = None
                return
            peak = self._near_peak
            self._near_peak = None
            self._last_near_capture_at = now
            completed_event = CaptureEvent(
                event_type=EVENT_TYPE_NEAR_THRESHOLD,
                timestamp=peak.timestamp,
                source_id=self._config.source_id,
                score=peak.score,
                playback_active=peak.playback_active,
                ducking_triggered=peak.ducking_triggered,
            )
            self._pending.append(
                PendingClip(
                    event=completed_event,
                    pre_frames=peak.pre_frames,
                    remaining_post_frames=self._post_roll_frames,
                )
            )


def build_wake_capture_collector(*, args, logger: logging.Logger) -> Optional[WakeCaptureCollector]:
    config = build_wake_capture_config(args)
    if not config.enabled:
        return None
    return WakeCaptureCollector(config=config, logger=logger)
