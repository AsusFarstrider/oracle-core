from __future__ import annotations

from .base import CapabilityDecision, RouteCapability


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: list[RouteCapability] = []

    def register(self, capability: RouteCapability) -> None:
        self._capabilities.append(capability)
        self._capabilities.sort(key=lambda item: item.priority, reverse=True)

    def evaluate(
        self,
        normalized_text: str,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> CapabilityDecision:
        for capability in self._capabilities:
            decision = capability.evaluate(
                normalized_text,
                source=source,
                session_id=session_id,
            )
            if decision is not None:
                return decision

        return CapabilityDecision(
            target="fallback_router",
            confidence=0.64,
            reason="No deterministic capability matched",
            normalized_text=normalized_text,
        )
