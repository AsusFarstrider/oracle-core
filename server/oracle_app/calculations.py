from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from datetime import date, datetime

from .calendar import load_calendar_events


@dataclass(frozen=True)
class DateCalculationQuery:
    kind: str
    target_text: str


@dataclass(frozen=True)
class ConversionUnit:
    canonical: str
    category: str
    to_base_factor: float | None = None
    singular: str | None = None


UNIT_ALIASES: dict[str, ConversionUnit] = {
    "c": ConversionUnit("celsius", "temperature", singular="celsius"),
    "celsius": ConversionUnit("celsius", "temperature", singular="celsius"),
    "centigrade": ConversionUnit("celsius", "temperature", singular="celsius"),
    "f": ConversionUnit("fahrenheit", "temperature", singular="fahrenheit"),
    "fahrenheit": ConversionUnit("fahrenheit", "temperature", singular="fahrenheit"),
    "km": ConversionUnit("kilometers", "length", 1000.0, "kilometer"),
    "kilometer": ConversionUnit("kilometers", "length", 1000.0, "kilometer"),
    "kilometers": ConversionUnit("kilometers", "length", 1000.0, "kilometer"),
    "mile": ConversionUnit("miles", "length", 1609.344, "mile"),
    "miles": ConversionUnit("miles", "length", 1609.344, "mile"),
    "meter": ConversionUnit("meters", "length", 1.0, "meter"),
    "meters": ConversionUnit("meters", "length", 1.0, "meter"),
    "m": ConversionUnit("meters", "length", 1.0, "meter"),
    "foot": ConversionUnit("feet", "length", 0.3048, "foot"),
    "feet": ConversionUnit("feet", "length", 0.3048, "foot"),
    "ft": ConversionUnit("feet", "length", 0.3048, "foot"),
    "inch": ConversionUnit("inches", "length", 0.0254, "inch"),
    "inches": ConversionUnit("inches", "length", 0.0254, "inch"),
    "in": ConversionUnit("inches", "length", 0.0254, "inch"),
    "cm": ConversionUnit("centimeters", "length", 0.01, "centimeter"),
    "centimeter": ConversionUnit("centimeters", "length", 0.01, "centimeter"),
    "centimeters": ConversionUnit("centimeters", "length", 0.01, "centimeter"),
    "mm": ConversionUnit("millimeters", "length", 0.001, "millimeter"),
    "millimeter": ConversionUnit("millimeters", "length", 0.001, "millimeter"),
    "millimeters": ConversionUnit("millimeters", "length", 0.001, "millimeter"),
    "kg": ConversionUnit("kilograms", "weight", 1.0, "kilogram"),
    "kilogram": ConversionUnit("kilograms", "weight", 1.0, "kilogram"),
    "kilograms": ConversionUnit("kilograms", "weight", 1.0, "kilogram"),
    "g": ConversionUnit("grams", "weight", 0.001, "gram"),
    "gram": ConversionUnit("grams", "weight", 0.001, "gram"),
    "grams": ConversionUnit("grams", "weight", 0.001, "gram"),
    "lb": ConversionUnit("pounds", "weight", 0.45359237, "pound"),
    "lbs": ConversionUnit("pounds", "weight", 0.45359237, "pound"),
    "pound": ConversionUnit("pounds", "weight", 0.45359237, "pound"),
    "pounds": ConversionUnit("pounds", "weight", 0.45359237, "pound"),
    "oz": ConversionUnit("ounces", "weight", 0.028349523125, "ounce"),
    "ounce": ConversionUnit("ounces", "weight", 0.028349523125, "ounce"),
    "ounces": ConversionUnit("ounces", "weight", 0.028349523125, "ounce"),
    "l": ConversionUnit("liters", "volume", 1.0, "liter"),
    "liter": ConversionUnit("liters", "volume", 1.0, "liter"),
    "liters": ConversionUnit("liters", "volume", 1.0, "liter"),
    "litre": ConversionUnit("liters", "volume", 1.0, "liter"),
    "litres": ConversionUnit("liters", "volume", 1.0, "liter"),
    "ml": ConversionUnit("milliliters", "volume", 0.001, "milliliter"),
    "milliliter": ConversionUnit("milliliters", "volume", 0.001, "milliliter"),
    "milliliters": ConversionUnit("milliliters", "volume", 0.001, "milliliter"),
    "tsp": ConversionUnit("teaspoons", "volume", 0.00492892159375, "teaspoon"),
    "teaspoon": ConversionUnit("teaspoons", "volume", 0.00492892159375, "teaspoon"),
    "teaspoons": ConversionUnit("teaspoons", "volume", 0.00492892159375, "teaspoon"),
    "tbsp": ConversionUnit("tablespoons", "volume", 0.01478676478125, "tablespoon"),
    "tablespoon": ConversionUnit("tablespoons", "volume", 0.01478676478125, "tablespoon"),
    "tablespoons": ConversionUnit("tablespoons", "volume", 0.01478676478125, "tablespoon"),
    "fl oz": ConversionUnit("fluid ounces", "volume", 0.0295735295625, "fluid ounce"),
    "fluid ounce": ConversionUnit("fluid ounces", "volume", 0.0295735295625, "fluid ounce"),
    "fluid ounces": ConversionUnit("fluid ounces", "volume", 0.0295735295625, "fluid ounce"),
    "gal": ConversionUnit("gallons", "volume", 3.785411784, "gallon"),
    "gallon": ConversionUnit("gallons", "volume", 3.785411784, "gallon"),
    "gallons": ConversionUnit("gallons", "volume", 3.785411784, "gallon"),
    "pt": ConversionUnit("pints", "volume", 0.473176473, "pint"),
    "pint": ConversionUnit("pints", "volume", 0.473176473, "pint"),
    "pints": ConversionUnit("pints", "volume", 0.473176473, "pint"),
    "qt": ConversionUnit("quarts", "volume", 0.946352946, "quart"),
    "quart": ConversionUnit("quarts", "volume", 0.946352946, "quart"),
    "quarts": ConversionUnit("quarts", "volume", 0.946352946, "quart"),
    "cup": ConversionUnit("cups", "volume", 0.2365882365, "cup"),
    "cups": ConversionUnit("cups", "volume", 0.2365882365, "cup"),
    "mph": ConversionUnit("miles per hour", "speed", 0.44704, "mile per hour"),
    "mile per hour": ConversionUnit("miles per hour", "speed", 0.44704, "mile per hour"),
    "miles per hour": ConversionUnit("miles per hour", "speed", 0.44704, "mile per hour"),
    "kph": ConversionUnit("kilometers per hour", "speed", 0.2777777778, "kilometer per hour"),
    "kmh": ConversionUnit("kilometers per hour", "speed", 0.2777777778, "kilometer per hour"),
    "kilometer per hour": ConversionUnit("kilometers per hour", "speed", 0.2777777778, "kilometer per hour"),
    "kilometers per hour": ConversionUnit("kilometers per hour", "speed", 0.2777777778, "kilometer per hour"),
    "square foot": ConversionUnit("square feet", "area", 0.09290304, "square foot"),
    "square feet": ConversionUnit("square feet", "area", 0.09290304, "square foot"),
    "sq ft": ConversionUnit("square feet", "area", 0.09290304, "square foot"),
    "sqft": ConversionUnit("square feet", "area", 0.09290304, "square foot"),
    "ft2": ConversionUnit("square feet", "area", 0.09290304, "square foot"),
    "square meter": ConversionUnit("square meters", "area", 1.0, "square meter"),
    "square meters": ConversionUnit("square meters", "area", 1.0, "square meter"),
    "sq m": ConversionUnit("square meters", "area", 1.0, "square meter"),
    "sqm": ConversionUnit("square meters", "area", 1.0, "square meter"),
    "m2": ConversionUnit("square meters", "area", 1.0, "square meter"),
}

SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

MONTH_NAMES = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _format_number(value: float) -> str:
    rounded = round(value, 4)
    if float(rounded).is_integer():
        return str(int(rounded))
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def _format_unit_label(unit: ConversionUnit, value: float) -> str:
    if unit.singular is None:
        return unit.canonical
    return unit.singular if abs(value) == 1 else unit.canonical


def _normalize_unit(text: str) -> str:
    normalized = text.strip().lower().replace("degrees ", "")
    normalized = normalized.replace("square foot", "square feet")
    normalized = normalized.replace("sq. ", "sq ")
    normalized = normalized.replace("fluid oz", "fl oz")
    normalized = normalized.replace("floz", "fl oz")
    return " ".join(normalized.split())


def _resolve_unit(text: str) -> ConversionUnit | None:
    return UNIT_ALIASES.get(_normalize_unit(text))


def _convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return value
    if from_unit == "celsius" and to_unit == "fahrenheit":
        return (value * 9.0 / 5.0) + 32.0
    if from_unit == "fahrenheit" and to_unit == "celsius":
        return (value - 32.0) * 5.0 / 9.0
    raise ValueError("Unsupported temperature conversion")


def convert_units(value: float, from_text: str, to_text: str) -> tuple[str, dict]:
    from_unit = _resolve_unit(from_text)
    to_unit = _resolve_unit(to_text)
    if from_unit is None or to_unit is None:
        raise ValueError("Unsupported unit")
    if from_unit.category != to_unit.category:
        raise ValueError("Incompatible unit conversion")

    if from_unit.category == "temperature":
        converted = _convert_temperature(value, from_unit.canonical, to_unit.canonical)
    else:
        assert from_unit.to_base_factor is not None
        assert to_unit.to_base_factor is not None
        base_value = value * from_unit.to_base_factor
        converted = base_value / to_unit.to_base_factor

    speech = (
        f"{_format_number(value)} {_format_unit_label(from_unit, value)} is "
        f"{_format_number(converted)} {_format_unit_label(to_unit, converted)}."
    )
    return speech, {
        "kind": "conversion",
        "input_value": value,
        "input_unit": from_unit.canonical,
        "output_value": converted,
        "output_unit": to_unit.canonical,
    }


