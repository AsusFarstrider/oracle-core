# Oracle Memory Contract

## Purpose

This document defines Oracle Memory as the owner of durable structured operational storage.

Oracle Memory records operational reality:

- what Oracle knew
- what Oracle heard
- what Oracle did
- what Oracle tried
- what failed
- what external systems reported
- what state existed at a given time

Memory is not the brain. Memory does not decide, route, dispatch, execute, or generate replies.

## Ownership Rule

Memory owns durable operational storage.

The brain owns:

- routing decisions
- command decisions
- state interpretation
- reply generation
- session behavior

Domains own:

- domain behavior
- domain execution
- domain validation
- domain result shaping

Provider bridges own:

- external service communication
- provider request/response translation
- provider health observation

UI owns:

- presentation
- operator review workflows
- controls backed by existing API boundaries

## Non-Execution Boundary

Memory must not:

- execute commands
- route requests
- dispatch actions
- call Home Assistant write APIs
- call LibreNMS write APIs
- control services
- restart services
- change voice behavior
- change routing behavior
- change reply generation
- act on suggestions or annotations automatically

Memory writes are factual records from trusted internal callers.

Memory reads are for diagnostics, review, UI/admin display, tests, and future read-only operational analysis.

## Live Write Failure Policy

Live Memory writes must fail open.

Exception: durable runbook lifecycle persistence that is required to prevent an
untracked or replay-unsafe mutation must fail closed before that mutation
begins. This includes creating the run and planned operation records required
by the runbook lifecycle contract. Optional audit/event writes remain
fail-open.

If a Memory write fails:

- Oracle startup must continue unless an existing non-Memory failure would already stop it
- routing must not change
- dispatch must not change
- command behavior must not change
- provider behavior must not change
- voice behavior must not change
- Home Assistant behavior must not change
- LibreNMS behavior must not change
- the failure should be logged as a warning
- the Memory exception must not escape the live write helper

Memory write success or failure must never become a condition for executing Oracle behavior.

## Durable Store

The canonical Oracle Memory SQLite path is:

- `data/oracle-memory.sqlite3`

The previous Suggestions SQLite store was provisional. Suggestion runs, suggestion packets, suggestion responses, suggestions, and suggestion reviews are operational memory records.

Suggestions-specific tables may remain specialized, but durable Suggestions storage must live inside the Memory-owned database or behind a clearly transitional adapter.

Do not create separate durable SQLite stores for operational records without a documented reason and operator approval.

## Required Core Tables

Initial Memory storage must include:

- `memory_schema_migrations`
- `memory_users`
- `memory_sources`
- `memory_events`
- `memory_orchestration_runs`
- `memory_orchestration_steps`

The orchestration tables are also the compatibility store for the staged
runbook-kernel extraction. Schema version `0004_runbook_kernel_metadata` adds
definition domain/version, correlation, activation idempotency, controller
state/version, and cancellation provenance without rewriting existing run
history.

Satellite activity may create or update a `memory_sources` row only when the
canonical source ID is currently present as `source_type: satellite` in the
current V1 Brain source registry. In canonical V2 the equivalent check uses the
enabled household source plus managed satellite binding. Existing Memory rows do
not authorize a retired source, and failure to load authoritative configuration
fails closed for new activity writes. Memory must not carry a separate hard-
coded satellite allowlist.

Future tables may include:

- `memory_sessions`
- `memory_transcripts`
- `memory_snapshots`
- `memory_cache_entries`
- `memory_rollups`
- `memory_evidence_refs`
- `memory_annotations`

## Event Taxonomy

Event names must be consistent, reviewable, and documented before broad runtime wiring.

Initial required event categories:

- `system.lifecycle`
- `system.config`
- `routing`
- `command`
- `provider.status`
- `satellite.status`
- `transcript`
- `external.home_assistant`
- `external.librenms`
- `memory`

Initial event names are defined in the active Oracle Memory roadmap and enforced by Memory taxonomy code.

Unknown event names must be rejected by Memory write helpers unless an explicit migration or review path adds them to the taxonomy.

## Correlation IDs

Every session, transcript, command attempt, provider observation, structured event, evidence reference, and applicable source record should support `correlation_id`.

Rules:

- create a `correlation_id` at the earliest reliable boundary
- propagate it through internal calls where practical
- store it on Memory records where applicable
- never require `correlation_id` to execute behavior
- absence of `correlation_id` must not break runtime behavior
- missing `correlation_id` should be visible in diagnostics

## `payload_json` Discipline

`payload_json` must not hide fields that are frequently queried, filtered, joined, indexed, or displayed in admin UI.

The following must be first-class columns where applicable:

- `event_type`
- `severity`
- `source_id`
- `session_id`
- `correlation_id`
- `provider`
- `domain`
- `status`
- `observed_at`
- `user_id`
- route/domain decision
- failure stage
- fingerprint/classification

`payload_json` is allowed for provider-specific details, low-frequency fields, non-critical metadata, raw response fragments, and transitional migration data.

## Retention

Retention must be explicit, configurable, and testable.

Planning defaults:

- successful raw transcripts: 14 days
- failed or low-confidence raw transcripts: 30 days
- transcript metadata: 90 days
- routine events: 90 days
- warning events: 180 days
- error events: 365 days
- critical events: 730 days
- provider status events: 180 days
- lifecycle events: 365 days
- snapshots: hourly for 14 days, daily compacted for 90 days
- cache history: 30 days where history is useful
- rollups: 365 days by default, configurable
- evidence refs: 90 days or until referenced raw evidence is expected to age out, whichever is shorter

Retention actions must emit structured Memory events once live retention is implemented.

Raw transcript storage must be separable from derived transcript metadata.

## Evidence References

Memory should not copy raw logs wholesale.

Memory may store:

- fingerprints
- counts
- first seen time
- last seen time
- classifications
- representative samples
- raw evidence references

Raw logs remain raw logs. Memory stores structured summaries and references.

## Log Rollups

Rollups must be deterministic, not fuzzy.

Pipeline:

1. parse
2. normalize
3. fingerprint
4. classify by known rule
5. store event, store rollup, or mark for review

Unknown repetitive patterns must not be silently discarded. They must become review-needed rollups.

Oracle Memory must not become a continuous raw-log-eating daemon.

## Annotations

Operator annotations are allowed, but they must remain separate from factual records.

Annotations may attach to sessions, transcripts, events, rollups, snapshots, sources, or suggestions.

Annotations do not alter factual records.

## V2 Configuration Reconciliation

Memory is not configuration authority. Stable request-source definitions and
associations come from `household.yaml`; authenticated ephemeral sources are
runtime observations; Brain, system, API, UI, and background workers are actors
rather than household source entries.

Memory may record sanitized configuration lifecycle audit events and observed
selected, applied, and projection generation IDs. It does not store authored
bundles, raw secrets, secret fingerprints, or values that override
`EffectiveConfig`.
