from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.calendar import CalendarEvent
from oracle_app.calculations import build_calculation_response, parse_conversion_query
from oracle_app.math_calculator import build_math_response, is_math_query


class CalculationTests(unittest.TestCase):
    def test_basic_math(self) -> None:
        speech, details = build_calculation_response("what is 12 divided by 3")
        self.assertEqual(speech, "4.")
        self.assertEqual(details["kind"], "math")

    def test_symbol_math(self) -> None:
        speech, details = build_calculation_response("calculate 7 * (4 + 2)")
        self.assertEqual(speech, "42.")
        self.assertEqual(details["expression"], "7 * (4 + 2)")

    def test_temperature_conversion(self) -> None:
        speech, details = build_calculation_response("what is 72 fahrenheit in celsius")
        self.assertEqual(speech, "72 fahrenheit is about 22.2 celsius.")
        self.assertEqual(details["output_unit"], "celsius")

    def test_distance_conversion(self) -> None:
        speech, details = build_calculation_response("convert 10 miles to kilometers")
        self.assertEqual(speech, "10 miles is about 16.09 kilometers.")
        self.assertEqual(details["output_unit"], "kilometers")

    def test_how_many_conversion(self) -> None:
        parsed = parse_conversion_query("how many feet is 2 meters")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.source_components[0].value, 2)
        self.assertEqual(parsed.source_components[0].unit.canonical, "meters")
        self.assertEqual(parsed.target_units[0].canonical, "feet")

    def test_speed_conversion(self) -> None:
        speech, details = build_calculation_response("convert 60 mph to kilometers per hour")
        self.assertEqual(speech, "60 miles per hour is about 96.56 kilometers per hour.")
        self.assertEqual(details["output_unit"], "kilometers per hour")

    def test_weight_conversion(self) -> None:
        speech, details = build_calculation_response("convert 16 ounces to grams")
        self.assertEqual(speech, "16 ounces is about 454 grams.")
        self.assertEqual(details["output_unit"], "grams")

    def test_area_conversion(self) -> None:
        speech, details = build_calculation_response("convert 100 square feet to square meters")
        self.assertEqual(speech, "100 square feet is about 9.29 square meters.")
        self.assertEqual(details["output_unit"], "square meters")

    def test_volume_conversion(self) -> None:
        speech, details = build_calculation_response("convert 2 quarts to liters")
        self.assertEqual(speech, "2 quarts is about 1.89 liters.")
        self.assertEqual(details["output_unit"], "liters")

    def test_teaspoons_to_tablespoons(self) -> None:
        speech, details = build_calculation_response("convert 3 teaspoons to tablespoons")
        self.assertEqual(speech, "3 teaspoons is 1 tablespoon.")
        self.assertEqual(details["output_unit"], "tablespoons")

    def test_tablespoons_to_milliliters(self) -> None:
        speech, details = build_calculation_response("convert 2 tbsp to milliliters")
        self.assertEqual(speech, "2 tablespoons is about 29.57 milliliters.")
        self.assertEqual(details["output_unit"], "milliliters")

    def test_fluid_ounces_to_cups(self) -> None:
        speech, details = build_calculation_response("convert 8 fluid ounces to cups")
        self.assertEqual(speech, "8 fluid ounces is 1 cup.")
        self.assertEqual(details["output_unit"], "cups")

    def test_cups_to_milliliters(self) -> None:
        speech, details = build_calculation_response("convert 1.5 cups to milliliters")
        self.assertEqual(speech, "1.5 cups is about 355 milliliters.")
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
        self.assertEqual(speech, "1 teaspoon is about 4.93 milliliters.")
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

    def test_days_until_holiday_uses_holiday_feed(self) -> None:
        execution = Mock()
        execution.load_events.return_value.value = [
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
            calendar_execution=execution,
        )

        self.assertEqual(speech, "There are 265 days until Christmas Day.")
        self.assertEqual(details["kind"], "date_until")
        self.assertEqual(details["date"], "2026-12-25")
        execution.load_events.assert_called_once_with(scope="holiday")

    def test_day_of_week_for_holiday_this_year(self) -> None:
        execution = Mock()
        execution.load_events.return_value.value = [
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
            calendar_execution=execution,
        )

        self.assertEqual(speech, "Thanksgiving Day is on a Thursday.")
        self.assertEqual(details["kind"], "date_weekday")
        self.assertEqual(details["weekday"], "Thursday")


if __name__ == "__main__":
    unittest.main()


