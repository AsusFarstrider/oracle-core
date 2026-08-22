from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime

from .config import get_weather_current_settings
from .provider_bridges.weewx_weather_station import get_weather_station_bridge
from .read_cache import BoundedReadCache, CachedRead
from .weather_forecast import fetch_weather_forecast
from .weather_models import CurrentWeatherQuery, ForecastPeriod, WeatherObservation


_CURRENT_WEATHER_CACHE: BoundedReadCache[WeatherObservation] = BoundedReadCache()
CURRENT_WEATHER_TTL_SECONDS = 30
CURRENT_WEATHER_STALE_MAX_SECONDS = 15 * 60
_LAST_CURRENT_READ: ContextVar[CachedRead[WeatherObservation] | None] = ContextVar(
    "last_current_weather_read",
    default=None,
)


def parse_current_weather_query(text: str) -> CurrentWeatherQuery:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return CurrentWeatherQuery(mode="summary")

    if any(
        phrase in normalized
        for phrase in (
            "full current weather",
            "full weather report",
            "full weather",
            "detailed weather",
            "all the weather details",
        )
    ):
        return CurrentWeatherQuery(mode="full")

    field_phrases: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "temperature",
            (
                "outside temperature",
                "current temperature",
                "temperature outside",
                "what is the temperature",
                "what's the temperature",
            ),
        ),
        ("wind", ("what is the wind", "what are the winds", "how windy is it", "is it windy", "wind outside")),
        ("humidity", ("what is the humidity", "how humid is it", "is it humid", "is it muggy")),
        ("pressure", ("what is the pressure", "what's the pressure", "what is the barometer", "what's the barometer")),
        ("rain", ("is it raining", "what is the rain rate", "how hard is it raining")),
    )
    for field, phrases in field_phrases:
        if any(phrase in normalized for phrase in phrases):
            return CurrentWeatherQuery(mode="field", field=field)

    return CurrentWeatherQuery(mode="summary")


def _degrees_to_cardinal(degrees: float | None) -> str | None:
    if degrees is None:
        return None
    directions = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")
    index = round((degrees % 360) / 45) % len(directions)
    return directions[index]


def _format_inhg(value: float) -> str:
    rounded = round(value, 2)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _format_inches_per_hour(value: float) -> str:
    rounded = round(value, 2)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _format_mph_value(value: float) -> int:
    if 0.0 < value < 1.0:
        return 1
    return max(0, round(value))


def _format_mph_phrase(value: float) -> str:
    rounded = _format_mph_value(value)
    unit = "mile per hour" if rounded == 1 else "miles per hour"
    return f"{rounded} {unit}"


def _format_current_details(observation: WeatherObservation) -> dict:
    return {
        "location": observation.location,
        "observation_timestamp": observation.generated_at.isoformat(),
        "age_seconds": observation.age_seconds,
        "freshness_class": observation.freshness_class,
        "source_name": observation.source_name,
        "source_type": observation.source_type,
        "temperature_f": observation.temperature_f,
        "dewpoint_f": observation.dewpoint_f,
        "humidity_pct": observation.humidity_pct,
        "heat_index_f": observation.heat_index_f,
        "barometer_inhg": observation.barometer_inhg,
        "wind_speed_mph": observation.wind_speed_mph,
        "wind_gust_mph": observation.wind_gust_mph,
        "wind_chill_f": observation.wind_chill_f,
        "wind_direction_deg": observation.wind_direction_deg,
        "wind_direction_cardinal": _degrees_to_cardinal(observation.wind_direction_deg),
        "rain_rate_in_h": observation.rain_rate_in_h,
        "inside_temperature_f": observation.inside_temperature_f,
        "inside_humidity_pct": observation.inside_humidity_pct,
        "rain_total_in": observation.rain_total_in,
    }


def _temperature_feel_phrase(details: dict) -> str | None:
    temperature = details.get("temperature_f")
    if not isinstance(temperature, (int, float)):
        return None

    value = float(temperature)
    if value <= 25.0:
        return "it is bitterly cold"
    if value <= 40.0:
        return "it is chilly"
    if value >= 88.0:
        return "it is hot"
    if value >= 78.0:
        return "it feels warm"
    return None


