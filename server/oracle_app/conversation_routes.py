from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request

from .conversation_results import build_conversation_result
from .memory.correlation import get_correlation_id
from .schemas import (
    CommandInterimEventsResponse,
    CommandRequest,
    CommandResponse,
    ConversationResult,
    RouteResponse,
)


RouteHandler = Callable[..., Any]


def register_conversation_routes(
    app: FastAPI,
    *,
    route_request: RouteHandler,
    command_request: RouteHandler,
    session_lookup: RouteHandler,
    command_events: RouteHandler,
) -> None:
    def canonical_command(payload: CommandRequest, request: Request) -> ConversationResult:
        response: CommandResponse = command_request(payload, request)
        return build_conversation_result(
            request=payload,
            response=response,
            trace_id=str(getattr(request.state, "correlation_id", "") or get_correlation_id()),
        )

    app.post("/api/conversation/route", response_model=RouteResponse)(route_request)
    app.post("/api/conversation/command", response_model=ConversationResult)(canonical_command)
    app.get("/api/conversation/session")(session_lookup)
    app.get("/api/conversation/command-events", response_model=CommandInterimEventsResponse)(command_events)
