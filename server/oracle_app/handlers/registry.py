from __future__ import annotations

from oracle_app.schemas import DispatchPlan

from .base import DispatchHandler


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, DispatchHandler] = {}

    def register(self, handler: DispatchHandler) -> None:
        self._handlers[handler.target] = handler

    def get(self, target: str) -> DispatchHandler | None:
        return self._handlers.get(target)

    def execute(self, dispatch: DispatchPlan) -> DispatchPlan:
        handler = self.get(dispatch.target)
        if handler is None:
            dispatch.status = "failed"
            dispatch.result = {
                "error": "unknown_dispatch_target",
                "detail": dispatch.target,
            }
            return dispatch
        return handler.handle(dispatch, self)
