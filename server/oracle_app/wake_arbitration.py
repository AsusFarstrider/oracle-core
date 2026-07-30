from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal


WakeDecisionValue = Literal["proceed", "stand_down"]
WakeDecisionReason = Literal[
    "highest_audio_level",
    "highest_wake_confidence",
    "most_recent",
]

DEFAULT_WAKE_ARBITRATION_WINDOW_MS = 1000
DEFAULT_WAKE_ARBITRATION_SCORING_STRATEGY = "audio_level_confidence_recent"


@dataclass(frozen=True)
class WakeClaim:
    satellite_id: str
    room_id: str | None = None
    profile: str | None = None
    client_timestamp: str | None = None
    wake_confidence: float | None = None
    audio_level: float | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class WakeClaimReceipt:
    claim_id: str
    interaction_id: str
    window_closes_at: datetime


@dataclass(frozen=True)
class WakeParticipant:
    satellite_id: str
    room_id: str | None
    profile: str | None
    client_timestamp: str | None
    received_at: datetime
    wake_confidence: float | None
    audio_level: float | None
    correlation_id: str | None


@dataclass(frozen=True)
class WakeDecision:
    interaction_id: str
    satellite_id: str
    winner_satellite_id: str
    decision: WakeDecisionValue
    reason: WakeDecisionReason
    window_ms: int
    participants: tuple[str, ...]


@dataclass
class _StoredClaim:
    claim_id: str
    claim: WakeClaim
    received_at: datetime


@dataclass
class _InteractionWindow:
    interaction_id: str
    opened_at: datetime
    window_closes_at: datetime
    claims: list[_StoredClaim] = field(default_factory=list)
    decisions: dict[str, WakeDecision] | None = None


class WakeArbitrationService:
    def __init__(
        self,
        *,
        window_ms: int = DEFAULT_WAKE_ARBITRATION_WINDOW_MS,
        scoring_strategy: str = DEFAULT_WAKE_ARBITRATION_SCORING_STRATEGY,
    ) -> None:
        if window_ms < 0:
            raise ValueError("window_ms must be non-negative")
        if scoring_strategy != DEFAULT_WAKE_ARBITRATION_SCORING_STRATEGY:
            raise ValueError(f"Unsupported wake arbitration scoring strategy: {scoring_strategy}")
        self.window_ms = int(window_ms)
        self.scoring_strategy = scoring_strategy
        self._lock = threading.Lock()
        self._windows: list[_InteractionWindow] = []
        self._claim_to_window: dict[str, _InteractionWindow] = {}

    def submit_claim(self, claim: WakeClaim, *, received_at: datetime | None = None) -> WakeClaimReceipt:
        satellite_id = str(claim.satellite_id or "").strip()
        if not satellite_id:
            raise ValueError("satellite_id is required")
        normalized_claim = WakeClaim(
            satellite_id=satellite_id,
            room_id=_clean_optional_text(claim.room_id),
            profile=_clean_optional_text(claim.profile),
            client_timestamp=_clean_optional_text(claim.client_timestamp),
            wake_confidence=_clean_optional_float(claim.wake_confidence),
            audio_level=_clean_optional_float(claim.audio_level),
            correlation_id=_clean_optional_text(claim.correlation_id),
        )
        now = _coerce_utc(received_at or datetime.now(UTC))

        with self._lock:
            window = self._find_open_window(now)
            if window is None:
                window = _InteractionWindow(
                    interaction_id=f"wake_{uuid.uuid4().hex}",
                    opened_at=now,
                    window_closes_at=now + timedelta(milliseconds=self.window_ms),
                )
                self._windows.append(window)

            claim_id = f"claim_{uuid.uuid4().hex}"
            stored_claim = _StoredClaim(claim_id=claim_id, claim=normalized_claim, received_at=now)
            window.claims.append(stored_claim)
            self._claim_to_window[claim_id] = window
            return WakeClaimReceipt(
                claim_id=claim_id,
                interaction_id=window.interaction_id,
                window_closes_at=window.window_closes_at,
            )

    def decision_for_claim(self, claim_id: str, *, now: datetime | None = None) -> WakeDecision | None:
        current_time = _coerce_utc(now or datetime.now(UTC))
        with self._lock:
            window = self._claim_to_window.get(claim_id)
            if window is None:
                raise KeyError(f"Unknown wake claim: {claim_id}")
            if current_time < window.window_closes_at and window.decisions is None:
                return None
            return self._finalize_locked(window)[claim_id]

    def finalize_due(self, *, now: datetime | None = None) -> list[WakeDecision]:
        current_time = _coerce_utc(now or datetime.now(UTC))
        decisions: list[WakeDecision] = []
        with self._lock:
            for window in self._windows:
                if current_time < window.window_closes_at:
                    continue
                decisions.extend(self._finalize_locked(window).values())
        return decisions

    def _find_open_window(self, received_at: datetime) -> _InteractionWindow | None:
        for window in reversed(self._windows):
            if window.decisions is not None:
                continue
            if received_at <= window.window_closes_at:
                return window
        return None

    def _finalize_locked(self, window: _InteractionWindow) -> dict[str, WakeDecision]:
        if window.decisions is not None:
            return window.decisions
        winner, reason = _select_winner(window.claims)
        participant_ids = tuple(stored.claim.satellite_id for stored in window.claims)
        decisions: dict[str, WakeDecision] = {}
        for stored in window.claims:
            decisions[stored.claim_id] = WakeDecision(
                interaction_id=window.interaction_id,
                satellite_id=stored.claim.satellite_id,
                winner_satellite_id=winner.claim.satellite_id,
                decision="proceed" if stored.claim_id == winner.claim_id else "stand_down",
                reason=reason,
                window_ms=self.window_ms,
                participants=participant_ids,
            )
        window.decisions = decisions
        return decisions


def _select_winner(claims: list[_StoredClaim]) -> tuple[_StoredClaim, WakeDecisionReason]:
    claims_with_audio = [claim for claim in claims if claim.claim.audio_level is not None]
    if claims_with_audio:
        return max(claims_with_audio, key=lambda item: (item.claim.audio_level, item.received_at)), "highest_audio_level"

    claims_with_confidence = [claim for claim in claims if claim.claim.wake_confidence is not None]
    if claims_with_confidence:
        return (
            max(claims_with_confidence, key=lambda item: (item.claim.wake_confidence, item.received_at)),
            "highest_wake_confidence",
        )

    return max(claims, key=lambda item: item.received_at), "most_recent"


def _clean_optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_optional_float(value: float | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
