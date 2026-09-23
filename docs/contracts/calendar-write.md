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

Stage 8 edit, reschedule, and delete/cancel use the same confirmation authority.
Delete/cancel is always consequential. A confirmation freezes the exact event
identity, calendar, mutation kind, recurrence scope, and resulting bounded
ordinary fields; any change invalidates it.

## Existing-Event Selection

An existing-event mutation begins with deterministic selection from canonical
Calendar evidence. Selection may use the requested calendar/person/source,
bounded time window, title, location, and other already-normalized Calendar
fields. Zero matches fail honestly. Multiple plausible matches produce a
bounded clarification and no mutation. Oracle never selects by provider order,
an arbitrary first result, or a hidden default calendar.

Selection and execution preserve the existing canonical user, authenticated
source context, configured calendar association, selected calendar, and
per-user default-calendar model. These facts organize resolution; they do not
create authentication, permission, or a new Calendar datastore.

## Bounded Modification Surface

Stage 8 supports editing an existing event, rescheduling it, changing bounded
ordinary event fields already represented by the Calendar semantic/provider
model, and deleting/canceling it. The same Brain-owned draft, clarification,
confirmation, and provider-backed commit authority used by creation owns these
operations.

The bounded ordinary update fields are the existing create semantics: title,
date, all-day status, and—for timed events—start plus end or duration resolved
to a concrete end. Rescheduling changes those date/time semantics. Location,
description/notes, attendees, invitations, reminders, and arbitrary provider
properties do not become writable merely because reads can display them.

Provider-native supported update/delete operations are required. If the
selected provider cannot express or verify a requested mutation, Oracle fails
honestly. It must not emulate update through delete/recreate, because that can
change identity, recurrence, reminder, invitation, or other provider-owned
semantics.

This surface does not add invitations, attendees, RSVP, sharing, permissions,
Calendar-account administration, or general provider metadata editing unless a
separate existing Calendar contract explicitly requires it.

## Recurrence Scope

For a recurring event, one occurrence and the entire series are distinct
mutation scopes. Explicit user wording controls. When wording does not resolve
the scope, Oracle asks whether edit/reschedule/delete applies to this occurrence
or the whole series and performs no provider mutation until answered.

Oracle must preserve the selected scope through draft, confirmation, commit,
provider result, cache invalidation, and audit. Provider lack of occurrence- or
series-level support fails honestly and is never bridged with delete/recreate.

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

For existing events, integrity also covers the stable selected event/provider
reference, owning calendar, semantic user/source context, operation, and
occurrence-versus-series scope. A stale provider revision/concurrency conflict
invalidates the candidate rather than applying it to newer state.

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

## Provider Reminder Boundary

Calendar create/edit does not invent an Oracle reminder or lead time. Stage 8
may project an applicable provider-owned event reminder into the existing alert
lifecycle only when that calendar is explicitly opted in. Projection preserves
the provider metadata unchanged and is idempotent by event occurrence and
provider reminder identity. An event without applicable provider reminder
metadata creates no Oracle alert.

Provider-native reminder interpretation belongs exclusively to the Calendar
provider bridge. Oracle runtime code never branches on iCalendar alarm action,
trigger, relativity, or repeat/nag representation. The bridge qualifies an
ordinary user reminder and emits a provider-neutral intent containing stable
reminder and event-occurrence identities, a concrete due instant, and bounded
event context. For Nextcloud, `DISPLAY` is the qualifying ordinary in-app
notification reminder; provider `EMAIL`/`AUDIO` delivery is not duplicated.
Each independently configured qualifying reminder produces one intent.
Provider repeat/nag metadata does not multiply Oracle alerts in Stage 8, and
Oracle never modifies or consumes the provider reminder.

Calendar association, per-user default calendar, and alert opt-in are
independent configuration facts. A shared calendar may associate and be the
default for multiple users without becoming alert-enabled.

## V2 Configuration Reconciliation

Calendar provider/account selection and write policy belong to
`domains/calendar.yaml`; logical user credential references, if introduced,
belong to the owning household user capability. Configuration migration and
activation do not weaken draft, clarification, confirmation, timezone, or
single-commit safety behavior.
