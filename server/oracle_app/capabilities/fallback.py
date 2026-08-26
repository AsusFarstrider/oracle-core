from __future__ import annotations

from .base import CapabilityDecision


class FallbackOllamaCapability:
    name = "fallback_ollama"
    priority = 0

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision:
        return CapabilityDecision("fallback_router", 0.64, "No deterministic capability matched", normalized_text)
