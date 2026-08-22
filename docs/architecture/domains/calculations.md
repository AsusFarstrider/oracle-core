# Calculations

Calculations and unit conversions are part of the system route and system-handler path rather than a dedicated calculation dispatch target.

## Structure

The current subsystem is split across:

- `server/oracle_app/calculations.py` for unit normalization, conversion, expression evaluation, query parsing, and result construction
- system-intent classification in `server/oracle_app/system_intents.py` plus capability routing in `server/oracle_app/capabilities/plugins.py`
- `server/oracle_app/handlers/system.py` for system-target execution

## Responsibilities

The current subsystem is responsible for:

- classifying calculation and conversion requests
- parsing conversion-style queries
- normalizing unit names against the current unit alias table
- evaluating arithmetic expressions through the constrained math evaluator
- building spoken summaries and structured result details
- executing through the system handler

## Data Shapes

The current implementation centers on:

- `ConversionUnit` as the internal unit-definition shape
- structured result payloads for conversion and arithmetic responses

## Current Surface

The current subsystem surface includes:

- arithmetic evaluation requests
- unit conversion requests across the currently supported unit categories

## Boundary

The calculations subsystem resolves arithmetic and unit-conversion requests on the brain and returns results through the system handler.
