from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any, Dict, Optional


_FOREGROUND_KINDS = {
    "reply",
    "ack",
    "followup_cue",
    "timer",
    "alarm",
    "alert",
    "notification",
    "sleep_expiry",
}
_HANDOFF_MODES = {"borrow", "replace"}
_INTERRUPT_POLICIES = {"none", "duck_ok", "pause_or_stronger", "stop_required"}
_RESUME_POLICIES = {"resume_previous", "no_resume", "replace_with_deferred"}


@dataclass
class CommandOutcome:
    transcript: str
    spoken_reply: str
    raw_response: Dict[str, Any]
    status: str = ""
    failure_code: str = ""
    effects: Dict[str, Any] | None = None
    source_id: str = ""
    session_id: str = ""
    trace_id: str = ""


@dataclass(frozen=True)
class WakeArbitrationDecision:
    interaction_id: str
    satellite_id: str
    winner_satellite_id: str
    decision: str
    reason: str
    participants: list[str]
    window_ms: int
    room_id: str | None = None
    profile: str | None = None
    raw_response: Dict[str, Any] | None = None

    @property
    def should_proceed(self) -> bool:
        return self.decision == "proceed"


@dataclass
class CaptureOutcome:
    pcm_bytes: Optional[bytes]
    stop_reason: str
    total_frames: int
    voiced_frames: int
    silence_frames: int
    max_energy: float
    noise_floor: float
    speech_threshold: float
    silence_threshold: float


@dataclass
class InterruptedPlayback:
    kind: str
    resume_action: str
    backend_type: str = ""
    session_id: str = ""
    resume_args: Dict[str, Any] | None = None
    restore_volume_level: int | None = None
    interruption_token: str = ""
    interrupted_by_session_id: str = ""
    superseded_by_session_id: str = ""
    interrupt_action: str = ""
    playback_state: str = ""


@dataclass(frozen=True)
class ForegroundAudioRequest:
    kind: str
    handoff_mode: str
    interrupt_policy: str
    resume_policy: str
    correlation_id: str = ""

    def __post_init__(self) -> None:
        kind = str(self.kind).strip().lower()
        handoff_mode = str(self.handoff_mode).strip().lower()
        interrupt_policy = str(self.interrupt_policy).strip().lower()
        resume_policy = str(self.resume_policy).strip().lower()
        correlation_id = str(self.correlation_id).strip() or uuid.uuid4().hex
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "handoff_mode", handoff_mode)
        object.__setattr__(self, "interrupt_policy", interrupt_policy)
        object.__setattr__(self, "resume_policy", resume_policy)
        object.__setattr__(self, "correlation_id", correlation_id)
        if kind not in _FOREGROUND_KINDS:
            raise ValueError(f"unsupported foreground kind: {self.kind}")
        if handoff_mode not in _HANDOFF_MODES:
            raise ValueError(f"unsupported handoff mode: {self.handoff_mode}")
        if interrupt_policy not in _INTERRUPT_POLICIES:
            raise ValueError(f"unsupported interrupt policy: {self.interrupt_policy}")
        if resume_policy not in _RESUME_POLICIES:
            raise ValueError(f"unsupported resume policy: {self.resume_policy}")
        if handoff_mode == "replace" and resume_policy == "resume_previous":
            raise ValueError("replace handoff cannot resume previous media")
        if handoff_mode == "borrow" and resume_policy == "replace_with_deferred":
            raise ValueError("borrow handoff cannot replace with deferred media")


@dataclass
class ForegroundHandoff:
    foreground_kind: str
    handoff_mode: str
    interrupted_sessions: list[InterruptedPlayback]
    resume_policy: str
    authority_correlation_id: str = ""
    foreground_session_id: str = ""
    deferred_resume: InterruptedPlayback | None = None

    def __post_init__(self) -> None:
        foreground_kind = str(self.foreground_kind).strip().lower()
        handoff_mode = str(self.handoff_mode).strip().lower()
        resume_policy = str(self.resume_policy).strip().lower()
        authority_correlation_id = str(self.authority_correlation_id).strip() or uuid.uuid4().hex
        foreground_session_id = str(self.foreground_session_id).strip()
        object.__setattr__(self, "foreground_kind", foreground_kind)
        object.__setattr__(self, "handoff_mode", handoff_mode)
        object.__setattr__(self, "resume_policy", resume_policy)
        object.__setattr__(self, "authority_correlation_id", authority_correlation_id)
        object.__setattr__(self, "foreground_session_id", foreground_session_id)
        if foreground_kind not in _FOREGROUND_KINDS:
            raise ValueError(f"unsupported foreground kind: {self.foreground_kind}")
        if handoff_mode not in _HANDOFF_MODES:
            raise ValueError(f"unsupported handoff mode: {self.handoff_mode}")
        if resume_policy not in _RESUME_POLICIES:
            raise ValueError(f"unsupported resume policy: {self.resume_policy}")
        if handoff_mode == "replace" and resume_policy == "resume_previous":
            raise ValueError("replace handoff cannot resume previous media")
        if handoff_mode == "borrow" and resume_policy == "replace_with_deferred":
            raise ValueError("borrow handoff cannot replace with deferred media")


@dataclass
class RuntimeState:
    next_wake_time: float = 0.0
    active_session_id: Optional[str] = None
    last_conversation_activity_at: Optional[float] = None
    wake_state: str = "idle"
    wake_arbitration_suppressed_until: float = 0.0
    next_alert_poll_at: float = 0.0
    next_error_tone_at: float = 0.0
    reply_output_handoff_until: float = 0.0
    wake_playback_state_checked_at: float = 0.0
    wake_playback_state_raw_active: bool = False
    wake_playback_mode_active: bool = False
    wake_playback_mode_hold_until: float = 0.0
    wake_above_threshold_frames: int = 0
    wake_last_mode: str = "idle"
