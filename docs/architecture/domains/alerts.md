# Alerts

This document describes the current alerts subsystem surface that handles timers, alarms, and reminders.

Timers, alarms, and reminders are part of the brain alert subsystem rather than a dedicated domain handler package.

## Structure

The current subsystem is split across:

- `server/oracle_app/system_intents.py` for classifying timer, alarm, and reminder requests into the `alerts` system action
- `server/oracle_app/alerts.py` for scheduling, listing, canceling, and consuming due alerts
- `GET /alerts/pending` as the brain-side delivery surface used by satellites
- `server/oracle_app/notifications/` for provider-neutral notification
  submission, satellite fan-out, idempotency, expiry, and suppression decisions

## Responsibilities

The current subsystem is responsible for:

- classifying alert requests
- parsing durations and clock times
- creating and tracking brain-owned alerts
- listing and canceling alerts
- exposing due alerts through the pending-alerts surface

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
- delivered flag

## Delivery Flow

The brain schedules alerts in a brain-owned file-backed store, satellites poll `/alerts/pending`, and due alerts are returned for local foreground-audio handling.

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

Alerts now persist across ordinary brain restarts, but the subsystem still needs additional hardening around long-term durability policy, operator visibility, and edge-case lifecycle behavior.

## V2 Configuration Reconciliation

Alert runtime truth remains Brain-owned operational state. Any configurable
policy belongs with the owning alert/notification capability, while delivery
targets use enabled canonical `source_id` references. Configuration activation
does not create, reschedule, deliver, or delete alerts.

V2 intentionally defines no `domains/alerts.yaml`. Brain persistence mechanics
belong to `brain.yaml:storage`, satellite polling/cue/playback behavior belongs
to satellite configuration and projections, and notification delivery or
suppression policy belongs to `domains/notifications.yaml`. A dedicated role
would require later evidence of substantial operator-owned alert policy and
schema review.
