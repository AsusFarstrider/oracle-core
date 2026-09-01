from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import re
from statistics import median
from typing import Callable

from .deterministic_values import parse_number, parse_ordinal


_MAX_ABS_VALUE = Fraction(10**100)
_MAX_POWER = 12
_MAX_EXPRESSION_NODES = 64
_DEFAULT_DECIMAL_PLACES = 6


@dataclass(frozen=True)
class MathResult:
    value: Fraction
    operation: str
    expression: str
    label: str | None = None
    unit: str | None = None
    currency: bool = False
    prefer_fraction: bool = False
    decimal_places: int | None = None
    extra: dict[str, str] | None = None


def _normalize(text: str) -> str:
    normalized = str(text or "").strip().lower().replace("’", "'")
    normalized = re.sub(r"(?<=\d),(?=\d{3}\b)", "", normalized)
    normalized = " ".join(normalized.split())
    return normalized.rstrip(" ?.!" ).strip()


def _fraction_from_number(text: str) -> Fraction:
    parsed = parse_number(text.strip())
    if parsed is None:
        raise ValueError(f"Unsupported numeric value: {text.strip() or '-'}")
    return Fraction(parsed)


def _bounded(value: Fraction) -> Fraction:
    if abs(value) > _MAX_ABS_VALUE:
        raise ValueError("Calculation result is outside the supported range")
    return value


def _eval_ast(node: ast.AST, *, counter: list[int]) -> Fraction:
    counter[0] += 1
    if counter[0] > _MAX_EXPRESSION_NODES:
        raise ValueError("Arithmetic expression is too complex")
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body, counter=counter)
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return _bounded(Fraction(str(node.value)))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _eval_ast(node.operand, counter=counter)
        return _bounded(operand if isinstance(node.op, ast.UAdd) else -operand)
    if isinstance(node, ast.BinOp):
        left = _eval_ast(node.left, counter=counter)
        right = _eval_ast(node.right, counter=counter)
        if isinstance(node.op, ast.Add):
            return _bounded(left + right)
        if isinstance(node.op, ast.Sub):
            return _bounded(left - right)
        if isinstance(node.op, ast.Mult):
            return _bounded(left * right)
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("Division by zero")
            return _bounded(left / right)
        if isinstance(node.op, ast.Pow):
            if right.denominator != 1 or abs(right.numerator) > _MAX_POWER:
                raise ValueError(f"Powers must use an integer exponent from {-_MAX_POWER} to {_MAX_POWER}")
            if left == 0 and right < 0:
                raise ValueError("Zero cannot be raised to a negative power")
            return _bounded(left ** right.numerator)
    raise ValueError("Unsupported arithmetic expression")


def _strip_expression_wrapper(text: str) -> str:
    expression = text
    for prefix in ("what is ", "what's ", "calculate ", "compute ", "give me "):
        if expression.startswith(prefix):
            return expression[len(prefix) :].strip()
    return expression


def _imperative_expression(expression: str) -> str:
    patterns = (
        (r"^(?:multiply) (?P<a>.+?) by (?P<b>.+)$", "({a}) * ({b})"),
        (r"^(?:divide) (?P<a>.+?) by (?P<b>.+)$", "({a}) / ({b})"),
        (r"^(?:add) (?P<a>.+?) (?:and|to) (?P<b>.+)$", "({a}) + ({b})"),
        (r"^(?:subtract) (?P<a>.+?) from (?P<b>.+)$", "({b}) - ({a})"),
    )
    for pattern, template in patterns:
        match = re.fullmatch(pattern, expression)
        if match:
            return template.format(**match.groupdict())
    return expression


