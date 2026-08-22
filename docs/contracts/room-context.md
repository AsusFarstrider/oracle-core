# Oracle Room Context

## Purpose

This document defines Oracle's room-context contract.

It defines:

- the source model for fixed and non-fixed sources
- the room-resolution contract
- the precedence order for room resolution
- the session boundary for room continuity
- the pending-clarification rules for unresolved room-relative commands
- the safety rules for room-required execution
- the routing and execution boundary for resolved room context

## Source Model Contract

Oracle owns stable request sources in `household.yaml`. The selected canonical
configuration generation is the only reusable runtime source authority.

The selected canonical configuration generation is authoritative for:

- source type
- whether a source is fixed
- an associated room when one exists

Required fields:

- `type`
- `enabled`
- `fixed`
- `associated_room_id` only when `fixed=true`

Stable sources are listed explicitly and authenticated through their configured
ingress. An authenticated ingress may assign an ephemeral source identity, but
it is non-fixed, roomless, and has no household configuration entry.

An enabled satellite's on-device UI may establish that satellite source through
the canonical host-local peer rule: the direct HTTP peer must match the host in
the satellite's configured Brain-facing control-service URL. This is a
host-level satellite trust boundary, not authority derived from a request body
or forwarded-client header.

An arbitrary client-supplied source string never creates an implicit source or
receives an association.

## Room Resolution Contract

The resolver uses:

- current utterance text
- normalized home-command text where relevant
- source
- session context when allowed
- authenticated source association
- room vocabulary

In canonical mode, room vocabulary consists only of enabled `household.yaml`
room IDs, display names, and aliases from the applied immutable snapshot. Home
Assistant area or entity discovery may provide operational provider evidence,
but it cannot create or rename an Oracle room, add a canonical room alias, or
override a configured room match. Migration-only room evidence remains private
and is never consulted by canonical requests.

The resolver output must include:

- `resolved_text`
- `resolved_room`
- `resolution_source`
- `room_required`
- `needs_clarification`

`resolved_text` is the canonical execution input.

`resolution_source` uses the following vocabulary:

- `explicit_room`
- `deictic_source_association`
- `session_room`
- `source_association_fallback`
- `unresolved`
- `not_needed`

Resolver metadata must be carried forward.

## Resolution Precedence

The resolution law is branch-aware:

1. explicit room mention in the current utterance always wins;
2. explicitly deictic or local wording uses the authenticated fixed source's
   associated room and must not borrow unrelated weak session room context;
3. a valid weak short-lived session room may resolve an ordinary non-deictic
   follow-up;
4. an ordinary non-deictic room-required command may fall back to the
   authenticated fixed source's associated room after session context; and
5. otherwise Oracle clarifies or fails safely.

Precedence rules:

- explicit current-utterance wording always wins
- deictic wording outranks session carryover
- session room remains weak and bounded
- source association is context, not authentication, authorization, or
  permanent room truth

## Session Contract

Sessions do not own room truth.

Sessions may hold a weak short-lived room reference only when follow-up continuity needs it.

### `active_room_ref`

`active_room_ref` is:

- weak
- short-lived
- home-only
- derived from what Oracle actually resolved and acted on

### Writeback Rules

Oracle writes `active_room_ref` only after the shared resolver has produced a concrete room for a `home_assistant` request.

Oracle prefers execution-time resolved room over text-only inference.

Oracle also writes `active_room_ref` for pending home flows already anchored to that resolved room.

Oracle does not write `active_room_ref` for:

- non-home targets
- ambiguity
- clarification miss
- safe-failure cases

### Session Carryover Rules

Session room carryover is allowed only when all of the following are true:

- the active context target is `home_assistant`
- the active context anchor is `strong`
- the prior home action resolved to a concrete room
- the new utterance is a plausible home follow-up
- the new utterance does not contain explicit room wording or stronger deictic or local wording

## Pending Clarification Contract

Oracle must not silently pass unresolved room-relative commands through to Home Assistant.

Oracle uses `pending_clarification` for unresolved room-relative commands.

### Bare Room Reply Rules

A bare room name resolves only an active pending room clarification.

A bare room name does not broadly retarget ordinary strong home context on its own.

### Structured Continuation Requirement

Oracle stores a structured continuation payload.

Oracle does not rely only on normalized text.

The continuation payload includes:

- `original_text`
- `normalized_text`
- `resolved_target`
- `room_requirement`
- `room_slot`
- `injection_kind`

`injection_kind` uses a bounded vocabulary:

- `lights_room`
- `climate_room`
- `generic_in_the_room`

## Safety Contract

Unresolved room-required commands must not execute unresolved.

Unresolved room-relative commands must clarify or fail safely.

Unresolved or invalid source-association drift must never cause blind action.

Ordinary non-room-dependent home commands may pass through normally when room is not needed.

## Routing And Execution Boundary

Oracle performs room resolution and text rewrite in the brain routing or normalization stage for home commands.

Oracle performs room resolution before dispatch planning.

Dispatch remains thin.

`resolved_text` is the canonical Home Assistant execution input.

Resolver metadata travels alongside the text for logging, session writeback, and safety checks.

An execution-time guard is required.

A room-required command must not execute unresolved if routing failed to resolve it.

Migration maps `default_room`, `source_default`, and equivalent V1 vocabulary
into `associated_room_id` and the canonical resolution-source terms. Tests are
updated with the runtime language; legacy names are retained only for a proven
external consumer with an owner and removal gate.
