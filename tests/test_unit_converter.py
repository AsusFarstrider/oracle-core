from __future__ import annotations

from decimal import Decimal

import pytest

from oracle_app.unit_converter import (
    UNIT_CATALOG,
    build_conversion_response,
    is_conversion_query,
    parse_conversion_query,
)


def _convert(text: str, *, context: dict | None = None) -> tuple[str, dict]:
    return build_conversion_response(text, conversion_context=context)


def _context(details: dict) -> dict:
    return {
        "payload": {
            "value": details["base_value"],
            "dimension": details["dimension"],
            "source_unit": details["source_unit"],
            "target_unit": details["target_unit"],
            "display_text": details["display_text"],
        }
    }


def test_catalog_covers_every_ratified_dimension_with_unique_canonical_units() -> None:
    assert set(unit.dimension for unit in UNIT_CATALOG.values()) == {
        "area",
        "data",
        "data_rate",
        "energy",
        "fuel_economy",
        "length",
        "mass",
        "power",
        "pressure",
        "speed",
        "temperature",
        "time",
        "volume",
    }
    assert len(UNIT_CATALOG) == 107


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("how many feet are in 4 miles", "4 miles is 21,120 feet."),
        ("convert 72 inches to feet", "72 inches is 6 feet."),
        ("what's 150 pounds in kilograms", "150 pounds is about 68.04 kilograms."),
        ("what's 30 celsius in fahrenheit", "30 celsius is 86 fahrenheit."),
        ("how many gallons is 18 liters", "18 liters is about 4.76 gallons."),
        ("convert 60 mph to kilometers per hour", "60 miles per hour is about 96.56 kilometers per hour."),
        ("convert 1 acre to square feet", "1 acre is 43,560 square feet."),
        ("convert 1 cubic foot to gallons", "1 cubic foot is about 7.48 gallons."),
        ("convert 2 terabytes to gigabytes", "2 terabytes is 2,000 gigabytes."),
        ("convert 32 psi to kilopascals", "32 psi is about 221 kilopascals."),
        ("convert 1 horsepower to watts", "1 horsepower is about 746 watts."),
        ("convert 1 kilowatt hour to btu", "1 kilowatt hour is about 3,412 btu."),
    ],
)
def test_reference_conversions_across_linear_dimensions(utterance: str, expected: str) -> None:
    assert _convert(utterance)[0] == expected


def test_decimal_and_binary_data_units_remain_distinct() -> None:
    speech, details = _convert("convert 1 GiB to gigabytes")
    assert speech == "1 gibibyte is about 1.07 gigabytes."
    assert Decimal(details["output_value"]) == Decimal("1.073741824")

    rate_speech, rate = _convert("how many megabits per second is 125 MB/s")
    assert rate_speech == "125 megabytes per second is 1,000 megabits per second."
    assert rate["output_value"] == "1000"

    binary_rate_speech, binary_rate = _convert("convert 1 MiB/s to megabytes per second")
    assert binary_rate_speech == "1 mebibyte per second is about 1.05 megabytes per second."
    assert binary_rate["output_value"] == "1.048576"


def test_mixed_input_and_output_are_typed_and_human_useful() -> None:
    assert _convert("how many inches is 6 feet 5 inches")[0] == "6 feet 5 inches is 77 inches."
    assert _convert("convert 74 inches to feet and inches")[0] == "74 inches is 6 feet 2 inches."
    assert _convert("how many minutes is 2 hours and 15 minutes")[0] == "2 hours 15 minutes is 135 minutes."
    assert _convert("what's 150 minutes in hours and minutes")[0] == "150 minutes is 2 hours 30 minutes."
    assert _convert("how many ounces is 3 pounds 4 ounces")[0] == "3 pounds 4 ounces is 52 ounces."


def test_cooking_spoken_fractions_and_cross_volume_equivalents() -> None:
    assert _convert("what's one and a half cups in tablespoons")[0] == "1.5 cups is 24 tablespoons."
    assert _convert("how many teaspoons is half a cup")[0] == "0.5 cups is 24 teaspoons."
    assert _convert("convert 8 fluid ounces to cups")[0] == "8 fluid ounces is 1 cup."


def test_temperature_affine_reference_values_and_absolute_zero() -> None:
    assert _convert("convert 32 fahrenheit to celsius")[0] == "32 fahrenheit is 0 celsius."
    assert _convert("convert 0 celsius to kelvin")[0] == "0 celsius is about 273.2 kelvin."
    with pytest.raises(ValueError, match="absolute zero"):
        _convert("convert negative 300 celsius to fahrenheit")


