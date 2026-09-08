from __future__ import annotations

from datetime import datetime, timedelta
import re

from .provider_bridges.nws_weather_forecast import NwsWeatherForecastBridge
from .weather_models import ForecastPeriod


WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

DAYTIME_HINTS = ("morning", "afternoon", "daytime")
NIGHTTIME_HINTS = ("night", "evening", "overnight")
PRACTICAL_UMBRELLA = (
    "umbrella", "will it rain", "going to rain", "expect rain",
)
PRACTICAL_COAT = (
    "need a coat", "wear a coat", "bring a coat", "take a coat",
    "need coats", "wear coats", "bring coats", "take coats",
    "need a jacket", "wear a jacket", "bring a jacket", "take a jacket",
    "need jackets", "wear jackets", "bring jackets", "take jackets",
)
PRACTICAL_SNOW = ("snow", "sleet", "freezing rain")
PRACTICAL_TEMPERATURE = (
    "how warm", "how cold", "temperature", "feel like outside", "outside feel like",
)
UNANCHORED_SCHEDULE = ("before i leave", "before we leave", "after dinner", "when i leave", "when we leave")


class ForecastOutOfRangeError(RuntimeError):
    pass


def _parse_forecast_period(item: dict) -> ForecastPeriod:
    return NwsWeatherForecastBridge().parse_forecast_period(item)


def _select_forecast_periods(query_text: str, periods: list[ForecastPeriod]) -> list[ForecastPeriod]:
    normalized = query_text.strip().lower()
    if not periods:
        return []

    first = periods[0]
    tzinfo = first.start_time.tzinfo
    now = datetime.now(tzinfo)
    wants_daytime = any(token in normalized for token in DAYTIME_HINTS)
    wants_nighttime = any(token in normalized for token in NIGHTTIME_HINTS)

    if "tomorrow" in normalized:
        target_day = (now + timedelta(days=1)).date()
        matched = [period for period in periods if period.start_time.date() == target_day]
        if wants_daytime:
            daytime = [period for period in matched if period.is_daytime]
            return daytime[:1]
        if wants_nighttime:
            nighttime = [period for period in matched if not period.is_daytime]
            return nighttime[:1]
        return matched[:2]

    for weekday_index, weekday_name in enumerate(WEEKDAY_NAMES):
        if weekday_name not in normalized:
            continue
        days_ahead = (weekday_index - now.weekday()) % 7
        if days_ahead == 0:
            target_day = now.date()
            matched = [period for period in periods if period.start_time.date() == target_day]
            if wants_daytime:
                daytime = [period for period in matched if period.is_daytime]
                return daytime[:1] or matched[:2] or periods[:2]
            if wants_nighttime:
                nighttime = [period for period in matched if not period.is_daytime]
                return nighttime[:1] or matched[:2] or periods[:2]
            return matched[:2] or periods[:2]
        else:
            target_day = (now + timedelta(days=days_ahead)).date()
        matched = [period for period in periods if period.start_time.date() == target_day]
        if wants_daytime:
            daytime = [period for period in matched if period.is_daytime]
            return daytime[:1]
        if wants_nighttime:
            nighttime = [period for period in matched if not period.is_daytime]
            return nighttime[:1]
        return matched[:2]

    if "tonight" in normalized:
        matched = [period for period in periods if period.name.lower() == "tonight"]
        return matched[:1] or periods[:1]

    if "weekend" in normalized:
        weekend_periods = [period for period in periods if period.start_time.weekday() in {5, 6}]
        if not weekend_periods:
            return periods[:2]

        if now.weekday() < 5:
            saturday_day = next(
                (
                    period
                    for period in weekend_periods
                    if period.start_time.weekday() == 5 and period.is_daytime
                ),
                None,
            )
            sunday_day = next(
                (
                    period
                    for period in weekend_periods
                    if period.start_time.weekday() == 6 and period.is_daytime
                ),
                None,
            )
            selected = [period for period in (saturday_day, sunday_day) if period is not None]
            return selected[:2] or weekend_periods[:2]

        active_weekend = [period for period in weekend_periods if period.end_time >= now]
        if now.weekday() == 5:
            saturday_active = next(
                (period for period in active_weekend if period.start_time.weekday() == 5),
                None,
            )
            sunday_day = next(
                (
                    period
                    for period in active_weekend
                    if period.start_time.weekday() == 6 and period.is_daytime
                ),
                None,
            )
            selected = [period for period in (saturday_active, sunday_day) if period is not None]
            return selected[:2] or active_weekend[:2] or weekend_periods[:2]

        sunday_active = [period for period in active_weekend if period.start_time.weekday() == 6]
        sunday_day = next((period for period in sunday_active if period.is_daytime), None)
        sunday_night = next((period for period in sunday_active if not period.is_daytime), None)
        selected = [period for period in (sunday_day, sunday_night) if period is not None]
        return selected[:2] or sunday_active[:2] or weekend_periods[:2]

    return periods[:2]


