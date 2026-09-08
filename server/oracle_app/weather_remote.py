from __future__ import annotations

import re
from dataclasses import dataclass

from .configuration.weather_runtime_settings import RemoteWeatherRuntimeSettings
from .provider_bridges.remote_weather import (
    NominatimNwsRemoteWeatherBridge,
    RemoteWeatherBridgeError,
    RemoteWeatherBridgeLocationError,
)
from .weather_current import build_current_weather_speech_from_details
from .weather_forecast import (
    ForecastOutOfRangeError,
    WEEKDAY_NAMES,
    format_forecast_summary,
    is_practical_forecast_query,
)
from .weather_models import CurrentWeatherQuery


class RemoteWeatherError(RuntimeError):
    error_code = "remote_weather_unavailable"


class RemoteWeatherLocationError(RemoteWeatherError):
    error_code = "remote_weather_location_unresolved"


class RemoteForecastOutOfRangeError(RemoteWeatherError):
    error_code = "remote_forecast_out_of_range"


@dataclass(frozen=True)
class RemoteCurrentWeatherQuery:
    location_text: str
    current_query: CurrentWeatherQuery


@dataclass(frozen=True)
class RemoteForecastQuery:
    location_text: str
    forecast_text: str


_LOCATION_FIRST_GRAMMAR_WORDS = frozenset(
    {
        "a",
        "an",
        "be",
        "is",
        "it",
        "the",
        "to",
        "weather",
        "will",
    }
)


def _is_location_first_grammar_fragment(location_text: str) -> bool:
    """Reject speech disfluencies captured as location-first place names."""

    tokens = re.findall(r"[a-z0-9]+", location_text.lower())
    return bool(tokens) and all(token in _LOCATION_FIRST_GRAMMAR_WORDS for token in tokens)


def _forecast_suffixes() -> list[str]:
    suffixes: list[str] = [
        " tomorrow night",
        " tomorrow morning",
        " tomorrow afternoon",
        " tomorrow",
        " tonight",
        " this weekend",
        " weekend",
        " later",
        " next week",
    ]
    for weekday_name in WEEKDAY_NAMES:
        suffixes.extend(
            [
                f" on this {weekday_name} night",
                f" on this {weekday_name} morning",
                f" on this {weekday_name} afternoon",
                f" on this {weekday_name}",
                f" this {weekday_name} night",
                f" this {weekday_name} morning",
                f" this {weekday_name} afternoon",
                f" this {weekday_name}",
                f" on {weekday_name} night",
                f" on {weekday_name} morning",
                f" on {weekday_name} afternoon",
                f" on {weekday_name}",
                f" {weekday_name} night",
                f" {weekday_name} morning",
                f" {weekday_name} afternoon",
                f" {weekday_name}",
            ]
        )
    return sorted(set(suffixes), key=len, reverse=True)


def _extract_forecast_suffix(text: str) -> str | None:
    normalized = " ".join(str(text).strip().lower().split())
    for suffix in _forecast_suffixes():
        if normalized.endswith(suffix):
            return suffix
    return None


def _strip_location_tail(location_text: str) -> str:
    trimmed = " ".join(location_text.strip().split()).rstrip("?.!,")
    for suffix in (" right now", " currently", " now", " please"):
        if trimmed.endswith(suffix):
            trimmed = trimmed[: -len(suffix)].rstrip(" ,")
    return trimmed


def _parse_remote_location_query(text: str) -> tuple[str, str] | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized or " in " not in normalized:
        return None

    base_text, _, location_text = normalized.rpartition(" in ")
    location_text = _strip_location_tail(location_text)
    if not base_text or not location_text:
        return None
    return base_text, location_text


def _split_location_prefixed_forecast(base_text: str, location_text: str) -> tuple[str, str] | None:
    for suffix in _forecast_suffixes():
        if not location_text.endswith(suffix):
            continue
        location_only = location_text[: -len(suffix)].strip(" ,")
        if not location_only:
            continue
        return location_only, f"{base_text}{suffix}"
    return None


