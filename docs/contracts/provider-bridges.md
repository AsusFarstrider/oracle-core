# Oracle Provider Bridges Contract

## Purpose

This document defines Oracle's provider bridge contract.

It defines:

- the required responsibility boundary between domains and provider bridges
- the required Oracle-native boundary shape
- the allowed provider-role model
- the forbidden scope for bridge extraction
- the migration and testing expectations for bridge work

If implementation and architecture notes conflict, this contract wins.

## Contract Goal

Provider bridges exist to prevent provider-specific behavior from becoming domain law.

The required end state is:

- domains speak Oracle-native concepts
- bridges translate Oracle-native requests and responses into provider-native requests and responses
- domains do not depend on provider internals

## Ownership Rule

### Domains Own

- Oracle-side parsing
- Oracle-side ranking
- Oracle-side matching
- Oracle-side clarification
- Oracle-side confirmation policy
- Oracle-side routing participation
- Oracle-side session policy
- cross-domain fallback decisions
- final response shaping

### Bridges Own

- provider request construction
- provider auth and token usage
- provider endpoint selection
- provider retries
- low-level provider fallback behavior
- provider-specific payload parsing
- light provider-side normalization
- provider error translation into domain-scoped Oracle errors

### Runtime And Control Plane Own

- playback authority
- satellite control
- interruption and resume
- reply-audio routing
- local runtime state
- command dispatch behavior

Bridge code must not claim runtime or control-plane ownership.

## Boundary Rule

Bridges must return Oracle-native objects and results.

Required:

- fields used by the domain must be Oracle-native
- result shapes used by the domain must be Oracle-native
- provider-specific payload structure must be hidden behind the bridge

Allowed:

- a bridge may return an opaque bridge-owned or provider-owned reference
- the domain may store or pass that reference

Disallowed:

- the domain interpreting provider internals inside that reference
- the domain reconstructing provider meaning from provider field names or payload fragments

## Leakage Rule

The target architecture forbids domains from depending on:

- provider-specific IDs
- provider-specific field names
- provider-specific response structures
- provider-specific error semantics

Temporary leakage during migration is tolerated only as an intermediate state.

Migration work must reduce that leakage rather than normalize it.

## Default Bridge Selection Rule

The default rule is:

- one active bridge per domain

Examples:

- one music bridge
- one audiobook bridge
- one calendar bridge

This is the standard Oracle pattern.

## Provider-Role Rule

Some domains may define multiple provider roles.

A provider role is allowed only when:

- the domain already has distinct provider-backed responsibilities
- those responsibilities cannot reasonably be treated as one interchangeable provider slot

Required:

- provider roles must be explicit
- provider roles must be narrowly named
- provider roles must be justified by current code reality

Disallowed:

- treating multiple interchangeable bridges per role as the default pattern
- inventing provider roles for theoretical future flexibility

### Weather Exception Rule

`weather` is a valid multi-role domain.

The current grounded role split is:

- `weather_current_provider`
- `weather_forecast_provider`

This is allowed because current and forecast behavior already rely on distinct provider responsibilities in the current code.

This weather exception must not be generalized into a default multi-bridge architecture.

## Config Selection Rule

Provider selection must be configured per domain or per provider role.

Allowed examples:

- `music_provider`
- `audiobook_provider`
- `calendar_provider`
- `weather_current_provider`
- `weather_forecast_provider`

Disallowed by default:

- multiple configured bridges per role
- fine-grained split such as search provider vs metadata provider vs playback provider

Such splits require current-code necessity, not design preference.

## Error Normalization Rule

Bridges must translate provider failures into domain-scoped Oracle errors.

Required:

- each domain defines the error vocabulary that matters to that domain
- bridge failures must be surfaced using that domain-scoped vocabulary

Disallowed:

- passing raw provider errors upward as the normal contract
- creating a single giant cross-domain provider error taxonomy

Provider detail may be retained internally or in debug detail fields, but it must not become the semantic contract the domain depends on.

## Non-Responsibility Rule

Bridges must not own:

- Oracle-side ranking
- Oracle-side matching
- Oracle-side clarification
- Oracle-side confirmation policy
- Oracle-side routing
- Oracle-side session policy
- cross-domain fallback decisions
- final user-facing response shaping
- playback authority behavior
- satellite command execution
- interruption and resume policy

If a bridge begins deciding Oracle behavior, the boundary has been violated.

## Runtime Boundary Rule

Provider bridges are not runtime or control-plane components.

Bridges must not handle:

- playback authority
- satellite control
- interruption or resume
- reply-audio routing
- local runtime state
- command dispatch

A bridge may prepare provider-backed data needed by runtime components, but it must not become the executor of runtime behavior.

## Migration Rule

The first extraction rule is mandatory:

