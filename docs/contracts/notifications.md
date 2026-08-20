# Notifications Domain Contract

## Status

The Brain-owned notifications domain is implemented with a provider-neutral
submission service, the `satellite_announcement` channel, and receipt-backed
external delivery through Apprise. Enabled definitions may use either or both
channels. The home-automation controller is the first domain-runbook caller.

## Ownership

- Calling domains and runbook controllers own the decision to submit an
  occurrence.
- The Oracle notifications domain owns allowlisted notification definitions,
  content, target resolution, suppression, idempotency, expiry, channel
  dispatch, and notification audit.
- Home Assistant owns its provider sensor state and configured household-mode
  evidence. Its authenticated event route feeds the home-automation domain;
  the owning runbook decides when to call notifications.
- Satellites execute the Brain-authored notification through the existing
  polling and foreground-audio boundaries. They do not interpret Home
  Assistant state or notification policy.

## Provider-Neutral Submission

Internal callers use:

```text
submit_notification(
  notification_type,
  occurrence_id,
  caller=...,
  context=...,
  correlation_id=...
)
```

`notification_type` and `occurrence_id` are required. `caller` identifies the
trusted internal domain or authenticated integration adapter. External
authentication remains the responsibility of the route/bridge before it calls
the domain service.

The current version accepts no non-empty context because deployed definitions
do not declare typed context fields. Unknown context fails closed rather than
being ignored or interpolated. Context support requires a later definition and
rendering contract.

Callers may not provide final text, recipients, satellite ids, channel
providers, suppression bypass, expiry, credentials, or provider-native
targets. Those resolve from notification configuration.

`emit_notification` is a compatibility alias. `submit_notification` is the
canonical domain API.

## Home Assistant Ingress

The authenticated Home Assistant adapter submits only:

- an Oracle notification id;
- a provider occurrence id used for idempotency.

Provider-supplied message text, target sources, audio policy, expiry, and
suppression policy are forbidden. Those values resolve from validated Oracle
configuration.

Callers identify themselves at the internal service boundary. The
home-automation controller uses its runbook identity and supplies only the
curated notification id and durable occurrence id. No Home Assistant-specific
notification ingress is part of the current contract.

## Definition Contract

Every notification definition has:

- a unique lowercase Oracle id;
- an explicit enabled flag;
- Brain-owned spoken text;
- one or more explicit satellite source ids;
- zero or more configured suppression-mode ids;
- a bounded delivery expiry;
- an allowlisted foreground-audio policy.

In canonical V2, notification audience entries are satellite source targets,
not users. Each source audience must resolve to an enabled satellite-backed
household source for the spoken channel. Associated users, authentication,
presence, preferences, and dynamic location do not participate in notification
targeting. Non-satellite sources do not become spoken endpoints merely because
they are stable household sources.

V1 supports `pause_resume`. New definitions using these established semantics
must be configurable without application or satellite code changes.

On satellites, `pause_resume` maps to a `notification` foreground request with
`borrow`, `pause_or_stronger`, and `resume_previous`. Satellites execute that
fixed Brain-authorized policy; they do not derive policy from Home Assistant.

## Suppression

Suppression helpers remain Home Assistant-owned evidence. Oracle maps each
mode id to one curated `input_boolean` entity and active state.

Oracle must enforce suppression at ingress and again before a due notification
is released. An occurrence suppressed by an active mode is discarded, not
delayed until the mode ends. Unknown suppression evidence fails silent; it
must never be treated as confirmed inactive.

Each definition chooses its suppression modes. A notification is not globally
suppressed merely because some other definition uses Quiet Mode.

## Satellite Announcement Channel

The current `satellite_announcement` channel fans one accepted occurrence into
one source-scoped alert per configured
target. Fan-out persistence is atomic. Repeated submission of the same
occurrence id must not create another announcement.

Expired notification alerts must not be released to a satellite. This avoids
speaking stale household state after a target reconnects.

Delivery uses authenticated leased satellite claim and explicit
acknowledgement; this capability does not add direct Brain-to-satellite push or
move policy into satellites.

Acknowledgement confirms that the local runtime accepted the foreground
operation. It does not prove that a person heard the announcement.

## External Channel Contract

Recipient groups are allowlisted domain configuration, separate from
notification definitions. Each group has an Oracle id, explicit enabled flag,
provider id, Apprise configuration key, and routing tag. V1 reserves provider
`apprise`. Concrete destination URLs, downstream provider identities, topics,
and credentials remain outside Oracle. Calling domains and runbooks never see
or supply those values.

An optional `external_delivery` block references only recipient-group ids and
declares delivery expiry, bounded attempts, retry spacing, and
allowlisted quiet-hours, repeat, and failure policies. External delivery is
accepted only when every referenced logical recipient group is enabled and all
implemented policy constraints validate. This prevents configuration from
appearing active while silently dropping delivery.

Enabled external delivery currently requires `quiet_hours_policy: bypass`.
`respect` remains a reserved fail-closed value until Oracle has a configured
quiet-hours and recipient-preference contract.

## Channel-Neutral Delivery Receipts

Oracle Memory durably stores one receipt for each unique combination of
notification type, occurrence id, channel, and logical destination id. Receipt
states are `pending`, `accepted`, `retry_wait`, `failed`, `expired`, and
`suppressed`; terminal receipts cannot transition again.