def test_inverse_fuel_economy_round_trip() -> None:
    speech, details = _convert("convert 30 miles per gallon to liters per 100 kilometers")
    assert speech == "30 miles per gallon is about 7.84 liters per 100 kilometers."
    reverse_context = _context(details)
    reverse_speech, reverse = _convert("and miles per gallon", context=reverse_context)
    assert reverse_speech == "7.84 liters per 100 kilometers is 30 miles per gallon."
    assert Decimal(reverse["output_value"]) == Decimal(30)


def test_context_reuses_prior_source_or_target_without_scraping_reply_text() -> None:
    first_speech, first = _convert("what is 5 miles in kilometers")
    assert first_speech == "5 miles is about 8.05 kilometers."
    assert _convert("what about 12 miles", context=_context(first))[0] == "12 miles is about 19.31 kilometers."

    temperature_speech, temperature = _convert("convert 72 fahrenheit to celsius")
    assert temperature_speech == "72 fahrenheit is about 22.2 celsius."
    assert _convert("what about 80", context=_context(temperature))[0] == "80 fahrenheit is about 26.7 celsius."

    mixed_speech, mixed = _convert("what is 6 feet 5 inches in centimeters")
    assert mixed_speech == "6 feet 5 inches is about 196 centimeters."
    assert _convert("and meters", context=_context(mixed))[0] == "196 centimeters is about 1.96 meters."


def test_contextual_forms_require_compatible_live_context() -> None:
    with pytest.raises(ValueError, match="no longer available"):
        _convert("what about 12 miles")
    with pytest.raises(ValueError, match="no longer available"):
        _convert("and meters")

    _, length = _convert("convert 5 miles to kilometers")
    with pytest.raises(ValueError, match="incompatible"):
        _convert("and kilograms", context=_context(length))


def test_explicit_precision_is_bounded_and_does_not_change_exact_result() -> None:
    speech, details = _convert("convert 1 cup to liters with four decimal places")
    assert speech == "1 cup is 0.2366 liters."
    assert details["output_value"] == "0.2365882365"
    assert details["decimal_places"] == 4
    with pytest.raises(ValueError, match="zero through twelve"):
        _convert("convert 1 cup to liters with thirteen decimal places")


def test_incompatible_calendar_currency_and_unknown_requests_fail_honestly() -> None:
    with pytest.raises(ValueError, match="density"):
        _convert("convert 2 cups to grams")
    with pytest.raises(ValueError, match="calendar periods"):
        _convert("convert 1 month to days")
    with pytest.raises(ValueError, match="Live currency"):
        _convert("convert $100 to euros")
    with pytest.raises(ValueError, match="Unsupported conversion unit"):
        _convert("convert 2 widgets to meters")


def test_owner_probe_accepts_only_bounded_conversion_forms() -> None:
    for text in (
        "convert 10 miles to kilometers",
        "how many inches is 6 feet 5 inches",
        "what about 12 miles",
        "what about 80",
        "and meters",
        "convert 2 cups to grams",
        "convert $100 to euros",
    ):
        assert is_conversion_query(text)
    for text in (
        "what about the weather",
        "how many lights are in the kitchen",
        "set the thermostat to 72 degrees",
        "convert this vague thing",
    ):
        assert not is_conversion_query(text)


def test_parser_returns_typed_source_and_target_units() -> None:
    query = parse_conversion_query("convert 74 inches to feet and inches")
    assert query is not None
    assert [(component.value, component.unit.canonical) for component in query.source_components] == [
        (Decimal(74), "inches")
    ]
    assert [unit.canonical for unit in query.target_units] == ["feet", "inches"]


@pytest.mark.parametrize(
    ("value", "source", "target"),
    [
        ("3.25", "miles", "kilometers"),
        ("2", "acres", "hectares"),
        ("4.5", "gallons", "liters"),
        ("175", "pounds", "kilograms"),
        ("55", "miles per hour", "meters per second"),
        ("2.75", "hours", "minutes"),
        ("3", "gibibytes", "gigabytes"),
        ("125", "megabytes per second", "megabits per second"),
        ("32", "psi", "bar"),
        ("3", "horsepower", "kilowatts"),
        ("4", "kilowatt hours", "btu"),
        ("72", "fahrenheit", "celsius"),
        ("30", "miles per gallon", "liters per 100 kilometers"),
    ],
)
def test_exact_internal_values_round_trip_across_every_dimension(
    value: str,
    source: str,
    target: str,
) -> None:
    _, forward = _convert(f"convert {value} {source} to {target}")
    _, reverse = _convert(f"convert {forward['output_value']} {target} to {source}")
    assert abs(Decimal(reverse["output_value"]) - Decimal(value)) < Decimal("1e-20")


def test_linear_round_trip_property_across_bounded_household_values() -> None:
    for miles in range(1, 51):
        _, forward = _convert(f"convert {miles} miles to kilometers")
        _, reverse = _convert(f"convert {forward['output_value']} kilometers to miles")
        assert Decimal(reverse["output_value"]) == Decimal(miles)
