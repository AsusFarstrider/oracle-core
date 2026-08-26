from __future__ import annotations

from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.routing_helpers import canonicalize_home_command, detect_implied_home_command, has_home_keyword

from .base import CapabilityDecision


class ImpliedHomeCapability:
    name = "implied_home"
    priority = 80

    def __init__(self, household_settings: HouseholdRuntimeSettings) -> None:
        self.household_settings = household_settings

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        implied = detect_implied_home_command(normalized_text, household_settings=self.household_settings)
        if implied is None:
            return None
        command, reason = implied
        return CapabilityDecision("home_assistant", 0.74, reason, command)


class KeywordHomeCapability:
    name = "keyword_home"
    priority = 60

    def __init__(self, household_settings: HouseholdRuntimeSettings) -> None:
        self.household_settings = household_settings

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        matched, keyword = has_home_keyword(normalized_text)
        if not matched or keyword is None:
            return None
        canonical = canonicalize_home_command(normalized_text, household_settings=self.household_settings)
        return CapabilityDecision("home_assistant", 0.82, f"Matched home automation keyword: {keyword}", canonical)
