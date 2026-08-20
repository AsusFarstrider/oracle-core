from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from oracle_app.configuration import EffectiveConfig, WeatherRuntimeSettings, inspect_candidate
from oracle_app.dispatch import build_dispatch_registry, execute_dispatch
from oracle_app.schemas import DispatchPlan
from oracle_app.ui_weather import build_ui_weather_snapshot
from oracle_app.weather_models import ForecastPeriod, ResolvedRemoteLocation, WeatherObservation
from oracle_app.weather_runtime import CanonicalWeatherExecution


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class WeatherRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_weather_selects_no_capability_providers(self) -> None:
        settings = WeatherRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.enabled)
        for capability in (settings.current, settings.forecast, settings.history, settings.remote):
            self.assertFalse(capability.enabled)
            self.assertIsNone(capability.provider_id)

    def test_preserves_independent_capability_edges_and_home_forecast_fallback(self) -> None:
        settings = WeatherRuntimeSettings.from_effective_config(
            self._effective_config(enabled=True, include_history_secret=True)
        )

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.current.provider_id, "local_current")
        self.assertEqual(settings.current.current_url, "http://current.invalid/current.json")
        self.assertEqual(settings.current.stale_after_seconds, 600)
        self.assertEqual(settings.forecast.provider_id, "home_forecast")
        self.assertEqual((settings.forecast.latitude, settings.forecast.longitude), (40.1, -75.2))
        self.assertEqual(settings.forecast.user_agent, "Oracle home forecast")
        self.assertEqual(settings.history.provider_id, "local_history")
        self.assertEqual(settings.history.history_url, "http://history.invalid/history.json")
        self.assertIsNotNone(settings.history.ssh_fallback)
        self.assertEqual(settings.history.ssh_fallback.password, "weather-history-password")  # type: ignore[union-attr]
        self.assertEqual(settings.remote.provider_id, "remote_weather")
        self.assertEqual(settings.remote.user_agent, "Oracle remote weather")
        self.assertNotIn("weather-history-password", repr(settings))

    def test_provider_coordinates_override_household_only_for_home_forecast(self) -> None:
        effective = self._effective_config(
            enabled=True,
            include_history_secret=True,
            forecast_coordinates=(41.5, -76.5),
        )

        settings = WeatherRuntimeSettings.from_effective_config(effective)

        self.assertEqual((settings.forecast.latitude, settings.forecast.longitude), (41.5, -76.5))
        self.assertFalse(hasattr(settings.remote, "latitude"))

    def test_current_selection_does_not_require_dormant_history_fallback_secret(self) -> None:
        effective = self._effective_config(current_only=True)

        settings = WeatherRuntimeSettings.from_effective_config(effective)

        self.assertTrue(settings.current.enabled)
        self.assertFalse(settings.history.enabled)

    def test_canonical_weather_execution_uses_typed_capability_edges(self) -> None:
        settings = WeatherRuntimeSettings.from_effective_config(
            self._effective_config(enabled=True, include_history_secret=True)
        )
        execution = CanonicalWeatherExecution(settings)
        now = datetime.now(timezone.utc)
        observation = WeatherObservation(
            location="Example Home",
            generated_at=now,
            age_seconds=0,
            temperature_f=72,
            humidity_pct=50,
            wind_speed_mph=4,
            wind_gust_mph=6,
            rain_rate_in_h=0,
        )
        period = ForecastPeriod(
            name="Tonight",
            start_time=now,
            end_time=now + timedelta(hours=12),
            is_daytime=False,
            temperature_f=55,
            temperature_trend=None,
            wind_speed="5 mph",
            wind_direction="N",
            short_forecast="Clear",
            detailed_forecast="Clear",
        )
        forecast = {
            "location": "Example Home",
            "state": "PA",
            "forecast_url": "https://api.weather.gov/forecast",
            "forecast_hourly_url": "https://api.weather.gov/hourly",
            "periods": [period],
        }
        historical = {
            "date": "2026-07-14",
            "temperature_min_f": 60,
            "temperature_max_f": 80,
            "temperature_avg_f": 70,
            "rain_total_in": 0,
        }

        with patch(
            "oracle_app.weather_current.get_weather_current_settings",
            side_effect=AssertionError("canonical current weather used V1 settings"),
        ), patch(
            "oracle_app.weather_forecast.get_forecast_settings",
            side_effect=AssertionError("canonical forecast used V1 settings"),
        ), patch(
            "oracle_app.weather_history.get_weather_history_settings",
            side_effect=AssertionError("canonical history used V1 settings"),
        ), patch(
            "oracle_app.weather_remote.get_forecast_settings",
            side_effect=AssertionError("canonical remote weather used V1 settings"),
        ), patch(
            "oracle_app.provider_bridges.weewx_weather_station.WeeWxWeatherStationBridge.fetch_typed_current_observation",
            return_value=observation,
        ), patch(
            "oracle_app.provider_bridges.nws_weather_forecast.NwsWeatherForecastBridge.fetch_typed_forecast_for_coordinates",
            return_value=forecast,
        ), patch(
            "oracle_app.provider_bridges.weewx_weather_station.WeeWxWeatherStationBridge.load_typed_history_entry",
            return_value=historical,
        ):
            registry = build_dispatch_registry(
                canonical_configuration=True,
                weather_execution=execution,
            )
            current = execute_dispatch(
                DispatchPlan(
                    target="weather",
                    hook="weather.current_weather",
                    payload={"action": "current_weather", "text": "what is the weather"},
                    status="pending_integration",
                ),
                registry=registry,
            )
            forecast_result = execute_dispatch(
                DispatchPlan(
                    target="weather",
                    hook="weather.weather_forecast",
                    payload={"action": "weather_forecast", "text": "forecast"},
                    status="pending_integration",
                ),
                registry=registry,
            )
            history = execute_dispatch(
                DispatchPlan(
                    target="weather",
                    hook="weather.weather_history",
                    payload={"action": "weather_history", "text": "weather yesterday"},
                    status="pending_integration",
                ),
                registry=registry,
            )
            ui = build_ui_weather_snapshot(
                canonical_execution=execution,
            )

        self.assertEqual(current.result["weather"]["temperature_f"], 72)
        self.assertEqual(forecast_result.result["forecast"]["location"], "Example Home")
        self.assertEqual(history.result["history"]["temperature_avg_f"], 70)
        self.assertEqual(ui["current"]["temperature_f"], 72)

    def test_canonical_remote_current_uses_typed_nws_request_settings(self) -> None:
        settings = WeatherRuntimeSettings.from_effective_config(
            self._effective_config(enabled=True, include_history_secret=True)
        )
        execution = CanonicalWeatherExecution(settings)
        location = ResolvedRemoteLocation(
            query_text="boston",
            label="Boston, MA",
            latitude=42.3,
            longitude=-71.0,
            city="Boston",
            state="MA",
        )
        point = {"properties": {"relativeLocation": {"properties": {"city": "Boston", "state": "MA"}}}}
        observation = {
            "properties": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stationId": "KBOS",
                "stationName": "Boston",
                "temperature": {"value": 10.0},
                "relativeHumidity": {"value": 50.0},
                "windDirection": {"value": 0.0},
                "windSpeed": {"value": 0.0},
                "windGust": {"value": None},
                "barometricPressure": {"value": 101000.0},
                "textDescription": "Clear",
            }
        }

        with patch(
            "oracle_app.weather_remote.get_forecast_settings",
            side_effect=AssertionError("canonical remote weather used V1 settings"),
        ), patch(
            "oracle_app.weather_remote._resolve_remote_location",
            return_value=location,
        ) as resolve, patch(
            "oracle_app.weather_remote._fetch_station_observation",
            return_value=(point, observation),
        ) as fetch:
            speech, details = execution.build_remote_current_response(
                "what is the weather in boston"
            )

        self.assertIn("Boston", speech)
        self.assertEqual(details["station_id"], "KBOS")
        self.assertEqual(resolve.call_args.kwargs["user_agent"], "Oracle remote weather")
        self.assertEqual(fetch.call_args.kwargs["timeout_seconds"], 12)

    def test_absent_optional_role_has_no_implicit_runtime_defaults(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            WeatherRuntimeSettings.from_effective_config(effective)

    def _effective_config(
        self,
        *,
        enabled: bool = False,
        current_only: bool = False,
        include_role: bool = True,
        include_history_secret: bool = False,
        forecast_coordinates: tuple[float, float] | None = None,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            weather_path = bundle / "domains" / "weather.yaml"
            if not include_role:
                weather_path.unlink()
            elif enabled or current_only:
                self._write_enabled_weather(
                    bundle,
                    current_only=current_only,
                    forecast_coordinates=forecast_coordinates,
                )
            if include_history_secret:
                (bundle / "secrets.env").write_text(
                    "WEATHER_HISTORY_PASSWORD=weather-history-password\n",
                    encoding="utf-8",
                )
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible, inspection.report)
            self.assertIsNotNone(inspection.bundle)
            self.assertIsNotNone(inspection.normalized_candidate_revision)
            self.assertIsNotNone(inspection.secrets)
            return EffectiveConfig(
                activation_generation_id="activation_11111111111111111111111111111111",
                config_generation_id="config_11111111111111111111111111111111",
                secret_generation_id="secrets_11111111111111111111111111111111",
                selection_operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids=MappingProxyType({}),
                config_revision=inspection.normalized_candidate_revision,
                bundle_id="example-home",
                schema_version=2,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )

    @staticmethod
    def _write_enabled_weather(
        bundle: Path,
        *,
        current_only: bool,
        forecast_coordinates: tuple[float, float] | None,
    ) -> None:
        household_path = bundle / "household.yaml"
        household = json.loads(json.dumps(__import__("yaml").safe_load(household_path.read_text())))
        household["household"].setdefault("home_location", {}).update(
            {"latitude": 40.1, "longitude": -75.2}
        )
        household_path.write_text(json.dumps(household), encoding="utf-8")

        capabilities = {
            "current": {"enabled": True, "provider": "local_current"},
            "forecast": {"enabled": not current_only, "provider": None if current_only else "home_forecast"},
            "history": {"enabled": not current_only, "provider": None if current_only else "local_history"},
            "remote": {"enabled": not current_only, "provider": None if current_only else "remote_weather"},
        }
        weather = {
            "enabled": True,
            **capabilities,
            "providers": {
                "local_current": {
                    "type": "weewx",
                    "current_url": "http://current.invalid/current.json",
                    "history_ssh_fallback": {
                        "host": "current.invalid",
                        "user": "oracle",
                        "password_secret": "DORMANT_CURRENT_HISTORY_PASSWORD",
                        "database_path": "/var/lib/weewx/archive.sdb",
                    },
                    "timeout_seconds": 7,
                    "stale_after_seconds": 600,
                },
                "local_history": {
                    "type": "weewx",
                    "current_url": "http://history.invalid/current.json",
                    "history_url": "http://history.invalid/history.json",
                    "history_ssh_fallback": {
                        "host": "history.invalid",
                        "user": "oracle",
                        "password_secret": "WEATHER_HISTORY_PASSWORD",
                        "database_path": "/var/lib/weewx/archive.sdb",
                        "timeout_seconds": 9,
                    },
                },
                "home_forecast": {
                    "type": "nws",
                    "user_agent": "Oracle home forecast",
                    "office": "PHI",
                    "timeout_seconds": 11,
                    **(
                        {}
                        if forecast_coordinates is None
                        else {
                            "latitude": forecast_coordinates[0],
                            "longitude": forecast_coordinates[1],
                        }
                    ),
                },
                "remote_weather": {
                    "type": "nws",
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "user_agent": "Oracle remote weather",
                    "timeout_seconds": 12,
                },
            },
        }
        (bundle / "domains" / "weather.yaml").write_text(json.dumps(weather), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