Receipts freeze the correlation id, provider name, attempt bound, retry
spacing, expiry, failure policy, and repeat policy needed for restart-safe
dispatch. They store
only Oracle identities and sanitized error class/code values. Provider URLs,
routing tags, topics, credentials, payload bodies, and provider-native receipt
objects are forbidden.

The store supports idempotent reservation, validated transitions, filtered
inspection, and due-work lookup. Satellite announcements reserve one receipt
per canonical target source: it remains pending through an alert lease, becomes
accepted on acknowledgement, and becomes suppressed or expired with the typed
terminal alert outcome. Receipt reconciliation is retry-safe after a crash.
An enabled external policy reserves one receipt per logical recipient group.
`first_per_correlation` is enforced by a unique Memory index; later occurrences
in the same correlation reuse the first receipt.

## Apprise Provider Bridge

`AppriseBridge` is the single provider boundary for non-satellite delivery. It
supports sanitized health checks and stateful `notify/{config_key}` requests
with an allowlisted routing tag, title, body, severity, and format. It never
accepts or returns concrete destination URLs, downstream provider identifiers,
topics, credentials, or raw Apprise response content.

Bridge HTTP 429/5xx, invalid responses, timeouts, and connection failures are
classified retryable. Other HTTP 4xx responses are permanent. A successful
2xx response means only that Apprise accepted the gateway request.

## External Dispatch Worker

The Brain lifecycle runs one worker that consumes only due `external`
receipts. It resolves the current enabled notification definition and logical
recipient group, then submits the definition-owned text through
`AppriseBridge`. It never accepts provider destinations from a receipt or
caller.

Suppression is evaluated again immediately before the provider request. Active
suppression completes the receipt as `suppressed`. Unavailable suppression
evidence defers without consuming a provider attempt and eventually expires
rather than failing open.

Retryable bridge failures transition to `retry_wait` using the receipt's
frozen spacing while attempts and expiry permit. Permanent failures, exhausted
attempts, invalid current policy, and retries that would occur after expiry
transition to `failed`. Work that reaches expiry before an attempt transitions
to `expired`. Provider attempts increment `attempt_count`; policy rejection
without a provider call does not.

Dispatch is restart-safe and idempotent at receipt reservation, but the
Apprise HTTP boundary is at-least-once. A process failure after Apprise accepts
a request but before Oracle commits `accepted` can cause a later retry.
`accepted` means only gateway acceptance, not downstream display or human
receipt.

## Submission Results

The existing top-level `status`, `notification_id`, `event_id`, and
`queued_targets` fields remain compatible. `channel_results` adds one sanitized
entry per configured channel. Durable external reservation reports `queued`;
repeat-policy reuse reports `duplicate`.

If best-effort external work cannot be reserved after satellite work is
queued, the top-level result is `partial` and the external channel reports
`failed`. If external policy is `required`, reservation failure raises
`NotificationRequestError`. Cross-store work is not transactional: required
failure can be reported after satellite alerts were already accepted.

## Read-Only Diagnostics

Admin diagnostics expose sanitized notification configuration state, live
Apprise health, exact external receipt counts by present status, and bounded
recent delivery history. Delivery history may include Oracle notification,
occurrence, correlation, logical destination, policy, timing, attempt, and
sanitized error identifiers.

Diagnostics must never expose notification message text, Apprise base URLs or
configuration keys, routing tags, downstream provider URLs/topics, credentials,
attachments, or raw provider responses. Diagnostics are observation-only and
cannot retry, cancel, suppress, enable, or submit a notification.

## Extension Rule

Adding another notification with supported semantics requires one validated
Oracle definition and a trusted caller. That caller may be Home Assistant, a
domain service, or a future runbook adapter. Notification-specific branches in
routing, callers, alerts, or satellite code are forbidden.

Behavior outside the established schema requires an explicit contract change
before implementation.

## Configuration Reconciliation

Canonical types, templates, audience, suppression,
channels, retry, and logical recipient mappings are active from
`domains/notifications.yaml`. Provider credentials and secret-bearing
destination URLs remain logical secrets.

Notification targets use enabled Oracle satellite-source identities. Configuration
activation never submits a notification, and migration must preserve existing
deduplication, suppression, receipt, and delivery semantics.

Canonical runtime selection follows enabled delivery reachability. Disabled
notification types are not operational definitions. An Apprise provider and
its logical URL secret become operational only when an enabled notification
type enables external delivery through an enabled logical recipient group that
references that provider. Retaining dormant groups, providers, or external
policy does not create a fallback channel or require its secret.

The Stage 3 canonical execution capability is bound to one immutable applied
snapshot. It resolves typed definitions and audiences from
`NotificationRuntimeSettings` and `SatelliteFleetRuntimeSettings`; evaluates
suppression through the exact typed
Home Assistant mode-state mapping; reserves the existing durable logical
receipts; and supplies both submission and polling-time delivery decisions
without consulting compatibility configuration getters. Enabled suppression requires
exactly one applied Home Assistant `mode_state` mapping for each referenced
household mode or activation is blocked.

The external worker may be started with that same canonical execution object.
It resolves current typed definitions, groups, provider URL, and timeout from
the applied snapshot while retaining the established receipt, retry, expiry,
and at-least-once laws. Sanitized admin reads likewise select the installed
canonical composition. The selected canonical Brain never consults retired JSON
or environment authorities.
