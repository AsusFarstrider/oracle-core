from __future__ import annotations

from oracle_app.system_intents import classify_system_intent

from .base import CapabilityDecision


class SystemCommandCapability:
    name = "system_commands"
    priority = 100

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        intent = classify_system_intent(normalized_text)
        if intent is None or intent.action not in {
            "confirm_pending", "cancel_pending", "refresh_cache", "switch_user",
            "repeat", "help", "courtesy", "unsupported_utility",
        }:
            return None
        return CapabilityDecision("system", intent.confidence, intent.reason, normalized_text)


class AlertsCapability:
    name = "alerts"
    priority = 76

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        intent = classify_system_intent(normalized_text)
        if intent is None or intent.action != "alerts":
            return None
        return CapabilityDecision("system", intent.confidence, intent.reason, normalized_text)


class TimeDateQueryCapability:
    name = "time_date_query"
    priority = 75

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        intent = classify_system_intent(normalized_text)
        if intent is None or intent.action not in {"current_time", "current_date", "current_time_date", "temporal"}:
            return None
        return CapabilityDecision("system", intent.confidence, intent.reason, normalized_text)


class MathAndConversionCapability:
    name = "math_and_conversion"
    priority = 74

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        intent = classify_system_intent(normalized_text)
        if intent is None or intent.action != "calculation":
            return None
        return CapabilityDecision("system", intent.confidence, intent.reason, normalized_text)
