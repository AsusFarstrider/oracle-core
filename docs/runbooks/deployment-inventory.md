# Oracle Deployment Inventory

> **Validated Stage 4 baseline:** the `/srv/oracle` installation,
> administration, activation, and recovery machinery described below passed
> clean-host lifecycle evidence on Debian 13/amd64 with `minimal-brain` and
> host-local ingress. Use the
> [standard installation runbook](standard-installation.md) for the supported
> operator procedure.

## Purpose

This runbook identifies the reusable deployment entry chains and the boundary
between development sources, immutable standard installations, and satellite
runtime assets. Household hosts, active revisions, profiles, and live evidence
belong in the household deployment authority and installed deployment state.

## Standard Debian Brain

A standard Brain installation is assembled beneath `/srv/oracle` from one
exact approved core artifact and one matching household deployment artifact.
The stable systemd service launches through the one complete activation
selected by `/srv/oracle/selection/active`.

That activation binds the exact:

- immutable application revision;
- validated Python environment;
- household deployment revision;
- canonical configuration and secret activation;
- service-definition identity;
- applicable persistent-state checkpoint.

Systemd definitions and the boot-lifetime administration socket remain host
facilities outside `/srv/oracle`. Application revisions, environments,
deployments, generations, activation records, selection state, operational
state, data, cache, and Oracle temporary material retain their declared
subtrees beneath the coherent installation root.

The service definition must not contain a particular application revision,
environment, deployment revision, or configuration generation. It reaches the
selected interpreter and application through the stable active-activation
namespace.

## Administration Entry Chain

The host-local Oracle administration CLI owns installation, dependency and
profile reconciliation, staging, update review, activation, rollback, repair,
and evidence export. It may request bounded elevation only for declared host
mutations.

The persistent Brain-hosted control plane accepts structured configuration,
secret, activation, recovery, and rollback operations through the protected
Unix-domain socket. It does not acquire package, account, systemd-definition,
or general host authority.

Online and offline operations share the same transaction, generation,
activation-record, integrity, publication, and recovery implementation.

## Development Posture

A development checkout may use its existing repository-relative service and
configuration mechanisms. It is not an installed immutable revision and must
not be mistaken for the standard `/srv/oracle` lifecycle. Shared application
logic should avoid depending on the process working directory or a developer's
home path.

## Satellite Assets

Reusable satellite runtime code, canonical service templates, projection
handling, and platform wrappers may exist in the core tree. Their presence does
not establish a Stage 4 standard satellite installation profile. Satellite
provisioning, package lifecycle, and platform certification retain their own
later evidence gates.

## Verification

For a standard Brain, verify:

- the systemd unit launches only through the active complete activation;
- every referenced immutable component exists and matches its identity;
- configuration and secret activation identities agree with the activation
  record;
- the service reaches readiness and expected health;
- drift inspection distinguishes managed immutable content from legitimate
  mutable state;
- the recorded active and previous-known-good activations match the filesystem
  selectors.

The exact installed identities and host evidence are authoritative. A branch,
moving tag, working tree, or remembered deployment path is not an installed
revision identity.
