from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from .schemas import CommandInterimEventsResponse, CommandResponse, PendingAlertsResponse, RouteResponse, SttResponse


RouteHandler = Callable[..., Any]


def register_voice_routes(
    app: FastAPI,
    *,
    route_request: RouteHandler,
    command_request: RouteHandler,
    deferred_resume: RouteHandler,
    ingest_text: RouteHandler,
    session_lookup: RouteHandler,
    pending_alerts: RouteHandler,
    command_events: RouteHandler,
    synthesize_speech: RouteHandler,
    transcribe_audio: RouteHandler,
) -> None:
    """Register temporary Slice 9 compatibility aliases only."""
    app.post("/api/voice/route", response_model=RouteResponse)(route_request)
    app.post("/api/voice/command", response_model=CommandResponse)(command_request)
    app.post("/command", response_model=CommandResponse)(command_request)
    app.post("/api/voice/deferred-resume")(deferred_resume)
    app.post("/api/voice/ingest/text", response_model=CommandResponse)(ingest_text)
    app.get("/api/voice/session")(session_lookup)
    app.get("/api/voice/command-events", response_model=CommandInterimEventsResponse)(command_events)
    app.get("/api/voice/alerts/pending", response_model=PendingAlertsResponse)(pending_alerts)
    app.get("/alerts/pending", response_model=PendingAlertsResponse)(pending_alerts)
    app.post("/api/voice/tts")(synthesize_speech)
    app.post("/tts")(synthesize_speech)
    app.post("/api/voice/stt", response_model=SttResponse)(transcribe_audio)
    app.post("/stt", response_model=SttResponse)(transcribe_audio)
