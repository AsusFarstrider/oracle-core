from __future__ import annotations

from typing import Protocol

from oracle_app.schemas import DispatchPlan


class DispatchHandler(Protocol):
    target: str

    def handle(self, dispatch: DispatchPlan, registry: "DispatchRegistry") -> DispatchPlan:
        ...


class DispatchRegistry(Protocol):
    def execute(self, dispatch: DispatchPlan) -> DispatchPlan:
        ...
