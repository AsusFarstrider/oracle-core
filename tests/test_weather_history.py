from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.weather_history import build_historical_weather_response, parse_historical_weather_query


class WeatherHistoryTests(unittest.TestCase):
    def test_parse_historical_weather_query_for_yesterday(self) -> None:
        parsed = parse_historical_weather_query(
            "what was the weather yesterday",
            now=datetime.fromisoformat("2026-04-04T12:00:00-04:00"),
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.target_date.isoformat(), "2026-04-03")
        self.assertIsNone(parsed.field)

    def test_parse_historical_weather_query_for_explicit_date_field(self) -> None:
        parsed = parse_historical_weather_query(
            "what was the humidity on april 2 2026",
            now=datetime.fromisoformat("2026-04-04T12:00:00-04:00"),
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.target_date.isoformat(), "2026-04-02")
        self.assertEqual(parsed.field, "humidity")

    @patch("oracle_app.weather_history._load_static_history_entry")
    def test_build_historical_weather_summary_from_static_json(self, mock_load_static_history_entry) -> None:
        mock_load_static_history_entry.return_value = {
            "date": "2026-04-03",
            "temperature_min_f": 42.4,
            "temperature_max_f": 63.3,
            "temperature_avg_f": 50.8,
            "humidity_min_pct": 80.0,
            "humidity_max_pct": 99.0,
            "humidity_avg_pct": 93.3,
            "wind_max_mph": 10.65,
            "wind_avg_mph": 3.43,
            "wind_gust_max_mph": 18.3,
            "rain_total_in": 0.26,
            "rain_rate_max_in_h": 0.071,
            "pressure_min_inhg": 30.09,
            "pressure_max_inhg": 30.31,
            "pressure_avg_inhg": 30.19,
        }

        speech, details = build_historical_weather_response(
            "what was the weather yesterday",
            now=datetime.fromisoformat("2026-04-04T12:00:00-04:00"),
        )

        self.assertIn("Friday, April 3, 2026", speech)
        self.assertEqual(details["date"], "2026-04-03")
        self.assertIsNone(details["field"])

    @patch("oracle_app.weather_history._query_day_row")
    def test_build_historical_weather_summary_from_sql_fallback(self, mock_query_day_row) -> None:
        with patch("oracle_app.weather_history._load_static_history_entry", return_value=None):
            mock_query_day_row.side_effect = [
                {"min": 42.4, "max": 63.3, "wsum": 4358751.0, "sumtime": 85800},
                {"min": 80.0, "max": 99.0, "wsum": 8006490.0, "sumtime": 85800},
                {"min": 0.0, "max": 10.65, "wsum": 294496.5, "sumtime": 85800},
                {"min": 0.0, "max": 20.6, "wsum": 0.0, "sumtime": 85800},
                {"min": 0.0, "max": 0.071, "sum": 0.26, "wsum": 0.0, "sumtime": 85500},
                {"min": 29.9, "max": 30.3, "wsum": 2583435.0, "sumtime": 85800},
            ]

            speech, details = build_historical_weather_response(
                "what was the weather yesterday",
                now=datetime.fromisoformat("2026-04-04T12:00:00-04:00"),
            )

        self.assertIn("Friday, April 3, 2026", speech)
        self.assertIn("42 to 63 degrees", speech)
        self.assertIn("0.26 inches of rain", speech)
        self.assertEqual(details["date"], "2026-04-03")

    @patch("oracle_app.weather_history._query_day_row")
    def test_build_historical_weather_rain_field_from_sql_fallback(self, mock_query_day_row) -> None:
        with patch("oracle_app.weather_history._load_static_history_entry", return_value=None):
            mock_query_day_row.side_effect = [
                {"min": 42.4, "max": 63.3, "wsum": 4358751.0, "sumtime": 85800},
                {"min": 80.0, "max": 99.0, "wsum": 8006490.0, "sumtime": 85800},
                {"min": 0.0, "max": 10.65, "wsum": 294496.5, "sumtime": 85800},
                {"min": 0.0, "max": 20.6, "wsum": 0.0, "sumtime": 85800},
                {"min": 0.0, "max": 0.071, "sum": 0.26, "wsum": 0.0, "sumtime": 85500},
                {"min": 29.9, "max": 30.3, "wsum": 2583435.0, "sumtime": 85800},
            ]

            speech, details = build_historical_weather_response(
                "how much rain did we get yesterday",
                now=datetime.fromisoformat("2026-04-04T12:00:00-04:00"),
            )

        self.assertIn("0.26 inches of rain", speech)
        self.assertEqual(details["field"], "rain")


if __name__ == "__main__":
    unittest.main()
