from __future__ import annotations

from decimal import Decimal

from oracle_app.deterministic_values import parse_duration, parse_number, parse_ordinal


def test_parse_number_accepts_literals_spoken_numbers_and_fractions() -> None:
    assert parse_number("-12.5") == Decimal("-12.5")
    assert parse_number("two hundred and forty six") == Decimal(246)
    assert parse_number("one thousand five") == Decimal(1005)
    assert parse_number("negative three point oh five") == Decimal("-3.05")
    assert parse_number("negative 12") == Decimal("-12")
    assert parse_number("twelve hundred") == Decimal("1200")
    assert parse_number("one and a half") == Decimal("1.5")
    assert parse_number("three quarters") == Decimal("0.75")
    assert parse_number("half a") == Decimal("0.5")
    assert parse_number("quarter of a") == Decimal("0.25")


def test_parse_number_rejects_unknown_partial_or_malformed_phrases() -> None:
    assert parse_number("square root of 16") is None
    assert parse_number("twelve miles") is None
    assert parse_number("and five") is None
    assert parse_number("five and") is None
    assert parse_number("one point twenty") is None
    assert parse_number("twenty nineteen") is None
    assert parse_number("one thousand one million") is None
    assert parse_number("") is None


def test_parse_ordinal_accepts_bounded_literal_and_spoken_forms() -> None:
    assert parse_ordinal("1st") == 1
    assert parse_ordinal("twenty-first") == 21
    assert parse_ordinal("one hundred and fifth") == 105
    assert parse_ordinal("one thousandth") == 1000


def test_parse_ordinal_rejects_cardinals_and_malformed_forms() -> None:
    assert parse_ordinal("twenty one") is None
    assert parse_ordinal("0th") is None
    assert parse_ordinal("first place") is None


def test_parse_duration_accepts_compound_and_fractional_units() -> None:
    parsed = parse_duration("for one hour and five minutes")
    assert parsed is not None
    assert parsed.seconds == 3900
    assert [(item.value, item.unit) for item in parsed.components] == [
        (Decimal(1), "hour"),
        (Decimal(5), "minute"),
    ]

    fractional = parse_duration("one and a half hours")
    assert fractional is not None
    assert fractional.seconds == 5400

    assert parse_duration("an hour").seconds == 3600
    assert parse_duration("half an hour").seconds == 1800
    assert parse_duration("one hour and a half").seconds == 5400
    assert parse_duration("a quarter of an hour").seconds == 900


def test_parse_duration_rejects_unknown_leftovers_and_policy_violations() -> None:
    assert parse_duration("five minutes please") is None
    assert parse_duration("minutes") is None
    assert parse_duration("zero minutes") is None
    assert parse_duration("minus five minutes") is None
    assert parse_duration("two hours", max_seconds=3600) is None


def test_parse_duration_zero_requires_explicit_policy() -> None:
    parsed = parse_duration("zero seconds", allow_zero=True)
    assert parsed is not None
    assert parsed.seconds == 0


def test_parse_duration_has_stable_boundaries_across_integer_minutes() -> None:
    for minutes in range(1, 121):
        parsed = parse_duration(f"{minutes} minutes", max_seconds=7_200)
        assert parsed is not None
        assert parsed.seconds == minutes * 60

    assert parse_duration("121 minutes", max_seconds=7_200) is None
