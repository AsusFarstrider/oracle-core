from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.weather_models import ForecastPeriod, ResolvedRemoteLocation
from oracle_app.weather_remote import (
    RemoteWeatherLocationError,
    build_remote_current_weather_response,
    build_remote_forecast_response,
    parse_remote_current_weather_query,
    parse_remote_forecast_query,
)


class RemoteWeatherTests(unittest.TestCase):
    def test_parse_remote_current_weather_query_summary(self) -> None:
        query = parse_remote_current_weather_query("what is the weather in boston")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.current_query.mode, "summary")

    def test_parse_remote_current_weather_query_field(self) -> None:
        query = parse_remote_current_weather_query("what is the humidity in chicago")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "chicago")
        self.assertEqual(query.current_query.mode, "field")
        self.assertEqual(query.current_query.field, "humidity")

    def test_parse_remote_current_weather_query_rejects_forecast_language(self) -> None:
        self.assertIsNone(parse_remote_current_weather_query("what is the weather tomorrow in boston"))

    def test_parse_remote_forecast_query_matches_location_forecast(self) -> None:
        query = parse_remote_forecast_query("what is the weather tomorrow in boston")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather tomorrow")

    def test_parse_remote_forecast_query_matches_weekday_location_forecast(self) -> None:
        query = parse_remote_forecast_query("what is the weather on tuesday in boston")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather on tuesday")

    def test_parse_remote_forecast_query_matches_weekday_night_location_forecast(self) -> None:
        query = parse_remote_forecast_query("what is the weather tuesday night in boston")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather tuesday night")

    def test_parse_remote_forecast_query_matches_location_first_weekday_forecast(self) -> None:
        query = parse_remote_forecast_query("what is the weather in boston on tuesday")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather on tuesday")

    def test_parse_remote_forecast_query_matches_location_first_weekday_night_forecast(self) -> None:
        query = parse_remote_forecast_query("what is the weather in boston tuesday night")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather tuesday night")

    def test_parse_remote_forecast_query_matches_location_first_this_weekday_forecast(self) -> None:
        query = parse_remote_forecast_query("what is the weather in boston this tuesday")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather this tuesday")

    def test_parse_remote_forecast_query_matches_forecast_for_location_shape(self) -> None:
        query = parse_remote_forecast_query("forecast for boston on tuesday")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather on tuesday")

    def test_parse_remote_forecast_query_matches_location_prefixed_forecast_for_shape(self) -> None:
        query = parse_remote_forecast_query("boston forecast for tuesday")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather tuesday")

    def test_parse_remote_forecast_query_still_matches_short_location_text(self) -> None:
        query = parse_remote_forecast_query("forecast for la on tuesday")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "la")

    def test_parse_remote_forecast_query_matches_practical_coat_phrase(self) -> None:
        query = parse_remote_forecast_query("do i need a coat in boston tomorrow")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather tomorrow")

    def test_parse_remote_forecast_query_matches_practical_umbrella_phrase(self) -> None:
        query = parse_remote_forecast_query("should i bring an umbrella in boston tomorrow")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather tomorrow")

    def test_parse_remote_forecast_query_matches_location_first_weather_be_shape(self) -> None:
        query = parse_remote_forecast_query("what will boston weather be tomorrow")

        self.assertIsNotNone(query)
        assert query is not None
        self.assertEqual(query.location_text, "boston")
        self.assertEqual(query.forecast_text, "what is the weather tomorrow")

    def test_parse_remote_forecast_query_rejects_location_made_of_speech_disfluency(self) -> None:
        self.assertIsNone(parse_remote_forecast_query("what it is is the weather tomorrow"))

    def test_parse_remote_forecast_query_rejects_shorter_grammar_fragment(self) -> None:
        self.assertIsNone(parse_remote_forecast_query("what is is the weather tomorrow"))

    @patch("oracle_app.weather_remote._fetch_station_observation")
    @patch("oracle_app.weather_remote._resolve_remote_location")
    def test_build_remote_current_weather_response_prefixes_location(
        self,
        mock_resolve_remote_location,
        mock_fetch_station_observation,
    ) -> None:
        mock_resolve_remote_location.return_value = ResolvedRemoteLocation(
            query_text="boston",
            label="Boston, Massachusetts",
            latitude=42.3588,
            longitude=-71.0578,
            city="Boston",
            state="Massachusetts",
            country="United States",
        )
        mock_fetch_station_observation.return_value = (
            {
                "properties": {
                    "relativeLocation": {
                        "properties": {
                            "city": "Boston",
                            "state": "MA",
                        }
                    }
                }
            },
            {
                "properties": {
                    "timestamp": "2026-04-04T20:35:00+00:00",
                    "stationId": "KBOS",
                    "stationName": "Boston, Logan International Airport",
                    "temperature": {"value": 4.0},
                    "relativeHumidity": {"value": 80.8},
                    "windDirection": {"value": 80.0},
                    "windSpeed": {"value": 35.172},
                    "windGust": {"value": None},
                    "barometricPressure": {"value": 102946.21},
                    "textDescription": "Cloudy and Windy",
                }
            },
        )

        speech, details = build_remote_current_weather_response("what is the weather in boston")

        self.assertTrue(speech.startswith("In Boston, MA, "))
        self.assertEqual(details["location"], "Boston, MA")
        self.assertEqual(details["station_id"], "KBOS")
        self.assertEqual(details["source_type"], "nws_observation")
        self.assertEqual(details["mode"], "summary")

    @patch("oracle_app.weather_remote._resolve_remote_location")
    def test_build_remote_current_weather_response_raises_for_unresolved_location(
        self,
        mock_resolve_remote_location,
    ) -> None:
        mock_resolve_remote_location.side_effect = RemoteWeatherLocationError("I couldn't resolve that location.")

        with self.assertRaises(RemoteWeatherLocationError):
            build_remote_current_weather_response("what is the weather in nowhere")

    def test_build_remote_current_weather_response_rejects_short_ambiguous_location(self) -> None:
        with self.assertRaises(RemoteWeatherLocationError):
            build_remote_current_weather_response("what is the weather in la")

    @patch("oracle_app.weather_remote._fetch_remote_forecast")
    @patch("oracle_app.weather_remote._resolve_remote_location")
    def test_build_remote_forecast_response_prefixes_location(
        self,
        mock_resolve_remote_location,
        mock_fetch_remote_forecast,
    ) -> None:
        eastern = timezone(timedelta(hours=-4))
        now = datetime.now(eastern)
        tomorrow_day_start = datetime.combine(
            now.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=eastern,
        )
        tomorrow_night_start = tomorrow_day_start - timedelta(hours=6)
        mock_resolve_remote_location.return_value = ResolvedRemoteLocation(
            query_text="boston",
            label="Boston, Massachusetts",
            latitude=42.3588,
            longitude=-71.0578,
            city="Boston",
            state="Massachusetts",
            country="United States",
        )
        mock_fetch_remote_forecast.return_value = {
            "location": "Boston",
            "state": "MA",
            "forecast_url": "https://api.weather.gov/gridpoints/BOX/71,90/forecast",
            "forecast_hourly_url": "https://api.weather.gov/gridpoints/BOX/71,90/forecast/hourly",
            "periods": [
                ForecastPeriod(
                    name="Tonight",
                    start_time=tomorrow_night_start,
                    end_time=tomorrow_day_start,
                    is_daytime=False,
                    temperature_f=39,
                    temperature_trend=None,
                    wind_speed="10 mph",
                    wind_direction="NW",
                    short_forecast="Mostly Clear",
                    detailed_forecast="Mostly Clear",
                ),
                ForecastPeriod(
                    name="Sunday",
                    start_time=tomorrow_day_start,
                    end_time=tomorrow_day_start + timedelta(hours=12),
                    is_daytime=True,
                    temperature_f=54,
                    temperature_trend=None,
                    wind_speed="12 mph",
                    wind_direction="NW",
                    short_forecast="Sunny",
                    detailed_forecast="Sunny",
                ),
            ],
        }

        speech, details = build_remote_forecast_response("what is the weather tomorrow in boston")

        self.assertTrue(speech.startswith("In Boston, MA, "))
        self.assertIn("Tomorrow will be sunny", speech)
        self.assertEqual(details["location"], "Boston, MA")
        self.assertEqual(details["source_type"], "nws_forecast")
        self.assertEqual(len(details["selected_periods"]), 1)


if __name__ == "__main__":
    unittest.main()
