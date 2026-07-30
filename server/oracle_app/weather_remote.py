from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib import parse, request

from .config import get_forecast_settings
from .configuration.weather_runtime_settings import RemoteWeatherRuntimeSettings
from .provider_bridges.nws_weather_forecast import (
    NwsWeatherForecastBridge,
    WeatherForecastBridgeError,
    get_weather_forecast_bridge,
)
from .weather_current import build_current_weather_speech_from_details
from .weather_forecast import ForecastOutOfRangeError, WEEKDAY_NAMES, format_forecast_summary
from .weather_models import CurrentWeatherQuery, ResolvedRemoteLocation


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


def _looks_like_practical_weather_query(text: str) -> bool:
    normalized = " ".join(str(text).strip().lower().split())
    practical_markers = (
        "need a coat",
        "need an umbrella",
        "bring an umbrella",
        "bring a jacket",
        "wear a coat",
        "wear a jacket",
        "feel like outside",
    )
    return any(marker in normalized for marker in practical_markers)


def _build_json_request(
    url: str,
    *,
    accept: str = "application/json",
    user_agent: str | None = None,
) -> request.Request:
    if user_agent is None:
        user_agent = str(get_forecast_settings()["user_agent"])
    return request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": accept,
        },
    )


def _get_json(
    url: str,
    *,
    accept: str = "application/json",
    user_agent: str | None = None,
    timeout_seconds: int | None = None,
) -> dict | list:
    if timeout_seconds is None:
        timeout_seconds = int(get_forecast_settings()["timeout_seconds"])
    req = _build_json_request(url, accept=accept, user_agent=user_agent)
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


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
            if not _looks_like_practical_weather_query(base_text):
                return None
            suffix = _extract_forecast_suffix(forecast_text)
            if suffix is None:
                return None
            forecast_text = f"what is the weather{suffix}"

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