def _current_salience(details: dict) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []

    rain_rate = details.get("rain_rate_in_h")
    if isinstance(rain_rate, (int, float)) and rain_rate > 0.0:
        if rain_rate < 0.01:
            items.append((100, "there is drizzle"))
        elif rain_rate < 0.1:
            items.append((104, "there is light rain"))
        elif rain_rate < 0.3:
            items.append((108, "it is raining"))
        else:
            items.append((112, "there is heavy rain"))

    gust = details.get("wind_gust_mph")
    speed = details.get("wind_speed_mph")
    if isinstance(gust, (int, float)) or isinstance(speed, (int, float)):
        gust_value = float(gust or 0.0)
        speed_value = float(speed or 0.0)
        if gust_value >= 25.0 or speed_value >= 18.0:
            items.append((90, "there are gusty winds"))
        elif gust_value >= 18.0 or speed_value >= 12.0:
            items.append((74, "there are gusts and a noticeable breeze"))

    humidity = details.get("humidity_pct")
    temperature = details.get("temperature_f")
    if isinstance(humidity, (int, float)):
        temperature_value = float(temperature) if isinstance(temperature, (int, float)) else None
        if humidity >= 86.0 and temperature_value is not None and temperature_value >= 78.0:
            items.append((72, "it feels muggy"))
        elif humidity >= 78.0 or (
            humidity >= 72.0 and temperature_value is not None and temperature_value >= 65.0
        ):
            items.append((56, "humidity is high"))
        elif humidity <= 25.0:
            items.append((56, "the air is very dry"))

    temperature_phrase = _temperature_feel_phrase(details)
    if temperature_phrase:
        items.append((48, temperature_phrase))

    items.sort(key=lambda item: item[0], reverse=True)
    return items


def _forecast_hint(details: dict, *, forecast_loader=None) -> str | None:
    try:
        forecast = (forecast_loader or fetch_weather_forecast)()
    except Exception:
        return None

    periods: list[ForecastPeriod] = forecast["periods"]
    if not periods:
        return None

    next_period = periods[0]
    short_forecast = next_period.short_forecast.strip().lower()
    if not short_forecast:
        return None
    label = next_period.name.strip().lower() or "later"
    if "storm" in short_forecast:
        return f"Stormy weather is possible {label}."
    if "rain" in short_forecast or "showers" in short_forecast:
        return f"Rain is expected {label}."
    if "wind" in short_forecast:
        return f"It may turn windier {label}."

    current_temp = details.get("temperature_f")
    if isinstance(current_temp, (int, float)) and abs(next_period.temperature_f - float(current_temp)) >= 8.0:
        if next_period.temperature_f > float(current_temp):
            return f"It should warm up {label}."
        return f"The temperature is expected to drop {label}."

    if any(token in short_forecast for token in ("sunny", "clear", "mostly sunny", "mostly clear")):
        return f"It should stay clear {label}."
    forecast_condition = next_period.short_forecast.strip()
    if forecast_condition:
        return f"Expect {forecast_condition.lower()} conditions {label}."
    return None


def fetch_weather_observation() -> WeatherObservation:
    cached = _fetch_weather_observation_cached()
    _LAST_CURRENT_READ.set(cached)
    return cached.value


def _fetch_weather_observation_cached() -> CachedRead[WeatherObservation]:
    settings = get_weather_current_settings()
    cache_key = f"current:{settings.get('provider')}:{settings.get('url')}"
    return _CURRENT_WEATHER_CACHE.read(
        cache_key,
        ttl_seconds=CURRENT_WEATHER_TTL_SECONDS,
        stale_max_seconds=CURRENT_WEATHER_STALE_MAX_SECONDS,
        loader=lambda: get_weather_station_bridge(settings).fetch_current_observation(settings=settings),
    )


def _build_current_summary(details: dict) -> str:
    base = f"It is currently {round(float(details['temperature_f']))} degrees."
    extras = [text for _, text in _current_salience(details)[:2]]

    if not extras:
        return base
    if len(extras) == 1:
        return f"{base[:-1]}, and {extras[0]}."
    if extras[0].startswith(("there is", "there are", "it is", "the air")):
        return f"{base[:-1]}, {extras[0]}, and {extras[1]}."
    return f"{base[:-1]}, with {extras[0]}, and {extras[1]}."


def format_weather_summary(observation: WeatherObservation) -> str:
    return _build_current_summary(_format_current_details(observation))