def _safe_eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in SAFE_OPERATORS:
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("Division by zero")
        return float(SAFE_OPERATORS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_OPERATORS:
        operand = _safe_eval_node(node.operand)
        return float(SAFE_OPERATORS[type(node.op)](operand))
    raise ValueError("Unsupported arithmetic expression")


def evaluate_math_expression(text: str) -> tuple[str, dict]:
    normalized = text.strip().lower()
    expression = normalized
    for prefix in ("what is ", "what's ", "calculate ", "compute "):
        if expression.startswith(prefix):
            expression = expression[len(prefix) :]
            break

    expression = expression.rstrip(" ?")
    replacements = (
        ("multiplied by", "*"),
        ("times", "*"),
        ("x", "*"),
        ("plus", "+"),
        ("minus", "-"),
        ("divided by", "/"),
        ("over", "/"),
    )
    for source, target in replacements:
        expression = expression.replace(source, target)

    expression = re.sub(r"[^0-9\.\+\-\*\/\(\)\s]", "", expression)
    expression = " ".join(expression.split())
    if not expression:
        raise ValueError("No arithmetic expression found")

    parsed = ast.parse(expression, mode="eval")
    value = _safe_eval_node(parsed)
    speech = f"The answer is {_format_number(value)}."
    return speech, {
        "kind": "math",
        "expression": expression,
        "value": value,
    }


def parse_conversion_query(text: str) -> tuple[float, str, str] | None:
    normalized = text.strip().lower()
    patterns = (
        r"^(?:convert )?(?P<value>-?\d+(?:\.\d+)?) (?P<from>[a-z ]+) to (?P<to>[a-z ]+)$",
        r"^(?:what is|what's) (?P<value>-?\d+(?:\.\d+)?) (?P<from>[a-z ]+) in (?P<to>[a-z ]+)$",
        r"^how many (?P<to>[a-z ]+) (?:is|are) (?P<value>-?\d+(?:\.\d+)?) (?P<from>[a-z ]+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if not match:
            continue
        return (
            float(match.group("value")),
            str(match.group("from")).strip(),
            str(match.group("to")).strip(),
        )
    return None


def parse_date_calculation_query(text: str) -> DateCalculationQuery | None:
    normalized = " ".join(text.strip().lower().split()).rstrip(" ?.!")
    if not normalized:
        return None

    patterns = (
        (r"^(?:how many days|how long) until (?P<target>.+)$", "until"),
        (r"^how many days since (?P<target>.+)$", "since"),
        (r"^what day of the week is (?P<target>.+)$", "weekday"),
    )
    for pattern, kind in patterns:
        match = re.match(pattern, normalized)
        if match:
            target_text = str(match.group("target")).strip()
            if target_text:
                return DateCalculationQuery(kind=kind, target_text=target_text)
    return None


def _normalize_target_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"\b(?:the|a|an)\b", " ", normalized)
    normalized = normalized.replace("'", "")
    normalized = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", normalized)
    return " ".join(normalized.split())


def _extract_target_year(text: str, *, today: date) -> tuple[str, int | None]:
    normalized = _normalize_target_text(text)
    if normalized.endswith(" this year"):
        return normalized[: -len(" this year")].strip(), today.year
    if normalized.endswith(" next year"):
        return normalized[: -len(" next year")].strip(), today.year + 1
    match = re.search(r"\b(20\d{2}|19\d{2})\b$", normalized)
    if match:
        year = int(match.group(1))
        return normalized[: match.start()].strip(), year
    return normalized, None


def _parse_numeric_date(text: str) -> date | None:
    for pattern in (
        r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})$",
        r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})$",
    ):
        match = re.match(pattern, text)
        if not match:
            continue
        year = int(match.groupdict().get("year") or 0)
        month = int(match.group("month"))
        day = int(match.group("day"))
        if year == 0:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _resolve_explicit_date(target_text: str, *, kind: str, today: date) -> tuple[date, str] | None:
    base_text, requested_year = _extract_target_year(target_text, today=today)
    short_numeric = re.match(r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})$", base_text)
    if short_numeric:
        candidate = _apply_missing_year(
            int(short_numeric.group("month")),
            int(short_numeric.group("day")),
            requested_year,
            kind=kind,
            today=today,
        )
        if candidate is None:
            return None
        return candidate, _format_date_label(candidate)

    numeric_date = _parse_numeric_date(base_text)
    if numeric_date is not None:
        return numeric_date, _format_date_label(numeric_date)

    normalized = _normalize_target_text(base_text)
    match = re.match(r"^(?P<month>[a-z]+) (?P<day>\d{1,2})$", normalized)
    if not match:
        return None
    month = MONTH_NAMES.get(match.group("month"))
    if month is None:
        return None
    candidate = _apply_missing_year(month, int(match.group("day")), requested_year, kind=kind, today=today)
    if candidate is None:
        return None
    return candidate, _format_date_label(candidate)


