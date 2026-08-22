from __future__ import annotations

from .dispatch import build_dispatch_plan, execute_dispatch
from .handlers.registry import HandlerRegistry
from .schemas import CommandRequest, CommandResponse, RouteResponse


IGNORED_TRANSCRIPT_REASON = "Ignored empty transcript after wake-word cleanup"


def build_ignored_command_response(
    payload: CommandRequest,
    *,
    registry: HandlerRegistry,
) -> CommandResponse:
    route = RouteResponse(
        target="system",
        confidence=1.0,
        reason=IGNORED_TRANSCRIPT_REASON,
        normalized_text="",
    )
    dispatch = build_dispatch_plan(payload, route)
    dispatch = execute_dispatch(dispatch, registry=registry)
    return CommandResponse(
        route=route,
        dispatch=dispatch,
        reply_text="",
        session_id=payload.session_id,
        effective_session_id=payload.session_id,
    )
