# Alerts

This document describes the current alerts subsystem surface and the shared
semantic foundation for timers, alarms, and reminders.

The ratified Stage 6 target behavior is governed by
[`system-utilities.md`](../../contracts/system-utilities.md). Timer, Alarm, and
Reminder family behavior remains governed by the shared contract. Timer, Alarm,
and Reminder family behavior is complete through Slices 7, 8, and 9.

Timers, alarms, and reminders are part of the brain alert subsystem rather than a dedicated domain handler package.

## Structure

The current subsystem is split across:

- `server/oracle_app/system_intents.py` for classifying timer, alarm, and reminder requests into the `alerts` system action
- `server/oracle_app/alerts.py` for alert-domain parsing and lifecycle helpers
- `server/oracle_app/alert_lifecycle.py` as the sole schedule/occurrence
  coordinator and family-policy integration boundary
- `server/oracle_app/alert_recurrence.py` for bounded calendar recurrence using
  the temporal owner's DST resolver
- `server/oracle_app/alert_targeting.py` for centralized canonical destination
  resolution
- `server/oracle_app/timers.py` for complete deterministic Timer family policy
  and compact timer state
- `server/oracle_app/alarms.py` for complete deterministic Alarm schedule,
  occurrence-management, and source-bound state policy
- `server/oracle_app/reminders.py` for complete deterministic Reminder
  recipient, schedule, occurrence-management, privacy, and state policy
- `server/oracle_app/memory/alerts.py` for transactional records, leases, transitions, and retention inputs
- `server/oracle_app/memory/alert_lifecycle.py` for schedule, occurrence,
  acknowledgement, and exception transactions
- authenticated `POST /api/satellite/alerts/claim` and
  `POST /api/satellite/alerts/{alert_id}/acknowledge` delivery surfaces plus
  occurrence-aware authenticated state/action surfaces
- `server/oracle_app/notifications/` for provider-neutral notification
  submission, satellite fan-out, idempotency, expiry, and suppression decisions

## Responsibilities

The subsystem is responsible for:

- classifying alert requests
- parsing durations and clock times
- creating and tracking brain-owned alerts
- listing and canceling alerts
- leasing due alerts to authenticated alert-capable satellites and recording
  explicit local acceptance
- persisting distinct schedules, logical occurrences, destination deliveries,
  and actor-typed acknowledgements
- bounded, idempotent recurrence materialization and restart reconciliation
- central local, room, household, recipient, and optional common-copy target
  resolution from applied canonical configuration
- occurrence-scoped skip, snooze lineage, and schedule-independent overrides

## Semantic Shape

A schedule records durable intent: family, one-time or recurring type,
timezone, start and intended local time, bounded recurrence rule, creator,
message, semantic target/recipients, status, metadata, and idempotency key.

An occurrence records one materialized due instance: stable schedule/key,
resolved UTC due time plus intended local wall time, exception/snooze parent,
status, frozen configuration revision and destinations, and transitions.
Recipient-directed schedules materialize one independent occurrence per person;
an explicit everyone reminder may additionally materialize one common-copy
occurrence that cannot acknowledge a person's occurrence.

The existing delivery row carries:

- alert id
- kind
- source
- session id
- due time
- created time
- message
- metadata
- pending, leased, acknowledged/completed, canceled, or expired status
- lease identity and expiry while claimed
- occurrence identity, delivery role, optional semantic recipient, and applied
  configuration revision

An acknowledgement records occurrence, optional delivery, actor type and
identity, action, timestamp, and idempotency key. Runtime delivery acceptance,
common-copy dismissal, person acknowledgement, snooze, and logical completion
therefore cannot share one untyped flag.

## Delivery Flow

The Brain schedules alerts transactionally in Memory SQLite. Managed satellite
runtimes claim due records with their projection credential. A response only
creates a bounded lease; the record remains durable until the runtime explicitly
acknowledges that it accepted the foreground operation. Expired leases return to
pending. The old pending-alert GET routes remain temporary Slice 9 client
migration surfaces and do not provide reliable completion semantics.

Each alert delivery remains source-scoped, while its occurrence may fan out to
multiple source-scoped deliveries.

The pending-alerts surface only returns due deliveries for the authenticated
requesting source. The semantic target resolver determines whether the logical
occurrence is local, room, household-wide, or recipient-directed.

On the current Pi satellite runtime:

- timer alerts prefer a local WAV sound file instead of spoken TTS
- alarm alerts prefer a local WAV sound file and then speak the due time as a follow-up
- reminder alerts play a short local chime and then speak the reminder text
- all three now enter through the same explicit foreground-audio handoff path used by reply and cue playback, so due alerts interrupt or replace current playback intentionally instead of acting like side sounds

