from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP, localcontext
import re

from .deterministic_values import parse_number


_MAX_ABS_VALUE = Decimal("1e100")
_MAX_DECIMAL_PLACES = 12
_FUEL_CONSTANT_MPG_US = Decimal("235.214583")


@dataclass(frozen=True)
class UnitDefinition:
    canonical: str
    singular: str
    dimension: str
    factor: Decimal | None
    aliases: tuple[str, ...]
    transform: str = "linear"


@dataclass(frozen=True)
class QuantityComponent:
    value: Decimal
    unit: UnitDefinition


@dataclass(frozen=True)
class ConversionQuery:
    source_components: tuple[QuantityComponent, ...]
    target_units: tuple[UnitDefinition, ...]
    source_text: str
    target_text: str
    decimal_places: int | None = None
    base_value: Decimal | None = None


def _unit(
    canonical: str,
    singular: str,
    dimension: str,
    factor: str | None,
    *aliases: str,
    transform: str = "linear",
) -> UnitDefinition:
    return UnitDefinition(
        canonical=canonical,
        singular=singular,
        dimension=dimension,
        factor=Decimal(factor) if factor is not None else None,
        aliases=tuple(dict.fromkeys((canonical, singular, *aliases))),
        transform=transform,
    )


_UNITS = (
    # Length, canonical base: meter.
    _unit("millimeters", "millimeter", "length", "0.001", "mm"),
    _unit("centimeters", "centimeter", "length", "0.01", "cm"),
    _unit("meters", "meter", "length", "1", "metres", "metre"),
    _unit("kilometers", "kilometer", "length", "1000", "km", "kilometres", "kilometre"),
    _unit("inches", "inch", "length", "0.0254"),
    _unit("feet", "foot", "length", "0.3048", "ft"),
    _unit("yards", "yard", "length", "0.9144", "yd"),
    _unit("miles", "mile", "length", "1609.344", "mi"),
    _unit("nautical miles", "nautical mile", "length", "1852", "nmi"),

    # Area, canonical base: square meter.
    _unit("square millimeters", "square millimeter", "area", "0.000001", "sq mm", "mm2"),
    _unit("square centimeters", "square centimeter", "area", "0.0001", "sq cm", "cm2"),
    _unit("square meters", "square meter", "area", "1", "sq m", "sqm", "m2"),
    _unit("square kilometers", "square kilometer", "area", "1000000", "sq km", "km2"),
    _unit("square inches", "square inch", "area", "0.00064516", "sq in", "in2"),
    _unit("square feet", "square foot", "area", "0.09290304", "sq ft", "sqft", "ft2"),
    _unit("square yards", "square yard", "area", "0.83612736", "sq yd", "yd2"),
    _unit("acres", "acre", "area", "4046.8564224"),
    _unit("hectares", "hectare", "area", "10000", "ha"),
    _unit("square miles", "square mile", "area", "2589988.110336", "sq mi", "mi2"),

    # Volume, canonical base: liter. US customary measures are explicit.
    _unit("milliliters", "milliliter", "volume", "0.001", "ml", "millilitres", "millilitre"),
    _unit("liters", "liter", "volume", "1", "l", "litres", "litre"),
    _unit("teaspoons", "teaspoon", "volume", "0.00492892159375", "tsp"),
    _unit("tablespoons", "tablespoon", "volume", "0.01478676478125", "tbsp"),
    _unit("cups", "cup", "volume", "0.2365882365"),
    _unit("fluid ounces", "fluid ounce", "volume", "0.0295735295625", "fl oz", "floz"),
    _unit("pints", "pint", "volume", "0.473176473", "pt"),
    _unit("quarts", "quart", "volume", "0.946352946", "qt"),
    _unit("gallons", "gallon", "volume", "3.785411784", "gal", "us gallons", "us gallon"),
    _unit("cubic inches", "cubic inch", "volume", "0.016387064", "cu in", "in3"),
    _unit("cubic feet", "cubic foot", "volume", "28.316846592", "cu ft", "ft3"),
    _unit("cubic centimeters", "cubic centimeter", "volume", "0.001", "cc", "cm3"),
    _unit("cubic meters", "cubic meter", "volume", "1000", "cu m", "m3"),

    # Mass, canonical base: kilogram.
    _unit("milligrams", "milligram", "mass", "0.000001", "mg"),
    _unit("grams", "gram", "mass", "0.001", "g"),
    _unit("kilograms", "kilogram", "mass", "1", "kg", "kilos", "kilo"),
    _unit("ounces", "ounce", "mass", "0.028349523125", "oz"),
    _unit("pounds", "pound", "mass", "0.45359237", "lb", "lbs"),
    _unit("us tons", "us ton", "mass", "907.18474", "short tons", "short ton", "tons", "ton"),
    _unit("metric tonnes", "metric tonne", "mass", "1000", "tonnes", "tonne", "metric tons", "metric ton"),
    _unit("stone", "stone", "mass", "6.35029318", "stones", "st"),

    # Speed, canonical base: meter per second.
    _unit("meters per second", "meter per second", "speed", "1", "m/s", "mps"),
    _unit("feet per second", "foot per second", "speed", "0.3048", "ft/s", "fps"),
    _unit("kilometers per hour", "kilometer per hour", "speed", "0.2777777777777777777777777778", "km/h", "kph", "kmh"),
    _unit("miles per hour", "mile per hour", "speed", "0.44704", "mph"),
    _unit("knots", "knot", "speed", "0.5144444444444444444444444444", "kt", "kts"),

    # Fixed time units, canonical base: second.
    _unit("milliseconds", "millisecond", "time", "0.001", "ms"),
    _unit("seconds", "second", "time", "1", "sec", "secs"),
    _unit("minutes", "minute", "time", "60", "min", "mins"),
    _unit("hours", "hour", "time", "3600", "hr", "hrs"),
    _unit("days", "day", "time", "86400"),
    _unit("weeks", "week", "time", "604800"),

    # Data, canonical base: bit. Decimal and binary prefixes remain distinct.
    _unit("bits", "bit", "data", "1"),
    _unit("bytes", "byte", "data", "8"),
    _unit("kilobits", "kilobit", "data", "1000", "kbit"),
    _unit("kilobytes", "kilobyte", "data", "8000", "kb"),
    _unit("megabits", "megabit", "data", "1000000", "mbit"),
    _unit("megabytes", "megabyte", "data", "8000000", "mb"),
    _unit("gigabits", "gigabit", "data", "1000000000", "gbit"),
    _unit("gigabytes", "gigabyte", "data", "8000000000", "gb"),
    _unit("terabits", "terabit", "data", "1000000000000", "tbit"),
    _unit("terabytes", "terabyte", "data", "8000000000000", "tb"),
    _unit("kibibits", "kibibit", "data", "1024", "kibit"),
    _unit("kibibytes", "kibibyte", "data", "8192", "kib"),
    _unit("mebibits", "mebibit", "data", "1048576", "mibit"),
    _unit("mebibytes", "mebibyte", "data", "8388608", "mib"),
    _unit("gibibits", "gibibit", "data", "1073741824", "gibit"),
    _unit("gibibytes", "gibibyte", "data", "8589934592", "gib"),
    _unit("tebibits", "tebibit", "data", "1099511627776", "tibit"),
    _unit("tebibytes", "tebibyte", "data", "8796093022208", "tib"),

    # Data rates, canonical base: bit per second.
    _unit("bits per second", "bit per second", "data_rate", "1", "bps"),
    _unit("bytes per second", "byte per second", "data_rate", "8", "byte/s"),
    _unit("kilobits per second", "kilobit per second", "data_rate", "1000", "kbps"),
    _unit("kilobytes per second", "kilobyte per second", "data_rate", "8000", "kb/s"),
    _unit("megabits per second", "megabit per second", "data_rate", "1000000", "mbps"),
    _unit("megabytes per second", "megabyte per second", "data_rate", "8000000", "mb/s"),
    _unit("gigabits per second", "gigabit per second", "data_rate", "1000000000", "gbps"),
    _unit("gigabytes per second", "gigabyte per second", "data_rate", "8000000000", "gb/s"),
    _unit("terabits per second", "terabit per second", "data_rate", "1000000000000", "tbps"),
    _unit("terabytes per second", "terabyte per second", "data_rate", "8000000000000", "tb/s"),
    _unit("kibibits per second", "kibibit per second", "data_rate", "1024", "kibit/s"),
    _unit("kibibytes per second", "kibibyte per second", "data_rate", "8192", "kib/s"),
    _unit("mebibits per second", "mebibit per second", "data_rate", "1048576", "mibit/s"),
    _unit("mebibytes per second", "mebibyte per second", "data_rate", "8388608", "mib/s"),
    _unit("gibibits per second", "gibibit per second", "data_rate", "1073741824", "gibit/s"),
    _unit("gibibytes per second", "gibibyte per second", "data_rate", "8589934592", "gib/s"),
    _unit("tebibits per second", "tebibit per second", "data_rate", "1099511627776", "tibit/s"),
    _unit("tebibytes per second", "tebibyte per second", "data_rate", "8796093022208", "tib/s"),

    # Pressure, power, and energy.
    _unit("pascals", "pascal", "pressure", "1", "pa"),
    _unit("kilopascals", "kilopascal", "pressure", "1000", "kpa"),
    _unit("psi", "psi", "pressure", "6894.757293168", "pounds per square inch"),
    _unit("bar", "bar", "pressure", "100000", "bars"),
    _unit("atmospheres", "atmosphere", "pressure", "101325", "atm"),
    _unit("inches of mercury", "inch of mercury", "pressure", "3386.389", "inhg"),
    _unit("watts", "watt", "power", "1", "w"),
    _unit("kilowatts", "kilowatt", "power", "1000", "kw"),
    _unit("horsepower", "horsepower", "power", "745.69987158227022", "hp"),
    _unit("joules", "joule", "energy", "1", "j"),
    _unit("kilojoules", "kilojoule", "energy", "1000", "kj"),
    _unit("watt hours", "watt hour", "energy", "3600", "wh"),
    _unit("kilowatt hours", "kilowatt hour", "energy", "3600000", "kwh"),
    _unit("btu", "btu", "energy", "1055.05585262", "british thermal units", "british thermal unit"),

    # Affine and inverse dimensions.
    _unit("celsius", "celsius", "temperature", None, "centigrade", "degrees celsius", "degree celsius", "c", transform="celsius"),
    _unit("fahrenheit", "fahrenheit", "temperature", None, "degrees fahrenheit", "degree fahrenheit", "f", transform="fahrenheit"),
    _unit("kelvin", "kelvin", "temperature", None, "k", transform="kelvin"),
    _unit("miles per gallon", "mile per gallon", "fuel_economy", None, "mpg", "us mpg", transform="mpg_us"),
    _unit("kilometers per liter", "kilometer per liter", "fuel_economy", None, "km/l", "kpl", transform="km_per_liter"),
    _unit("liters per 100 kilometers", "liter per 100 kilometers", "fuel_economy", None, "l/100 km", "l/100km", "liters per hundred kilometers", transform="liters_per_100km"),
)


