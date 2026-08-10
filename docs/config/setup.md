# Canonical Configuration Setup

Status: the canonical V2 configuration model is implemented. Stage 4 validated
the standard installation lifecycle on Debian 13/amd64 with the provider-free
`minimal-brain` profile. Other platforms and optional profiles remain
experimental or unverified until they receive their own lifecycle evidence.

## Authority

Read these reusable authorities first:

- [`configuration.md`](../contracts/configuration.md) defines configuration
  law and lifecycle invariants;
- [`configuration-bundle.md`](../architecture/configuration-bundle.md) explains
  the implementation architecture;
- [`configuration-schema.md`](../reference/configuration-schema.md) defines the
  fixed roles and fields.

## Create A Household Bundle

Begin from the complete generic tree under
[`examples/config/`](../../examples/config/). Create one isolated household
definition in the deployment authority; do not edit the example tree in place
or place `*.example.*` files inside a live authored bundle.

Then:

1. assign a stable, unique `bundle_id`;
2. set the household timezone and locale;
3. define users, rooms, modes, and stable sources;
4. define only the satellites the household actually manages;
5. select and configure only required domain providers;
6. keep optional providers disabled when their infrastructure is absent;
7. declare logical secret references without placing values in YAML;
8. validate the complete bundle and review its semantic diff; and
9. materialize an exact household deployment revision before installation.

The deployment revision pins one exact Oracle core commit and Git tree. The
target consumes that revision with separately supplied secrets and must reject
a core artifact that does not match the pin.

## Authored Configuration And Installed Generations

The standard Stage 4 installation uses the `external_read_only` authoring model
for household-authored YAML. The persistent Brain consumes the selected
immutable installed generation; it does not rewrite the materialized household
deployment revision or treat runtime state as a replacement authority.

Initial validation, generation creation, migration, and activation occur
through an explicit installation or maintenance operation. A valid operation:

- reads one complete fixed-role candidate;
- validates syntax, schema, semantics, secret requirements, profiles, and
  ingress compatibility;
- creates immutable configuration and secret generations;
- binds them into one complete immutable activation record;
- selects that record atomically; and
- restarts and verifies Oracle through the standard lifecycle.

Invalid candidates never replace the active generation. Recovery and rollback
select a previously validated complete activation rather than rebuilding a
combination from loose component pointers.

## Secrets

Raw secret values are separate from core and household deployment artifacts.
The authoritative household secret companion is maintained through structured
write-only operations; immutable installed secret generations are runtime
projections selected by a complete activation.

Routine creation, replacement, rotation, and removal may use the authorized
host-local Unix-socket control plane. Secret values must not be returned through
status, plans, audits, diagnostics, errors, or evidence exports. Offline
maintenance uses the same validation, transaction, generation, activation, and
recovery implementation when the Brain cannot run.

## Runtime And Optional Providers

The provider-free minimal configuration must remain healthy without Home
Assistant, media servers, Ollama, voice providers, or other optional household
infrastructure. Missing infrastructure for a canonically disabled provider is
intentional unavailability, not a health failure.

If configuration enables behavior that requires an absent installation profile
or host facility, readiness fails with an actionable explanation. Installing a
profile does not enable its behavior; canonical configuration remains the
behavior authority.

## Administration Boundary

Household operators use the host-local Oracle administration interface rather
than editing immutable application revisions or installed generations. Online
structured configuration and secret operations are admitted through the local
Unix-domain socket and serialized through Oracle's maintenance transaction
lock. Host-level installation, dependency, account, permission, and systemd
changes remain elevated administration operations.

The Stage 4 standard interface is host-local. It does not expose unrestricted
maintenance authority through HTTP or another network transport. Later System
Mode configuration editing must use the same candidate, validation, generation,
activation, and recovery lifecycle rather than directly replacing files.
