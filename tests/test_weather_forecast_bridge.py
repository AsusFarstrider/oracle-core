from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.provider_bridges.nws_weather_forecast import (
    NwsWeatherForecastBridge,
    WeatherForecastBridgeConfigurationError,
    WeatherForecastBridgeError,
    get_weather_forecast_bridge,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class WeatherForecastBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        from oracle_app.provider_bridges.nws_weather_forecast import clear_nws_point_cache

        clear_nws_point_cache()

    def _settings(self) -> dict:
        return {
            "provider": "nws",
            "latitude": 40.1,
            "longitude": -75.2,
            "timeout_seconds": 8,
            "user_agent": "Oracle test",
        }

    @patch("oracle_app.provider_bridges.nws_weather_forecast.request.urlopen")
    def test_fetch_local_forecast_returns_oracle_native_forecast(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = [
            _Response(
                {
                    "properties": {
                        "forecast": "https://api.weather.gov/gridpoints/PHI/1,2/forecast",
                        "forecastHourly": "https://api.weather.gov/gridpoints/PHI/1,2/forecast/hourly",
                        "relativeLocation": {
                            "properties": {
                                "city": "Home",
                                "state": "PA",
                            }
                        },
                    }
                }
            ),
            _Response(
                {
                    "properties": {
                        "periods": [
                            {
                                "name": "Tonight",
                                "startTime": "2026-05-06T18:00:00-04:00",
                                "endTime": "2026-05-07T06:00:00-04:00",
                                "isDaytime": False,
                                "temperature": 51,
                                "temperatureTrend": None,
                                "windSpeed": "5 mph",
                                "windDirection": "NW",
                                "shortForecast": "Mostly Cloudy",
                                "detailedForecast": "Mostly Cloudy",
                            }
                        ]
                    }
                }
            ),
        ]

        forecast = NwsWeatherForecastBridge().fetch_local_forecast(settings=self._settings())

        self.assertEqual(forecast["location"], "Home")
        self.assertEqual(forecast["state"], "PA")
        self.assertEqual(forecast["forecast_url"], "https://api.weather.gov/gridpoints/PHI/1,2/forecast")
        self.assertEqual(len(forecast["periods"]), 1)
        self.assertEqual(forecast["periods"][0].name, "Tonight")
        self.assertEqual(forecast["periods"][0].temperature_f, 51)

    def test_fetch_local_forecast_requires_coordinates(self) -> None:
        settings = self._settings()
        settings["latitude"] = None

        with self.assertRaises(WeatherForecastBridgeConfigurationError):
            NwsWeatherForecastBridge().fetch_local_forecast(settings=settings)

    @patch("oracle_app.provider_bridges.nws_weather_forecast.request.urlopen")
    def test_missing_forecast_endpoint_is_domain_scoped_error(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response({"properties": {}})

        with self.assertRaises(WeatherForecastBridgeError) as ctx:
            NwsWeatherForecastBridge().fetch_local_forecast(settings=self._settings())

        self.assertEqual(ctx.exception.error_code, "forecast_location_out_of_range")

    def test_get_weather_forecast_bridge_rejects_unsupported_provider(self) -> None:
        settings = self._settings()
        settings["provider"] = "other"

        with self.assertRaises(WeatherForecastBridgeConfigurationError):
            get_weather_forecast_bridge(settings)


if __name__ == "__main__":
    unittest.main()
