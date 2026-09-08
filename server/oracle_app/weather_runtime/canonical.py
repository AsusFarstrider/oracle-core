from __future__ import annotations

from typing import Any

from oracle_app.configuration.weather_runtime_settings import WeatherRuntimeSettings
from oracle_app.provider_bridges.nws_weather_forecast import NwsWeatherForecastBridge
from oracle_app.provider_bridges.remote_weather import NominatimNwsRemoteWeatherBridge
from oracle_app.provider_bridges.weewx_weather_station import WeeWxWeatherStationBridge
from oracle_app.read_cache import BoundedReadCache
from oracle_app.weather_current import (
    _format_current_details,
    build_current_weather_speech_from_details,
)
from oracle_app.weather_forecast import _select_forecast_periods, format_forecast_summary
from oracle_app.weather_history import (
    HistoricalWeatherQuery,
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
from oracle_app.weather_solar import build_solar_day_snapshot, build_solar_response


class CanonicalWeatherExecution:
    """All configured weather capabilities bound to one applied snapshot."""

    def __init__(self, settings: WeatherRuntimeSettings) -> None:
        self.settings = settings
        self.station = WeeWxWeatherStationBridge()
        self.nws = NwsWeatherForecastBridge()
        self.remote = NominatimNwsRemoteWeatherBridge()
        self._current_cache: BoundedReadCache[WeatherObservation] = BoundedReadCache()
        self._forecast_cache: BoundedReadCache[dict[str, Any]] = BoundedReadCache()
        self._history_cache: BoundedReadCache[tuple[str, dict[str, Any]]] = BoundedReadCache()

    def fetch_current(self, *, force_refresh: bool = False, allow_stale: bool = True):
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
            force_refresh=force_refresh,
            allow_stale=allow_stale,
        )

    def build_current_response(
        self,
        query_text: str = "",
        *,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        cached = self.fetch_current(force_refresh=force_refresh, allow_stale=allow_stale)
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
            "refresh_status": cached.refresh_status,
            "failure_kind": cached.failure_kind,
        }

    def fetch_forecast(self, *, force_refresh: bool = False, allow_stale: bool = True) -> dict[str, Any]:
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
            force_refresh=force_refresh,
            allow_stale=allow_stale,
        )
        return {
            **cached.value,
            "freshness": cached.freshness,
            "age_seconds": round(cached.age_seconds, 3),
            "stale_reason": cached.stale_reason,
            "refresh_status": cached.refresh_status,
            "failure_kind": cached.failure_kind,
        }

    def build_forecast_response(
        self,
        query_text: str,
        *,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        forecast = self.fetch_forecast(force_refresh=force_refresh, allow_stale=allow_stale)
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
            "refresh_status": forecast["refresh_status"],
            "failure_kind": forecast["failure_kind"],
            "selected_periods": [_period_payload(period) for period in selected],
        }

    def build_history_response(self, query_text: str, *, now=None) -> tuple[str, dict[str, Any]]:
        history = self.settings.history
        if not self.settings.enabled or not history.enabled:
            raise RuntimeError("Historical weather is not configured")
        parsed = parse_historical_weather_query(query_text, now=now)
        if parsed is None:
            raise RuntimeError("Historical weather query could not be parsed")
        cached = self._history_cache.read(
            f"history:{history.provider_id}:{parsed.target_date.isoformat()}:{parsed.field or 'summary'}:{self.settings.config_revision}",
            ttl_seconds=60 * 60,
            stale_max_seconds=24 * 60 * 60,
            loader=lambda: self._build_history_uncached(parsed),
        )
        speech, details = cached.value
        if cached.freshness == "stale":
            speech = (
                "I couldn't refresh the weather history, so this is the latest saved result. "
                f"{speech}"
            )
        return speech, {
            **details,
            "freshness": cached.freshness,
            "cache_age_seconds": round(cached.age_seconds, 3),
            "stale_reason": cached.stale_reason,
            "refresh_status": cached.refresh_status,
            "failure_kind": cached.failure_kind,
        }

    def _build_history_uncached(
        self, parsed: HistoricalWeatherQuery
    ) -> tuple[str, dict[str, Any]]:
        history = self.settings.history
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
            remote_bridge=self.remote,
        )

    def build_remote_forecast_response(self, query_text: str):
        return build_remote_forecast_response(
            query_text,
            runtime_settings=self.settings.remote,
            remote_bridge=self.remote,
        )

    def build_solar_response(self, query_text: str, *, now=None):
        return build_solar_response(query_text, settings=self.settings.solar, now=now)

    def build_solar_snapshot(self, *, now=None):
        return build_solar_day_snapshot(settings=self.settings.solar, now=now)

    def fetch_alerts(self, *, force_refresh: bool = False, allow_stale: bool = True):
        forecast = self.settings.forecast
        if (
            not self.settings.enabled
            or not forecast.enabled
            or forecast.latitude is None
            or forecast.longitude is None
            or forecast.user_agent is None
        ):
            raise RuntimeError("Weather alerts are not configured")
        return self.remote.fetch_alerts(
            latitude=forecast.latitude,
            longitude=forecast.longitude,
            user_agent=forecast.user_agent,
            timeout_seconds=forecast.timeout_seconds or 8,
            force_refresh=force_refresh,
            allow_stale=allow_stale,
        )

    def build_alerts_response(self):
        payload = self.fetch_alerts()
        alerts = payload["alerts"]
        if not alerts:
            speech = "There are no active National Weather Service watches or warnings for home."
        else:
            material = [item for item in alerts if item.get("severity") in {"Extreme", "Severe", "Moderate"}]
            selected = material or alerts
            names = [str(item.get("event") or "weather alert") for item in selected[:3]]
            speech = f"There {'is' if len(names) == 1 else 'are'} {len(names)} active weather {'alert' if len(names) == 1 else 'alerts'}: {', '.join(names)}."
        if payload["freshness"] == "stale":
            speech = f"I couldn't refresh weather alerts, so this is the latest saved update. {speech}"
        return speech, payload


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
        "probability_of_precipitation_pct": period.probability_of_precipitation_pct,
    }
