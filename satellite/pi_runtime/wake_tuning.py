from __future__ import annotations

from dataclasses import dataclass


WAKE_MODE_IDLE = "idle"
WAKE_MODE_PLAYBACK = "playback"


@dataclass(frozen=True)
class WakeTuningProfile:
    mode: str
    wake_threshold: float
    wake_log_threshold: float
    required_consecutive_frames: int


def select_wake_mode(*, playback_active: bool) -> str:
    return WAKE_MODE_PLAYBACK if playback_active else WAKE_MODE_IDLE


def resolve_effective_playback_active(
    *,
    raw_playback_active: bool,
    previous_effective_playback_active: bool,
    previous_hold_until: float,
    now: float,
    hold_seconds: float,
) -> tuple[bool, float]:
    if raw_playback_active:
        return True, now + max(0.0, hold_seconds)
    if previous_effective_playback_active and now < previous_hold_until:
        return True, previous_hold_until
    return False, 0.0


def get_wake_profile(args, *, playback_active: bool) -> WakeTuningProfile:
    mode = select_wake_mode(playback_active=playback_active)
    if mode == WAKE_MODE_PLAYBACK:
        wake_threshold = float(args.wake_playback_threshold)
        wake_log_threshold = float(args.wake_playback_log_threshold)
        required_consecutive_frames = max(1, int(args.wake_playback_consecutive_frames))
    else:
        wake_threshold = float(args.wake_threshold)
        wake_log_threshold = float(args.wake_log_threshold)
        required_consecutive_frames = 1
    return WakeTuningProfile(
        mode=mode,
        wake_threshold=wake_threshold,
        wake_log_threshold=wake_log_threshold,
        required_consecutive_frames=required_consecutive_frames,
    )


def classify_duck_stage(score: float, *, trigger_threshold: float) -> int | None:
    if score >= float(trigger_threshold):
        return 3
    return None
