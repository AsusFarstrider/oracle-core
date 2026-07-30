from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .config import get_source_registry, get_wake_arbitration_settings
from .memory.correlation import get_correlation_id
from .schemas import WakeClaimRequest, WakeClaimResponse
from .wake_arbitration import WakeArbitrationService, WakeClaim


logger = logging.getLogger("oracle-brain.api")

_SERVICE_LOCK = Lock()
_SERVICE: WakeArbitrationService | None = None
_SERVICE_KEY: tuple[int, str] | None = None


def get_wake_arbitration_service(settings: dict[str, Any] | None = None) -> WakeArbitrationService:
    global _SERVICE, _SERVICE_KEY
    effective_settings = settings or get_wake_arbitration_settings()
    window_ms = int(effective_settings.get("window_ms", 500))
    scoring_strategy = str(effective_settings.get("scoring_strategy") or "audio_level_confidence_recent")
    key = (window_ms, scoring_strategy)
    with _SERVICE_LOCK:
        if _SERVICE is None or _SERVICE_KEY != key:
            _SERVICE = WakeArbitrationService(window_ms=window_ms, scoring_strategy=scoring_strategy)
            _SERVICE_KEY = key
        return _SERVICE


def wake_claim(payload: WakeClaimRequest, request: Request) -> WakeClaimResponse:
    header_correlation_id = request.headers.get("X-Oracle-Correlation-Id")
    correlation_id = payload.correlation_id or header_correlation_id or get_correlation_id()
    if payload.correlation_id and header_correlation_id and payload.correlation_id != header_correlation_id:
        raise HTTPException(status_code=422, detail="Correlation ID header/body mismatch")

    settings = get_wake_arbitration_settings()
    service = get_wake_arbitration_service(settings)
    room_id, profile = _resolve_claim_metadata(payload)
    receipt = service.submit_claim(
        WakeClaim(
            satellite_id=payload.satellite_id,
            room_id=room_id,
            profile=profile,
            client_timestamp=payload.timestamp,
            wake_confidence=payload.wake_confidence,
            audio_level=payload.audio_level,
            correlation_id=correlation_id,
        ),
        received_at=datetime.now(UTC),
    )
    decision = service.decision_for_claim(receipt.claim_id)
    while decision is None:
        sleep_seconds = max(0.0, (receipt.window_closes_at - datetime.now(UTC)).total_seconds())
        if sleep_seconds > 0:
            time.sleep(min(sleep_seconds, 0.05))
        decision = service.decision_for_claim(receipt.claim_id)

    logger.info(
        "wake_arbitration_decision satellite_id=%s winner_satellite_id=%s decision=%s reason=%s interaction_id=%s participants=%d",
        decision.satellite_id,
        decision.winner_satellite_id,
        decision.decision,
        decision.reason,
        decision.interaction_id,
        len(decision.participants),
    )
    return WakeClaimResponse(
        interaction_id=decision.interaction_id,
        satellite_id=decision.satellite_id,
        winner_satellite_id=decision.winner_satellite_id,
        decision=decision.decision,
        reason=decision.reason,
        participants=list(decision.participants),
        window_ms=decision.window_ms,
        room_id=room_id,
        profile=profile,
    )


def _resolve_claim_metadata(payload: WakeClaimRequest) -> tuple[str | None, str | None]:
    room_id = _clean_optional_text(payload.room_id)
    profile = _clean_optional_text(payload.profile)
    if room_id and profile:
        return room_id, profile

    source_entry = get_source_registry().get(str(payload.satellite_id).strip()) or {}
    if not room_id:
        room_id = _clean_optional_text(source_entry.get("default_room"))
    if not profile:
        ui_entry = source_entry.get("ui") if isinstance(source_entry.get("ui"), dict) else {}
        profile = _clean_optional_text(ui_entry.get("profile"))
    return room_id, profile


def _clean_optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def register_wake_arbitration_routes(app: FastAPI) -> None:
    app.post("/api/satellite/wake", response_model=WakeClaimResponse)(wake_claim)