def parse_remote_current_weather_query(text: str) -> RemoteCurrentWeatherQuery | None:
    parts = _parse_remote_location_query(text)
    if parts is None:
        return None
    base_text, location_text = parts
    normalized = " ".join(str(text).strip().lower().split())

    if any(
        token in normalized
        for token in (
            "yesterday",
            "today",
            "tomorrow",
            "tonight",
            "forecast",
            "weekend",
            "next week",
            "later",
            *WEEKDAY_NAMES,
        )
    ):
        return None

    current_query: CurrentWeatherQuery | None = None

    if any(
        phrase in base_text
        for phrase in (
            "weather",
            "what's it like",
            "what is it like",
            "outside temperature",
            "current temperature",
            "what is the temperature",
            "what's the temperature",
            "what is the wind",
            "what are the winds",
            "how windy is it",
            "is it windy",
            "what is the humidity",
            "how humid is it",
            "is it humid",
            "is it muggy",
            "what is the pressure",
            "what's the pressure",
            "what is the barometer",
            "what's the barometer",
            "is it raining",
            "what is the rain rate",
            "how hard is it raining",
            "full current weather",
            "full weather report",
            "full weather",
            "detailed weather",
            "all the weather details",
        )
    ):
        from .weather_current import parse_current_weather_query

        current_query = parse_current_weather_query(base_text)

    if current_query is None:
        return None

    return RemoteCurrentWeatherQuery(
        location_text=location_text,
        current_query=current_query,
    )


def parse_remote_forecast_query(text: str) -> RemoteForecastQuery | None:
    normalized = " ".join(str(text).strip().lower().split())

    alternate_location_first = re.match(
        r"^what (?:will|is|will the|is the)? ?(?P<location>[a-z0-9][a-z0-9 .'-]{1,40}?) weather(?: be)?(?P<suffix> tomorrow| tonight| this weekend| weekend| next week| later|(?: on )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?: night| morning| afternoon)?)$",
        normalized,
    )
    if alternate_location_first is not None:
        location_only = alternate_location_first.group("location").strip(" ,")
        suffix = alternate_location_first.group("suffix").strip()
        if location_only and suffix:
            if _is_location_first_grammar_fragment(location_only):
                return None
            if not suffix.startswith(("tomorrow", "tonight", "this weekend", "weekend", "next week", "later")):
                suffix = suffix.removeprefix("on ").strip()
            return RemoteForecastQuery(
                location_text=location_only,
                forecast_text=f"what is the weather {suffix}",
            )

    parts = _parse_remote_location_query(text)
    if parts is not None:
        base_text, location_text = parts
        forecast_text = base_text
        location_only = location_text

        if not any(
            token in base_text
            for token in ("forecast", "tomorrow", "tonight", "weekend", "next week", "later", *WEEKDAY_NAMES)
        ):
            split = _split_location_prefixed_forecast(base_text, location_text)
            if split is None:
                return None
            location_only, forecast_text = split

        if not any(
            token in forecast_text
            for token in ("forecast", "tomorrow", "tonight", "weekend", "next week", "later", *WEEKDAY_NAMES)
        ):
            return None

        if "weather" not in forecast_text and "forecast" not in forecast_text:
            if not is_practical_forecast_query(base_text):
                return None
            suffix = _extract_forecast_suffix(forecast_text)
            if suffix is None:
                return None

        return RemoteForecastQuery(location_text=location_only, forecast_text=forecast_text)

    if " forecast for " in normalized:
        location_only, _, suffix = normalized.partition(" forecast for ")
        location_only = location_only.strip(" ,")
        suffix = suffix.strip()
        if location_only and suffix:
            return RemoteForecastQuery(
                location_text=location_only,
                forecast_text=f"what is the weather {suffix}",
            )
    if not normalized.startswith("forecast for "):
        return None

    rest = normalized[len("forecast for ") :]
    for suffix in _forecast_suffixes():
        if not rest.endswith(suffix):
            continue
        location_only = rest[: -len(suffix)].strip(" ,")
        if not location_only:
            continue
        return RemoteForecastQuery(
            location_text=location_only,
            forecast_text=f"what is the weather{suffix}",
        )
    return None


def _prefix_location(label: str, speech: str, *, lowercase_first: bool = True) -> str:
    stripped = speech.strip()
    if not stripped:
        return stripped
    if lowercase_first and stripped[0].isupper():
        stripped = stripped[0].lower() + stripped[1:]
    return f"In {label}, {stripped}"


