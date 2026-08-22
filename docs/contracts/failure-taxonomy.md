# Oracle Failure Taxonomy

Last updated: 2026-04-11

## Purpose

This document defines Oracle's route/parser/fallback failure taxonomy.

It defines:

- the accepted failure categories
- the primary pipeline-stage grouping for those categories
- the canonical taxonomy outcome labels used to describe expected handling

This is a contract taxonomy document, not a replacement for the roadmap.

## Failure Envelope

At shared-runtime seam boundaries, explicit failure classification must include:

- `failure_class`
- `owning_component`

These fields describe internal ownership and inspection path.

They do not replace user-visible reply wording.

## Shared Runtime Failure Classes

The shared runtime uses the following failure classes at important seam boundaries:

- `router_failure`
- `domain_failure`
- `transport_failure`
- `authority_mismatch`
- `stt_failure`
- `tts_failure`
- `control_service_failure`
- `startup_validation_failure`
- `contract_failure`

Classification rule:

- classify by where the failure occurred, not by the user-visible symptom
- malformed fallback-router output is `router_failure`
- downstream execution failure stays with the domain or control path that failed
- transport or reachability failure does not get relabeled as domain behavior
- authority truth that contradicts handoff or resume behavior is `authority_mismatch`
- invalid seam input or output shape is `contract_failure`

## Primary Grouping Rule

Failures are classified primarily by pipeline stage, not by user-visible symptom.

User-visible symptoms may be noted inside each category, but the top-level grouping stays attached to where Oracle's interpretation failed.

## Accepted Categories

### 1. `route_miss`

Definition:

- Oracle routes to the wrong target before domain-specific parsing or execution has a chance to succeed.

Examples:

- general informational fallback drifts into `home_assistant`
- probable audiobook title stays in `music` when the route should have switched
- room-first Home Assistant phrasing falls through to `fallback_router`

Expected ownership:

- routing capability selection
- route refinement
- route-time normalization helpers

### 2. `parser_miss`

Definition:

- Oracle reaches the right domain, but the domain parser fails to recognize a natural phrasing that should have been supported.

Examples:

- music parser misses a reordered title/artist phrase
- audiobook parser misses narrator or edition wording
- audiobook parser misses series-order play phrasing

Expected ownership:

- domain parser and normalization logic

### 3. `clarification_policy_miss`

Definition:

- Oracle has candidate evidence, but clarification behavior is too literal, too broad, or too weakly bounded.

Examples:

- valid edition shorthand is rejected during audiobook clarification
- pending clarification over-captures unrelated turns
- deterministic narrowing exists but the clarification state does not reflect it correctly

Expected ownership:

- pending clarification matching
- pending-state routing gates
- clarification prompt shaping

### 4. `cross_domain_rescue_miss`

Definition:

- Oracle should have rescued between media domains deterministically, but failed to do so or did so too aggressively.

Examples:

- weak music hit beats a clearly stronger audiobook match
- music-to-audiobook rescue triggers on too small a score gap
- a real title-matching music result is displaced without meeting the stricter rescue threshold

Expected ownership:

- media rescue policy
- music scoring/fallback thresholds

### 5. `fallback_policy_miss`

Definition:

- Oracle reaches the last-resort fallback layer too early, too late, or for the wrong kind of uncertainty.

Examples:

- Ollama best-guess runs even though deterministic clarification was defensible
- a generic informational utterance does not reach `fallback_router`
- a media request becomes an ungrounded fallback instead of a bounded deterministic clarification

Expected ownership:

- rescue-policy ordering
- Ollama fallback boundaries

### 6. `hard_not_found`

Definition:

- Oracle bottoms out in a deterministic not-found result after the intended route/parser/clarification/rescue sequence has been exhausted.

Examples:

- out-of-library title with no defensible deterministic or bounded fallback candidate
- audiobook search has no valid series entry for a requested ordinal

Expected ownership:

- final domain result handling
- explicit failure wording and no-result boundaries

## Expected Behaviors

The canonical taxonomy outcome labels are:

- `route_only`
  Oracle should select the intended route target without needing a special clarification or rescue path.
- `deterministic_clarify`
  Oracle should produce a bounded deterministic clarification from real candidate evidence.
- `clarification_narrow`
  Oracle should deterministically narrow an existing clarification state and re-prompt from the narrowed set.
- `cross_domain_rescue`
  Oracle should deterministically rescue from one media domain to the other.
- `fallback_router_last_resort`
  Oracle should reach fallback interpretation only after deterministic routing and clarification options are not defensible.
- `hard_not_found`
  Oracle should return a deterministic not-found result after the allowed rescue path is exhausted.

## Configuration Lifecycle Failures

Configuration validation findings, activation blockers, and operational-
readiness failures are separate categories. Severity and `blocks_activation`
are separate fields. Provider unavailability is readiness, not deterministic
configuration invalidity. No configuration failure may be silently converted
into legacy fallback or field-level precedence.
