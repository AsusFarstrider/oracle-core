# Calendar Write Contract

This contract defines the behavioral guarantees for calendar write flow ownership and safety.

## Surface Split

Calendar read and calendar write are distinct surfaces.

They must not blur in:

- routing
- parsing
- confirmation behavior
- state handling

## Confirmation Requirement

Calendar writes require explicit confirmation before commit.

Oracle must not create a calendar event immediately after initial parsing, even when the request appears obvious.

## State Ownership

Voice calendar-write pending state is:

- brain-owned
- session-scoped
- short-lived

Satellite layers must not own, infer, or preserve calendar-write draft state or confirmation state.

Structured UI calendar-write state may use:

- brain-owned UI draft state
- short-lived `draft_id`-scoped state
- dedicated `/api/ui/calendar/*` endpoints

The UI path and voice path must remain separate interaction models even though both commit through the brain calendar domain.

## Cancellation Boundary

Unrelated commands cancel pending calendar-write state.

Oracle must not carry calendar-write draft or confirmation state across a context break.

UI draft cancellation and expiry may follow a different lifecycle than voice session cancellation, but stale UI drafts must still expire and must not commit without explicit confirmation.

## Commit Integrity

No calendar write may mutate after confirmation.

The event that is committed must exactly match the event that was confirmed.

If any field changes after confirmation, the prior confirmation is invalid and the flow must return to clarification and confirmation before commit.

## All-Day Support

Calendar write supports all-day events.

Rules:

- if an event is marked all-day, it must not also carry timed-event semantics
- all-day events must not require `start_time`
- all-day events must not require `end_time`
- all-day events must not require duration
- timed events must not be ambiguously treated as all-day

This applies to both:

- the voice calendar-write path
- the structured `/api/ui/calendar/*` create path

## V2 Configuration Reconciliation

Calendar provider/account selection and write policy belong to
`domains/calendar.yaml`; logical user credential references, if introduced,
belong to the owning household user capability. Configuration migration and
activation do not weaken draft, clarification, confirmation, timezone, or
single-commit safety behavior.
