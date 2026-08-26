from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .conversation_results import decode_deferred_satellite_playback
from .schemas import DeferredSatelliteResumeRequest, VoiceDeferredResumeRequest
from .satellite_authentication import authenticate_satellite_source


RouteHandler = Callable[..., Any]


def register_satellite_playback_routes(
    app: FastAPI,
    *,
    deferred_resume: RouteHandler,
) -> None:
    def resume(payload: DeferredSatelliteResumeRequest, request: Request) -> dict[str, object]:
        _composition, source_id = authenticate_satellite_source(
            request,
            claimed_source_id=payload.source,
        )
        try:
            deferred_session = decode_deferred_satellite_playback(payload.continuation_token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return deferred_resume(VoiceDeferredResumeRequest(
            source=source_id,
            deferred_session=deferred_session,
        ))

    app.post("/api/satellite/deferred-resume")(resume)
