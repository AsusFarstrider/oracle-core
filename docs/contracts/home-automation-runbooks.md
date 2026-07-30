# Home-Automation Runbook Contract

## Status

The canonical Home Assistant event ingress and home-automation controller are
implemented. A neutral side-entry example below uses
`migration_mode: runbook` to illustrate the reusable delayed/repeated
entry-monitoring lifecycle. It is an example, not evidence about a real door or
household deployment.

## Provider Boundary

Home Assistant sends a stable `event_id`, provider `entity_id`, provider
`state`, and optional `occurred_at` to authenticated endpoint
`POST /api/integrations/home-assistant/events`. The adapter maps provider
identities through `config/home-automation-runbooks.json`. The controller sees
canonical values such as `entry_state`, `side_entry`, and `open`. Provider ids
must not appear in correlation keys or notification calls.

Unknown entities and states are ignored with a reason. Quiet Mode maps to
`mode_state`, but communication suppression remains notifications-domain
policy evaluated at submission and delivery.

The adapter durably stores the newest canonical state per subject. Replayed
event ids and evidence strictly older than that snapshot are ignored, so a
late open event cannot reopen a workflow after a newer close.

## Entry Lifecycle

An `open` event in `runbook` mode creates one correlated durable run, persists
its wait, verifies fresh HA state after the delay, and calls
`notifications.submit` only while the entry remains open. Repeats use durable
waits and a configured notification bound. A correlated close cancels a
waiting run. Occurrence identity is the durable run id plus notification
ordinal, preserving notification idempotency across scheduler replay.

## Restart And Failure Behavior

- Waiting runs survive restart and resume from stored due time.
- Excess lateness fails without notifying.
- Unknown, unavailable, missing, or failed HA state reads use bounded retries.
- Exceeding the provider-failure bound fails without guessing the door is open.
- Notification capability failure records a failed operation and fails the run.
- The definition and provider mapping are frozen in the active run payload.

The controller currently uses compatibility storage `kind=routine` with
`definition_domain=home_automation`. The composite scheduler excludes that
domain; only the home-automation scheduler resumes these runs.

`notification_delivery_enabled` is an explicit fail-closed gate. When false,
the controller records a `notification_simulation` operation with the stable
occurrence identity but does not call `notifications.submit`. This supports
timing, repeat, restart, and cancellation soak without satellite audio.
Enabled entry definitions set it true; only real provider-driven occurrences may
announce during this slice.

A notification-domain `suppressed` result does not consume the runbook's
bounded notification count. The run remains waiting and uses a new occurrence
identity at the next repeat, allowing Quiet Mode release to restore delivery
while the door remains open.

## Migration Modes

- `direct_notification`: observe events but start no run; HA owns delivery.
- `runbook`: the runbook owns timing and notification submission.

The `direct_notification` value remains available for staged migration of a
future automation only when an external owner actually exists. Oracle no
longer exposes a direct HA notification endpoint, and canonical entry
definitions must remain `runbook`.

## Configuration

Canonical provider mappings, subjects, notification-type references,
timing/retry bounds, and migration mode are owned by
`domains/home-assistant.yaml`. They may not contain credentials, commands,
notification text, satellite IDs, or HA service calls. The selected Home
Assistant runtime view resolves the event-ingress logical secret reference.
Canonical definitions, Oracle-to-Home-Assistant
mappings, timing policy, and migration mode belong to the active
`domains/home-assistant.yaml` role.

Migration between supported canonical schema versions preserves applicable
definitions and rejection rules. Retired inputs are errors, not overrides.
Definitions continue to invoke only registered
Oracle operations and cannot reference scripts, URLs, provider-native commands,
service names, raw external entity IDs outside the HA mapping edge, or
credentials.

An enabled canonical Home Assistant bridge requires its selected provider API
credential. The separate event-ingress credential is required and resolved only
when at least one enabled Home Assistant automation can consume authenticated
events. Merely retaining a disabled automation or an optional ingress secret
reference does not make that dormant credential operational.