def _describe_forecast_period(period: ForecastPeriod, *, label: str | None = None) -> str:
    spoken_label = label or period.name
    temp_word = "high" if period.is_daytime else "low"
    return (
        f"{spoken_label} will be {period.short_forecast.lower()} with a {temp_word} near "
        f"{period.temperature_f}."
    )


def _weekend_transition_label(period: ForecastPeriod) -> str:
    lowered = period.name.strip().lower()
    if lowered in {"tonight", "saturday night", "sunday night"}:
        return "tonight"
    if period.is_daytime and period.start_time.weekday() == 5:
        return "this afternoon"
    return "tomorrow"


def format_forecast_summary(query_text: str, periods: list[ForecastPeriod]) -> str:
    normalized = query_text.strip().lower()
    selected = _select_forecast_periods(query_text, periods)
    if not selected:
        raise ForecastOutOfRangeError("That time is outside the current forecast window.")

    practical = _format_practical_forecast(normalized, selected)
    if practical is not None:
        return practical

    if "weekend" in normalized:
        if len(selected) == 1:
            return f"For the rest of the weekend, {_describe_forecast_period(selected[0], label='it')}"

        first, second = selected[0], selected[1]
        return (
            f"For the rest of the weekend, {_describe_forecast_period(first, label=_weekend_transition_label(first))}"
            f" Then {_describe_forecast_period(second, label=_weekend_transition_label(second))}".replace("..", ".")
        )

    if "tomorrow" in normalized:
        if len(selected) == 1:
            return _describe_forecast_period(selected[0], label="Tomorrow")

        first, second = selected[0], selected[1]
        return (
            f"{_describe_forecast_period(first, label='Tomorrow')}"
            f" {_describe_forecast_period(second, label='Tomorrow night')}"
        )

    if len(selected) == 1:
        return _describe_forecast_period(selected[0])

    first, second = selected[0], selected[1]
    return f"{_describe_forecast_period(first)} {_describe_forecast_period(second)}"


def needs_schedule_anchor(query_text: str) -> bool:
    normalized = " ".join(query_text.strip().lower().split())
    if not any(phrase in normalized for phrase in UNANCHORED_SCHEDULE):
        return False
    return not bool(re.search(
        r"\b(?:at|around|by|before|after)\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\b",
        normalized,
    ))


def is_practical_forecast_query(query_text: str) -> bool:
    normalized = " ".join(query_text.strip().lower().split())
    return any(
        phrase in normalized
        for phrase in (*PRACTICAL_UMBRELLA, *PRACTICAL_COAT, *PRACTICAL_SNOW, *PRACTICAL_TEMPERATURE)
    )


def _format_practical_forecast(normalized: str, selected: list[ForecastPeriod]) -> str | None:
    descriptions = " ".join(
        f"{period.short_forecast} {period.detailed_forecast}".casefold() for period in selected
    )
    precip_probabilities = [
        period.probability_of_precipitation_pct
        for period in selected
        if period.probability_of_precipitation_pct is not None
    ]
    max_precip = max(precip_probabilities) if precip_probabilities else None
    temperatures = [period.temperature_f for period in selected]
    label = _practical_window_label(selected)

    if any(phrase in normalized for phrase in PRACTICAL_UMBRELLA):
        wet_words = ("rain", "shower", "thunderstorm", "drizzle", "sleet")
        wet = any(word in descriptions for word in wet_words) or (max_precip is not None and max_precip >= 35)
        if wet:
            evidence = f" precipitation chances reach {max_precip} percent" if max_precip is not None else " rain is in the forecast"
            return f"Yes, bring an umbrella;{evidence} {label}."
        if max_precip is not None:
            return f"Probably not; the precipitation chance stays at or below {max_precip} percent {label}."
        return f"Probably not; the forecast is {selected[0].short_forecast.lower()} {label}, but no precipitation percentage is available."

    if any(phrase in normalized for phrase in PRACTICAL_COAT):
        low = min(temperatures)
        high = max(temperatures)
        if low <= 45:
            return f"Yes, a coat is sensible; forecast temperatures range from {low} to {high} degrees {label}."
        if low <= 60:
            return f"A light jacket may be useful; forecast temperatures range from {low} to {high} degrees {label}."
        return f"Probably not; forecast temperatures range from {low} to {high} degrees {label}."

    if any(phrase in normalized for phrase in PRACTICAL_SNOW):
        wintry = any(word in descriptions for word in PRACTICAL_SNOW)
        if wintry:
            return f"Yes, wintry precipitation is in the forecast {label}: {selected[0].short_forecast}."
        return f"No snow is shown in the available forecast {label}; it calls for {selected[0].short_forecast.lower()}."

    if any(phrase in normalized for phrase in PRACTICAL_TEMPERATURE):
        low = min(temperatures)
        high = max(temperatures)
        return f"Forecast temperatures range from {low} to {high} degrees {label}."
    return None


def _practical_window_label(periods: list[ForecastPeriod]) -> str:
    if len(periods) == 1:
        return periods[0].name.casefold()
    return f"from {periods[0].name.casefold()} through {periods[-1].name.casefold()}"
