# Calculations

Calculations and unit conversions are part of the system route and system-handler path rather than a dedicated calculation dispatch target.

## Structure

The current subsystem is split across:

- `server/oracle_app/math_calculator.py` for strict Math recognition, parsing,
  typed rational/decimal evaluation, bounded domain operations, human
  formatting, and structured results
- `server/oracle_app/unit_converter.py` for the governed unit/dimension catalog,
  strict quantity parsing, exact canonical-base conversion, affine and inverse
  transforms, mixed quantities, human precision, and structured results
- `server/oracle_app/calculations.py` for the retained calculation entrypoint,
  date compatibility and delegation to the Math and Conversion owners
- `server/oracle_app/deterministic_values.py` for the Stage 6 strict shared
  numeric, ordinal, and compound-duration primitives
- system-intent classification in `server/oracle_app/system_intents.py` plus capability routing in `server/oracle_app/capabilities/system.py`
- `server/oracle_app/handlers/system.py` for system-target execution

## Responsibilities

The current subsystem is responsible for:

- classifying calculation and conversion requests
- strictly parsing the finite Stage 6 Math grammar without deleting unknown
  tokens
- evaluating arithmetic, fractions, percentages, fixed-value money,
  powers/roots, aggregation, proportions, and bounded geometry
- preserving exact rational results where possible while exposing separately
  rounded human display text
- resolving calculation follow-up only from the canonical typed effective
  session context
- parsing ordinary, mixed-unit, precision-qualified, and contextual conversion
  requests through one Conversion owner
- resolving unit aliases against one explicit code-owned catalog
- validating dimensions and converting through exact canonical bases, affine
  temperature transforms, or bounded inverse fuel-economy transforms
- evaluating arithmetic expressions through the constrained Math evaluator
- building spoken summaries and structured result details
- executing through the system handler

## Data Shapes

The current implementation centers on:

- `UnitDefinition`, `QuantityComponent`, and `ConversionQuery` as the typed
  Conversion catalog/query shapes
- `MathResult` as the typed internal calculation value and presentation input
- structured result payloads carrying exact value, display value, operation,
  and bounded unit/currency metadata

## Current Surface

The current subsystem surface includes:

- arithmetic with precedence, negatives, decimals, spoken numbers, and
  fractions
- percentage, tip/tax/discount/split, power/root, aggregation, proportional
  scaling, and finite explicit-input geometry requests
- explicit rounding and short calculation follow-up within the existing
  source/session lifecycle
- linear, affine, inverse, derived, and mixed-unit conversion requests across
  every governed category below

## Boundary

The calculations subsystem resolves arithmetic and unit-conversion requests on the brain and returns results through the system handler.

## Stage 6 Boundary

Stage 6 retains the system route/handler authority and separates strict Math
recognition/parsing, typed evaluation, and human formatting from governed
conversion semantics. This is a cohesive utility boundary, not a new top-level
route target or universal grammar.

Unknown tokens reject instead of being stripped into another valid expression.
Structured result metadata feeds the existing effective-session lifecycle for
bounded follow-up. Math and conversion may share numeric/value primitives, but
conversion dimension semantics remain conversion-owned and timezones remain
Time/Date-owned. LLM fallback cannot compute results.

Slice 4 replaces the permissive Math evaluator. Unknown tokens, arbitrary AST
nodes, excessive expression complexity, out-of-range powers/results, division
by zero, and invalid root domains reject rather than being reinterpreted.
Exact result metadata is the only source for follow-up; reply text is never
scraped back into a value. Geometry and proportion handling remain a finite
catalog with explicit values and no hidden estimation assumptions.

Slice 5 replaces the legacy float factor table. Conversion recognition and
execution now share one owner, and canonical base values—not reply text—feed
the existing typed conversion session slot. Unknown units, incompatible
dimensions, calendar-variable months/years, and excluded provider-backed
currency fail honestly. Timezone conversion remains owned by Time/Date.

## Governed Conversion Catalog

The executable catalog in `unit_converter.py` is closed and code-owned. Alias,
factor, transform, or canonical-unit changes are reviewed behavior changes.
Decimal prefixes use powers of 1,000 and binary prefixes use powers of 1,024;
they are never treated as synonyms.

| Dimension | Canonical base | Supported canonical units |
| --- | --- | --- |
| Length | meter | millimeters, centimeters, meters, kilometers, inches, feet, yards, miles, nautical miles |
| Area | square meter | square millimeters/centimeters/meters/kilometers, square inches/feet/yards/miles, acres, hectares |
| Volume | liter | milliliters, liters, teaspoons, tablespoons, cups, fluid ounces, pints, quarts, US gallons, cubic inches/feet/centimeters/meters |
| Mass | kilogram | milligrams, grams, kilograms, ounces, pounds, US tons, metric tonnes, stone |
| Speed | meter per second | meters/feet per second, kilometers/miles per hour, knots |
| Fixed time | second | milliseconds, seconds, minutes, hours, days, weeks |
| Data | bit | bits/bytes with decimal kilo/mega/giga/tera and binary kibi/mebi/gibi/tebi prefixes |
| Data rate | bit per second | bits/bytes per second with decimal kilo/mega/giga/tera prefixes |
| Pressure | pascal | pascals, kilopascals, PSI, bar, atmospheres, inches of mercury |
| Power | watt | watts, kilowatts, horsepower |
| Energy | joule | joules, kilojoules, watt-hours, kilowatt-hours, BTU |
| Temperature | celsius | celsius, fahrenheit, kelvin |
| Fuel economy | liters per 100 kilometers | US miles per gallon, kilometers per liter, liters per 100 kilometers |

Mixed input adds components only within one linear dimension. Mixed output is
explicitly ordered largest-to-smallest, such as feet then inches or hours then
minutes. Generic volume-to-mass conversion is invalid without density. MPG is
the explicit US-gallon definition; live currency rates and calendar-variable
month/year durations are not members of this catalog.
