from __future__ import annotations

from oracle_app import state
from oracle_app.audiobook import parse_audiobook_intent
from oracle_app.audiobook_runtime.pending import looks_like_pending_audiobook_clarification
from oracle_app.calendar import is_calendar_request
from oracle_app.calendar_write import parse_calendar_write_request
from oracle_app.configuration.calendar_runtime_settings import CalendarRuntimeSettings
from oracle_app.configuration.household_runtime_settings import HouseholdRuntimeSettings
from oracle_app.configuration.information_runtime_settings import NewsRuntimeSettings
from oracle_app.music_runtime.parsing import parse_music_intent
from oracle_app.music_runtime.pending import looks_like_pending_music_clarification
from oracle_app.news import is_news_request
from oracle_app.room_context import canonical_pending_room_reply_name
from oracle_app.routing_helpers import has_home_keyword
from oracle_app.system_intents import classify_system_intent
from oracle_app.session_state import get_pending_state
from oracle_app.weather_intents import classify_weather_intent

from .base import CapabilityDecision


class PendingConfirmationCapability:
    name = "pending_confirmation"
    priority = 99

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if state.load_pending_confirmation(source, session_id) is None:
            return None
        if normalized_text not in {"yes", "yes please", "add it", "do it"}:
            return None
        return CapabilityDecision("system", 0.99, "Matched pending confirmation context", "confirm")


class PendingUtilityCapability:
    name = "pending_utility"
    priority = 98

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        pending = get_pending_state(source, session_id, domain="utilities")
        if pending is None or not normalized_text:
            return None
        return CapabilityDecision("system", 0.98, "Matched pending deterministic utility clarification", normalized_text)


class PendingInformationalCapability:
    name = "pending_informational"
    priority = 97.5

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        pending = get_pending_state(source, session_id, domain="informational")
        if pending is None or not normalized_text:
            return None
        target = str(pending.get("target_domain") or "").strip().lower()
        if target not in {"facts", "weather", "calendar", "news"}:
            return None
        return CapabilityDecision(target, 0.975, "Matched pending informational clarification", normalized_text)


class PendingHomeCapability:
    name = "pending_home"
    priority = 89.5

    def __init__(self, household_settings: HouseholdRuntimeSettings) -> None:
        self.household_settings = household_settings

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if state.load_pending_home_request(source, session_id) is None:
            return None
        if not canonical_pending_room_reply_name(normalized_text, self.household_settings):
            return None
        return CapabilityDecision("home_assistant", 0.9, "Matched pending home clarification context", normalized_text)


class PendingCalendarCapability:
    name = "pending_calendar"
    priority = 89.25

    def __init__(
        self,
        news_runtime_settings: NewsRuntimeSettings | None,
        calendar_runtime_settings: CalendarRuntimeSettings | None,
    ) -> None:
        self.news_runtime_settings = news_runtime_settings
        self.calendar_runtime_settings = calendar_runtime_settings

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if state.load_pending_calendar_write_request(source, session_id) is None or not normalized_text:
            return None
        if classify_system_intent(normalized_text) is not None or classify_weather_intent(normalized_text) is not None:
            return None
        if self.news_runtime_settings is not None and is_news_request(normalized_text, runtime_settings=self.news_runtime_settings):
            return None
        if parse_music_intent(normalized_text) is not None or parse_audiobook_intent(normalized_text) is not None:
            return None
        if has_home_keyword(normalized_text)[0]:
            return None
        timezone_name = None if self.calendar_runtime_settings is None else self.calendar_runtime_settings.timezone
        if is_calendar_request(normalized_text, timezone_name=timezone_name) or parse_calendar_write_request(normalized_text) is not None:
            return None
        return CapabilityDecision("calendar", 0.88, "Matched pending calendar clarification context", normalized_text)


class PendingAudiobookCapability:
    name = "pending_audiobook"
    priority = 88

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        pending = state.load_pending_audiobook_request(source, session_id)
        if pending is None or not normalized_text:
            return None
        if parse_audiobook_intent(normalized_text) is not None or parse_music_intent(normalized_text) is not None:
            return None
        if classify_system_intent(normalized_text) is not None or has_home_keyword(normalized_text)[0]:
            return None
        if not looks_like_pending_audiobook_clarification(normalized_text, pending):
            return None
        return CapabilityDecision("audiobook", 0.9, "Matched pending audiobook clarification context", normalized_text)


class PendingMusicCapability:
    name = "pending_music"
    priority = 89

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        pending = state.load_pending_music_request(source, session_id)
        if pending is None or not normalized_text:
            return None
        if parse_music_intent(normalized_text) is not None or classify_system_intent(normalized_text) is not None:
            return None
        if has_home_keyword(normalized_text)[0] or not looks_like_pending_music_clarification(normalized_text, pending):
            return None
        return CapabilityDecision("music", 0.9, "Matched pending music clarification context", normalized_text)
