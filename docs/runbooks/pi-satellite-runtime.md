# Pi Satellite Runtime

This runbook records the operational runtime shape of the Pi wake satellite.

For structural design details, use
[satellite-runtime.md](../architecture/satellite-runtime.md) and
[control-service.md](../architecture/control-service.md).

## What This Runbook Is For

Use this runbook when you need to understand what is running on a Pi satellite host, how the runtime starts, and which major runtime stages are part of the operational request path.

The retained canonical runtime runs shared code from an immutable selected local
projection, with only finite host bootstrap layered on top. It is not a Stage 4
validated standard-installation profile.

## Canonical Runtime Entry Chain

- `scripts/oracle-satellite.canonical.service`
- local projection authority selection and immutable interaction snapshot
- `satellite/pi_wake_satellite.py`
- `pi_runtime.run(args)` with projected behavior plus finite host bootstrap

## Runtime Stages

At a high level, the current runtime stages are:

- config validation and config-report generation
- satellite config HTTP surface startup
- audio input open
- wake loop
- capture and request pipeline
- local playback interruption and resume handling
- due-alert polling
- optional wake-capture collection

## Runtime Split

The Pi satellite runtime handles wake, capture, request, reply, and local runtime coordination.

On playback-capable hosts, the local control service is a neighboring runtime surface that provides local playback-control operations and playback-authority state.

## Host-Specific Runtime Values

Host-specific thresholds, deployed service arguments, and live runtime values
belong in the household deployment authority and installed deployment state,
not in this runbook.

The checked-in provisioning and service assets are retained implementation and
development tooling. They require separate dependency, permission, reboot,
update, recovery, and rollback evidence before being documented as a validated
installation profile.

## Current Operational Note

Audio hardware may become available after the service begins starting. A
declared restart policy may retry a bounded device-open failure, but readiness
must remain false until the configured capture path opens successfully. Record
any host-specific startup limitation in that household's deployment evidence.