def _math(text: str, *, context: dict | None = None) -> tuple[str, dict]:
    return build_math_response(text, math_context=context)


def test_stage6_math_arithmetic_spoken_numbers_and_precedence() -> None:
    assert _math("what's negative 12 times 7")[0] == "-84."
    assert _math("what is twelve hundred plus one thousand two hundred")[0] == "2400."
    assert _math("what is one point five plus two and a half")[0] == "4."
    assert _math("what's 5 plus 8 times 3")[0] == "29."
    assert _math("what's 5 plus 8, then multiply that by 3")[0] == "39."


def test_stage6_math_fractions_and_percentages() -> None:
    assert _math("what's half of 37")[0] == "18 1/2."
    assert _math("what's three quarters of 80")[0] == "60."
    assert _math("what's one half plus one quarter")[0] == "3/4."
    assert _math("what's 15% of 80")[0] == "15% of 80 is 12."
    assert _math("20 is what percent of 50")[0] == "20 is 40%."
    assert _math("increase 75 by 12%")[0] == "84."
    assert _math("decrease 100 by 30%")[0] == "70."
    assert _math("what's the percent change from 80 to 92")[0] == "The percent change is 15%."


def test_stage6_math_money_is_fixed_value_arithmetic_only() -> None:
    assert _math("what's a 20% tip on $73")[0] == "The tip is $14.60, making the total $87.60."
    assert _math("what's $85 with 6% sales tax")[0] == "The sales tax is $5.10, making the total $90.10."
    assert _math("what's 30% off $120")[0] == "The discounted price is $84.00, saving $36.00."
    assert _math("split $96 four ways")[0] == "$24.00."


def test_stage6_math_powers_roots_and_bounds() -> None:
    assert _math("what's 12 squared")[0] == "144."
    assert _math("what's 5 cubed")[0] == "125."
    assert _math("what's 2 to the tenth power")[0] == "1024."
    assert _math("what's the square root of 144")[0] == "12."
    assert _math("what's the cube root of 27")[0] == "3."
    with pytest.raises(ValueError, match="limited"):
        _math("what's 2 to the thirteenth power")
    with pytest.raises(ValueError, match="non-negative"):
        _math("what's the square root of negative one")


def test_stage6_math_aggregation_proportion_and_geometry() -> None:
    assert _math("what's the average of 82, 91, 74, and 88")[0] == "83.75."
    assert _math("add up 12, 17, 31, and 9")[0] == "69."
    assert _math("what's the median of 12, 17, 31, and 9")[0] == "14.5."
    assert _math("if i need 3 cups for 8 people, how much do i need for 12")[0] == "4.5 cups."
    assert _math("if 5 gallons treats 20,000 gallons, how much do i need for 12,000")[0] == "3 gallons."
    assert _math("what's the area of a 12-by-14 room")[0] == "168."
    assert _math("what's the circumference of a 27-foot circle")[0] == "84.823002 feet."
    assert _math("what's the area of a triangle with base 10 and height 4")[0] == "20."
    assert _math("what's the volume of a 2-by-3-by-4 box")[0] == "24."


def test_stage6_math_contextual_followup_and_rounding() -> None:
    context = {"payload": {"value": "216/5", "display_text": "43.2"}}
    assert _math("add that to 240", context=context)[0] == "283.2."
    assert _math("divide that by four", context=context)[0] == "10.8."
    assert _math("what if i divide it by 6 instead", context=context)[0] == "7.2."
    assert _math("round that to two decimal places", context=context)[0] == "43.20."
    assert _math("nearest whole number", context=context)[0] == "43."
    assert _math("scale this from four servings to six", context=context)[0] == "64.8."
    with pytest.raises(ValueError, match="no longer available"):
        _math("divide that by four")


def test_stage6_math_rejects_unknown_tokens_and_unsafe_ast() -> None:
    for text in (
        "what is 2 plus bananas 3",
        "what is __import__('os').system('id')",
        "what is [1, 2, 3]",
        "what is 2 // 1",
        "what is 2 plus 3 please",
    ):
        with pytest.raises((ValueError, SyntaxError)):
            _math(text)
        assert not is_math_query(text)


def test_stage6_math_structured_result_preserves_exact_value() -> None:
    speech, details = _math("what's one divided by three")
    assert speech == "0.333333."
    assert details == {
        "kind": "math",
        "operation": "arithmetic",
        "expression": "one divided by three",
        "value": "0.33333333333333333333",
        "exact_value": "1/3",
        "display_text": "0.333333",
    }
