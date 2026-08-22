from __future__ import annotations

from .inference import InferenceClient
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .audiobook_runtime.canonical import CanonicalAudiobookExecution
from .music_runtime.canonical import CanonicalMusicExecution
from .information_runtime import CanonicalFactsExecution, CanonicalNewsExecution
from .calendar_runtime import CanonicalCalendarExecution
from .weather_runtime import CanonicalWeatherExecution
from .network_runtime import CanonicalNetworkExecution
from .handlers import (
    AudiobookHandler,
    CalendarHandler,
    FactsHandler,
    FallbackRouterHandler,
    HandlerRegistry,
    HomeAssistantHandler,
    MusicHandler,
    NetworkHandler,
    NewsHandler,
    SystemHandler,
    WeatherHandler,
)
from .schemas import CommandRequest, DispatchPlan, RouteResponse
from .dispatch_dtos import target_outcome, validate_target_payload
from .system_intents import build_system_hook, classify_system_intent, system_action_requires_text
from .weather_intents import build_weather_hook, classify_weather_intent


def build_dispatch_plan(
    payload: CommandRequest,
    route: RouteResponse,
    *,
    original_text: str | None = None,
) -> DispatchPlan:
    hook, upstream_payload = _plan_dispatch_target(payload, route, original_text=original_text)

    return DispatchPlan(
        target=route.target,
        hook=hook,
        payload=upstream_payload,
        status="pending_integration",
    )


def _plan_dispatch_target(
    payload: CommandRequest,
    route: RouteResponse,
    *,
    original_text: str | None = None,
) -> tuple[str, dict[str, str | None]]:
    if route.target == "audiobook":
        return _plan_text_target(payload, route, target="audiobook")
    if route.target == "facts":
        return _plan_facts_target(payload, route, original_text=original_text)
    if route.target == "fallback_router":
        return _plan_fallback_router_target(payload, route)
    if route.target == "home_assistant":
        return _plan_home_assistant_target(payload, route)
    if route.target == "music":
        return _plan_text_target(payload, route, target="music")
    if route.target == "network":
        return _plan_text_target(payload, route, target="network")
    if route.target == "calendar":
        return _plan_text_target(payload, route, target="calendar")
    if route.target == "news":
        return _plan_text_target(payload, route, target="news")
    if route.target == "weather":
        return _plan_weather_target(payload, route)
    if route.target == "system":
        return _plan_system_target(payload, route)
    return _plan_fallback_router_target(payload, route)


def _plan_text_target(
    payload: CommandRequest,
    route: RouteResponse,
    *,
    target: str,
) -> tuple[str, dict[str, str | None]]:
    upstream_payload: dict[str, str | None] = {
        "text": payload.text,
        "normalized_text": route.normalized_text,
        "source": payload.source,
        "session_id": payload.session_id,
    }
    if target in {"music", "audiobook"}:
        upstream_payload["playback_target_source_id"] = payload.playback_target_source_id
    return (
        f"{target}.execute",
        upstream_payload,
    )


def _plan_home_assistant_target(
    payload: CommandRequest,
    route: RouteResponse,
) -> tuple[str, dict[str, str | None]]:
    return (
        "home_assistant.execute",
        {
            "text": route.normalized_text,
            "source": payload.source,
            "session_id": payload.session_id,
        },
    )


def _plan_system_target(
    payload: CommandRequest,
    route: RouteResponse,
) -> tuple[str, dict[str, str | None]]:
    intent = classify_system_intent(route.normalized_text)
    if intent is None:
        action = "unknown_system_operation"
        hook = "system.unknown_operation"
    else:
        action = intent.action
        hook = build_system_hook(intent.action)
    upstream_payload: dict[str, str | None] = {
        "action": action,
        "source": payload.source,
        "session_id": payload.session_id,
        "alert_delivery_target_source_id": payload.alert_delivery_target_source_id,
    }
    if system_action_requires_text(action):
        upstream_payload["text"] = route.normalized_text
    return hook, upstream_payload


def _plan_weather_target(
    payload: CommandRequest,
    route: RouteResponse,
) -> tuple[str, dict[str, str | None]]:
    intent = classify_weather_intent(route.normalized_text)
    action = "current_weather"
    if intent is not None and intent.action in {
        "weather_forecast",
        "weather_history",
        "remote_current_weather",
        "remote_weather_forecast",
    }:
        action = intent.action

    upstream_payload: dict[str, str | None] = {
        "action": action,
        "source": payload.source,
        "session_id": payload.session_id,
        "text": route.normalized_text,
    }
    return build_weather_hook(action), upstream_payload


def _plan_fallback_router_target(
    payload: CommandRequest,
    route: RouteResponse,
) -> tuple[str, dict[str, str | None]]:
    return (
        "fallback_router.decide",
        {
            "prompt": route.normalized_text,
            "source": payload.source,
            "session_id": payload.session_id,
        },
    )


def _plan_facts_target(
    payload: CommandRequest,
    route: RouteResponse,
    *,
    original_text: str | None = None,
) -> tuple[str, dict[str, str | None]]:
    return (
        "facts.lookup",
        {
            "query": original_text if original_text is not None else payload.text,
            "normalized_text": route.normalized_text,
            "source": payload.source,
            "session_id": payload.session_id,
        },
    )


def build_dispatch_registry(
    *,
    inference_client: InferenceClient | None = None,
    household_settings: HouseholdRuntimeSettings | None = None,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
    canonical_configuration: bool = False,
    canonical_media_targets: bool = False,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    music_execution: CanonicalMusicExecution | None = None,
    facts_execution: CanonicalFactsExecution | None = None,
    news_execution: CanonicalNewsExecution | None = None,
    calendar_execution: CanonicalCalendarExecution | None = None,
    weather_execution: CanonicalWeatherExecution | None = None,
    network_execution: CanonicalNetworkExecution | None = None,
) -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register(
        AudiobookHandler(
            household_settings,
            canonical_playback_target=canonical_media_targets,
            canonical_execution=audiobook_execution,
            canonical_authority=canonical_configuration,
        )
    )
    registry.register(
        FactsHandler(
            facts_execution,
            inference=inference_client,
            canonical_authority=canonical_configuration,
        )
    )
    registry.register(
        SystemHandler(
            household_settings,
            calendar_execution,
            home_assistant_settings,
        )
    )
    registry.register(FallbackRouterHandler(inference_client))
    registry.register(
        HomeAssistantHandler(
            household_settings,
            home_assistant_settings,
            canonical_authority=canonical_configuration,
        )
    )
    registry.register(
        CalendarHandler(calendar_execution, canonical_authority=canonical_configuration)
    )
    registry.register(
        MusicHandler(
            canonical_playback_target=canonical_media_targets,
            canonical_execution=music_execution,
            audiobook_execution=audiobook_execution,
            inference=inference_client,
            canonical_authority=canonical_configuration,
        )
    )
    registry.register(
        NetworkHandler(network_execution, canonical_authority=canonical_configuration)
    )
    registry.register(NewsHandler(news_execution, canonical_authority=canonical_configuration))
    registry.register(
        WeatherHandler(weather_execution, canonical_authority=canonical_configuration)
    )
    return registry


def execute_dispatch(
    dispatch: DispatchPlan,
    *,
    registry: HandlerRegistry,
) -> DispatchPlan:
    validate_target_payload(dispatch)
    executed = registry.execute(dispatch)
    target_outcome(executed)
    return executed
