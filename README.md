# Oracle

Oracle is a LAN-first, local household assistant built around one central Brain
and thin client satellites. The Brain owns routing, dispatch, capability
execution, state, and final reply shaping. Satellites provide edge functions
such as capture, playback, display, and local device interaction.

Oracle is a personal, pre-1.0 project. Exact Git commits and Git trees are the
authoritative identities for installation, update, rollback, and provenance.
Human-readable release versions label selected approved commits; they do not
replace those immutable identities.

## Architecture

Oracle separates reusable application behavior from household-owned deployment
material:

- the reusable core contains the Brain, provider bridges, routing, APIs,
  satellite software, user interfaces, schemas, tests, and reusable
  documentation;
- each household supplies its own canonical configuration, selected
  installation profiles, ingress posture, and deployment metadata;
- secret values are supplied separately from both the core and household
  deployment artifacts;
- generated configuration, deployment state, durable runtime data, caches, and
  temporary files have separate ownership and lifecycle boundaries.

Canonical household configuration decides which capabilities and providers are
enabled. Installing code for an optional provider does not enable it, and a
disabled provider is intentionally unavailable rather than unhealthy.

## Provider-Free Operation

The minimal Brain profile is designed to operate without an LLM provider or
external household infrastructure. Deterministic built-in capabilities remain
available while optional integrations are disabled through canonical
configuration. Requests outside the enabled deterministic surface fail with an
intentional unsupported-capability reply.

External systems such as Home Assistant, media servers, and local model servers
are independently operated providers. Oracle may supply reusable bridges and
optional Oracle-side dependencies for them, but the Oracle installer does not
install or manage those external services.

## Voice Providers

Oracle retains a provider-neutral speech architecture.

- Fast-Whisper is the current deployed STT provider in the primary development
  household and remains a retained core implementation.
- Piper is the current deployed TTS provider in that household and remains a
  retained core implementation.
- whisper.cpp is a retained alternate STT provider that was previously
  functional but is not currently deployed or freshly verified. Its adapter
  expects a separately supplied compatible executable and model; upstream
  whisper.cpp source is not vendored in this repository.

Deployed household evidence is distinct from clean-host profile validation. The
provider-free Stage 4 installation baseline neither removes nor certifies an
optional voice provider.

## Repository Guide

The repository contains the reusable Oracle distribution, including:

- `server/` — Brain application, routing, dispatch, domains, and HTTP surfaces;
- `satellite/` — reusable edge runtimes and control clients;
- `house_ui/`, `ui/`, and `satellite_ui/` — reusable browser interfaces,
  including the supported responsive/mobile web surface;
- `docs/` — reusable contracts, architecture, references, roadmaps, and guides;
- `examples/` — generic configuration and integration examples;
- `scripts/` — reusable development, validation, installation, and operational
  tools;
- `tests/` — self-contained core tests and explicitly classified test assets.

Files being present in the core tree does not install development dependencies
or enable every capability. Standard installations materialize the faithful
committed core tree as an immutable application revision, while the selected
  production profile controls runtime dependencies and facilities.

A historical React Native proof of concept remains outside clean core and the
standard installation. Any future native Oracle app will establish its own
package identity and compatibility contract when that separate project begins.

## Development And Distribution

The default branch contains approved generated distribution snapshots. It is
not an independent authoring branch. Changes are developed and tested in the
project's private integration authority, reviewed for the reusable boundary,
and promoted as complete sanitized snapshots. The exact candidate tested by
clean-core CI is the commit eligible for approval.

Proposed contributions are reviewed and incorporated through that development
authority before a later snapshot promotion. This keeps reusable distribution
history clean without asking household operators to use Git or manage source
repositories.

## Installation Status

The standard Debian installation and lifecycle tooling are being established in
Stage 4. The first household-consumable release will be `v0.1.0` only after the
clean-host installation, systemd startup, reboot, update, automatic recovery,
explicit rollback, and preservation requirements have been validated. Until
then, approved untagged commits are development and deliberate testing pins.

## License

Oracle reusable core is licensed under the Apache License 2.0. See
[`LICENSE`](LICENSE) for the complete terms.
