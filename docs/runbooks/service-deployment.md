# Standard Debian Brain Service

> **Stage 4 target contract:** this document records the approved standard
> service design. The `/srv/oracle` launcher, administration interface,
> activation lifecycle, and recovery commands are not operationally supported
> until their implementation and clean-host evidence are complete.

## Purpose

This runbook describes the standard Debian Brain service posture. A private
development installation may continue using its existing checkout and service
arrangement; it is not modified by this standard-installation guide.

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