def build_remote_current_weather_response(
    query_text: str,
    *,
    runtime_settings: RemoteWeatherRuntimeSettings,
    remote_bridge: NominatimNwsRemoteWeatherBridge | None = None,
) -> tuple[str, dict]:
    parsed = parse_remote_current_weather_query(query_text)
    if parsed is None:
        raise RemoteWeatherLocationError("I couldn't tell which remote location you meant.")

    if not runtime_settings.enabled or runtime_settings.user_agent is None:
        raise RemoteWeatherError("Remote weather is not configured")
    user_agent = runtime_settings.user_agent
    timeout_seconds = runtime_settings.timeout_seconds
    bridge = remote_bridge or NominatimNwsRemoteWeatherBridge()
    try:
        location = bridge.resolve_location(
            parsed.location_text, user_agent=user_agent, timeout_seconds=timeout_seconds
        )
        details = bridge.fetch_current(
            location, user_agent=user_agent, timeout_seconds=timeout_seconds
        )
    except RemoteWeatherBridgeLocationError as exc:
        raise RemoteWeatherLocationError(exc.detail) from exc
    except RemoteWeatherBridgeError as exc:
        raise RemoteWeatherError(exc.detail) from exc
    speech, query = build_current_weather_speech_from_details(
        query_text.rpartition(" in ")[0],
        details,
        include_forecast_hint=False,
    )
    speech = _prefix_location(details["location"], speech)
    if details.get("freshness") == "stale":
        speech = (
            "I couldn't refresh the remote weather, so this is the latest saved observation. "
            f"{speech}"
        )
    return speech, {
        **details,
        "mode": query.mode,
        "field": query.field,
        "stale": details["freshness_class"] == "stale",
    }


def build_remote_forecast_response(
    query_text: str,
    *,
    runtime_settings: RemoteWeatherRuntimeSettings,
    remote_bridge: NominatimNwsRemoteWeatherBridge | None = None,
) -> tuple[str, dict]:
    parsed = parse_remote_forecast_query(query_text)
    if parsed is None:
        raise RemoteWeatherLocationError("I couldn't tell which remote location you meant.")

    from .weather_forecast import _select_forecast_periods

    if not runtime_settings.enabled or runtime_settings.user_agent is None:
        raise RemoteWeatherError("Remote weather is not configured")
    bridge = remote_bridge or NominatimNwsRemoteWeatherBridge()
    try:
        location = bridge.resolve_location(
            parsed.location_text,
            user_agent=runtime_settings.user_agent,
            timeout_seconds=runtime_settings.timeout_seconds,
        )
    except RemoteWeatherBridgeLocationError as exc:
        raise RemoteWeatherLocationError(exc.detail) from exc
    except RemoteWeatherBridgeError as exc:
        raise RemoteWeatherError(exc.detail) from exc
    try:
        forecast = bridge.fetch_forecast(
            location,
            user_agent=runtime_settings.user_agent,
            timeout_seconds=runtime_settings.timeout_seconds,
        )
    except RemoteWeatherBridgeLocationError as exc:
        raise RemoteWeatherLocationError(exc.detail) from exc
    except RemoteWeatherBridgeError as exc:
        raise RemoteWeatherError(exc.detail) from exc
    periods = list(forecast.get("periods") or [])
    if not periods:
        raise RemoteWeatherError("No forecast periods were returned for that location")

    try:
        speech = format_forecast_summary(parsed.forecast_text, periods)
    except ForecastOutOfRangeError as exc:
        raise RemoteForecastOutOfRangeError(str(exc)) from exc
    location_label = ", ".join(
        part
        for part in (
            str(forecast.get("location") or "").strip() or location.city,
            str(forecast.get("state") or "").strip() or location.state,
        )
        if part
    ) or location.label
    speech = _prefix_location(location_label, speech, lowercase_first=False)
    if forecast.get("freshness") == "stale":
        speech = (
            "I couldn't refresh the remote forecast, so this is the latest saved forecast. "
            f"{speech}"
        )

    selected = _select_forecast_periods(parsed.forecast_text, periods)
    return speech, {
        "location": location_label,
        "requested_location": location.query_text,
        "source_name": "National Weather Service",
        "source_type": "nws_forecast",
        "forecast_url": str(forecast.get("forecast_url") or "").strip(),
        "forecast_hourly_url": str(forecast.get("forecast_hourly_url") or "").strip(),
        "freshness": forecast.get("freshness"),
        "age_seconds": forecast.get("age_seconds"),
        "stale_reason": forecast.get("stale_reason"),
        "refresh_status": forecast.get("refresh_status"),
        "failure_kind": forecast.get("failure_kind"),
        "selected_periods": [
            {
                "name": period.name,
                "start_time": period.start_time.isoformat(),
                "end_time": period.end_time.isoformat(),
                "is_daytime": period.is_daytime,
                "temperature_f": period.temperature_f,
                "short_forecast": period.short_forecast,
                "probability_of_precipitation_pct": period.probability_of_precipitation_pct,
            }
            for period in selected
        ],
    }
