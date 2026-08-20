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
- act on suggestions automatically

Memory writes are factual records from trusted internal callers.

Memory reads are for diagnostics, review, UI/admin display, tests, and future read-only operational analysis.

## Live Write Failure Policy

Live Memory writes must fail open.

Exceptions are durable runbook lifecycle persistence needed to prevent an
untracked or replay-unsafe mutation and required alert lifecycle mutations
needed to prevent loss or duplicate delivery. Those owner paths fail closed.
Optional audit/event writes remain fail-open.

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

The previous Suggestions SQLite store was provisional migration input.
Suggestion runs, the one current packet/response exchange, suggestions, and
suggestion reviews are operational Memory records in the canonical database.

Do not create separate durable SQLite stores for operational records without a documented reason and operator approval.

## Required Core Tables

Memory schema `0009_durable_alerts` includes:

- `memory_schema_migrations`
- `memory_users`
- `memory_sources`
- `memory_events`
- `memory_orchestration_runs`
- `memory_orchestration_steps`
- `memory_sessions`
- `memory_transcripts`
- `memory_current_projections`
- `memory_notification_deliveries`
- `memory_alerts`
- `memory_alert_transitions`
- `suggestion_runs`
- `suggestions`
- `suggestion_reviews`
- `suggestion_exchange_current`

The orchestration tables are also the compatibility store for the staged
runbook-kernel extraction. Schema version `0004_runbook_kernel_metadata` adds
definition domain/version, correlation, activation idempotency, controller
state/version, and cancellation provenance without rewriting existing run
history.

Canonical configuration is the sole current identity authority. On startup or
activation, Memory reconciles configured users and sources to `active`,
`disabled`, or `retired` historical dimensions. Known V1 satellite aliases are
rewritten only when the configured satellite-to-source binding proves a unique
canonical owner; unknown identities fail closed and remain retired. Historical
null users are not inferred. User dimensions contain no role vocabulary.

Only real internal producers (`brain`, `system`, `api`, `ui`, and `background`)
have code-owned source dimensions. Conversation is a modality, not a generic
source. Existing Memory rows do not authorize a retired source.

`memory_current_projections` is a current-state upsert model. It is not snapshot
history, is never age-pruned, and derives staleness from `observed_at`.

The alerts domain owns alert behavior while Memory owns its transactions.
Active alert rows carry payload and lease state; transition rows preserve the
required mutation audit. Active rows block source retirement. Satellite
notification outcomes also use `memory_notification_deliveries`; local timer,
alarm, reminder, and sleep-timer records do not.

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

`payload_json` is allowed for provider-specific details, low-frequency fields, non-critical metadata, raw response fragments, and transitional migration data.

## Retention

Retention must be explicit, configurable, and testable.

Typed configuration is the only runtime retention authority. The approved
defaults are:

- successful raw transcripts: 14 days
- failed or low-confidence raw transcripts: 30 days
- transcript metadata: 90 days
- routine events: 90 days
- warning events: 180 days
- error events: 365 days
- critical events: 730 days
- provider status events: 180 days
- lifecycle events: 365 days
- session metadata: 90 days, with active sessions protected
- orchestration terminal runs and steps: 365 days, atomically
- terminal alerts: 90 days; pending and leased alerts protected
- notification receipts: accepted/suppressed 90 days; failed/expired 365 days
- Suggestions raw evidence and run diagnostics: 90 days
- current Suggestions packet/response: 30 days, overwritten rather than versioned
- completed Suggestions envelopes: 365 days; active envelopes protected
- mock suggestions: 30 days

Real unreviewed suggestions remain until review. Compact reviewed records remain
durable until explicitly deleted or superseded. Current projections do not
expire by age.

One Memory retention executor produces an exact class-by-class dry-run and
fails closed for unknown classes, states, severities, categories, invalid
timestamps, and future timestamps. It deletes transcripts before their session,
protects active sessions and running/waiting work, transitions genuinely overdue
notification receipts before later retention, and emits one aggregate
`retention_pruned` event only when an apply changes data. Slice 6 does not enable
destructive live enforcement; the reviewed first apply, bounded recovery point,
and recovery-point disposal belong to the coordinated Slice 10 cutover.

Raw transcript storage must be separable from derived transcript metadata.

## V2 Configuration Reconciliation

Memory is not configuration authority. Stable request-source definitions and
associations come from `household.yaml`; authenticated ephemeral sources are
runtime observations; Brain, system, API, UI, and background workers are actors
rather than household source entries.

Memory may record sanitized configuration lifecycle audit events and observed
selected, applied, and projection generation IDs. It does not store authored
bundles, raw secrets, secret fingerprints, or values that override
`EffectiveConfig`.
