from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo



@dataclass(frozen=True)
class HistoricalWeatherQuery:
    target_date: date
    field: str | None = None


def parse_historical_weather_query(text: str, *, now: datetime | None = None) -> HistoricalWeatherQuery | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None

    target_date = _extract_target_date(normalized, now=now)
    if target_date is None:
        return None

    field = _extract_field(normalized)
    if field is None and "weather" not in normalized:
        return None
    return HistoricalWeatherQuery(target_date=target_date, field=field)


def _extract_target_date(normalized: str, *, now: datetime | None = None) -> date | None:
    reference = now or datetime.now().astimezone()
    if "yesterday" in normalized:
        return (reference - timedelta(days=1)).date()

    cleaned = normalized.replace(",", " ")
    cleaned = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", cleaned)
    parts = cleaned.split()

    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    for index, token in enumerate(parts):
        month = month_names.get(token)
        if month is None or index + 1 >= len(parts):
            continue
        day_token = parts[index + 1]
        if not day_token.isdigit():
            continue
        year = reference.year
        if index + 2 < len(parts) and parts[index + 2].isdigit():
            year = int(parts[index + 2])
            if year < 100:
                year += 2000
        try:
            return date(year, month, int(day_token))
        except ValueError:
            return None

    for token in parts:
        if token.count("/") not in {1, 2}:
            continue
        date_parts = token.split("/")
        try:
            if len(date_parts) == 2:
                month, day_value = [int(item) for item in date_parts]
                year = reference.year
            else:
                month, day_value, year = [int(item) for item in date_parts]
                if year < 100:
                    year += 2000
            return date(year, month, day_value)
        except ValueError:
            return None

    return None


def _extract_field(normalized: str) -> str | None:
    field_phrases: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("temperature", ("temperature", "how hot", "how cold")),
        ("humidity", ("humidity", "humid", "muggy")),
        ("wind", ("wind", "windy", "gust")),
        ("rain", ("rain", "raining", "precipitation")),
        ("pressure", ("pressure", "barometer")),
    )
    for field, phrases in field_phrases:
        if any(phrase in normalized for phrase in phrases):
            return field
    return None


def _local_midnight_epoch(target_date: date) -> int:
    tzinfo = datetime.now().astimezone().tzinfo
    if isinstance(tzinfo, ZoneInfo):
        local_tz = tzinfo
    else:
        local_tz = datetime.now().astimezone().tzinfo
    local_midnight = datetime(target_date.year, target_date.month, target_date.day, tzinfo=local_tz)
    return int(local_midnight.timestamp())


def _weighted_average(row: dict[str, object] | None) -> float | None:
    if row is None:
        return None
    wsum = row.get("wsum")
    sumtime = row.get("sumtime")
    if not isinstance(wsum, (int, float)) or not isinstance(sumtime, int) or sumtime <= 0:
        return None
    return float(wsum) / float(sumtime)


def _day_label(target_date: date) -> str:
    return target_date.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _format_number(value: float | None, *, digits: int = 0) -> str:
    if value is None:
        return "unknown"
    rounded = round(float(value), digits)
    if digits == 0:
        return str(int(rounded))
    return f"{rounded:.{digits}f}".rstrip("0").rstrip(".")


def _build_historical_speech(query: HistoricalWeatherQuery, details: dict[str, object]) -> str:
    label = _day_label(query.target_date)
    field = query.field

    if field == "temperature":
        return (
            f"On {label}, the temperature ranged from "
            f"{_format_number(details.get('temperature_min_f'))} to {_format_number(details.get('temperature_max_f'))} degrees, "
            f"with an average around {_format_number(details.get('temperature_avg_f'))}."
        )
    if field == "humidity":
        return (
            f"On {label}, humidity ranged from "
            f"{_format_number(details.get('humidity_min_pct'))} to {_format_number(details.get('humidity_max_pct'))} percent, "
            f"with an average around {_format_number(details.get('humidity_avg_pct'))} percent."
        )
    if field == "wind":
        gust = details.get("wind_gust_max_mph")
        return (
            f"On {label}, wind averaged about {_format_number(details.get('wind_avg_mph'))} miles per hour "
            f"and reached {_format_number(gust)} miles per hour at its peak."
        )
    if field == "rain":
        rain_total = float(details.get("rain_total_in") or 0.0)
        if rain_total <= 0.0:
            return f"On {label}, no rain was recorded."
        return (
            f"On {label}, about {_format_number(details.get('rain_total_in'), digits=2)} inches of rain fell, "
            f"with a peak rate of {_format_number(details.get('rain_rate_max_in_h'), digits=2)} inches per hour."
        )
    if field == "pressure":
        return (
            f"On {label}, the barometer ranged from {_format_number(details.get('pressure_min_inhg'), digits=2)} "
            f"to {_format_number(details.get('pressure_max_inhg'), digits=2)} inches of mercury."
        )

    rain_total = float(details.get("rain_total_in") or 0.0)
    humidity_avg = details.get("humidity_avg_pct")
    wind_gust = details.get("wind_gust_max_mph")
    parts = [
        f"On {label}, the temperature ranged from {_format_number(details.get('temperature_min_f'))} to {_format_number(details.get('temperature_max_f'))} degrees, with an average around {_format_number(details.get('temperature_avg_f'))}.",
    ]
    if isinstance(humidity_avg, (int, float)) and float(humidity_avg) >= 72.0:
        parts.append(f"Humidity averaged about {_format_number(humidity_avg)} percent.")
    if rain_total > 0.0:
        parts.append(f"About {_format_number(details.get('rain_total_in'), digits=2)} inches of rain fell.")
    elif rain_total == 0.0:
        parts.append("No rain was recorded.")
    if isinstance(wind_gust, (int, float)) and float(wind_gust) >= 10.0:
        parts.append(f"The strongest gust reached {_format_number(wind_gust)} miles per hour.")
    return " ".join(parts)