def _tokenize_expression(expression: str) -> tuple[str, bool]:
    expression = _imperative_expression(expression)
    expression = re.sub(r"\bmultiplied by\b", "*", expression)
    expression = re.sub(r"\bdivided by\b", "/", expression)
    expression = re.sub(r"\btimes\b", "*", expression)
    expression = re.sub(r"\bplus\b", "+", expression)
    expression = re.sub(r"\bminus\b", "-", expression)
    expression = re.sub(r"\bto the power of\b", "^", expression)
    expression = re.sub(r"\bto the\b", "^", expression)

    parts = re.split(r"([()+\-*/^])", expression)
    encoded: list[str] = []
    prefer_fraction = False
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if token in {"(", ")", "+", "-", "*", "/", "^"}:
            encoded.append("**" if token == "^" else token)
            continue
        value = _fraction_from_number(token)
        prefer_fraction = prefer_fraction or any(word in token for word in ("half", "quarter"))
        encoded.append(f"({value.numerator}/{value.denominator})")
    if not encoded:
        raise ValueError("No arithmetic expression found")
    return " ".join(encoded), prefer_fraction


def _evaluate_expression(text: str) -> MathResult:
    expression = _strip_expression_wrapper(text)
    if ", then " in expression:
        first, *steps = expression.split(", then ")
        result = _evaluate_expression(first)
        value = result.value
        for step in steps:
            step_text = step.replace("that", _decimal_text(value)).replace("it", _decimal_text(value))
            result = _evaluate_expression(step_text)
            value = result.value
        return MathResult(
            value=value,
            operation="sequential_arithmetic",
            expression=expression,
            prefer_fraction=result.prefer_fraction,
        )
    encoded, prefer_fraction = _tokenize_expression(expression)
    try:
        parsed = ast.parse(encoded, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ValueError("Unsupported arithmetic syntax") from exc
    value = _eval_ast(parsed, counter=[0])
    return MathResult(
        value=value,
        operation="arithmetic",
        expression=expression,
        prefer_fraction=prefer_fraction,
    )


def _context_value(math_context: dict[str, object] | None) -> Fraction:
    payload = math_context.get("payload") if isinstance(math_context, dict) else None
    raw = payload.get("value") if isinstance(payload, dict) else None
    if raw is None:
        raise ValueError("That calculation reference is no longer available")
    try:
        return Fraction(str(raw))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("The prior calculation value is invalid") from exc


def _parse_followup(text: str, math_context: dict[str, object] | None) -> MathResult | None:
    if ", then " in text:
        return None
    uses_context = bool(re.search(r"\b(?:that|it|this)\b", text)) or bool(
        re.fullmatch(r"(?:give me )?(?:\d+|[a-z -]+) decimal places?|nearest whole number", text)
    )
    current = _context_value(math_context) if uses_context else None
    if current is None:
        return None
    current_text = _decimal_text(current)

    round_match = re.fullmatch(
        r"(?:(?:round (?:that|it)|give me) (?:to )?)?(?:(?P<places>\d+|[a-z -]+) decimal places?|(?P<whole>nearest whole number))",
        text,
    )
    if round_match:
        places = 0 if round_match.group("whole") else int(_fraction_from_number(str(round_match.group("places"))))
        if not 0 <= places <= 12:
            raise ValueError("Rounding supports zero through twelve decimal places")
        with localcontext() as context:
            context.prec = max(40, places + 20)
            decimal_value = Decimal(current.numerator) / Decimal(current.denominator)
            rounded_value = decimal_value.quantize(Decimal(1).scaleb(-places))
        return MathResult(
            value=Fraction(rounded_value),
            operation="round",
            expression=text,
            decimal_places=places,
        )

    if re.fullmatch(r"double (?:that|it|this)", text):
        return MathResult(_bounded(current * 2), "scale", text)
    if re.fullmatch(r"(?:halve|half) (?:that|it|this)", text):
        return MathResult(current / 2, "scale", text)

    scale = re.fullmatch(r"scale (?:that|it|this) from (.+?) servings? to (.+?)(?: servings?)?", text)
    if scale:
        original = _fraction_from_number(scale.group(1))
        target = _fraction_from_number(scale.group(2))
        if original == 0:
            raise ValueError("Scale reference must be non-zero")
        return MathResult(_bounded(current * target / original), "scale", text)

    replacements = {
        "that": current_text,
        "it": current_text,
        "this": current_text,
    }
    expression = text
    for source, target in replacements.items():
        expression = re.sub(rf"\b{source}\b", target, expression)
    expression = re.sub(r"^what if i ", "", expression)
    expression = re.sub(r"\binstead$", "", expression).strip()
    try:
        result = _evaluate_expression(expression)
    except ValueError:
        return None
    return MathResult(result.value, "contextual_arithmetic", text, prefer_fraction=result.prefer_fraction)


def _parse_root_or_power(text: str) -> MathResult | None:
    square_root = re.fullmatch(r"(?:what is |what's )?(?:the )?square root of (.+)", text)
    cube_root = re.fullmatch(r"(?:what is |what's )?(?:the )?cube root of (.+)", text)
    match = square_root or cube_root
    if match:
        value = _fraction_from_number(match.group(1))
        degree = 2 if square_root else 3
        if value < 0 and degree == 2:
            raise ValueError("Square root requires a non-negative value")
        with localcontext() as context:
            context.prec = 40
            decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
            if degree == 2:
                rooted = decimal_value.sqrt()
            else:
                sign = Decimal(-1) if decimal_value < 0 else Decimal(1)
                rooted = sign * (abs(decimal_value) ** (Decimal(1) / Decimal(3)))
        return MathResult(Fraction(rooted), "root", text)

    simple_power = re.fullmatch(r"(?:what is |what's )?(.+?) (squared|cubed)", text)
    arbitrary = re.fullmatch(r"(?:what is |what's )?(.+?) to the (.+?) power", text)
    if simple_power:
        base = _fraction_from_number(simple_power.group(1))
        exponent = 2 if simple_power.group(2) == "squared" else 3
    elif arbitrary:
        base = _fraction_from_number(arbitrary.group(1))
        ordinal = parse_ordinal(arbitrary.group(2))
        if ordinal is not None:
            exponent = ordinal
        else:
            exponent_value = _fraction_from_number(arbitrary.group(2))
            if exponent_value.denominator != 1:
                raise ValueError("Power exponent must be an integer")
            exponent = exponent_value.numerator
    else:
        return None
    if abs(exponent) > _MAX_POWER:
        raise ValueError(f"Powers are limited to exponent {_MAX_POWER}")
    return MathResult(_bounded(base**exponent), "power", text)


def _parse_percentage(text: str) -> MathResult | None:
    percent_text = " ".join(re.sub(r"\s*%\s*", " percent ", text).split())
    forms: tuple[tuple[str, Callable[[Fraction, Fraction], Fraction], str], ...] = (
        (r"(.+?) is what percent of (.+)", lambda value, whole: value / whole * 100, "percent_ratio"),
        (r"(?:what is |what's )?(.+?) percent of (.+)", lambda percent, value: value * percent / 100, "percent_of"),
        (r"increase (.+?) by (.+?) percent", lambda value, percent: value * (1 + percent / 100), "percent_increase"),
        (r"decrease (.+?) by (.+?) percent", lambda value, percent: value * (1 - percent / 100), "percent_decrease"),
        (r"(?:what is |what's )?the percent change from (.+?) to (.+)", lambda old, new: (new - old) / old * 100, "percent_change"),
    )
    for pattern, operation, kind in forms:
        match = re.fullmatch(pattern, percent_text)
        if not match:
            continue
        left = _fraction_from_number(match.group(1))
        right = _fraction_from_number(match.group(2))
        if (kind == "percent_ratio" and right == 0) or (kind == "percent_change" and left == 0):
            raise ValueError("Percentage comparison requires a non-zero reference value")
        value = _bounded(operation(left, right))
        label = {
            "percent_of": f"{_format_value(left)}% of {_format_value(right)}",
            "percent_ratio": f"{_format_value(left)} is",
            "percent_change": "The percent change is",
        }.get(kind)
        return MathResult(value, kind, text, label=label, unit="percent" if kind in {"percent_ratio", "percent_change"} else None)
    return None


def _parse_money(text: str) -> MathResult | None:
    normalized = re.sub(r"\s*%\s*", " percent ", text.replace("$", ""))
    normalized = " ".join(normalized.split())
    tip = re.fullmatch(r"(?:what is |what's )?(?:a )?(.+?) percent tip on (.+)", normalized)
    tax = re.fullmatch(r"(?:what is |what's )?(.+?) with (.+?) percent sales tax", normalized)
    discount = re.fullmatch(r"(?:what is |what's )?(.+?) percent off (.+)", normalized)
    split = re.fullmatch(r"split (.+?) (?:into |between )?(.+?) ways", normalized)
    if tip:
        percent, base = _fraction_from_number(tip.group(1)), _fraction_from_number(tip.group(2))
        amount = base * percent / 100
        return MathResult(amount, "tip", text, currency=True, extra={"total": str(base + amount)})
    if tax:
        base, percent = _fraction_from_number(tax.group(1)), _fraction_from_number(tax.group(2))
        tax_amount = base * percent / 100
        return MathResult(base + tax_amount, "sales_tax", text, currency=True, extra={"tax": str(tax_amount)})
    if discount:
        percent, base = _fraction_from_number(discount.group(1)), _fraction_from_number(discount.group(2))
        saved = base * percent / 100
        return MathResult(base - saved, "discount", text, currency=True, extra={"saved": str(saved)})
    if split:
        base, ways = _fraction_from_number(split.group(1)), _fraction_from_number(split.group(2))
        if ways <= 0:
            raise ValueError("Split count must be positive")
        return MathResult(base / ways, "split", text, currency="$" in text)
    return None


def _parse_value_list(text: str) -> list[Fraction]:
    parts = [item.strip() for item in re.split(r",\s*(?:and\s+)?", text) if item.strip()]
    if len(parts) < 2:
        raise ValueError("Aggregation requires at least two values")
    return [_fraction_from_number(item) for item in parts]


def _parse_aggregation(text: str) -> MathResult | None:
    patterns = (
        (r"(?:what is |what's )?(?:the )?(?:average|mean) of (.+)", "average"),
        (r"(?:add up|sum) (.+)", "sum"),
        (r"(?:what is |what's )?(?:the )?(?:highest|maximum|max) (?:of )?(.+)", "maximum"),
        (r"(?:what is |what's )?(?:the )?(?:lowest|minimum|min) (?:of )?(.+)", "minimum"),
        (r"(?:what is |what's )?(?:the )?median (?:of )?(.+)", "median"),
    )
    for pattern, kind in patterns:
        match = re.fullmatch(pattern, text)
        if not match:
            continue
        values = _parse_value_list(match.group(1))
        if kind == "average":
            value = sum(values, Fraction()) / len(values)
        elif kind == "sum":
            value = sum(values, Fraction())
        elif kind == "maximum":
            value = max(values)
        elif kind == "minimum":
            value = min(values)
        else:
            value = median(values)
        return MathResult(_bounded(value), kind, text)
    return None


def _parse_proportion(text: str) -> MathResult | None:
    match = re.fullmatch(
        r"if (?:i need )?(.+?) (?P<unit>[a-z]+) (?:for|treats) (.+?)(?: [a-z]+)?, how much (?:do i need )?for (.+?)(?: [a-z]+)?",
        text,
    )
    if match:
        amount = _fraction_from_number(match.group(1))
        original = _fraction_from_number(match.group(3))
        target = _fraction_from_number(match.group(4))
        if original == 0:
            raise ValueError("Proportion reference must be non-zero")
        return MathResult(_bounded(amount * target / original), "proportion", text, unit=match.group("unit"))

    scale = re.fullmatch(r"scale (?:that|it|this) from (.+?) servings? to (.+?) servings?", text)
    if scale:
        raise ValueError("Scaling a prior value requires calculation context")
    return None


def _parse_geometry(text: str) -> MathResult | None:
    geometry_text = re.sub(r"-(foot|feet|meter|meters)\b", r" \1", text)
    rectangle = re.fullmatch(
        r"(?:what is |what's )?the (?P<kind>area|perimeter) of (?:a )?(?P<a>.+?)(?:-| )by(?:-| )(?P<b>.+?)(?: (?P<unit>feet|foot|meters|meter))?(?: rectangle| room)?",
        geometry_text,
    )
    if rectangle:
        a = _fraction_from_number(rectangle.group("a"))
        b = _fraction_from_number(rectangle.group("b"))
        kind = rectangle.group("kind")
        value = a * b if kind == "area" else 2 * (a + b)
        unit = rectangle.group("unit")
        if unit:
            unit = "feet" if unit in {"foot", "feet"} else "meters"
            unit = f"square {unit}" if kind == "area" else unit
        return MathResult(_bounded(value), f"rectangle_{kind}", text, unit=unit)

    circle = re.fullmatch(
        r"(?:what is |what's )?the (?P<kind>area|circumference) of (?:a )?(?P<diameter>.+?)(?:-| )(?P<unit>foot|feet|meter|meters) circle",
        geometry_text,
    )
    explicit_circle = re.fullmatch(
        r"(?:what is |what's )?the (?P<kind>area|circumference) of (?:a )?circle with (?:a )?(?P<measure>radius|diameter) of (?P<diameter>.+?)(?: (?P<unit>foot|feet|meter|meters))?",
        geometry_text,
    )
    if circle or explicit_circle:
        matched = circle or explicit_circle
        assert matched is not None
        diameter = _fraction_from_number(matched.group("diameter"))
        if explicit_circle and explicit_circle.group("measure") == "radius":
            diameter *= 2
        pi = Fraction(Decimal("3.141592653589793238462643383279502884"))
        kind = matched.group("kind")
        value = pi * diameter if kind == "circumference" else pi * (diameter / 2) ** 2
        unit = matched.group("unit")
        if unit:
            unit = "feet" if unit in {"foot", "feet"} else "meters"
            if kind == "area":
                unit = f"square {unit}"
        return MathResult(_bounded(value), f"circle_{kind}", text, unit=unit)

    volume = re.fullmatch(
        r"(?:what is |what's )?the volume of (?:a )?(.+?)(?:-| )by(?:-| )(.+?)(?:-| )by(?:-| )(.+?)(?: (?P<unit>feet|foot|meters|meter))?(?: box| rectangular prism)?",
        geometry_text,
    )
    if volume:
        value = _fraction_from_number(volume.group(1)) * _fraction_from_number(volume.group(2)) * _fraction_from_number(volume.group(3))
        unit = volume.group("unit")
        if unit:
            unit = "feet" if unit in {"foot", "feet"} else "meters"
        return MathResult(_bounded(value), "rectangular_volume", text, unit=f"cubic {unit}" if unit else None)

    triangle = re.fullmatch(
        r"(?:what is |what's )?the area of (?:a )?triangle with (?:a )?base (?:of )?(.+?) and (?:a )?height (?:of )?(.+?)(?: (?P<unit>feet|foot|meters|meter))?",
        geometry_text,
    )
    if triangle:
        value = _fraction_from_number(triangle.group(1)) * _fraction_from_number(triangle.group(2)) / 2
        unit = triangle.group("unit")
        if unit:
            unit = "feet" if unit in {"foot", "feet"} else "meters"
        return MathResult(_bounded(value), "triangle_area", text, unit=f"square {unit}" if unit else None)
    return None


def parse_math_query(text: str, *, math_context: dict[str, object] | None = None) -> MathResult:
    normalized = _normalize(text)
    if not normalized:
        raise ValueError("No calculation was provided")

    followup = _parse_followup(normalized, math_context)
    if followup is not None:
        return followup

    for parser in (
        _parse_money,
        _parse_percentage,
        _parse_root_or_power,
        _parse_aggregation,
        _parse_proportion,
        _parse_geometry,
    ):
        result = parser(normalized)
        if result is not None:
            return result
    fraction_of = re.fullmatch(r"(?:what is |what's )?(.+?) of (.+)", normalized)
    if fraction_of:
        fraction = _fraction_from_number(fraction_of.group(1))
        whole = _fraction_from_number(fraction_of.group(2))
        return MathResult(_bounded(fraction * whole), "fraction_of", normalized, prefer_fraction=True)
    return _evaluate_expression(normalized)


def is_math_query(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if re.search(r"\b(?:that|it|this)\b", normalized) and re.search(
        r"\b(?:add|subtract|multiply|divide|double|halve|half|round|scale)\b", normalized
    ):
        return True
    if re.fullmatch(r"(?:give me )?(?:\d+|[a-z -]+) decimal places?|nearest whole number", normalized):
        return True
    try:
        parse_math_query(normalized)
    except (ValueError, SyntaxError, InvalidOperation, ZeroDivisionError):
        expression = _strip_expression_wrapper(normalized)
        try:
            encoded, _ = _tokenize_expression(expression)
            ast.parse(encoded, mode="eval")
            return True
        except (ValueError, SyntaxError):
            pass
        root = re.fullmatch(r"(?:what is |what's )?(?:the )?(?:square|cube) root of (.+)", normalized)
        if root is not None and parse_number(root.group(1)) is not None:
            return True
        return False
    return True


def _decimal_text(value: Fraction, *, places: int = _DEFAULT_DECIMAL_PLACES) -> str:
    with localcontext() as context:
        context.prec = max(40, places + 20)
        decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
        if places >= 0:
            quantum = Decimal(1).scaleb(-places)
            decimal_value = decimal_value.quantize(quantum)
        text = format(decimal_value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _format_value(value: Fraction, *, prefer_fraction: bool = False, places: int | None = None) -> str:
    if places is not None:
        with localcontext() as context:
            context.prec = max(40, places + 20)
            decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
            return format(decimal_value.quantize(Decimal(1).scaleb(-places)), f".{places}f")
    if prefer_fraction and value.denominator != 1 and value.denominator <= 16:
        whole, remainder = divmod(abs(value.numerator), value.denominator)
        sign = "-" if value < 0 else ""
        if whole and remainder:
            return f"{sign}{whole} {remainder}/{value.denominator}"
        return f"{sign}{remainder}/{value.denominator}"
    return _decimal_text(value)


def _currency(value: Fraction) -> str:
    return f"${_format_value(value, places=2)}"


def build_math_response(
    text: str,
    *,
    math_context: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    result = parse_math_query(text, math_context=math_context)
    display = _format_value(
        result.value,
        prefer_fraction=result.prefer_fraction,
        places=result.decimal_places,
    )
    if result.currency:
        display = _currency(result.value)

    if result.operation == "tip":
        total = _currency(Fraction(str((result.extra or {})["total"])))
        speech = f"The tip is {display}, making the total {total}."
    elif result.operation == "sales_tax":
        tax = _currency(Fraction(str((result.extra or {})["tax"])))
        speech = f"The sales tax is {tax}, making the total {display}."
    elif result.operation == "discount":
        saved = _currency(Fraction(str((result.extra or {})["saved"])))
        speech = f"The discounted price is {display}, saving {saved}."
    elif result.operation == "percent_of" and result.label:
        speech = f"{result.label} is {display}."
    elif result.unit == "percent":
        speech = f"{result.label or 'The result'} {display}%."
    elif result.unit:
        speech = f"{display} {result.unit}."
    else:
        speech = f"{display}."

    exact_value = f"{result.value.numerator}/{result.value.denominator}"
    details: dict[str, object] = {
        "kind": "math",
        "operation": result.operation,
        "expression": result.expression,
        "value": _decimal_text(result.value, places=20),
        "exact_value": exact_value,
        "display_text": display,
    }
    if result.unit:
        details["unit"] = result.unit
    if result.currency:
        details["currency"] = "USD"
    if result.decimal_places is not None:
        details["decimal_places"] = result.decimal_places
    return speech, details
