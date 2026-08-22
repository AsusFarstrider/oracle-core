from __future__ import annotations

from typing import Any

from oracle_app.configuration.weather_runtime_settings import WeatherRuntimeSettings
from oracle_app.provider_bridges.nws_weather_forecast import NwsWeatherForecastBridge
from oracle_app.provider_bridges.weewx_weather_station import WeeWxWeatherStationBridge
from oracle_app.read_cache import BoundedReadCache
from oracle_app.weather_current import (
    _format_current_details,
    build_current_weather_speech_from_details,
)
from oracle_app.weather_forecast import _select_forecast_periods, format_forecast_summary
from oracle_app.weather_history import (
    _build_historical_speech,
    _local_midnight_epoch,
    _weighted_average,
    parse_historical_weather_query,
)
from oracle_app.weather_models import ForecastPeriod, WeatherObservation
from oracle_app.weather_remote import (
    build_remote_current_weather_response,
    build_remote_forecast_response,
)


class CanonicalWeatherExecution:
    """All configured weather capabilities bound to one applied snapshot."""

    def __init__(self, settings: WeatherRuntimeSettings) -> None:
        self.settings = settings
        self.station = WeeWxWeatherStationBridge()
        self.nws = NwsWeatherForecastBridge()
        self._current_cache: BoundedReadCache[WeatherObservation] = BoundedReadCache()
        self._forecast_cache: BoundedReadCache[dict[str, Any]] = BoundedReadCache()

    def fetch_current(self):
        current = self.settings.current
        if not self.settings.enabled or not current.enabled or current.current_url is None:
            raise RuntimeError("Current weather is not configured")
        return self._current_cache.read(
            f"current:{current.provider_id}:{current.current_url}:{self.settings.config_revision}",
            ttl_seconds=30,
            stale_max_seconds=15 * 60,
            loader=lambda: self.station.fetch_typed_current_observation(
                url=current.current_url or "",
                timeout_seconds=current.timeout_seconds or 8,
                stale_after_seconds=current.stale_after_seconds or 1800,
            ),
        )

    def build_current_response(self, query_text: str = "") -> tuple[str, dict[str, Any]]:
        cached = self.fetch_current()
        observation = cached.value
        details = _format_current_details(observation)
        speech, query = build_current_weather_speech_from_details(
            query_text,
            details,
            include_forecast_hint=self.settings.forecast.enabled,
            forecast_loader=self.fetch_forecast,
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

    def fetch_forecast(self) -> dict[str, Any]:
        forecast = self.settings.forecast
        if (
            not self.settings.enabled
            or not forecast.enabled
            or forecast.latitude is None
            or forecast.longitude is None
            or forecast.user_agent is None
        ):
            raise RuntimeError("Weather forecast is not configured")
        cached = self._forecast_cache.read(
            f"forecast:{forecast.provider_id}:{forecast.latitude}:{forecast.longitude}:{self.settings.config_revision}",
            ttl_seconds=10 * 60,
            stale_max_seconds=2 * 60 * 60,
            loader=lambda: self.nws.fetch_typed_forecast_for_coordinates(
                latitude=forecast.latitude or 0.0,
                longitude=forecast.longitude or 0.0,
                user_agent=forecast.user_agent or "",
                timeout_seconds=forecast.timeout_seconds or 8,
            ),
        )
        return {
            **cached.value,
            "freshness": cached.freshness,
            "age_seconds": round(cached.age_seconds, 3),
            "stale_reason": cached.stale_reason,
        }

    def build_forecast_response(self, query_text: str) -> tuple[str, dict[str, Any]]:
        forecast = self.fetch_forecast()
        periods: list[ForecastPeriod] = forecast["periods"]
        selected = _select_forecast_periods(query_text, periods)
        speech = format_forecast_summary(query_text, periods)
        if forecast["freshness"] == "stale":
            speech = f"I couldn't refresh the forecast, so this is the latest saved forecast. {speech}"
        return speech, {
            "location": forecast["location"],
            "state": forecast["state"],
            "forecast_url": forecast["forecast_url"],
            "forecast_hourly_url": forecast["forecast_hourly_url"],
            "freshness": forecast["freshness"],
            "age_seconds": forecast["age_seconds"],
            "stale_reason": forecast["stale_reason"],
            "selected_periods": [_period_payload(period) for period in selected],
        }

    def build_history_response(self, query_text: str, *, now=None) -> tuple[str, dict[str, Any]]:
        history = self.settings.history
        if not self.settings.enabled or not history.enabled:
            raise RuntimeError("Historical weather is not configured")
        parsed = parse_historical_weather_query(query_text, now=now)
        if parsed is None:
            raise RuntimeError("Historical weather query could not be parsed")
        static_entry = self.station.load_typed_history_entry(
            parsed.target_date,
            history_url=history.history_url,
            timeout_seconds=history.timeout_seconds or 8,
        )
        if static_entry is not None:
            details = dict(static_entry)
            details["field"] = parsed.field
            return _build_historical_speech(parsed, details), details
        fallback = history.ssh_fallback
        if fallback is None:
            raise RuntimeError("No local historical weather was found for that date")
        epoch = _local_midnight_epoch(parsed.target_date)
        rows = {
            name: self.station.query_typed_day_row(
                table,
                epoch,
                host=fallback.host,
                user=fallback.user,
                password=fallback.password,
                database_path=fallback.database_path,
                timeout_seconds=fallback.timeout_seconds,
            )
            for name, table in {
                "temp": "archive_day_outTemp",
                "humidity": "archive_day_outHumidity",
                "wind": "archive_day_windSpeed",
                "gust": "archive_day_windGust",
                "rain": "archive_day_rain",
                "pressure": "archive_day_barometer",
            }.items()
        }
        if rows["temp"] is None:
            raise RuntimeError("No local historical weather was found for that date")
        temp = rows["temp"]
        details = {
            "date": parsed.target_date.isoformat(),
            "field": parsed.field,
            "temperature_min_f": temp.get("min"),
            "temperature_max_f": temp.get("max"),
            "temperature_avg_f": _weighted_average(temp),
            "humidity_min_pct": _value(rows["humidity"], "min"),
            "humidity_max_pct": _value(rows["humidity"], "max"),
            "humidity_avg_pct": _weighted_average(rows["humidity"]),
            "wind_max_mph": _value(rows["wind"], "max"),
            "wind_avg_mph": _weighted_average(rows["wind"]),
            "wind_gust_max_mph": _value(rows["gust"], "max"),
            "rain_total_in": _value(rows["rain"], "sum"),
            "rain_rate_max_in_h": _value(rows["rain"], "max"),
            "pressure_min_inhg": _value(rows["pressure"], "min"),
            "pressure_max_inhg": _value(rows["pressure"], "max"),
            "pressure_avg_inhg": _weighted_average(rows["pressure"]),
        }
        return _build_historical_speech(parsed, details), details

    def build_remote_current_response(self, query_text: str):
        return build_remote_current_weather_response(
            query_text,
            runtime_settings=self.settings.remote,
            canonical_authority=True,
        )

    def build_remote_forecast_response(self, query_text: str):
        return build_remote_forecast_response(
            query_text,
            runtime_settings=self.settings.remote,
            canonical_authority=True,
        )


def _value(row: dict[str, object] | None, key: str):
    return None if row is None else row.get(key)


def _period_payload(period: ForecastPeriod) -> dict[str, Any]:
    return {
        "name": period.name,
        "start_time": period.start_time.isoformat(),
        "end_time": period.end_time.isoformat(),
        "is_daytime": period.is_daytime,
        "temperature_f": period.temperature_f,
        "short_forecast": period.short_forecast,
    }
