from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException

from .conversation_results import decode_deferred_satellite_playback
from .schemas import DeferredSatelliteResumeRequest, VoiceDeferredResumeRequest


RouteHandler = Callable[..., Any]


def register_satellite_playback_routes(
    app: FastAPI,
    *,
    deferred_resume: RouteHandler,
) -> None:
    def resume(payload: DeferredSatelliteResumeRequest) -> dict[str, object]:
        try:
            deferred_session = decode_deferred_satellite_playback(payload.continuation_token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return deferred_resume(VoiceDeferredResumeRequest(
            source=payload.source,
            deferred_session=deferred_session,
        ))

    app.post("/api/satellite/deferred-resume")(resume)
