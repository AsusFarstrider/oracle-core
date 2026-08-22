# Control-Service Operations

This runbook records the operational surface of the satellite control service.

## What This Runbook Is For

Use this runbook when you need to verify that the control service is up, inspect its health surfaces, restart it, or understand basic control-service behavior during playback operations.

For broader incident narrowing, use [incident-triage.md](incident-triage.md).
For deployment entry-chain details, use
[deployment-inventory.md](deployment-inventory.md).

## Canonical Service Shape

The retained canonical Linux implementation uses
`scripts/oracle-satellite-control.canonical.service`; Windows installations use
the canonical branch emitted by `install-windows-satellite-control-task.ps1`.
Both select one immutable local projection/secret activation before constructing
`ControlServiceSettings`. Behavioral environment variables and CLI flags are
rejected in canonical mode.

On playback-capable hosts, the control service is the local playback-control
surface used alongside the interaction runtime. These launch assets are not a
Stage 4 validated standard-installation profile.

## Health Verification

Basic health checks:

- service status with `systemctl status oracle-satellite-control.service --no-pager -l`
- service logs with `journalctl -u oracle-satellite-control.service`
- `GET /health`
- `GET /health/config`

Expected result:

- the service is active
- `/health` responds
- `/health/config` responds and does not report blocking config errors

## Exposed Surfaces

The current operational surfaces are:

- `GET /health`
- `GET /health/config`
- `GET /playback-authority`
- `POST /control`

`POST /control` is the main command surface for local playback-control actions.

Operational note:

- repeated passive `GET /playback-authority` reads now use a short in-process cache for long-form state inside the control service
- long-form control actions invalidate that cache before acting
- command-confirmation polling still bypasses the cache and reads fresh state
- operators may therefore see a mix of very fast passive reads and slower cache-refresh reads during repeated localhost polling

## Restart

Typical restart flow:

- inspect current status
- restart with `sudo systemctl restart oracle-satellite-control.service`
- recheck status
- recheck `/health` and `/health/config`

## Basic Behavior

At a high level, the control service:

- accepts authenticated control requests
- forwards playback-control actions to the local adapter surface
- reports playback-authority state
- participates in interrupt and resume handling when Oracle needs local output ownership

It is the local playback-control neighbor of the Pi satellite runtime, not the brain-side routing or reply surface.

## Common Failure Signals

Common operational signals include:

- service not running or restart loop
- `/health/config` reporting blocking config errors
- control commands received but not executed successfully
- no local playback effect after a control request
- playback-authority surface not reflecting the expected active owner

If those checks fail, continue in [incident-triage.md](incident-triage.md).
