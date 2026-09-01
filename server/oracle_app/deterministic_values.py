from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


_SMALL_NUMBERS = {
    "zero": 0,
    "oh": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_LARGE_SCALES = {"thousand": 1_000, "million": 1_000_000}
_DIGIT_WORDS = {key: str(value) for key, value in _SMALL_NUMBERS.items() if value < 10}
_FRACTIONS = {
    "half": Decimal("0.5"),
    "a half": Decimal("0.5"),
    "half a": Decimal("0.5"),
    "half an": Decimal("0.5"),
    "one half": Decimal("0.5"),
    "quarter": Decimal("0.25"),
    "a quarter": Decimal("0.25"),
    "quarter of a": Decimal("0.25"),
    "quarter of an": Decimal("0.25"),
    "one quarter": Decimal("0.25"),
    "three quarters": Decimal("0.75"),
}
_DURATION_UNITS = {
    "second": Decimal(1),
    "seconds": Decimal(1),
    "minute": Decimal(60),
    "minutes": Decimal(60),
    "hour": Decimal(3_600),
    "hours": Decimal(3_600),
    "day": Decimal(86_400),
    "days": Decimal(86_400),
    "week": Decimal(604_800),
    "weeks": Decimal(604_800),
}
_NUMBER_LITERAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_ORDINAL_LITERAL_RE = re.compile(r"^(\d+)(?:st|nd|rd|th)$")
_ORDINAL_WORDS = {
    "first": "one",
    "second": "two",
    "third": "three",
    "fourth": "four",
    "fifth": "five",
    "sixth": "six",
    "seventh": "seven",
    "eighth": "eight",
    "ninth": "nine",
    "tenth": "ten",
    "eleventh": "eleven",
    "twelfth": "twelve",
    "thirteenth": "thirteen",
    "fourteenth": "fourteen",
    "fifteenth": "fifteen",
    "sixteenth": "sixteen",
    "seventeenth": "seventeen",
    "eighteenth": "eighteen",
    "nineteenth": "nineteen",
    "twentieth": "twenty",
    "thirtieth": "thirty",
    "fortieth": "forty",
    "fiftieth": "fifty",
    "sixtieth": "sixty",
    "seventieth": "seventy",
    "eightieth": "eighty",
    "ninetieth": "ninety",
    "hundredth": "hundred",
    "thousandth": "thousand",
    "millionth": "million",
}


@dataclass(frozen=True)
class DurationComponent:
    value: Decimal
    unit: str
    seconds: Decimal


@dataclass(frozen=True)
class ParsedDuration:
    seconds: int
    components: tuple[DurationComponent, ...]


def _normalized_words(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").split())


def _parse_spoken_integer(tokens: list[str]) -> int | None:
    if not tokens:
        return None
    total = 0
    group = 0
    saw_number = False
    previous_was_and = False
    previous_token = ""
    last_large_scale = 1_000_000_001
    for index, token in enumerate(tokens):
        if token == "and":
            if not saw_number or previous_was_and or index == len(tokens) - 1:
                return None
            previous_was_and = True
            continue
        previous_was_and = False
        if token in _SMALL_NUMBERS:
            if previous_token in _SMALL_NUMBERS:
                return None
            if previous_token in _TENS and _SMALL_NUMBERS[token] >= 10:
                return None
            if previous_token == "hundred" and group % 100 != 0:
                return None
            group += _SMALL_NUMBERS[token]
            saw_number = True
            previous_token = token
            continue
        if token in _TENS:
            if previous_token in _SMALL_NUMBERS or previous_token in _TENS:
                return None
            group += _TENS[token]
            saw_number = True
            previous_token = token
            continue
        if token == "hundred":
            if group <= 0 or group >= 20 or previous_token not in _SMALL_NUMBERS:
                return None
            group *= 100
            saw_number = True
            previous_token = token
            continue
        scale = _LARGE_SCALES.get(token)
        if scale is not None:
            if group <= 0 or scale >= last_large_scale:
                return None
            total += group * scale
            group = 0
            saw_number = True
            last_large_scale = scale
            previous_token = token
            continue
        return None
    return total + group if saw_number else None


def parse_number(value: str) -> Decimal | None:
    """Parse one complete numeric phrase; unknown or trailing tokens reject."""

    literal = str(value or "").strip().lower()
    if _NUMBER_LITERAL_RE.fullmatch(literal):
        try:
            return Decimal(literal)
        except InvalidOperation:
            return None
    normalized = _normalized_words(value)
    if not normalized:
        return None

    sign = Decimal(1)
    for prefix in ("negative ", "minus "):
        if normalized.startswith(prefix):
            sign = Decimal(-1)
            normalized = normalized[len(prefix) :]
            break
    if not normalized:
        return None

    if _NUMBER_LITERAL_RE.fullmatch(normalized):
        try:
            return sign * Decimal(normalized)
        except InvalidOperation:
            return None

    direct_fraction = _FRACTIONS.get(normalized)
    if direct_fraction is not None:
        return sign * direct_fraction

    mixed_fraction = re.fullmatch(
        r"(.+?) and (a half|one half|half|a quarter|one quarter|quarter|three quarters)",
        normalized,
    )
    if mixed_fraction is not None:
        whole = _parse_spoken_integer(mixed_fraction.group(1).split())
        fraction = _FRACTIONS.get(mixed_fraction.group(2))
        if whole is None or fraction is None:
            return None
        return sign * (Decimal(whole) + fraction)

    if " point " in f" {normalized} ":
        whole_text, separator, decimal_text = normalized.partition(" point ")
        if not separator or not whole_text or not decimal_text:
            return None
        whole = _parse_spoken_integer(whole_text.split())
        decimal_tokens = decimal_text.split()
        if whole is None or not decimal_tokens or any(token not in _DIGIT_WORDS for token in decimal_tokens):
            return None
        return sign * Decimal(f"{whole}.{''.join(_DIGIT_WORDS[token] for token in decimal_tokens)}")

    integer = _parse_spoken_integer(normalized.split())
    return sign * Decimal(integer) if integer is not None else None


def parse_ordinal(value: str) -> int | None:
    """Parse one complete positive ordinal phrase."""

    normalized = _normalized_words(value)
    if not normalized:
        return None
    literal_match = _ORDINAL_LITERAL_RE.fullmatch(normalized)
    if literal_match is not None:
        ordinal = int(literal_match.group(1))
        return ordinal if ordinal > 0 else None
    tokens = normalized.split()
    cardinal_final = _ORDINAL_WORDS.get(tokens[-1])
    if cardinal_final is None:
        return None
    cardinal = parse_number(" ".join((*tokens[:-1], cardinal_final)))
    if cardinal is None or cardinal != cardinal.to_integral_value() or cardinal <= 0:
        return None
    return int(cardinal)


def parse_duration(
    value: str,
    *,
    allow_zero: bool = False,
    max_seconds: int | None = None,
) -> ParsedDuration | None:
    """Parse a complete compound duration without capability-specific policy."""

    normalized = _normalized_words(value)
    if normalized.startswith("for "):
        normalized = normalized[4:].strip()
    normalized = re.sub(
        r"\b(?:a\s+)?(half|quarter)\s+(?:of\s+)?(?:a|an)\s+(?=(?:second|minute|hour|day|week)s?\b)",
        r"\1 ",
        normalized,
    )
    normalized = re.sub(
        r"\b(?:a|an)\s+(?=(?:second|minute|hour|day|week)s?\b)",
        "one ",
        normalized,
    )
    if not normalized:
        return None

    tokens = normalized.split()
    components: list[DurationComponent] = []
    number_tokens: list[str] = []
    for token in tokens:
        multiplier = _DURATION_UNITS.get(token)
        if multiplier is None:
            if token == "and" and not number_tokens and components:
                continue
            number_tokens.append(token)
            continue
        if not number_tokens:
            return None
        parsed_value = parse_number(" ".join(number_tokens))
        if parsed_value is None or parsed_value < 0:
            return None
        seconds = parsed_value * multiplier
        components.append(
            DurationComponent(
                value=parsed_value,
                unit=token[:-1] if token.endswith("s") else token,
                seconds=seconds,
            )
        )
        number_tokens = []

    if number_tokens and components and " ".join(number_tokens) in _FRACTIONS:
        parsed_value = parse_number(" ".join(number_tokens))
        prior_unit = components[-1].unit
        multiplier = _DURATION_UNITS[prior_unit]
        assert parsed_value is not None
        components.append(
            DurationComponent(
                value=parsed_value,
                unit=prior_unit,
                seconds=parsed_value * multiplier,
            )
        )
        number_tokens = []
    if number_tokens or not components:
        return None
    total = sum((component.seconds for component in components), Decimal(0))
    if total != total.to_integral_value():
        return None
    seconds = int(total)
    if seconds < 0 or (seconds == 0 and not allow_zero):
        return None
    if max_seconds is not None and seconds > max_seconds:
        return None
    return ParsedDuration(seconds=seconds, components=tuple(components))
