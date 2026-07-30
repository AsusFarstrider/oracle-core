from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request

from .memory.correlation import get_correlation_id
from .memory.satellite_activity import observe_satellite_activity
from .schemas import SatelliteActivityRequest, SatelliteActivityResponse


logger = logging.getLogger("oracle-brain.api")


def satellite_activity(payload: SatelliteActivityRequest, request: Request) -> SatelliteActivityResponse:
    header_correlation_id = request.headers.get("X-Oracle-Correlation-Id")
    correlation_id = payload.correlation_id or header_correlation_id or get_correlation_id()
    if payload.correlation_id and header_correlation_id and payload.correlation_id != header_correlation_id:
        raise HTTPException(status_code=422, detail="Correlation ID header/body mismatch")
    try:
        observe_satellite_activity(
            source_id=payload.source_id,
            event_type=payload.event_type,
            status=payload.status,
            correlation_id=correlation_id,
            observed_at=payload.observed_at,
            payload=payload.payload,
            snapshot=payload.snapshot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning(
            "satellite_activity_endpoint_write_failed source_id=%s event_type=%s detail=%s",
            payload.source_id,
            payload.event_type or "-",
            exc,
        )
    return SatelliteActivityResponse(accepted=True)


def register_satellite_activity_routes(app: FastAPI) -> None:
    app.post("/api/satellite/activity", response_model=SatelliteActivityResponse)(satellite_activity)
