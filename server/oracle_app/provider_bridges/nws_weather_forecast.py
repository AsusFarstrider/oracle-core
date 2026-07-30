from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib import error, request

from oracle_app.weather_models import ForecastPeriod
from oracle_app.read_cache import BoundedReadCache


_POINT_CACHE: BoundedReadCache[dict[str, Any]] = BoundedReadCache()
POINT_CACHE_TTL_SECONDS = 24 * 60 * 60


def clear_nws_point_cache() -> None:
    _POINT_CACHE.invalidate()


class WeatherForecastBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class WeatherForecastBridgeConfigurationError(WeatherForecastBridgeError):
    pass


class NwsWeatherForecastBridge:
    provider_name = "nws"

    def fetch_local_forecast(self, *, settings: dict[str, Any]) -> dict[str, Any]:
        latitude = settings["latitude"]
        longitude = settings["longitude"]
        if latitude is None or longitude is None:
            raise WeatherForecastBridgeConfigurationError(
                "forecast_unconfigured",
                "Forecast latitude/longitude is not configured",
            )
        return self.fetch_forecast_for_coordinates(
            latitude=float(latitude),
            longitude=float(longitude),
            settings=settings,
        )

    def fetch_forecast_for_coordinates(
        self,
        *,
        latitude: float,
        longitude: float,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        return self.fetch_typed_forecast_for_coordinates(
            latitude=latitude,
            longitude=longitude,
            user_agent=str(settings["user_agent"]),
            timeout_seconds=int(settings["timeout_seconds"]),
        )

    def fetch_typed_forecast_for_coordinates(
        self,
        *,
        latitude: float,
        longitude: float,
        user_agent: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        points_payload = self.fetch_typed_point_payload(
            latitude=latitude,
            longitude=longitude,
            user_agent=user_agent,
            timeout_seconds=timeout_seconds,
        )
        properties = points_payload.get("properties") or {}
        forecast_url = str(properties.get("forecast", "")).strip()
        forecast_hourly_url = str(properties.get("forecastHourly", "")).strip()
        if not forecast_url:
            raise WeatherForecastBridgeError(
                "forecast_location_out_of_range",
                "Forecast endpoint missing from NWS points response",
            )

        forecast_payload = self._get_json(
            forecast_url,
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
        )
        periods_raw = ((forecast_payload.get("properties") or {}).get("periods") or [])
        periods = [self.parse_forecast_period(item) for item in periods_raw if isinstance(item, dict)]
        if not periods:
            raise WeatherForecastBridgeError("forecast_unavailable", "Forecast response missing periods")

        relative_location = (((properties.get("relativeLocation") or {}).get("properties")) or {})
        return {
            "location": str(relative_location.get("city", "Unknown location")).strip(),
            "state": str(relative_location.get("state", "")).strip(),
            "forecast_url": forecast_url,
            "forecast_hourly_url": forecast_hourly_url,
            "periods": periods,
        }

    def fetch_point_payload(
        self,
        *,
        latitude: float,
        longitude: float,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        return self.fetch_typed_point_payload(
            latitude=latitude,
            longitude=longitude,
            user_agent=str(settings["user_agent"]),
            timeout_seconds=int(settings["timeout_seconds"]),
        )

    def fetch_typed_point_payload(
        self,
        *,
        latitude: float,
        longitude: float,
        user_agent: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        url = f"https://api.weather.gov/points/{latitude},{longitude}"
        payload = _POINT_CACHE.read(
            f"point:{latitude}:{longitude}",
            ttl_seconds=POINT_CACHE_TTL_SECONDS,
            stale_max_seconds=POINT_CACHE_TTL_SECONDS,
            allow_stale=False,
            loader=lambda: self._get_json(
                url,
                timeout_seconds=timeout_seconds,
                user_agent=user_agent,
            ),
        ).value
        if not isinstance(payload, dict):
            raise WeatherForecastBridgeError("forecast_unavailable", "Unexpected NWS point payload")
        return payload

    def parse_forecast_period(self, item: dict[str, Any]) -> ForecastPeriod:
        start_time = datetime.fromisoformat(str(item["startTime"]))
        end_time = datetime.fromisoformat(str(item["endTime"]))
        return ForecastPeriod(
            name=str(item.get("name", "")).strip(),
            start_time=start_time,
            end_time=end_time,
            is_daytime=bool(item.get("isDaytime", False)),
            temperature_f=int(item.get("temperature")),
            temperature_trend=item.get("temperatureTrend"),
            wind_speed=str(item.get("windSpeed", "")).strip(),
            wind_direction=str(item.get("windDirection", "")).strip(),
            short_forecast=str(item.get("shortForecast", "")).strip(),
            detailed_forecast=str(item.get("detailedForecast", "")).strip(),
        )

    def _get_json(self, url: str, *, timeout_seconds: int, user_agent: str) -> dict[str, Any]:
        req = request.Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/geo+json",
            },
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WeatherForecastBridgeError(
                "forecast_unavailable",
                detail or f"NWS forecast returned HTTP {exc.code}",
            ) from exc
        except error.URLError as exc:
            raise WeatherForecastBridgeError("forecast_unavailable", str(exc.reason)) from exc
        if not isinstance(payload, dict):
            raise WeatherForecastBridgeError("forecast_unavailable", "NWS forecast returned unexpected JSON")
        return payload


def get_weather_forecast_bridge(settings: dict[str, Any]) -> NwsWeatherForecastBridge:
    provider = str(settings.get("provider") or "nws").strip().lower()
    if provider == "nws":
        return NwsWeatherForecastBridge()
    raise WeatherForecastBridgeConfigurationError(
        "forecast_unconfigured",
        f"Unsupported weather forecast provider: {provider}",
    )
