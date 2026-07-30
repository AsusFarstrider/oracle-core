from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.weather import (
    ForecastPeriod,
    WeatherObservation,
    build_weather_response,
    format_forecast_summary,
    format_weather_summary,
)


class WeatherFormattingTests(unittest.TestCase):
    def _obs(
        self,
        *,
        temp: float,
        humidity: float | None,
        wind_speed: float | None,
        gust: float | None,
        rain: float | None,
    ) -> WeatherObservation:
        return WeatherObservation(
            location="Test",
            generated_at=datetime.now(timezone.utc),
            age_seconds=30.0,
            temperature_f=temp,
            humidity_pct=humidity,
            wind_speed_mph=wind_speed,
            wind_gust_mph=gust,
            rain_rate_in_h=rain,
            barometer_inhg=30.12,
            wind_direction_deg=90.0,
        )

    def _period(
        self,
        *,
        name: str,
        start: str,
        end: str,
        is_daytime: bool,
        temp: int,
        short: str,
    ) -> ForecastPeriod:
        return ForecastPeriod(
            name=name,
            start_time=datetime.fromisoformat(start),
            end_time=datetime.fromisoformat(end),
            is_daytime=is_daytime,
            temperature_f=temp,
            temperature_trend=None,
            wind_speed="5 mph",
            wind_direction="NW",
            short_forecast=short,
            detailed_forecast=short,
        )

    def test_temperature_only_when_normal(self) -> None:
        text = format_weather_summary(
            self._obs(temp=41.0, humidity=50.0, wind_speed=5.0, gust=8.0, rain=0.0)
        )
        self.assertEqual(text, "It is currently 41 degrees.")

    def test_reports_drizzle_when_rain_rate_is_tiny(self) -> None:
        text = format_weather_summary(
            self._obs(temp=41.0, humidity=50.0, wind_speed=5.0, gust=8.0, rain=0.005)
        )
        self.assertIn("drizzle", text)

    def test_reports_light_rain_when_rain_rate_is_low(self) -> None:
        text = format_weather_summary(
            self._obs(temp=41.0, humidity=50.0, wind_speed=5.0, gust=8.0, rain=0.05)
        )
        self.assertIn("light rain", text)

    def test_reports_rain_when_raining(self) -> None:
        text = format_weather_summary(
            self._obs(temp=41.0, humidity=50.0, wind_speed=5.0, gust=8.0, rain=0.2)
        )
        self.assertIn("rain", text)

    def test_reports_heavy_rain_when_rain_rate_is_high(self) -> None:
        text = format_weather_summary(
            self._obs(temp=41.0, humidity=50.0, wind_speed=5.0, gust=8.0, rain=0.4)
        )
        self.assertIn("heavy rain", text)

    def test_reports_wind_only_when_threshold_exceeded(self) -> None:
        text = format_weather_summary(
            self._obs(temp=41.0, humidity=50.0, wind_speed=13.0, gust=21.0, rain=0.0)
        )
        self.assertIn("gusts", text)

    def test_reports_humidity_only_when_out_of_range(self) -> None:
        text = format_weather_summary(
            self._obs(temp=41.0, humidity=80.0, wind_speed=3.0, gust=5.0, rain=0.0)
        )
        self.assertIn("humid", text)

    def test_reports_high_humidity_when_temperature_is_warm(self) -> None:
        text = format_weather_summary(
            self._obs(temp=71.0, humidity=84.0, wind_speed=2.0, gust=4.0, rain=0.0)
        )
        self.assertIn("humidity is high", text)

    def test_reports_muggy_when_heat_and_humidity_are_oppressive(self) -> None:
        text = format_weather_summary(
            self._obs(temp=82.0, humidity=88.0, wind_speed=2.0, gust=4.0, rain=0.0)
        )
        self.assertIn("muggy", text)

    def test_summary_avoids_with_there_are_grammar(self) -> None:
        text = format_weather_summary(
            self._obs(temp=39.0, humidity=82.0, wind_speed=20.0, gust=28.0, rain=0.0)
        )
        self.assertNotIn("with there are", text)
        self.assertIn("there are gusty winds", text)

    def test_reports_chilly_when_temperature_is_cold(self) -> None:
        text = format_weather_summary(
            self._obs(temp=34.0, humidity=45.0, wind_speed=2.0, gust=4.0, rain=0.0)
        )
        self.assertIn("chilly", text)

    @patch("oracle_app.weather_current.fetch_weather_observation")
    @patch("oracle_app.weather_current.fetch_weather_forecast")
    def test_field_specific_wind_response_uses_current_details(self, mock_fetch_forecast, mock_fetch_observation) -> None:
        mock_fetch_forecast.return_value = {"periods": []}
        mock_fetch_observation.return_value = self._obs(
            temp=60.0,
            humidity=60.0,
            wind_speed=7.0,
            gust=12.0,
            rain=0.0,
        )

        speech, details = build_weather_response("what is the wind")

        self.assertIn("wind is out of the east", speech)
        self.assertEqual(details["field"], "wind")
        self.assertEqual(details["mode"], "field")

    @patch("oracle_app.weather_current.fetch_weather_observation")
    @patch("oracle_app.weather_current.fetch_weather_forecast")
    def test_full_current_weather_response_lists_meaningful_fields(self, mock_fetch_forecast, mock_fetch_observation) -> None:
        mock_fetch_forecast.return_value = {"periods": []}
        mock_fetch_observation.return_value = self._obs(
            temp=60.0,
            humidity=82.0,
            wind_speed=4.0,
            gust=6.0,
            rain=0.0,
        )

        speech, details = build_weather_response("full current weather")

        self.assertIn("Current weather for Test", speech)
        self.assertIn("Humidity is 82 percent", speech)
        self.assertIn("The barometer is 30.12 inches of mercury", speech)
        self.assertEqual(details["mode"], "full")

    @patch("oracle_app.weather_current.fetch_weather_observation")
    @patch("oracle_app.weather_current.fetch_weather_forecast")
    def test_summary_can_append_single_forecast_hint(self, mock_fetch_forecast, mock_fetch_observation) -> None:
        mock_fetch_observation.return_value = self._obs(
            temp=58.0,
            humidity=82.0,
            wind_speed=2.0,
            gust=4.0,
            rain=0.0,
        )
        mock_fetch_forecast.return_value = {
            "periods": [
                self._period(
                    name="Tonight",
                    start="2026-03-14T18:00:00-04:00",
                    end="2026-03-15T06:00:00-04:00",
                    is_daytime=False,
                    temp=42,
                    short="Rain Showers",
                )
            ]
        }

        speech, details = build_weather_response("what is the weather")

        self.assertIn("Rain is expected tonight.", speech)
        self.assertEqual(details["mode"], "summary")

    @patch("oracle_app.weather_current.fetch_weather_observation")
    @patch("oracle_app.weather_current.fetch_weather_forecast")
    def test_summary_can_append_single_temperature_trend_hint(self, mock_fetch_forecast, mock_fetch_observation) -> None:
        mock_fetch_observation.return_value = self._obs(
            temp=60.0,
            humidity=55.0,
            wind_speed=2.0,
            gust=4.0,
            rain=0.0,
        )
        mock_fetch_forecast.return_value = {
            "periods": [
                self._period(
                    name="Tonight",
                    start="2026-03-14T18:00:00-04:00",
                    end="2026-03-15T06:00:00-04:00",
                    is_daytime=False,
                    temp=42,
                    short="Mostly Cloudy",
                )
            ]
        }

        speech, _ = build_weather_response("what is the weather")

        self.assertIn("The temperature is expected to drop tonight.", speech)

    @patch("oracle_app.weather_current.fetch_weather_observation")
    @patch("oracle_app.weather_current.fetch_weather_forecast")
    def test_summary_can_append_generic_forecast_condition_hint(self, mock_fetch_forecast, mock_fetch_observation) -> None:
        mock_fetch_observation.return_value = self._obs(
            temp=57.9,
            humidity=55.0,
            wind_speed=2.0,
            gust=4.0,
            rain=0.0,
        )
        mock_fetch_forecast.return_value = {
            "periods": [
                self._period(
                    name="Tonight",
                    start="2026-03-14T18:00:00-04:00",
                    end="2026-03-15T06:00:00-04:00",
                    is_daytime=False,
                    temp=50,
                    short="Mostly Cloudy",
                )
            ]
        }

        speech, _ = build_weather_response("what is the weather")

        self.assertIn("Expect mostly cloudy conditions tonight.", speech)

    def test_forecast_summary_for_generic_forecast(self) -> None:
        periods = [
            self._period(
                name="Tonight",
                start="2026-03-14T18:00:00-04:00",
                end="2026-03-15T06:00:00-04:00",
                is_daytime=False,
                temp=42,
                short="Mostly Clear",
            ),
            self._period(
                name="Sunday",
                start="2026-03-15T06:00:00-04:00",
                end="2026-03-15T18:00:00-04:00",
                is_daytime=True,
                temp=58,
                short="Sunny",
            ),
        ]
        text = format_forecast_summary("what is the forecast", periods)
        self.assertIn("Tonight will be mostly clear", text)
        self.assertIn("Sunday will be sunny", text)

    @patch("oracle_app.weather_forecast.datetime")
    def test_forecast_summary_for_tomorrow(self, mock_datetime) -> None:
        mock_datetime.now.return_value = datetime.fromisoformat("2026-03-14T19:30:00-04:00")
        periods = [
            self._period(
                name="Tonight",
                start="2026-03-14T18:00:00-04:00",
                end="2026-03-15T06:00:00-04:00",
                is_daytime=False,
                temp=42,
                short="Mostly Clear",
            ),
            self._period(
                name="Sunday",
                start="2026-03-15T06:00:00-04:00",
                end="2026-03-15T18:00:00-04:00",
                is_daytime=True,
                temp=58,
                short="Sunny",
            ),
            self._period(
                name="Sunday Night",
                start="2026-03-15T18:00:00-04:00",
                end="2026-03-16T06:00:00-04:00",
                is_daytime=False,
                temp=39,
                short="Partly Cloudy",
            ),
        ]
        text = format_forecast_summary("what is the weather tomorrow", periods)
        self.assertIn("Tomorrow will be sunny", text)
        self.assertIn("Tomorrow night will be partly cloudy", text)

    @patch("oracle_app.weather_forecast.datetime")
    def test_forecast_summary_for_named_weekday(self, mock_datetime) -> None:
        mock_datetime.now.return_value = datetime.fromisoformat("2026-03-14T19:30:00-04:00")
        periods = [
            self._period(
                name="Tonight",
                start="2026-03-14T18:00:00-04:00",
                end="2026-03-15T06:00:00-04:00",
                is_daytime=False,
                temp=42,
                short="Mostly Clear",
            ),
            self._period(
                name="Sunday",
                start="2026-03-15T06:00:00-04:00",
                end="2026-03-15T18:00:00-04:00",
                is_daytime=True,
                temp=58,
                short="Sunny",
            ),
            self._period(
                name="Tuesday",
                start="2026-03-17T06:00:00-04:00",
                end="2026-03-17T18:00:00-04:00",
                is_daytime=True,
                temp=51,
                short="Rain Showers",
            ),
            self._period(
                name="Tuesday Night",
                start="2026-03-17T18:00:00-04:00",
                end="2026-03-18T06:00:00-04:00",
                is_daytime=False,
                temp=39,
                short="Mostly Cloudy",
            ),
        ]
        text = format_forecast_summary("what is the weather on tuesday", periods)
        self.assertIn("Tuesday will be rain showers", text)
        self.assertIn("Tuesday Night will be mostly cloudy", text)

    @patch("oracle_app.weather_forecast.datetime")
    def test_forecast_summary_for_this_weekday_night(self, mock_datetime) -> None:
        mock_datetime.now.return_value = datetime.fromisoformat("2026-03-14T19:30:00-04:00")
        periods = [
            self._period(
                name="Tuesday",
                start="2026-03-17T06:00:00-04:00",
                end="2026-03-17T18:00:00-04:00",
                is_daytime=True,
                temp=51,
                short="Rain Showers",
            ),
            self._period(
                name="Tuesday Night",
                start="2026-03-17T18:00:00-04:00",
                end="2026-03-18T06:00:00-04:00",
                is_daytime=False,
                temp=39,
                short="Mostly Cloudy",
            ),
        ]
        text = format_forecast_summary("what is the weather this tuesday night", periods)
        self.assertEqual(text, "Tuesday Night will be mostly cloudy with a low near 39.")

    @patch("oracle_app.weather_forecast.datetime")
    def test_forecast_summary_for_this_coming_weekday(self, mock_datetime) -> None:
        mock_datetime.now.return_value = datetime.fromisoformat("2026-03-14T19:30:00-04:00")
        periods = [
            self._period(
                name="Tuesday",
                start="2026-03-17T06:00:00-04:00",
                end="2026-03-17T18:00:00-04:00",
                is_daytime=True,
                temp=51,
                short="Rain Showers",
            ),
            self._period(
                name="Next Tuesday",
                start="2026-03-24T06:00:00-04:00",
                end="2026-03-24T18:00:00-04:00",
                is_daytime=True,
                temp=62,
                short="Sunny",
            ),
        ]
        text = format_forecast_summary("what is the weather this coming tuesday", periods)
        self.assertIn("Tuesday will be rain showers", text)

    @patch("oracle_app.weather_forecast.datetime")
    def test_forecast_summary_for_next_weekday_uses_next_upcoming_day(self, mock_datetime) -> None:
        mock_datetime.now.return_value = datetime.fromisoformat("2026-03-14T19:30:00-04:00")
        periods = [
            self._period(
                name="Tuesday",
                start="2026-03-17T06:00:00-04:00",
                end="2026-03-17T18:00:00-04:00",
                is_daytime=True,
                temp=51,
                short="Rain Showers",
            ),
            self._period(
                name="Tuesday Night",
                start="2026-03-17T18:00:00-04:00",
                end="2026-03-18T06:00:00-04:00",
                is_daytime=False,
                temp=39,
                short="Mostly Cloudy",
            ),
            self._period(
                name="Next Tuesday",
                start="2026-03-24T06:00:00-04:00",
                end="2026-03-24T18:00:00-04:00",
                is_daytime=True,
                temp=62,
                short="Sunny",
            ),
            self._period(
                name="Next Tuesday Night",
                start="2026-03-24T18:00:00-04:00",
                end="2026-03-25T06:00:00-04:00",
                is_daytime=False,
                temp=44,
                short="Partly Cloudy",
            ),
        ]
        text = format_forecast_summary("what is the weather next tuesday", periods)
        self.assertIn("Tuesday will be rain showers", text)
        self.assertIn("Tuesday Night will be mostly cloudy", text)

    @patch("oracle_app.weather_forecast.datetime")
    def test_weekend_forecast_prefers_current_weekend_context(self, mock_datetime) -> None:
        mock_datetime.now.return_value = datetime.fromisoformat("2026-03-14T19:30:00-04:00")
        periods = [
            self._period(
                name="Tonight",
                start="2026-03-14T19:00:00-04:00",
                end="2026-03-15T06:00:00-04:00",
                is_daytime=False,
                temp=24,
                short="Mostly Cloudy",
            ),
            self._period(
                name="Sunday",
                start="2026-03-15T06:00:00-04:00",
                end="2026-03-15T18:00:00-04:00",
                is_daytime=True,
                temp=47,
                short="Partly Sunny",
            ),
            self._period(
                name="Sunday Night",
                start="2026-03-15T18:00:00-04:00",
                end="2026-03-16T06:00:00-04:00",
                is_daytime=False,
                temp=39,
                short="Rain Showers",
            ),
        ]
        text = format_forecast_summary("what is the weather this weekend", periods)
        self.assertIn("For the rest of the weekend", text)
        self.assertIn("tonight will be mostly cloudy", text)
        self.assertIn("tomorrow will be partly sunny", text)

    @patch("oracle_app.weather_forecast.datetime")
    def test_weekend_forecast_uses_this_afternoon_for_active_saturday_day_period(self, mock_datetime) -> None:
        mock_datetime.now.return_value = datetime.fromisoformat("2026-03-14T15:30:00-04:00")
        periods = [
            self._period(
                name="This Afternoon",
                start="2026-03-14T15:00:00-04:00",
                end="2026-03-14T18:00:00-04:00",
                is_daytime=True,
                temp=60,
                short="Mostly Cloudy",
            ),
            self._period(
                name="Tonight",
                start="2026-03-14T18:00:00-04:00",
                end="2026-03-15T06:00:00-04:00",
                is_daytime=False,
                temp=39,
                short="Rain Showers",
            ),
            self._period(
                name="Sunday",
                start="2026-03-15T06:00:00-04:00",
                end="2026-03-15T18:00:00-04:00",
                is_daytime=True,
                temp=52,
                short="Partly Sunny",
            ),
        ]
        text = format_forecast_summary("what is the weather this weekend", periods)
        self.assertIn("this afternoon will be mostly cloudy", text)


if __name__ == "__main__":
    unittest.main()
