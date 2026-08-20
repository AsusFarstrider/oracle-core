# Oracle Test Corpus

This document records the small support surface around the Oracle test corpus used by current regression fixtures.

## Fixture Relationship

The canonical machine-readable deterministic command ledger and its disposition
matrix live at:

- `tests/fixtures/utterance_ledger.json`
- `tests/fixtures/utterance_capability_matrix.json`

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

### `execution_level`

Allowed values:

- `route_executed`
- `handler_executed`

Meaning:

- `route_executed`: exercised through routing or context-selection checks
- `handler_executed`: exercised through handler-level execution paths

Every supported entry is executable at one of those two levels. Missing or
deferred capability behavior belongs in the disposition matrix with a durable
roadmap destination rather than appearing as a false passing case.

Each ledger entry also names its stable owner and `capability_disposition`.
Where deterministic, `expected_action` records the action below the route
target. The six facts regression cases are read from this ledger; there is no
second Python-authored utterance bank.

## Corpus Coverage Note

The ledger is a focused regression surface rather than a claim of exhaustive
natural-language understanding. It covers every current deterministic owner,
including representative routing, clarification, rescue, collision, and
not-found behavior. Model-prompt conformance remains a separate test surface.
