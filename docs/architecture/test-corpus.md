# Oracle Test Corpus

This document records the small support surface around the Oracle test corpus used by current regression fixtures.

## Fixture Relationship

The current machine-readable corpus fixture lives at:

- `tests/fixtures/phase_c_utterance_bank.json`

## Metadata Labels

### `risk`

Allowed values:

- `high`
- `medium`
- `low`

Meaning:

- `high`: user-visible misses with higher regression cost
- `medium`: important natural-language coverage with meaningful regression value
- `low`: useful breadth coverage with lower immediate regression cost

### `origin`

Allowed values:

- `synthetic`
- `operator_observed`

Meaning:

- `synthetic`: proactively generated phrasing in the corpus
- `operator_observed`: phrasing grounded in operator-observed testing or debug history

### `coverage`

Allowed values:

- `fixture_only`
- `route_executed`
- `handler_executed`

Meaning:

- `fixture_only`: present in the corpus without executable regression coverage
- `route_executed`: exercised through routing or context-selection checks
- `handler_executed`: exercised through handler-level execution paths

## Corpus Coverage Note

The current corpus is a focused regression surface rather than a full language dataset. It covers selected routing, clarification, rescue, and not-found cases that support active regression checks.