def _resolve_remote_location(
    location_text: str,
    *,
    user_agent: str | None = None,
    timeout_seconds: int | None = None,
) -> ResolvedRemoteLocation:
    stripped_location = " ".join(location_text.strip().split())
    if stripped_location.isalpha() and len(stripped_location) <= 3 and " " not in stripped_location:
        raise RemoteWeatherLocationError(f"{stripped_location.upper()} is too ambiguous. Please say the city and state.")

    url = (
        "https://nominatim.openstreetmap.org/search?"
        + parse.urlencode(
            {
                "q": location_text,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 3,
            }
        )
    )
    payload = _get_json(url, user_agent=user_agent, timeout_seconds=timeout_seconds)
    if not isinstance(payload, list) or not payload:
        raise RemoteWeatherLocationError(f"I couldn't resolve the location {location_text}.")

    matches = [item for item in payload if isinstance(item, dict)]
    if not matches:
        raise RemoteWeatherLocationError(f"I couldn't resolve the location {location_text}.")

    top = matches[0]
    top_name = str(top.get("name") or "").strip().lower()
    top_importance = float(top.get("importance") or 0.0)
    for candidate in matches[1:]:
        candidate_name = str(candidate.get("name") or "").strip().lower()
        candidate_importance = float(candidate.get("importance") or 0.0)
        if candidate_name == top_name and abs(top_importance - candidate_importance) <= 0.05:
            raise RemoteWeatherLocationError(
                f"{location_text.title()} is ambiguous. Please say the city and state."
            )

    address = top.get("address") or {}
    city = (
        str(address.get("city") or "").strip()
        or str(address.get("town") or "").strip()
        or str(address.get("village") or "").strip()
        or str(top.get("name") or "").strip()
    )
    state = str(address.get("state") or "").strip() or None
    country = str(address.get("country") or "").strip() or None
    label = ", ".join(part for part in (city, state) if part)
    if not label:
        label = str(top.get("display_name") or location_text).strip()

    try:
        latitude = float(top["lat"])
        longitude = float(top["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RemoteWeatherLocationError(f"I couldn't resolve the location {location_text}.") from exc

    return ResolvedRemoteLocation(
        query_text=location_text,
        label=label,
        latitude=latitude,
        longitude=longitude,
        city=city or None,
        state=state,
        country=country,
    )


def _get_point_payload(
    latitude: float,
    longitude: float,
    *,
    user_agent: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    payload = _get_json(
        f"https://api.weather.gov/points/{latitude:.4f},{longitude:.4f}",
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(payload, dict):
        raise RemoteWeatherError("Unexpected point payload")
    return payload


def _fetch_station_observation(
    latitude: float,
    longitude: float,
    *,
    user_agent: str | None = None,
    timeout_seconds: int | None = None,
) -> tuple[dict, dict]:
    point_payload = _get_point_payload(
        latitude,
        longitude,
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
    )
    properties = point_payload.get("properties") or {}
    stations_url = str(properties.get("observationStations") or "").strip()
    if not stations_url:
        raise RemoteWeatherLocationError("That location is outside the current remote weather coverage.")

    stations_payload = _get_json(
        stations_url,
        accept="application/geo+json",
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
    )
    features = stations_payload.get("features") or []
    if not features:
        raise RemoteWeatherError("No observation stations were returned for that location")

    station = features[0]
    station_props = station.get("properties") or {}
    station_id = str(station_props.get("stationIdentifier") or "").strip()
    if not station_id:
        raise RemoteWeatherError("No remote weather station identifier was available")

    observation_payload = _get_json(
        f"https://api.weather.gov/stations/{station_id}/observations/latest",
        accept="application/geo+json",
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(observation_payload, dict):
        raise RemoteWeatherError("Unexpected current observation payload")
    return point_payload, observation_payload


def _fetch_remote_forecast(
    latitude: float,
    longitude: float,
    *,
    runtime_settings: RemoteWeatherRuntimeSettings | None = None,
) -> dict:
    try:
        if runtime_settings is not None:
            if not runtime_settings.enabled or runtime_settings.user_agent is None:
                raise RemoteWeatherError("Remote weather is not configured")
            return NwsWeatherForecastBridge().fetch_typed_forecast_for_coordinates(
                latitude=latitude,
                longitude=longitude,
                user_agent=runtime_settings.user_agent,
                timeout_seconds=runtime_settings.timeout_seconds or 8,
            )
        settings = get_forecast_settings()
        return get_weather_forecast_bridge(settings).fetch_forecast_for_coordinates(
            latitude=latitude,
            longitude=longitude,
            settings=settings,
        )
    except WeatherForecastBridgeError as exc:
        if exc.error_code == "forecast_location_out_of_range":
            raise RemoteWeatherLocationError("That location is outside the current remote forecast coverage.") from exc
        raise RemoteWeatherError(str(exc)) from exc


def _value(properties: dict, key: str) -> float | None:
    item = properties.get(key)
    if not isinstance(item, dict):
        return None
    value = item.get("value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _c_to_f(value_c: float | None) -> float | None:
    if value_c is None:
        return None
    return (float(value_c) * 9.0 / 5.0) + 32.0


def _kmh_to_mph(value_kmh: float | None) -> float | None:
    if value_kmh is None:
        return None
    return float(value_kmh) * 0.621371


def _pa_to_inhg(value_pa: float | None) -> float | None:
    if value_pa is None:
        return None
    return float(value_pa) * 0.000295299830714


def _classify_freshness(age_seconds: float) -> str:
    if age_seconds <= 20 * 60:
        return "fresh"
    if age_seconds <= 60 * 60:
        return "aging"
    return "stale"


def _prefix_location(label: str, speech: str, *, lowercase_first: bool = True) -> str:
    stripped = speech.strip()
    if not stripped:
        return stripped
    if lowercase_first and stripped[0].isupper():
        stripped = stripped[0].lower() + stripped[1:]
    return f"In {label}, {stripped}"


def _build_remote_details(
    *,
    location: ResolvedRemoteLocation,
    point_payload: dict,
    observation_payload: dict,
) -> dict:
    point_props = point_payload.get("properties") or {}
    obs_props = observation_payload.get("properties") or {}
    timestamp = str(obs_props.get("timestamp") or "").strip()
    if not timestamp:
        raise RemoteWeatherError("Remote observation payload missing timestamp")

    generated_at = datetime.fromisoformat(timestamp)
    now = datetime.now(timezone.utc).astimezone(generated_at.tzinfo)
    age_seconds = max(0.0, (now - generated_at).total_seconds())

    temperature_f = _c_to_f(_value(obs_props, "temperature"))
    if temperature_f is None:
        raise RemoteWeatherError("Remote observation payload missing temperature")

    relative_props = ((point_props.get("relativeLocation") or {}).get("properties")) or {}
    canonical_label = ", ".join(
        part
        for part in (
            str(relative_props.get("city") or "").strip() or location.city,
            str(relative_props.get("state") or "").strip() or location.state,
        )
        if part
    ) or location.label

    return {
        "location": canonical_label,
        "requested_location": location.query_text,
        "observation_timestamp": generated_at.isoformat(),
        "age_seconds": age_seconds,
        "freshness_class": _classify_freshness(age_seconds),
        "source_name": "National Weather Service",
        "source_type": "nws_observation",
        "temperature_f": temperature_f,
        "humidity_pct": _value(obs_props, "relativeHumidity"),
        "barometer_inhg": _pa_to_inhg(_value(obs_props, "barometricPressure")),
        "wind_speed_mph": _kmh_to_mph(_value(obs_props, "windSpeed")),
        "wind_gust_mph": _kmh_to_mph(_value(obs_props, "windGust")),
        "wind_direction_deg": _value(obs_props, "windDirection"),
        "rain_rate_in_h": None,
        "station_id": str(obs_props.get("stationId") or "").strip(),
        "station_name": str(obs_props.get("stationName") or "").strip(),
        "text_description": str(obs_props.get("textDescription") or "").strip(),
        "provider_location_label": location.label,
    }


def build_remote_current_weather_response(
    query_text: str,
    *,
    runtime_settings: RemoteWeatherRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> tuple[str, dict]:
    parsed = parse_remote_current_weather_query(query_text)
    if parsed is None:
        raise RemoteWeatherLocationError("I couldn't tell which remote location you meant.")

    if canonical_authority and (
        runtime_settings is None or not runtime_settings.enabled or runtime_settings.user_agent is None
    ):
        raise RemoteWeatherError("Remote weather is not configured")
    user_agent = None if runtime_settings is None else runtime_settings.user_agent
    timeout_seconds = None if runtime_settings is None else runtime_settings.timeout_seconds
    location = _resolve_remote_location(
        parsed.location_text,
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
    )
    point_payload, observation_payload = _fetch_station_observation(
        location.latitude,
        location.longitude,
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
    )
    details = _build_remote_details(
        location=location,
        point_payload=point_payload,
        observation_payload=observation_payload,
    )
    speech, query = build_current_weather_speech_from_details(
        query_text.rpartition(" in ")[0],
        details,
        include_forecast_hint=False,
    )
    speech = _prefix_location(details["location"], speech)
    return speech, {
        **details,
        "mode": query.mode,
        "field": query.field,
        "stale": details["freshness_class"] == "stale",
    }


def build_remote_forecast_response(
    query_text: str,
    *,
    runtime_settings: RemoteWeatherRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> tuple[str, dict]:
    parsed = parse_remote_forecast_query(query_text)
    if parsed is None:
        raise RemoteWeatherLocationError("I couldn't tell which remote location you meant.")

    from .weather_forecast import _select_forecast_periods

    if canonical_authority and (
        runtime_settings is None or not runtime_settings.enabled or runtime_settings.user_agent is None
    ):
        raise RemoteWeatherError("Remote weather is not configured")
    location = _resolve_remote_location(
        parsed.location_text,
        user_agent=None if runtime_settings is None else runtime_settings.user_agent,
        timeout_seconds=None if runtime_settings is None else runtime_settings.timeout_seconds,
    )
    forecast = _fetch_remote_forecast(
        location.latitude,
        location.longitude,
        runtime_settings=runtime_settings,
    )
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

    selected = _select_forecast_periods(parsed.forecast_text, periods)
    return speech, {
        "location": location_label,
        "requested_location": location.query_text,
        "source_name": "National Weather Service",
        "source_type": "nws_forecast",
        "forecast_url": str(forecast.get("forecast_url") or "").strip(),
        "forecast_hourly_url": str(forecast.get("forecast_hourly_url") or "").strip(),
        "selected_periods": [
            {
                "name": period.name,
                "start_time": period.start_time.isoformat(),
                "end_time": period.end_time.isoformat(),
                "is_daytime": period.is_daytime,
                "temperature_f": period.temperature_f,
                "short_forecast": period.short_forecast,
            }
            for period in selected
        ],
    }
