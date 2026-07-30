from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class WeatherObservation:
    location: str
    generated_at: datetime
    age_seconds: float
    temperature_f: float
    humidity_pct: float | None
    wind_speed_mph: float | None
    wind_gust_mph: float | None
    rain_rate_in_h: float | None
    dewpoint_f: float | None = None
    heat_index_f: float | None = None
    wind_chill_f: float | None = None
    barometer_inhg: float | None = None
    inside_temperature_f: float | None = None
    inside_humidity_pct: float | None = None
    rain_total_in: float | None = None
    wind_direction_deg: float | None = None
    source_name: str = "WeeWX"
    source_type: str = "weewx"
    freshness_class: str = "fresh"


@dataclass(frozen=True)
class CurrentWeatherQuery:
    mode: str
    field: str | None = None


@dataclass(frozen=True)
class ResolvedRemoteLocation:
    query_text: str
    label: str
    latitude: float
    longitude: float
    city: str | None = None
    state: str | None = None
    country: str | None = None
    provider: str = "Nominatim"


@dataclass
class ForecastPeriod:
    name: str
    start_time: datetime
    end_time: datetime
    is_daytime: bool
    temperature_f: int
    temperature_trend: str | None
    wind_speed: str
    wind_direction: str
    short_forecast: str
    detailed_forecast: str
