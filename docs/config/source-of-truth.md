# Oracle Configuration Source Of Truth

## Canonical Runtime Rule

The configuration activation bound by the selected complete installation
activation is Oracle's only runtime configuration authority. It pairs one
immutable normalized configuration generation with one immutable secret
generation. Running processes consume that applied snapshot and never reread
household-authored YAML as an override.

The normative reusable authorities are:

- [`configuration.md`](../contracts/configuration.md);
- [`configuration-bundle.md`](../architecture/configuration-bundle.md); and
- [`configuration-schema.md`](../reference/configuration-schema.md).

## Household Authoring Authority

The human-authored source is one fixed-role bundle in an isolated household
definition. A private deployment authority may contain multiple isolated
household definitions, but a standard target receives only its own exact
materialized deployment revision and separately supplied secrets. The target
does not need repository access or Git knowledge.

Standard Stage 4 installations use `external_read_only` for household-authored
YAML. The persistent Brain does not rewrite the deployment revision. Supported
non-secret changes begin in the household deployment authority and reach the
target as a new validated deployment or configuration revision.

## Secret Authority

The authoritative secret companion is a separately protected household source
representation. Structured write-only operations may create, replace, rotate,
or remove values through the authorized host-local control plane. Each accepted
change produces a new immutable secret generation and complete activation.

Installed secret generations are runtime projections, not competing authored
sources. Secret values never enter core or household deployment artifacts,
configuration hashes, ordinary status, plans, audits, diagnostics, errors, or
evidence exports.

## Separate Operational State

These are not authored configuration:

- caches, sessions, alerts, playback, run, and provider state;
- selected/applied references and lifecycle observations;
- application, dependency-environment, and deployment identities;
- logs, temporary data, and generated operational evidence; and
- external gateway, firewall, VPN, or identity-provider configuration.

Operational state cannot override canonical configuration. Durable state is
preserved and migrated only through its declared lifecycle contract.

## Activation And Recovery

Validation creates immutable configuration and secret generations and binds
them into one complete immutable installation activation. Activation selects
that record atomically and uses the standard restart lifecycle. A configuration
store pointer cannot silently override the configuration identity bound by the
complete installation activation.

If validation, restart, readiness, or health verification fails, recovery
selects the prior compatible complete activation. Explicit rollback uses that
same complete-record mechanism rather than reconstructing independent
application, environment, deployment, configuration, or secret pointers.

Legacy environment, CLI, local JSON, and domain JSON precedence remains
executable only inside migration tooling and bounded characterization. Once an
installation crosses the canonical boundary, populated legacy behavioral input
is an error rather than a fallback authority.