1. preserve current domain behavior
2. reduce provider leakage
3. harden the boundary

Disallowed during first extraction unless explicitly approved:

- redesigning domain behavior
- redesigning routing behavior
- redesigning session behavior
- redesigning clarification policy
- redesigning reply shaping

Bridge extraction is boundary work first.

## Testing Rule

Bridge extraction must be tested at three levels when applicable.

### Translation Tests

Required:

- Oracle-native request to provider-native request translation
- provider-native response to Oracle-native object translation
- provider error to domain-scoped Oracle error translation

### Domain Boundary Tests

Required:

- domain behavior remains unchanged when the bridge replaces direct provider access
- ranking, clarification, and confirmation behavior stay domain-owned

### Control-Plane Separation Tests

Required when the domain also touches playback or runtime work:

- bridge extraction does not alter control-plane ownership
- provider bridge code does not become the owner of dispatch, interruption, or local playback truth

## Current-Code Guidance

The current codebase already shows the boundary Oracle should strengthen.

Relevant grounded examples:

- `calendar` now uses an explicit bridge in [server/oracle_app/provider_bridges/nextcloud_calendar.py](../../server/oracle_app/provider_bridges/nextcloud_calendar.py)
- `news` now uses an explicit bridge in [server/oracle_app/provider_bridges/rss_news.py](../../server/oracle_app/provider_bridges/rss_news.py) while keeping source selection in the domain
- `audiobook` now uses an explicit bridge in [server/oracle_app/provider_bridges/audiobookshelf_audiobook.py](../../server/oracle_app/provider_bridges/audiobookshelf_audiobook.py) through the existing [server/oracle_app/audiobook_runtime/client.py](../../server/oracle_app/audiobook_runtime/client.py) surface
- `music` now uses an explicit bridge in [server/oracle_app/provider_bridges/plex_music.py](../../server/oracle_app/provider_bridges/plex_music.py) through the existing [server/oracle_app/music_runtime/client.py](../../server/oracle_app/music_runtime/client.py) surface while keeping control-plane access in [server/oracle_app/music_runtime/control.py](../../server/oracle_app/music_runtime/control.py)
  Music selections expose generic provider references; Plex identity aliases
  are limited to bridge internals, legacy-selection compatibility, and the
  deployed satellite transport payload.
- the audiobook bridge normalizes item, progress, track, chapter, and playback
  session fields before they reach domain/runtime/UI code; only the opaque
  provider session reference is retained for provider sync and close calls
- `home_assistant` now uses an explicit bridge in [server/oracle_app/provider_bridges/home_assistant.py](../../server/oracle_app/provider_bridges/home_assistant.py) while keeping room-context, confirmation, dispatch status, and reply behavior in [server/oracle_app/handlers/home_assistant.py](../../server/oracle_app/handlers/home_assistant.py)
- network probe and LibreNMS read bridges now return immutable Oracle-owned
  observation DTOs from
  [server/oracle_app/provider_bridges/network_observations.py](../../server/oracle_app/provider_bridges/network_observations.py),
  with conversion to the established read-model dictionaries at the network
  domain boundary
- `weather` already implies multiple provider roles across [server/oracle_app/weather_current.py](../../server/oracle_app/weather_current.py), [server/oracle_app/weather_forecast.py](../../server/oracle_app/weather_forecast.py), and [server/oracle_app/weather_remote.py](../../server/oracle_app/weather_remote.py)

These examples inform the contract, but they do not override it.

## Forbidden Patterns

The following are forbidden patterns:

- abstracting beyond current reality
- building universal provider layers before domain boundaries are stable
- leaking provider IDs into domains as durable domain inputs
- moving Oracle logic into bridges
- mixing bridge extraction with runtime or control-plane rewrites
- treating multi-bridge consultation as the default
- redesigning domains during first extraction

## Summary

Provider bridge law in Oracle is:

- domains stay Oracle-native
- bridges absorb provider mechanics
- runtime and control plane remain separate
- one bridge per domain is the default
- multiple provider roles are explicit exceptions
- migration preserves behavior before it broadens architecture

## V2 Configuration Reconciliation

Provider selection and provider-specific configuration live in the fixed role
owned by that domain. A genuinely Brain-wide service such as shared speech or
inference transport may be defined narrowly in `brain.yaml`, while each domain
still owns whether and how it uses that role.

Oracle has no universal `providers.yaml`, provider override chain, or
deployment-defined bridge registry. Bridges consume typed immutable
configuration and declared secret handles rather than files or environment
variables.

The canonical Plex bridge accepts the selected typed music-provider connection
directly. It does not require a V1-shaped settings dictionary and cannot reopen
environment or local-file configuration. Provider search and queue construction
remain bridge responsibilities; playback targeting, transport, interruption,
and local playback truth remain music-domain and control-service
responsibilities.
