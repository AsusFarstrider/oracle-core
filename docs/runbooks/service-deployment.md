# Standard Debian Brain Service

> **Validated installation baseline:** the complete-selector entrypoint,
> administration transport, restart handshake, bounded recovery helper, and
> standard installer lifecycle passed clean-host acceptance on Debian 13/amd64.
> The provider-free `minimal-brain` and locked full-production profile have
> separate exact requirements and verification evidence.

## Purpose

This runbook describes the standard Debian Brain service posture. A private
repository may remain source authority, but a checkout-based production service
is not a supported second runtime architecture.

For the exact artifact bootstrap, staging, assembly, service installation,
activation, update, recovery, rollback, and verification commands, use the
[standard Debian installation runbook](standard-installation.md). The
[administration CLI reference](../reference/administration-cli.md) defines the
plan/apply and elevation boundary for each command.

## Lifecycle Authority

Systemd is the outer process-lifecycle authority. It provides boot enablement,
supervision, restart policy, orderly stop, exit reporting, and journal
integration. A systemd active state is not Oracle readiness or health.

The stable unit launches Oracle through `/srv/oracle/selection/active`, which
selects one immutable complete activation. The unit must not hard-code a core
revision, Python-environment identity, household deployment revision, or
configuration generation.

The selected activation provides stable confined references to its exact
application, environment, deployment, and configuration activation. Startup
validates the canonical activation record and referenced filesystem identities
before accepting ordinary work.

## Runtime Identity

The persistent Brain runs as the dedicated non-login `oracle` system identity
with primary group `oracle`. It has no writable service-account home and no
general host-administration privilege.

The service may read and execute installed immutable components, manage its
bounded configuration, secret, activation, control-state, data, cache, and
temporary surfaces, and expose the protected host-local administration socket.
It cannot install application revisions or environments, modify systemd
definitions, acquire packages, or manage host accounts.

## Ingress

Host-local loopback HTTP ingress is the safe default. Household-LAN listening
requires an explicit installation or maintenance choice and matching canonical
access policy. LAN exposure does not imply public-Internet exposure.

The online administration interface uses the systemd-managed Unix-domain
socket, not HTTP. Socket admission is controlled by the Oracle identity,
`oracle-admin` group, root authority, filesystem permissions, and validated
kernel peer credentials.

## Activation And Restart

Oracle uses restart-based activation:

1. validate and assemble a complete immutable activation;
2. retain the prior known-good activation;
3. quiesce new work;
4. atomically replace the `selection/active` symlink;
5. deliberately exit;
6. let systemd restart through the new complete activation;
7. verify process state, readiness, health, and configuration identity;
8. mark known-good only after verification.

On required verification failure, recovery restores the previous complete
activation selector, restarts, verifies it, and records the result. Explicit
rollback uses the same complete-record mechanism.

The standard unit uses `Restart=always`; an explicit systemd stop still remains
stopped. Before serving, the entrypoint records the exact selected activation
in the boot-lifetime runtime directory. After an online activation response is
flushed, Oracle signals its own process with `SIGTERM`, allowing Uvicorn's
existing graceful shutdown to quiesce work. The unit then restarts through the
new `selection/active` target.

One unprivileged `ExecStopPost` command compares the stopped-process marker to
the pending candidate. The prior process exiting for the planned transition
does not trigger rollback. A candidate that exits before verification—or before
it can record its marker—restores the complete previous-known-good activation.
The helper can modify only Oracle activation, configuration, secret, selection,
and control state; it does not call systemctl, sudo, a package manager, or a
general host command.

The implementation files are:

- `server/app_standard.py`;
- `server/oracle_app/installation_runtime.py`;
- `scripts/oracle-standard-lifecycle.py`;
- `scripts/oracle-brain-standard.service`.

Standard mode also binds every built-in writable runtime default away from the
selected immutable application revision. Memory (including alerts), restart
checkpoints, and retained Suggestions records use `/srv/oracle/data`; Home Assistant,
facts, TTS, Python, and library caches use `/srv/oracle/cache`; temporary work
uses `/srv/oracle/tmp`. The fixed service environment redirects Python bytecode,
XDG/Hugging Face caches, and host temporary APIs to those lifecycle surfaces.
Standard startup fails when the canonical Memory path contradicts the supported
managed data location instead of silently writing elsewhere.

## Operations

Use the Oracle administration CLI for status, drift inspection, staging,
update, rollback, repair, and evidence export. Use `systemctl` and `journalctl`
for host-level process and journal inspection. Do not repair immutable managed
content by editing it in place.

Operational verification distinguishes:

- systemd process state;
- Oracle readiness;
- Oracle health;
- active and previous-known-good activation identities;
- configuration and secret readiness;
- dependency and profile state;
- managed-content drift;
- the last installation, activation, recovery, or rollback result.
