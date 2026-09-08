from __future__ import annotations

from .capabilities.fallback import FallbackOllamaCapability
from .capabilities.household import ImpliedHomeCapability, KeywordHomeCapability
from .capabilities.information import (
    CalendarCapability, FactsCapability, ForecastQueryCapability,
    HistoricalWeatherCapability, NetworkCapability, NewsCapability,
    WeatherQueryCapability,
)
from .capabilities.media import AudiobookCapability, MusicCapability, ProbableAudiobookTitleCapability
from .capabilities.registry import CapabilityRegistry
from .capabilities.session import (
    PendingAudiobookCapability, PendingCalendarCapability,
    PendingConfirmationCapability, PendingHomeCapability, PendingInformationalCapability, PendingMusicCapability,
    PendingUtilityCapability,
)
from .capabilities.system import (
    AlertsCapability, MathAndConversionCapability, SystemCommandCapability,
    TimeDateQueryCapability,
)
from .text_normalization import normalize_text
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.information_runtime_settings import NewsRuntimeSettings
from .configuration.calendar_runtime_settings import CalendarRuntimeSettings
from .route_refinement import refine_route
from .route_refinement import PlaybackRouteState
from .schemas import RouteResponse


DETERMINISTIC_FALLBACK_REENTRY_TARGETS = frozenset(
    {"home_assistant", "calendar", "music", "news", "audiobook", "weather", "system"}
)


def build_route_capability_registry(
    household_settings: HouseholdRuntimeSettings,
    *,
    facts_enabled: bool = False,
    news_settings: NewsRuntimeSettings | None = None,
    calendar_settings: CalendarRuntimeSettings | None = None,
) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register(SystemCommandCapability())
    registry.register(PendingConfirmationCapability())
    registry.register(PendingUtilityCapability())
    registry.register(PendingInformationalCapability())
    registry.register(ImpliedHomeCapability(household_settings))
    registry.register(TimeDateQueryCapability())
    registry.register(MathAndConversionCapability())
    registry.register(AlertsCapability())
    registry.register(AudiobookCapability())
    registry.register(
        CalendarCapability(calendar_settings)
    )
    registry.register(NetworkCapability())
    registry.register(
        NewsCapability(news_settings)
    )
    registry.register(PendingAudiobookCapability())
    registry.register(
        PendingCalendarCapability(
            news_settings,
            calendar_settings,
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
    household_settings: HouseholdRuntimeSettings,
    playback_state: PlaybackRouteState | None = None,
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
        playback_state=playback_state,
    )


def validate_fallback_reentry(
    *,
    proposed_target: str,
    original_text: str,
    normalized_text: str,
    source: str | None,
    session_id: str | None,
    registry: CapabilityRegistry,
    household_settings: HouseholdRuntimeSettings,
    playback_state: PlaybackRouteState | None = None,
) -> RouteResponse | None:
    """Require a fallback proposal to be independently owned by canonical routing."""

    target = str(proposed_target or "").strip().lower()
    if target not in DETERMINISTIC_FALLBACK_REENTRY_TARGETS:
        return None
    if target == "system" and normalize_text(original_text) != normalize_text(normalized_text):
        return None
    route = choose_route(
        normalized_text,
        source=source,
        session_id=session_id,
        registry=registry,
        household_settings=household_settings,
        playback_state=playback_state,
    )
    if route.target != target:
        return None
    return route.model_copy(
        update={
            "reason": f"Fallback proposal validated by canonical {target} owner",
        }
    )
