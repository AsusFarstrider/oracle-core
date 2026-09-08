from __future__ import annotations

from oracle_app.calendar import is_calendar_request
from oracle_app.calendar_context import is_calendar_context_followup
from oracle_app.configuration.calendar_runtime_settings import CalendarRuntimeSettings
from oracle_app.configuration.information_runtime_settings import NewsRuntimeSettings
from oracle_app.network import is_network_request
from oracle_app.news import is_news_request
from oracle_app.news_context import is_news_context_followup
from oracle_app.weather_intents import classify_weather_intent

from .base import CapabilityDecision


class CalendarCapability:
    name = "calendar"
    priority = 77

    def __init__(self, runtime_settings: CalendarRuntimeSettings | None) -> None:
        self.runtime_settings = runtime_settings

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if self.runtime_settings is None or not self.runtime_settings.enabled:
            return None
        person_id = self.runtime_settings.person_id_for_query(normalized_text, source_id=source)
        calendar_id = self.runtime_settings.calendar_id_for_query(normalized_text)
        if not is_calendar_request(
            normalized_text,
            timezone_name=self.runtime_settings.timezone,
            person_id=person_id,
            calendar_id=calendar_id,
        ) and not is_calendar_context_followup(
            normalized_text, source=source, session_id=session_id
        ):
            return None
        return CapabilityDecision("calendar", 0.9, "Matched calendar request", normalized_text)


class WeatherQueryCapability:
    name = "weather_query"
    priority = 70

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        intent = classify_weather_intent(normalized_text)
        if intent is None or intent.action not in {
            "current_weather", "remote_current_weather", "weather_solar", "weather_alerts"
        }:
            return None
        return CapabilityDecision("weather", intent.confidence, intent.reason, normalized_text)


class ForecastQueryCapability:
    name = "forecast_query"
    priority = 72

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        intent = classify_weather_intent(normalized_text)
        if intent is None or intent.action not in {"weather_forecast", "remote_weather_forecast"}:
            return None
        return CapabilityDecision("weather", intent.confidence, intent.reason, normalized_text)


class HistoricalWeatherCapability:
    name = "historical_weather"
    priority = 71

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        intent = classify_weather_intent(normalized_text)
        if intent is None or intent.action != "weather_history":
            return None
        return CapabilityDecision("weather", intent.confidence, intent.reason, normalized_text)


class NetworkCapability:
    name = "network"
    priority = 69

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if not is_network_request(normalized_text):
            return None
        return CapabilityDecision("network", 0.88, "Matched network health query", normalized_text)


class NewsCapability:
    name = "news"
    priority = 69

    def __init__(self, runtime_settings: NewsRuntimeSettings | None) -> None:
        self.runtime_settings = runtime_settings

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if self.runtime_settings is None or not self.runtime_settings.enabled:
            return None
        if not is_news_request(
            normalized_text, runtime_settings=self.runtime_settings
        ) and not is_news_context_followup(
            normalized_text, source=source, session_id=session_id
        ):
            return None
        return CapabilityDecision("news", 0.9, "Matched news request", normalized_text)


class FactsCapability:
    name = "facts"
    priority = 10
    _QUESTION_PREFIXES = (
        "what is ", "what are ", "where is ", "where are ", "where was ",
        "where were ", "when is ", "when was ", "when were ", "when did ",
        "who is ", "who was ", "who wrote ", "who invented ", "how does ",
        "how do ", "how long ", "how old ", "where did ", "what source ",
        "what was your source", "tell me about ", "explain ",
    )

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def evaluate(self, normalized_text: str, *, source: str | None = None, session_id: str | None = None) -> CapabilityDecision | None:
        if not self.enabled or not normalized_text.startswith(self._QUESTION_PREFIXES):
            return None
        return CapabilityDecision("facts", 0.72, "Matched factual lookup request", normalized_text)
