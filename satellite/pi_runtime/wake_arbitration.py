from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .models import CaptureOutcome, WakeArbitrationDecision
from .oracle_client import report_satellite_activity, submit_wake_claim


WAKE_STATE_IDLE = "idle"
WAKE_STATE_PROVISIONAL_LISTENING = "provisional_listening"
WAKE_STATE_ACTIVE_LISTENING = "active_listening"
WAKE_STATE_DISCARDING = "discarding"
WAKE_STATE_SUPPRESSED = "suppressed"


@dataclass
class ProvisionalCapture:
    thread: threading.Thread
    _result: CaptureOutcome | None = None
    _error: BaseException | None = None

    @classmethod
    def start(cls, capture_func: Callable[[], CaptureOutcome]) -> "ProvisionalCapture":
        capture = cls(thread=threading.Thread(target=lambda: None))

        def _run() -> None:
            try:
                capture._result = capture_func()
            except BaseException as exc:
                capture._error = exc

        capture.thread = threading.Thread(target=_run, name="oracle-provisional-capture", daemon=True)
        capture.thread.start()
        return capture

    def wait(self, timeout: float | None = None) -> CaptureOutcome:
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            raise TimeoutError("provisional capture did not finish before timeout")
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("provisional capture finished without a result")
        return self._result


@dataclass(frozen=True)
class WakeArbitratedCapture:
    capture_outcome: CaptureOutcome | None
    capture_elapsed_ms: float
    decision: WakeArbitrationDecision | None
    proceeded: bool


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_wake_claim_for_decision(
    *,
    args,
    satellite_id: str,
    wake_confidence: float,
    audio_level: float | None,
    correlation_id: str,
    timestamp: str | None = None,
) -> WakeArbitrationDecision:
    return submit_wake_claim(
        args.oracle_url,
        satellite_id=satellite_id,
        timestamp=timestamp or utc_timestamp(),
        wake_confidence=wake_confidence,
        audio_level=audio_level,
        correlation_id=correlation_id,
        credential=getattr(args, "brain_api_key", ""),
        timeout=float(getattr(args, "wake_arbitration_timeout_seconds", 5.0) or 5.0),
    )


def handle_wake_arbitration_loss(
    *,
    args,
    logger,
    runtime_state,
    decision: WakeArbitrationDecision,
    correlation_id: str,
    wake_score: float,
    audio_level: float | None,
    now: float | None = None,
) -> None:
    current_time = time.time() if now is None else now
    loser_suppression_seconds = max(
        0.0,
        float(getattr(args, "wake_arbitration_loser_suppression_ms", 10000) or 10000) / 1000.0,
    )
    runtime_state.wake_state = WAKE_STATE_SUPPRESSED
    runtime_state.wake_arbitration_suppressed_until = current_time + loser_suppression_seconds
    runtime_state.next_wake_time = max(
        float(getattr(runtime_state, "next_wake_time", 0.0) or 0.0),
        runtime_state.wake_arbitration_suppressed_until,
    )
    logger.info(
        "wake_arbitration_lost source=%s winner=%s interaction_id=%s suppression_ms=%d",
        args.source,
        decision.winner_satellite_id,
        decision.interaction_id,
        int(loser_suppression_seconds * 1000),
    )
    report_satellite_activity(
        args.oracle_url,
        source_id=args.source,
        event_type="wake_arbitration_lost",
        status="available",
        correlation_id=correlation_id,
        payload={
            "interaction_id": decision.interaction_id,
            "winner_satellite_id": decision.winner_satellite_id,
            "wake_score": wake_score,
            "audio_level": audio_level,
            "participants": decision.participants,
        },
        timeout=0.05,
        credential=getattr(args, "brain_api_key", ""),
    )


def arbitrate_provisional_capture(
    *,
    args,
    logger,
    runtime_state,
    capture_func: Callable[[], CaptureOutcome],
    satellite_id: str,
    wake_confidence: float,
    audio_level: float | None,
    correlation_id: str,
    on_proceed: Callable[[], None] | None = None,
) -> WakeArbitratedCapture:
    runtime_state.wake_state = WAKE_STATE_PROVISIONAL_LISTENING
    capture_started_at = time.perf_counter()
    provisional_capture = ProvisionalCapture.start(capture_func)

    decision = None
    try:
        decision = submit_wake_claim_for_decision(
            args=args,
            satellite_id=satellite_id,
            wake_confidence=wake_confidence,
            audio_level=audio_level,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.warning(
            "wake_arbitration_claim_failed source=%s correlation_id=%s detail=%s proceeding_fail_open=true",
            satellite_id,
            correlation_id,
            exc,
        )

    if decision is not None and not decision.should_proceed:
        runtime_state.wake_state = WAKE_STATE_DISCARDING
        try:
            provisional_capture.wait()
        except Exception as exc:
            logger.warning(
                "wake_arbitration_discard_capture_failed source=%s correlation_id=%s detail=%s",
                satellite_id,
                correlation_id,
                exc,
            )
        capture_elapsed_ms = (time.perf_counter() - capture_started_at) * 1000.0
        handle_wake_arbitration_loss(
            args=args,
            logger=logger,
            runtime_state=runtime_state,
            decision=decision,
            correlation_id=correlation_id,
            wake_score=wake_confidence,
            audio_level=audio_level,
        )
        return WakeArbitratedCapture(
            capture_outcome=None,
            capture_elapsed_ms=capture_elapsed_ms,
            decision=decision,
            proceeded=False,
        )

    runtime_state.wake_state = WAKE_STATE_ACTIVE_LISTENING
    if on_proceed is not None:
        on_proceed()
    capture_outcome = provisional_capture.wait()
    capture_elapsed_ms = (time.perf_counter() - capture_started_at) * 1000.0
    return WakeArbitratedCapture(
        capture_outcome=capture_outcome,
        capture_elapsed_ms=capture_elapsed_ms,
        decision=decision,
        proceeded=True,
    )
