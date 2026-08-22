# Oracle Health Ownership

## Purpose

This document defines Oracle's health ownership contract and the required semantics for health reporting.

It defines:

- which layers own health logic
- the required semantic meaning of health reporting
- the contract roles of `/health` and `/health/<subsystem>` endpoints
- the boundary between health reporting and config validation

## Ownership Rule

Health is a first-class subsystem.

The ownership contract is:

- the API layer owns HTTP route exposure for health endpoints
- domain or provider layers own domain-specific probe logic
- health composition and normalization belong to a health layer, not to request dispatch
- dispatch does not own subsystem health probing

## Health Semantics Taxonomy

Health reporting must distinguish among three concepts:

### 1. Config Exists

Meaning:

- the subsystem has enough configuration present to attempt use

Examples:

- Home Assistant token exists
- Audiobookshelf URL, token, and library id exist
- calendar ICS URL exists
- STT provider has a configured model path

This is not the same as network reachability or runtime usability.

### 2. Dependency Reachable

Meaning:

- Oracle can contact the external dependency or local executable it relies on

Examples:

- HTTP request to Home Assistant succeeds
- Ollama version endpoint responds
- Audiobookshelf ping responds
- Piper binary resolves on disk
- whisper.cpp binary resolves on disk

This is not the same as full request success for every workload.

In canonical mode, Audiobookshelf health uses the selected provider and each
enabled configured user's exact logical credential from the immutable applied
snapshot. It reports failure if any configured account cannot complete the
bounded ping. It never falls back to a global or legacy token. Playback targets
reported by that probe are the audiobook-domain-admitted canonical source IDs,
not provider discovery or general fleet capability.

### 3. Subsystem Usable

Meaning:

- the subsystem is configured and reachable enough to perform its intended primary action

Examples:

- STT provider has a binary, model, and ready execution path
- TTS provider has a binary, model, and config file
- calendar feed is configured, fetchable, and parseable
- a playback-capable music source has both Plex config and a valid satellite control target

Usable is stricter than merely configured or reachable.

## Response Semantics

Health responses must represent the configured, reachable, and usable semantics.

These semantics may appear as explicit fields or as clearly derivable response values, but they must be represented in the health surface.

A coarse top-level status may be present, but it does not replace these semantics.

## Endpoint Taxonomy

### `GET /health`

Role:

- shallow brain-level liveness and config-summary endpoint

Requirements:

- it remains lightweight
- it does not perform broad live dependency probing
- it may expose selected config-present booleans or equivalent summary information for major integrations
- it does not replace subsystem-specific readiness endpoints

### `GET /health/<subsystem>`

Role:

- subsystem-specific readiness endpoint

Requirements:

- it reports config-present semantics
- it reports dependency-reachable semantics where applicable
- it reports subsystem-usable semantics where applicable
- it exposes enough information to distinguish configuration failure from dependency reachability failure and subsystem usability failure

## Boundary Relative To Config Validation

Config validation and health reporting are distinct surfaces.

Config validation answers:

- whether required configuration is present
- whether configuration is internally consistent
- whether conflicts or deprecated inputs exist

Health reporting answers:

- whether a subsystem can currently be reached
- whether a subsystem is currently usable for its primary action

Health reporting does not replace config validation.
Config validation does not replace health reporting.

Canonical V2 also separates activation compatibility from both surfaces.

Activation compatibility answers:

- whether the Oracle/runtime version supports the bundle or projection schema;
- whether required secret references are present in the candidate secret
  generation;
- whether generated satellite projections are valid; and
- whether known applied satellite runtimes can consume their desired
  projections.

Operational readiness answers whether the configured provider, gateway, host,
or satellite is currently available. A provider outage or external gateway
mismatch does not make deterministic configuration structurally invalid.

Configuration reports separate validation findings, activation blockers, and
operational-readiness findings. Finding severity and `blocks_activation` remain
separate fields. Public liveness never exposes configuration or secret detail.