Timer and Alarm ringing share one borrowing foreground handoff per destination.
An Alarm requests host display attention, speaks its identity and due time, and
reasserts at a bounded cadence while the same logical occurrence remains open.
Each newly delivered Reminder briefly borrows the same foreground authority,
requests display attention for its chime-and-speech presentation, then restores
suitable interrupted media; the obligation remains in Brain state until a
typed person or common-copy action resolves it.
The browser's always-present Alerts surface uses one compact source-bound state
projection composed from the three canonical family owners and their existing
typed actions. One sequence-guarded refresh loop drives the Home summary and
permanent management page, retains last-good state behind a visible degraded
banner, and prevents late responses from replacing newer occurrence state. A
ringing Alarm takes over the display with large snooze and dismiss controls and
a bounded browser wake-lock request.

Home Assistant notification occurrences also enter this source-scoped store.
They are created once per configured target, carry a bounded expiry, and are
held or discarded when configured suppression evidence is unavailable or
active. The shared satellite runtime handles `notification` as a borrowing
foreground event: pause interruptible playback, speak through Brain TTS, then
resume the interrupted session.

Each satellite notification target also owns a channel-neutral Memory delivery
receipt. It remains pending while the alert is pending or leased, becomes
accepted on acknowledgement, and becomes suppressed or expired with the
corresponding terminal alert outcome. Receipt reconciliation is retry-safe after
a process crash. The authenticated two-second claim surface uses one read-only
Memory preflight to bypass the heavier claim and receipt passes only when no
due alert, expired lease, active notification repair, or pending receipt exists;
the delivery cadence and durable path are unchanged.

## Current Surface

The current subsystem surface includes:

- creating timers, alarms, and reminders
- listing current timers, alarms, and reminders
- count-style and next-due queries for timers, alarms, and reminders
- canceling one or all matching alerts

Current user-facing note:

- generic timer status remains part of the `system` alerts surface
- audiobook sleep timer status remains part of the audiobook surface
- when both exist for the same source, system timer status may mention the active audiobook sleep timer explicitly to avoid ambiguity
- Timer, Alarm, and Reminder family behavior is complete through Slices 7–9;
  their focused satellite scheduling UI/runtime projection is complete through
  Slice 10.

## Lifecycle And Reconciliation

Terminal alert records are retained for the configured 90-day horizon. Active
records prevent source retirement. Required storage mutations fail closed;
optional diagnostic telemetry does not own alert truth.

One Brain-owned coordinator owns bounded recurrence materialization, due
projection, late-state classification, and restart reconciliation. Expansion
is finite and keyed by schedule plus intended instance, so restart cannot
duplicate an occurrence. Destination projection is separately idempotent and
freezes the applied configuration revision and resolved destinations on first
due reconciliation; later configuration changes affect future occurrences but
do not rewrite created occurrence history.

The fixed late foundations are 10 minutes for timers and 20 minutes for alarms
and reminders. After those windows the coordinator marks timers/alarms missed
and reminders overdue without creating surprise delivery work. A reminder with
no eligible destination remains truthfully outstanding until it becomes
overdue. The Timer family owns its complete ten-minute ringing, late
identification, selection, adjustment, cancellation, and dismissal policy. The
Alarm family owns its persistent schedule, occurrence exception, snooze,
20-minute ringing, display-attention, and missed-state policy. The Reminder
owner holds its recipient, acknowledgement, snooze, and overdue policy.

Timer, alarm, and reminder policy remains distinct, but separate semantic
stores, schedulers, recurrence engines, and target registries are not
permitted. Notification fan-out and receipt patterns remain below this
boundary; user-created reminders do not become curated notifications.

Satellite runtime acceptance records `delivery_accepted` while leaving the
logical occurrence open. Common projection dismissal affects only that common
delivery. Person/system acknowledgement can complete its logical occurrence
and converges the occurrence's still-active deliveries. The existing
foreground-audio authority remains the sole owner of interruption and resume
outcome.

The schema migration adopts existing timer, alarm, and reminder delivery rows
into deterministic one-time schedule and occurrence identities. It preserves
the existing alert and source identity. Notification and audiobook sleep-timer
rows retain their existing owners.

## V2 Configuration Reconciliation

Alert runtime truth remains Brain-owned operational state. Any configurable
policy belongs with the owning alert/notification capability, while delivery
targets use enabled canonical `source_id` references. Configuration activation
does not create, reschedule, deliver, or delete alerts.

V2 intentionally defines no `domains/alerts.yaml`. Brain persistence mechanics
belong to `brain.yaml:storage.memory`, satellite claim/cue/playback behavior belongs
to satellite configuration and projections, and notification delivery or
suppression policy belongs to `domains/notifications.yaml`. A dedicated role
would require later evidence of substantial operator-owned alert policy and
schema review. The obsolete `storage.alerts` JSON setting is rejected.