def _apply_missing_year(
    month: int,
    day_of_month: int,
    requested_year: int | None,
    *,
    kind: str,
    today: date,
) -> date | None:
    candidate_year = requested_year if requested_year is not None else today.year
    try:
        candidate = date(candidate_year, month, day_of_month)
    except ValueError:
        return None
    if requested_year is not None:
        return candidate
    if kind in {"until", "weekday"} and candidate < today:
        try:
            return date(today.year + 1, month, day_of_month)
        except ValueError:
            return None
    if kind == "since" and candidate > today:
        try:
            return date(today.year - 1, month, day_of_month)
        except ValueError:
            return None
    return candidate


def _format_date_label(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def _normalize_event_summary(summary: str) -> str:
    normalized = summary.strip().lower().replace("'", "")
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"\bday\b", " ", normalized)
    normalized = re.sub(r"[^a-z0-9 ]", " ", normalized)
    return " ".join(normalized.split())


def _resolve_holiday_date(target_text: str, *, kind: str, today: date) -> tuple[date, str] | None:
    base_text, requested_year = _extract_target_year(target_text, today=today)
    normalized_target = _normalize_event_summary(base_text)
    if not normalized_target:
        return None

    events = load_calendar_events(scope="holiday")
    candidates: list[tuple[int, date, str]] = []
    target_tokens = tuple(token for token in normalized_target.split() if token)
    for event in events:
        event_date = event.start.date()
        if requested_year is not None and event_date.year != requested_year:
            continue
        normalized_summary = _normalize_event_summary(event.summary)
        if normalized_summary == normalized_target:
            score = 100
        elif normalized_target in normalized_summary:
            score = 80
        elif target_tokens and all(token in normalized_summary for token in target_tokens):
            score = 60
        else:
            continue
        candidates.append((score, event_date, event.summary))

    if not candidates:
        return None

    if kind in {"until", "weekday"}:
        future = [item for item in candidates if item[1] >= today]
        pool = future or candidates
        _, resolved_date, summary = sorted(pool, key=lambda item: (-item[0], item[1]))[0]
        return resolved_date, summary

    past = [item for item in candidates if item[1] <= today]
    pool = past or candidates
    _, resolved_date, summary = sorted(pool, key=lambda item: (item[1], item[0]), reverse=True)[0]
    return resolved_date, summary


