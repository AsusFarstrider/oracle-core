# Alerts

This document describes the current alerts subsystem surface that handles timers, alarms, and reminders.

Timers, alarms, and reminders are part of the brain alert subsystem rather than a dedicated domain handler package.

## Structure

The current subsystem is split across:

- `server/oracle_app/system_intents.py` for classifying timer, alarm, and reminder requests into the `alerts` system action
- `server/oracle_app/alerts.py` for alert-domain parsing and lifecycle helpers
- `server/oracle_app/memory/alerts.py` for transactional records, leases, transitions, and retention inputs
- authenticated `POST /api/satellite/alerts/claim` and
  `POST /api/satellite/alerts/{alert_id}/acknowledge` delivery surfaces
- `server/oracle_app/notifications/` for provider-neutral notification
  submission, satellite fan-out, idempotency, expiry, and suppression decisions

## Responsibilities

The current subsystem is responsible for:

- classifying alert requests
- parsing durations and clock times
- creating and tracking brain-owned alerts
- listing and canceling alerts
- leasing due alerts to authenticated alert-capable satellites and recording
  explicit local acceptance

## Alert Shape

Scheduled alerts currently carry these high-level fields:

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

## Delivery Flow

The Brain schedules alerts transactionally in Memory SQLite. Managed satellite
runtimes claim due records with their projection credential. A response only
creates a bounded lease; the record remains durable until the runtime explicitly
acknowledges that it accepted the foreground operation. Expired leases return to
pending. The old pending-alert GET routes remain temporary Slice 9 client
migration surfaces and do not provide reliable completion semantics.

Alert delivery remains source-scoped.

The pending-alerts surface only returns due alerts for the requesting source, so timers, alarms, and reminders speak back only on the satellite or client that created them.

On the current Pi satellite runtime:

- timer alerts prefer a local WAV sound file instead of spoken TTS
- alarm alerts prefer a local WAV sound file and then speak the due time as a follow-up
- reminder alerts continue to use spoken TTS
- all three now enter through the same explicit foreground-audio handoff path used by reply and cue playback, so due alerts interrupt or replace current playback intentionally instead of acting like side sounds

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
a process crash.

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

## Current Limitation

Terminal alert records are retained for the configured 90-day horizon. Active
records prevent source retirement. Required storage mutations fail closed;
optional diagnostic telemetry does not own alert truth.

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
