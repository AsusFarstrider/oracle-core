from __future__ import annotations

from datetime import datetime, timedelta

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