def _normalize_alias(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = normalized.replace("²", "2").replace("³", "3")
    normalized = normalized.replace("sq. ", "sq ").replace("cu. ", "cu ")
    normalized = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", normalized)
    return " ".join(normalized.split())


UNIT_ALIASES: dict[str, UnitDefinition] = {}
UNIT_CATALOG: dict[str, UnitDefinition] = {}
for _definition in _UNITS:
    if _definition.canonical in UNIT_CATALOG:
        raise RuntimeError(f"Duplicate canonical conversion unit: {_definition.canonical}")
    UNIT_CATALOG[_definition.canonical] = _definition
    for _alias in _definition.aliases:
        _key = _normalize_alias(_alias)
        if _key in UNIT_ALIASES and UNIT_ALIASES[_key] != _definition:
            raise RuntimeError(f"Ambiguous conversion unit alias: {_key}")
        UNIT_ALIASES[_key] = _definition

_ALIAS_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(
        re.escape(alias) for alias in sorted(UNIT_ALIASES, key=lambda item: (-len(item), item))
    ) + r")(?![a-z0-9])"
)


def _normalize_text(value: str) -> str:
    text = str(value or "").strip()
    replacements = (
        (r"\bTB/s\b", "terabytes per second"),
        (r"\bGB/s\b", "gigabytes per second"),
        (r"\bMB/s\b", "megabytes per second"),
        (r"\bKB/s\b", "kilobytes per second"),
        (r"\bTiB/s\b", "tebibytes per second"),
        (r"\bGiB/s\b", "gibibytes per second"),
        (r"\bMiB/s\b", "mebibytes per second"),
        (r"\bKiB/s\b", "kibibytes per second"),
        (r"\bTiB\b", "tebibytes"),
        (r"\bGiB\b", "gibibytes"),
        (r"\bMiB\b", "mebibytes"),
        (r"\bKiB\b", "kibibytes"),
        (r"\bTB\b", "terabytes"),
        (r"\bGB\b", "gigabytes"),
        (r"\bMB\b", "megabytes"),
        (r"\bKB\b", "kilobytes"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = text.lower().replace("²", "2").replace("³", "3").replace("°", " degrees ")
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)
    return " ".join(text.split()).rstrip(" ?.!" ).strip()


def resolve_unit(text: str) -> UnitDefinition | None:
    return UNIT_ALIASES.get(_normalize_alias(text))


def _parse_quantity(text: str) -> tuple[QuantityComponent, ...]:
    normalized = _normalize_alias(text)
    matches = list(_ALIAS_PATTERN.finditer(normalized))
    if not matches:
        raise ValueError(f"Unsupported conversion unit in: {text}")
    components: list[QuantityComponent] = []
    cursor = 0
    for match in matches:
        number_text = normalized[cursor : match.start()].strip()
        if number_text.startswith("and "):
            number_text = number_text[4:].strip()
        if not number_text:
            raise ValueError("Every conversion unit requires an explicit value")
        value = parse_number(number_text)
        if value is None:
            raise ValueError(f"Unsupported conversion value: {number_text}")
        unit = resolve_unit(match.group(0))
        assert unit is not None
        components.append(QuantityComponent(value=value, unit=unit))
        cursor = match.end()
    if normalized[cursor:].strip():
        raise ValueError(f"Unsupported conversion text: {normalized[cursor:].strip()}")
    dimensions = {component.unit.dimension for component in components}
    if len(dimensions) != 1:
        raise ValueError("Mixed input units must describe one dimension")
    if len(components) > 1 and any(component.unit.transform != "linear" for component in components):
        raise ValueError("Affine or inverse units cannot be combined as a mixed quantity")
    return tuple(components)


def _parse_target_units(text: str) -> tuple[UnitDefinition, ...]:
    direct = resolve_unit(text)
    if direct is not None:
        return (direct,)
    parts = [part.strip() for part in re.split(r"\s+and\s+", _normalize_alias(text)) if part.strip()]
    if len(parts) < 2:
        raise ValueError(f"Unsupported target conversion unit: {text}")
    units: list[UnitDefinition] = []
    for part in parts:
        unit = resolve_unit(part)
        if unit is None:
            raise ValueError(f"Unsupported target conversion unit: {part}")
        units.append(unit)
    if len({unit.dimension for unit in units}) != 1:
        raise ValueError("Mixed output units must describe one dimension")
    if len({unit.canonical for unit in units}) != len(units):
        raise ValueError("Mixed output units cannot repeat a unit")
    if any(unit.transform != "linear" for unit in units):
        raise ValueError("Affine or inverse units cannot be mixed output units")
    factors = [unit.factor for unit in units]
    if any(factor is None for factor in factors) or factors != sorted(factors, reverse=True):
        raise ValueError("Mixed output units must be ordered from largest to smallest")
    return tuple(units)


def _context_payload(conversion_context: dict[str, object] | None) -> dict[str, object]:
    payload = conversion_context.get("payload") if isinstance(conversion_context, dict) else None
    if not isinstance(payload, dict):
        raise ValueError("That conversion reference is no longer available")
    return payload


def _parse_decimal_places(text: str) -> tuple[str, int | None]:
    match = re.search(
        r"(?:,?\s+(?:rounded\s+)?to|,?\s+with)\s+([a-z0-9-]+)\s+decimal places?$",
        text,
    )
    if match is None:
        return text, None
    parsed = parse_number(match.group(1))
    if parsed is None or parsed != parsed.to_integral_value() or not 0 <= parsed <= _MAX_DECIMAL_PLACES:
        raise ValueError("Conversion precision supports zero through twelve decimal places")
    return text[: match.start()].strip(), int(parsed)


def parse_conversion_query(
    text: str,
    *,
    conversion_context: dict[str, object] | None = None,
) -> ConversionQuery | None:
    normalized, decimal_places = _parse_decimal_places(_normalize_text(text))
    if not normalized:
        return None

    value_followup = re.fullmatch(r"what about (?P<source>.+)", normalized)
    if value_followup is not None:
        payload = _context_payload(conversion_context)
        source_text = value_followup.group("source")
        if parse_number(source_text) is not None:
            source_unit = str(payload.get("source_unit") or "")
            if "+" in source_unit or resolve_unit(source_unit) is None:
                raise ValueError("A number-only follow-up requires one prior source unit")
            source_text = f"{source_text} {source_unit}"
        source_components = _parse_quantity(source_text)
        target_names = [part for part in str(payload.get("target_unit") or "").split("+") if part]
        if not target_names:
            raise ValueError("The prior conversion target is unavailable")
        target_units = tuple(resolve_unit(name) for name in target_names)
        if any(unit is None for unit in target_units):
            raise ValueError("The prior conversion target is invalid")
        return ConversionQuery(
            source_components=source_components,
            target_units=tuple(unit for unit in target_units if unit is not None),
            source_text=source_text,
            target_text=" and ".join(target_names),
            decimal_places=decimal_places,
        )

    target_followup = re.fullmatch(r"and (?P<target>[a-z0-9/ ]+)", normalized)
    if target_followup is not None:
        payload = _context_payload(conversion_context)
        target_units = _parse_target_units(target_followup.group("target"))
        try:
            base_value = Decimal(str(payload.get("value")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("The prior conversion value is invalid") from exc
        dimension = str(payload.get("dimension") or "")
        if any(unit.dimension != dimension for unit in target_units):
            raise ValueError("The requested follow-up unit is incompatible with the prior conversion")
        return ConversionQuery(
            source_components=(),
            target_units=target_units,
            source_text=str(payload.get("display_text") or "the prior value"),
            target_text=target_followup.group("target"),
            decimal_places=decimal_places,
            base_value=base_value,
        )

    patterns = (
        r"^convert (?P<source>.+?) to (?P<target>.+)$",
        r"^(?:what is|what's) (?P<source>.+?) in (?P<target>.+)$",
        r"^how many (?P<target>.+?) (?:are in|is|are) (?P<source>.+)$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, normalized)
        if match is None:
            continue
        source_text = match.group("source").strip()
        target_text = match.group("target").strip()
        return ConversionQuery(
            source_components=_parse_quantity(source_text),
            target_units=_parse_target_units(target_text),
            source_text=source_text,
            target_text=target_text,
            decimal_places=decimal_places,
        )
    return None


def is_conversion_query(text: str) -> bool:
    normalized = _normalize_text(text)
    what_about = re.fullmatch(r"what about (.+)", normalized)
    if what_about is not None:
        if parse_number(what_about.group(1)) is not None:
            return True
        try:
            _parse_quantity(what_about.group(1))
            return True
        except ValueError:
            return False
    target_followup = re.fullmatch(r"and ([a-z0-9/ ]+)", normalized)
    if target_followup is not None:
        try:
            _parse_target_units(target_followup.group(1))
            return True
        except ValueError:
            return False
    if not normalized.startswith(("convert ", "what is ", "what's ", "how many ")):
        return False
    if re.search(r"\b(?:dollars?|euros?|pounds sterling|yen|currency)\b|\$", normalized):
        return " to " in normalized or " in " in normalized
    if re.search(r"\b(?:months?|years?)\b", normalized):
        return " to " in normalized or " in " in normalized
    try:
        return parse_conversion_query(normalized) is not None
    except ValueError:
        return False


def _to_base(value: Decimal, unit: UnitDefinition) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        if unit.transform == "linear":
            assert unit.factor is not None
            result = value * unit.factor
        elif unit.transform == "celsius":
            result = value
        elif unit.transform == "fahrenheit":
            result = (value - Decimal(32)) * Decimal(5) / Decimal(9)
        elif unit.transform == "kelvin":
            result = value - Decimal("273.15")
        elif unit.transform == "mpg_us":
            if value <= 0:
                raise ValueError("Fuel economy must be greater than zero")
            result = _FUEL_CONSTANT_MPG_US / value
        elif unit.transform == "km_per_liter":
            if value <= 0:
                raise ValueError("Fuel economy must be greater than zero")
            result = Decimal(100) / value
        elif unit.transform == "liters_per_100km":
            if value <= 0:
                raise ValueError("Fuel economy must be greater than zero")
            result = value
        else:
            raise ValueError("Unsupported unit transform")
    if unit.dimension == "temperature" and result < Decimal("-273.15"):
        raise ValueError("Temperature cannot be below absolute zero")
    if abs(result) > _MAX_ABS_VALUE:
        raise ValueError("Conversion value is outside the supported range")
    return result


def _from_base(value: Decimal, unit: UnitDefinition) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        if unit.transform == "linear":
            assert unit.factor is not None
            result = value / unit.factor
        elif unit.transform == "celsius":
            result = value
        elif unit.transform == "fahrenheit":
            result = value * Decimal(9) / Decimal(5) + Decimal(32)
        elif unit.transform == "kelvin":
            result = value + Decimal("273.15")
        elif unit.transform == "mpg_us":
            if value <= 0:
                raise ValueError("Fuel economy must be greater than zero")
            result = _FUEL_CONSTANT_MPG_US / value
        elif unit.transform == "km_per_liter":
            if value <= 0:
                raise ValueError("Fuel economy must be greater than zero")
            result = Decimal(100) / value
        elif unit.transform == "liters_per_100km":
            if value <= 0:
                raise ValueError("Fuel economy must be greater than zero")
            result = value
        else:
            raise ValueError("Unsupported unit transform")
    if abs(result) > _MAX_ABS_VALUE:
        raise ValueError("Conversion result is outside the supported range")
    return result


def _decimal_string(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _format_decimal(value: Decimal, *, dimension: str, places: int | None = None) -> tuple[str, bool]:
    explicit_places = places is not None
    if not explicit_places:
        absolute = abs(value)
        if dimension == "temperature":
            places = 1
        elif absolute == absolute.to_integral_value():
            places = 0
        elif absolute >= 100:
            places = 0
        elif absolute >= 1:
            places = 2
        else:
            places = 4
    quantum = Decimal(1).scaleb(-places)
    rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    if places == 0:
        text = f"{int(rounded):,}"
    else:
        text = format(rounded, f".{places}f")
        if not explicit_places:
            text = text.rstrip("0").rstrip(".")
    return text, rounded != value


def _unit_label(unit: UnitDefinition, value: Decimal) -> str:
    return unit.singular if abs(value) == 1 else unit.canonical


def _format_components(components: tuple[QuantityComponent, ...]) -> str:
    rendered: list[str] = []
    for component in components:
        value_text, _ = _format_decimal(component.value, dimension=component.unit.dimension)
        rendered.append(f"{value_text} {_unit_label(component.unit, component.value)}")
    return " ".join(rendered)


def _format_mixed_output(
    base_value: Decimal,
    units: tuple[UnitDefinition, ...],
    *,
    places: int | None,
) -> tuple[str, tuple[dict[str, str], ...], bool]:
    negative = base_value < 0
    remaining = abs(base_value)
    rendered: list[str] = []
    structured: list[dict[str, str]] = []
    rounded_any = False
    for index, unit in enumerate(units):
        assert unit.factor is not None
        raw = remaining / unit.factor
        if index < len(units) - 1:
            value = raw.to_integral_value(rounding=ROUND_FLOOR)
            remaining -= value * unit.factor
        else:
            value = raw
        if negative and index == 0:
            value = -value
        text, rounded = _format_decimal(value, dimension=unit.dimension, places=places if index == len(units) - 1 else 0)
        rounded_any = rounded_any or rounded
        rendered.append(f"{text} {_unit_label(unit, value)}")
        structured.append({"value": _decimal_string(value), "unit": unit.canonical})
    return " ".join(rendered), tuple(structured), rounded_any


def build_conversion_response(
    text: str,
    *,
    conversion_context: dict[str, object] | None = None,
) -> tuple[str, dict[str, object]]:
    normalized = _normalize_text(text)
    if re.search(r"\b(?:dollars?|euros?|pounds sterling|yen|currency)\b|\$", normalized):
        raise ValueError("Live currency conversion requires a provider and is not a deterministic conversion")
    if re.search(r"\b(?:months?|years?)\b", normalized):
        raise ValueError("Months and years are calendar periods, not fixed-duration conversion units")

    query = parse_conversion_query(text, conversion_context=conversion_context)
    if query is None:
        raise ValueError("Unsupported conversion request")
    source_dimension = (
        query.source_components[0].unit.dimension
        if query.source_components
        else query.target_units[0].dimension
    )
    target_dimension = query.target_units[0].dimension
    if source_dimension != target_dimension:
        source_name = source_dimension.replace("_", " ")
        target_name = target_dimension.replace("_", " ")
        if {source_dimension, target_dimension} == {"volume", "mass"}:
            raise ValueError(
                f"I can't convert {source_name} to {target_name} without material density; volume and mass are different dimensions"
            )
        raise ValueError(f"I can't convert {source_name} to {target_name} because they are different dimensions")

    if query.base_value is not None:
        base_value = query.base_value
        source_display = str(_context_payload(conversion_context).get("display_text") or "The prior value")
        source_unit_key = str(_context_payload(conversion_context).get("source_unit") or "")
    else:
        base_value = sum((_to_base(component.value, component.unit) for component in query.source_components), Decimal(0))
        source_display = _format_components(query.source_components)
        source_unit_key = "+".join(component.unit.canonical for component in query.source_components)

    if len(query.target_units) == 1:
        target = query.target_units[0]
        output_value = _from_base(base_value, target)
        output_text, rounded = _format_decimal(
            output_value,
            dimension=target.dimension,
            places=query.decimal_places,
        )
        output_display = f"{output_text} {_unit_label(target, output_value)}"
        output_components: tuple[dict[str, str], ...] = (
            {"value": _decimal_string(output_value), "unit": target.canonical},
        )
    else:
        output_display, output_components, rounded = _format_mixed_output(
            base_value,
            query.target_units,
            places=query.decimal_places,
        )
        output_value = None

    qualifier = "about " if rounded and query.decimal_places is None else ""
    speech = f"{source_display} is {qualifier}{output_display}."
    target_unit_key = "+".join(unit.canonical for unit in query.target_units)
    details: dict[str, object] = {
        "kind": "conversion",
        "dimension": target_dimension,
        "base_value": _decimal_string(base_value),
        "source_unit": source_unit_key,
        "target_unit": target_unit_key,
        "output_components": output_components,
        "display_text": output_display,
        "rounded": rounded,
    }
    if output_value is not None:
        details["output_value"] = _decimal_string(output_value)
        details["output_unit"] = query.target_units[0].canonical
    if query.decimal_places is not None:
        details["decimal_places"] = query.decimal_places
    return speech, details