def _format_weather_field_response(field: str, details: dict) -> str:
    if field == "temperature":
        feel_phrase = _temperature_feel_phrase(details)
        if feel_phrase:
            return f"It is currently {round(float(details['temperature_f']))} degrees, and {feel_phrase}."
        return f"It is currently {round(float(details['temperature_f']))} degrees."

    if field == "wind":
        speed = details.get("wind_speed_mph")
        gust = details.get("wind_gust_mph")
        direction = details.get("wind_direction_cardinal")
        if not isinstance(speed, (int, float)) and not isinstance(gust, (int, float)):
            return "I don't have wind information from the current weather feed."
        phrases: list[str] = []
        if isinstance(speed, (int, float)):
            if direction:
                phrases.append(f"wind is out of the {direction} at {_format_mph_phrase(float(speed))}")
            else:
                phrases.append(f"wind is {_format_mph_phrase(float(speed))}")
        if isinstance(gust, (int, float)) and float(gust) > max(float(speed or 0.0), 0.0):
            phrases.append(f"gusts are up to {_format_mph_phrase(float(gust))}")
        return f"Currently, {' and '.join(phrases)}."

    if field == "humidity":
        humidity = details.get("humidity_pct")
        if not isinstance(humidity, (int, float)):
            return "I don't have humidity information from the current weather feed."
        return f"Humidity is {round(float(humidity))} percent."

    if field == "pressure":
        barometer = details.get("barometer_inhg")
        if not isinstance(barometer, (int, float)):
            return "I don't have pressure information from the current weather feed."
        return f"The barometer is {_format_inhg(float(barometer))} inches of mercury."

    if field == "rain":
        rain_rate = details.get("rain_rate_in_h")
        if not isinstance(rain_rate, (int, float)):
            return "I don't have rain information from the current weather feed."
        if float(rain_rate) <= 0.0:
            return "It is not raining right now."
        return f"Rain is falling at {_format_inches_per_hour(float(rain_rate))} inches per hour."

    return format_weather_summary(
        WeatherObservation(
            location=str(details["location"]),
            generated_at=datetime.fromisoformat(str(details["observation_timestamp"])),
            age_seconds=float(details["age_seconds"]),
            temperature_f=float(details["temperature_f"]),
            dewpoint_f=details.get("dewpoint_f"),
            humidity_pct=details.get("humidity_pct"),
            heat_index_f=details.get("heat_index_f"),
            wind_speed_mph=details.get("wind_speed_mph"),
            wind_gust_mph=details.get("wind_gust_mph"),
            wind_chill_f=details.get("wind_chill_f"),
            rain_rate_in_h=details.get("rain_rate_in_h"),
            barometer_inhg=details.get("barometer_inhg"),
            inside_temperature_f=details.get("inside_temperature_f"),
            inside_humidity_pct=details.get("inside_humidity_pct"),
            rain_total_in=details.get("rain_total_in"),
            wind_direction_deg=details.get("wind_direction_deg"),
            source_name=str(details["source_name"]),
            source_type=str(details["source_type"]),
            freshness_class=str(details["freshness_class"]),
        )
    )


def _format_full_weather_response(details: dict) -> str:
    parts = [f"Current weather for {details['location']}: it is {round(float(details['temperature_f']))} degrees."]

    humidity = details.get("humidity_pct")
    if isinstance(humidity, (int, float)):
        parts.append(f"Humidity is {round(float(humidity))} percent.")

    pressure = details.get("barometer_inhg")
    if isinstance(pressure, (int, float)):
        parts.append(f"The barometer is {_format_inhg(float(pressure))} inches of mercury.")

    speed = details.get("wind_speed_mph")
    gust = details.get("wind_gust_mph")
    direction = details.get("wind_direction_cardinal")
    if isinstance(speed, (int, float)):
        wind_text = f"Wind is {direction + ' ' if direction else ''}{_format_mph_phrase(float(speed))}".strip()
        if isinstance(gust, (int, float)) and float(gust) > max(float(speed or 0.0), 0.0):
            wind_text += f", with gusts up to {_format_mph_phrase(float(gust))}"
        parts.append(f"{wind_text}.")

    rain_rate = details.get("rain_rate_in_h")
    if isinstance(rain_rate, (int, float)):
        if float(rain_rate) <= 0.0:
            parts.append("There is no rain right now.")
        else:
            parts.append(f"Rain is falling at {_format_inches_per_hour(float(rain_rate))} inches per hour.")

    return " ".join(parts)


def build_current_weather_speech_from_details(
    query_text: str,
    details: dict,
    *,
    include_forecast_hint: bool = True,
    forecast_loader=None,
) -> tuple[str, CurrentWeatherQuery]:
    query = parse_current_weather_query(query_text)

    if query.mode == "full":
        speech = _format_full_weather_response(details)
    elif query.mode == "field" and query.field is not None:
        speech = _format_weather_field_response(query.field, details)
    else:
        speech = _build_current_summary(details)
        if include_forecast_hint:
            hint = _forecast_hint(details, forecast_loader=forecast_loader)
            if hint is not None:
                speech = f"{speech} {hint}"

    freshness_class = str(details.get("freshness_class") or "")
    age_seconds = details.get("age_seconds")
    if freshness_class in {"aging", "stale"} and isinstance(age_seconds, (int, float)):
        speech = f"The latest weather update is about {round(float(age_seconds) / 60)} minutes old. {speech}"

    return speech, query


def build_weather_response(query_text: str = "") -> tuple[str, dict]:
    _LAST_CURRENT_READ.set(None)
    observation = fetch_weather_observation()
    cached = _LAST_CURRENT_READ.get() or CachedRead(observation, "fresh", 0.0)
    details = _format_current_details(observation)
    speech, query = build_current_weather_speech_from_details(
        query_text,
        details,
        include_forecast_hint=True,
    )
    if cached.freshness == "stale":
        speech = f"I couldn't refresh the weather, so this is the latest saved update. {speech}"

    return speech, {
        **details,
        "mode": query.mode,
        "field": query.field,
        "stale": observation.freshness_class == "stale",
        "freshness": cached.freshness,
        "cache_age_seconds": round(cached.age_seconds, 3),
        "stale_reason": cached.stale_reason,
    }
