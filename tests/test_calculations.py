from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.calendar import CalendarEvent
from oracle_app.calculations import build_calculation_response, parse_conversion_query


class CalculationTests(unittest.TestCase):
    def test_basic_math(self) -> None:
        speech, details = build_calculation_response("what is 12 divided by 3")
        self.assertEqual(speech, "The answer is 4.")
        self.assertEqual(details["kind"], "math")

    def test_symbol_math(self) -> None:
        speech, details = build_calculation_response("calculate 7 * (4 + 2)")
        self.assertEqual(speech, "The answer is 42.")
        self.assertEqual(details["expression"], "7 * (4 + 2)")

    def test_temperature_conversion(self) -> None:
        speech, details = build_calculation_response("what is 72 fahrenheit in celsius")
        self.assertEqual(speech, "72 fahrenheit is 22.2222 celsius.")
        self.assertEqual(details["output_unit"], "celsius")

    def test_distance_conversion(self) -> None:
        speech, details = build_calculation_response("convert 10 miles to kilometers")
        self.assertEqual(speech, "10 miles is 16.0934 kilometers.")
        self.assertEqual(details["output_unit"], "kilometers")

    def test_how_many_conversion(self) -> None:
        parsed = parse_conversion_query("how many feet is 2 meters")
        self.assertEqual(parsed, (2.0, "meters", "feet"))

    def test_speed_conversion(self) -> None:
        speech, details = build_calculation_response("convert 60 mph to kilometers per hour")
        self.assertEqual(speech, "60 miles per hour is 96.5606 kilometers per hour.")
        self.assertEqual(details["output_unit"], "kilometers per hour")

    def test_weight_conversion(self) -> None:
        speech, details = build_calculation_response("convert 16 ounces to grams")
        self.assertEqual(speech, "16 ounces is 453.5924 grams.")
        self.assertEqual(details["output_unit"], "grams")

    def test_area_conversion(self) -> None:
        speech, details = build_calculation_response("convert 100 square feet to square meters")
        self.assertEqual(speech, "100 square feet is 9.2903 square meters.")
        self.assertEqual(details["output_unit"], "square meters")

    def test_volume_conversion(self) -> None:
        speech, details = build_calculation_response("convert 2 quarts to liters")
        self.assertEqual(speech, "2 quarts is 1.8927 liters.")
        self.assertEqual(details["output_unit"], "liters")

    def test_teaspoons_to_tablespoons(self) -> None:
        speech, details = build_calculation_response("convert 3 teaspoons to tablespoons")
        self.assertEqual(speech, "3 teaspoons is 1 tablespoon.")
        self.assertEqual(details["output_unit"], "tablespoons")

    def test_tablespoons_to_milliliters(self) -> None:
        speech, details = build_calculation_response("convert 2 tbsp to milliliters")
        self.assertEqual(speech, "2 tablespoons is 29.5735 milliliters.")
        self.assertEqual(details["output_unit"], "milliliters")

    def test_fluid_ounces_to_cups(self) -> None:
        speech, details = build_calculation_response("convert 8 fluid ounces to cups")
        self.assertEqual(speech, "8 fluid ounces is 1 cup.")
        self.assertEqual(details["output_unit"], "cups")

    def test_cups_to_milliliters(self) -> None:
        speech, details = build_calculation_response("convert 1.5 cups to milliliters")
        self.assertEqual(speech, "1.5 cups is 354.8824 milliliters.")
        self.assertEqual(details["output_unit"], "milliliters")

    def test_pints_to_cups(self) -> None:
        speech, details = build_calculation_response("convert 1 pint to cups")
        self.assertEqual(speech, "1 pint is 2 cups.")
        self.assertEqual(details["output_unit"], "cups")

    def test_cups_to_tablespoons(self) -> None:
        speech, details = build_calculation_response("convert 0.5 cups to tablespoons")
        self.assertEqual(speech, "0.5 cups is 8 tablespoons.")
        self.assertEqual(details["output_unit"], "tablespoons")

    def test_teaspoons_to_milliliters(self) -> None:
        speech, details = build_calculation_response("convert 1 teaspoon to milliliters")
        self.assertEqual(speech, "1 teaspoon is 4.9289 milliliters.")
        self.assertEqual(details["output_unit"], "milliliters")

    def test_days_until_explicit_date(self) -> None:
        speech, details = build_calculation_response(
            "how many days until july 4",
            today=date(2026, 4, 4),
        )
        self.assertEqual(speech, "There are 91 days until July 4, 2026.")
        self.assertEqual(details["kind"], "date_until")
        self.assertEqual(details["date"], "2026-07-04")

    def test_days_since_explicit_date(self) -> None:
        speech, details = build_calculation_response(
            "how many days since march 30",
            today=date(2026, 4, 4),
        )
        self.assertEqual(speech, "It has been 5 days since March 30, 2026.")
        self.assertEqual(details["kind"], "date_since")
        self.assertEqual(details["days"], 5)

    @patch("oracle_app.calculations.load_calendar_events")
    def test_days_until_holiday_uses_holiday_feed(self, mock_load_calendar_events) -> None:
        mock_load_calendar_events.return_value = [
            CalendarEvent(
                uid="holiday-1",
                summary="Christmas Day",
                start=datetime.fromisoformat("2026-12-25T00:00:00-05:00"),
                end=datetime.fromisoformat("2026-12-26T00:00:00-05:00"),
                all_day=True,
                location="",
            )
        ]

        speech, details = build_calculation_response(
            "how many days until christmas",
            today=date(2026, 4, 4),
        )

        self.assertEqual(speech, "There are 265 days until Christmas Day.")
        self.assertEqual(details["kind"], "date_until")
        self.assertEqual(details["date"], "2026-12-25")
        mock_load_calendar_events.assert_called_once_with(scope="holiday")

    @patch("oracle_app.calculations.load_calendar_events")
    def test_day_of_week_for_holiday_this_year(self, mock_load_calendar_events) -> None:
        mock_load_calendar_events.return_value = [
            CalendarEvent(
                uid="holiday-2",
                summary="Thanksgiving Day",
                start=datetime.fromisoformat("2026-11-26T00:00:00-05:00"),
                end=datetime.fromisoformat("2026-11-27T00:00:00-05:00"),
                all_day=True,
                location="",
            )
        ]

        speech, details = build_calculation_response(
            "what day of the week is thanksgiving this year",
            today=date(2026, 4, 4),
        )

        self.assertEqual(speech, "Thanksgiving Day is on a Thursday.")
        self.assertEqual(details["kind"], "date_weekday")
        self.assertEqual(details["weekday"], "Thursday")


if __name__ == "__main__":
    unittest.main()
