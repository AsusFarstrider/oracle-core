from __future__ import annotations

from dataclasses import dataclass

from .constants import FORECAST_QUERY_PHRASES, WEATHER_QUERY_PHRASES
from .weather_forecast import WEEKDAY_NAMES
from .weather_history import parse_historical_weather_query
from .weather_remote import parse_remote_current_weather_query, parse_remote_forecast_query


@dataclass(frozen=True)
class WeatherIntent:
    action: str
    reason: str
    confidence: float


def detect_current_weather_query(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False

    if any(
        token in normalized
        for token in ("tomorrow", "tonight", "forecast", "weekend", "next week", "later", *WEEKDAY_NAMES)
    ):
        return False

    if normalized in {"weather", "current weather"}:
        return True

    return any(phrase in normalized for phrase in WEATHER_QUERY_PHRASES)


def detect_forecast_weather_query(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False

    if any(phrase in normalized for phrase in FORECAST_QUERY_PHRASES):
        return True

    if "forecast" in normalized:
        return True

    future_tokens = ("tomorrow", "tonight", "weekend", "next week", "later", *WEEKDAY_NAMES)
    return "weather" in normalized and any(token in normalized for token in future_tokens)


def classify_weather_intent(normalized_text: str) -> WeatherIntent | None:
    if parse_historical_weather_query(normalized_text) is not None:
        return WeatherIntent(
            action="weather_history",
            reason="Matched historical weather query",
            confidence=0.9,
        )

    if parse_remote_forecast_query(normalized_text) is not None:
        return WeatherIntent(
            action="remote_weather_forecast",
            reason="Matched remote forecast query",
            confidence=0.9,
        )

    if detect_forecast_weather_query(normalized_text):
        return WeatherIntent(
            action="weather_forecast",
            reason="Matched forecast query",
            confidence=0.9,
        )

    if parse_remote_current_weather_query(normalized_text) is not None:
        return WeatherIntent(
            action="remote_current_weather",
            reason="Matched remote weather query",
            confidence=0.9,
        )

    if detect_current_weather_query(normalized_text):
        return WeatherIntent(
            action="current_weather",
            reason="Matched weather query",
            confidence=0.9,
        )

    return None


def build_weather_hook(action: str) -> str:
    if action == "weather_history":
        return "weather.weather_history"
    if action == "weather_forecast":
        return "weather.weather_forecast"
    if action == "remote_weather_forecast":
        return "weather.remote_weather_forecast"
    if action == "remote_current_weather":
        return "weather.remote_current_weather"
    return "weather.current_weather"
