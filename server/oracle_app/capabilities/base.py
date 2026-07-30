from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from oracle_app.schemas import RouteResponse


@dataclass(frozen=True)
class CapabilityDecision:
    target: str
    confidence: float
    reason: str
    normalized_text: str

    def to_route_response(self) -> RouteResponse:
        return RouteResponse(
            target=self.target,
            confidence=self.confidence,
            reason=self.reason,
            normalized_text=self.normalized_text,
        )


class RouteCapability(Protocol):
    name: str
    priority: int

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision | None:
        ...
