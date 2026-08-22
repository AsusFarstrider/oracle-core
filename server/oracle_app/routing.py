from __future__ import annotations

from .capabilities import (
    CapabilityRegistry,
    AlertsCapability,
    AudiobookCapability,
    CalendarCapability,
    FallbackOllamaCapability,
    FactsCapability,
    ForecastQueryCapability,
    HistoricalWeatherCapability,
    ImpliedHomeCapability,
    KeywordHomeCapability,
    MathAndConversionCapability,
    MusicCapability,
    NetworkCapability,
    NewsCapability,
    PendingAudiobookCapability,
    PendingCalendarCapability,
    PendingConfirmationCapability,
    PendingHomeCapability,
    PendingMusicCapability,
    ProbableAudiobookTitleCapability,
    SystemCommandCapability,
    TimeDateQueryCapability,
    WeatherQueryCapability,
)
from .text_normalization import normalize_text
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.information_runtime_settings import NewsRuntimeSettings
from .configuration.calendar_runtime_settings import CalendarRuntimeSettings
from .route_refinement import refine_route
from .schemas import RouteResponse


def build_route_capability_registry(
    household_settings: HouseholdRuntimeSettings | None = None,
    *,
    facts_enabled: bool = False,
    news_settings: NewsRuntimeSettings | None = None,
    canonical_information: bool = False,
    calendar_settings: CalendarRuntimeSettings | None = None,
    canonical_calendar: bool = False,
) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(SystemCommandCapability())
    registry.register(PendingConfirmationCapability())
    registry.register(ImpliedHomeCapability(household_settings))
    registry.register(TimeDateQueryCapability())
    registry.register(MathAndConversionCapability())
    registry.register(AlertsCapability())
    registry.register(AudiobookCapability())
    registry.register(
        CalendarCapability(calendar_settings, canonical_authority=canonical_calendar)
    )
    registry.register(NetworkCapability())
    registry.register(
        NewsCapability(news_settings, canonical_authority=canonical_information)
    )
    registry.register(PendingAudiobookCapability())
    registry.register(
        PendingCalendarCapability(
            news_settings,
            calendar_settings,
            canonical_authority=canonical_information,
        )
    )
    registry.register(PendingMusicCapability())
    registry.register(PendingHomeCapability(household_settings))
    registry.register(ProbableAudiobookTitleCapability())
    registry.register(MusicCapability())
    registry.register(ForecastQueryCapability())
    registry.register(HistoricalWeatherCapability())
    registry.register(WeatherQueryCapability())
    registry.register(KeywordHomeCapability(household_settings))
    registry.register(FactsCapability(facts_enabled))
    registry.register(FallbackOllamaCapability())
    return registry


def choose_route(
    text: str,
    *,
    source: str | None = None,
    session_id: str | None = None,
    registry: CapabilityRegistry,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> RouteResponse:
    normalized = normalize_text(text)
    route = registry.evaluate(
        normalized,
        source=source,
        session_id=session_id,
    ).to_route_response()
    return refine_route(
        route,
        normalized_text=normalized,
        source=source,
        session_id=session_id,
        household_settings=household_settings,
    )