def _resolve_date_target(target_text: str, *, kind: str, today: date) -> tuple[date, str]:
    explicit = _resolve_explicit_date(target_text, kind=kind, today=today)
    if explicit is not None:
        return explicit
    holiday = _resolve_holiday_date(target_text, kind=kind, today=today)
    if holiday is not None:
        return holiday
    raise ValueError("I couldn't resolve that date.")


def _format_delta_days(delta_days: int) -> str:
    if delta_days == 0:
        return "today"
    if delta_days == 1:
        return "1 day"
    return f"{delta_days} days"


def _build_date_calculation_response(query: DateCalculationQuery, *, today: date) -> tuple[str, dict]:
    target_date, label = _resolve_date_target(query.target_text, kind=query.kind, today=today)

    if query.kind == "weekday":
        speech = f"{label} is on a {target_date.strftime('%A')}."
        return speech, {
            "kind": "date_weekday",
            "target": label,
            "date": target_date.isoformat(),
            "weekday": target_date.strftime("%A"),
        }

    delta_days = (target_date - today).days
    if query.kind == "until":
        if delta_days == 0:
            speech = f"{label} is today."
        else:
            speech = f"There are {_format_delta_days(delta_days)} until {label}."
        return speech, {
            "kind": "date_until",
            "target": label,
            "date": target_date.isoformat(),
            "days": delta_days,
        }

    elapsed_days = abs(delta_days)
    if elapsed_days == 0:
        speech = f"{label} is today."
    else:
        speech = f"It has been {_format_delta_days(elapsed_days)} since {label}."
    return speech, {
        "kind": "date_since",
        "target": label,
        "date": target_date.isoformat(),
        "days": elapsed_days,
    }


def build_calculation_response(text: str, *, today: date | None = None) -> tuple[str, dict]:
    date_query = parse_date_calculation_query(text)
    if date_query is not None:
        resolved_today = today or datetime.now().astimezone().date()
        return _build_date_calculation_response(date_query, today=resolved_today)
    conversion = parse_conversion_query(text)
    if conversion is not None:
        value, from_unit, to_unit = conversion
        return convert_units(value, from_unit, to_unit)
    return evaluate_math_expression(text)
