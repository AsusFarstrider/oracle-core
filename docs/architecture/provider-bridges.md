# Provider Bridges

## Purpose

This document defines the architectural role of provider bridges in Oracle.

Provider bridges exist so that:

- domains stay Oracle-native
- bridges translate Oracle-native requests and responses into provider-native requests and responses
- swapping providers is primarily a bridge change, not a domain rewrite
- provider-specific behavior, IDs, payload shapes, retries, and quirks stay out of domain logic

Provider bridges are not an abstraction exercise.

They are a boundary tool for keeping Oracle behavior separate from provider integration details.

## Core Model

Oracle follows this direction:

- brain owns routing, policy, state, and reply shaping
- domains own Oracle behavior
- provider bridges own provider translation
- runtime and control-plane components own execution and local playback truth

Stated simply:

- domains speak Oracle
- bridges speak provider

That means:

- a domain should ask for Oracle-native operations
- a bridge should translate those operations into provider-native requests
- a bridge should translate provider-native responses into Oracle-native objects

The domain should not need to understand:

- provider endpoint structure
- provider auth mechanics
- provider field names
- provider payload nesting
- provider retry rules
- provider-specific error semantics

## System Placement

Provider bridges sit below the domain and above provider-specific transport details.

The intended shape is:

- routing selects a domain
- the domain parses and applies Oracle-side behavior
- the domain calls a provider bridge when it needs external provider data or provider-backed actions
- the bridge talks to the provider and returns Oracle-native results
- the domain performs Oracle-side ranking, clarification, confirmation, and response shaping

Provider bridges do not sit in:

- routing
- dispatch selection
- reply shaping
- satellite control
- playback authority
- runtime-local state ownership

## Current Code Reality

The current codebase already contains several partial bridge seams, even where the term "bridge" is not yet used.

### Calendar

The calendar domain already separates Oracle-side behavior from some backend mechanics.

Current seams:

- [server/oracle_app/calendar.py](../../server/oracle_app/calendar.py)
- [server/oracle_app/calendar_write.py](../../server/oracle_app/calendar_write.py)
- [server/oracle_app/provider_bridges/nextcloud_calendar.py](../../server/oracle_app/provider_bridges/nextcloud_calendar.py)

Current shape:

- the domain owns query parsing, time-range inference, matching, and write clarification
- the bridge owns Nextcloud-specific fetch, auth, ICS parse, and write mechanics
- provider mechanics are now explicit bridge-owned behavior

### News

The news domain is already close to a tiny bridge model.

Current seam:

- [server/oracle_app/news.py](../../server/oracle_app/news.py)
- [server/oracle_app/provider_bridges/rss_news.py](../../server/oracle_app/provider_bridges/rss_news.py)

Current shape:

- the domain owns request parsing and source detection
- the RSS bridge owns fetch, XML parse, and date normalization mechanics

### Audiobook

The audiobook domain now has an explicit Audiobookshelf bridge under its existing public domain surface.

Current seams:

- [server/oracle_app/audiobook_runtime/client.py](../../server/oracle_app/audiobook_runtime/client.py)
- [server/oracle_app/provider_bridges/audiobookshelf_audiobook.py](../../server/oracle_app/provider_bridges/audiobookshelf_audiobook.py)
- [server/oracle_app/audiobook_runtime/playback.py](../../server/oracle_app/audiobook_runtime/playback.py)
- [server/oracle_app/handlers/audiobook.py](../../server/oracle_app/handlers/audiobook.py)

Current shape:

- the domain owns parsing, scoring, clarification, and sleep-timer behavior
- the Audiobookshelf bridge owns provider requests, auth, progress filtering, stream fetch mechanics, and sync/close payload translation
- runtime playback, active state, and satellite control remain outside the bridge
- provider-specific IDs and session semantics still leak upward as migration debt

### Music

The music domain now has an explicit Plex bridge under the existing music client surface, but provider leakage remains migration debt above that boundary.

Current seams:

- [server/oracle_app/music_runtime/client.py](../../server/oracle_app/music_runtime/client.py)
- [server/oracle_app/provider_bridges/plex_music.py](../../server/oracle_app/provider_bridges/plex_music.py)
- [server/oracle_app/music_runtime/control.py](../../server/oracle_app/music_runtime/control.py)
- [server/oracle_app/music_runtime/policy.py](../../server/oracle_app/music_runtime/policy.py)
- [server/oracle_app/handlers/music.py](../../server/oracle_app/handlers/music.py)

Current shape:

- the Plex bridge owns endpoint construction, tokenized requests, XML parsing, metadata traversal, fallback track lookup, and native queue manifest expansion
- satellite control-plane logic is already separated from provider lookup
- the domain still carries Plex IDs and Plex-specific candidate shapes directly as migration debt

### Home Assistant

Home Assistant now has an explicit provider bridge without pretending Oracle has a universal home-automation command model.

Current seams:

- [server/oracle_app/handlers/home_assistant.py](../../server/oracle_app/handlers/home_assistant.py)
- [server/oracle_app/provider_bridges/home_assistant.py](../../server/oracle_app/provider_bridges/home_assistant.py)

Current shape:

- the domain owns room-context and confirmation behavior
- the bridge owns Home Assistant conversation requests, direct service requests, conversation-id reuse/update, provider payload parsing, success-target extraction, and entity state verification
- final dispatch status and user-facing reply behavior remain in the domain/brain path

### Apprise

Apprise is the single bridge for notifications leaving Oracle through
non-satellite transports.

Current seams:

- `server/oracle_app/provider_bridges/apprise.py`
- `server/oracle_app/notifications/receipts.py`
- `server/oracle_app/notifications/channels/external.py`
- `server/oracle_app/notifications/external_worker.py`
- `server/oracle_app/admin_notifications_routes.py`

Current shape:

- the notifications domain owns content, logical audiences, suppression,
  idempotency, retry bounds, expiry, and durable receipt state;
- the bridge owns Apprise endpoint construction, stateful key/tag payloads,
  timeout/error translation, and sanitized gateway acceptance;
- Apprise owns all concrete provider URLs, credentials, topics, and transport
  behavior outside Oracle;
- satellite delivery remains a native notifications-domain channel.

### Weather

Weather is the first real, code-grounded example of a multi-role provider domain.

Current seams:

- [server/oracle_app/weather_current.py](../../server/oracle_app/weather_current.py)
- [server/oracle_app/provider_bridges/weewx_weather_station.py](../../server/oracle_app/provider_bridges/weewx_weather_station.py)
- [server/oracle_app/weather_forecast.py](../../server/oracle_app/weather_forecast.py)
- [server/oracle_app/provider_bridges/nws_weather_forecast.py](../../server/oracle_app/provider_bridges/nws_weather_forecast.py)
- [server/oracle_app/weather_remote.py](../../server/oracle_app/weather_remote.py)
- [server/oracle_app/weather_history.py](../../server/oracle_app/weather_history.py)

Current shape:

- current and historical weather rely on different provider surfaces than forecast
- current and historical local-station provider mechanics now sit behind `WeeWxWeatherStationBridge`
- forecast provider mechanics now sit behind `NwsWeatherForecastBridge`
- remote location resolution is also provider-backed
- this is not one interchangeable provider problem

## Default Bridge Selection Model

The default model is:

- one active bridge per domain

Examples:

- one music bridge
- one audiobook bridge
- one calendar bridge

This is the standard pattern Oracle should prefer.

The reason is simple:

- most domains have one primary provider-backed responsibility
- one bridge keeps the boundary small
- one bridge reduces accidental abstraction growth

## Provider Roles

Some domains legitimately require more than one provider role.

A provider role means:

- the domain already has distinct provider-backed responsibilities
- those responsibilities are not reasonably served by one interchangeable provider surface

This is not:

- multiple interchangeable bridges per role
- broad multi-bridge consultation as the normal pattern

This is:

- one domain
- multiple narrowly justified provider roles

### Weather As The First Real Example

Weather already implies multiple provider roles in the current code.

The grounded split is:

- `weather_current_provider`
- `weather_forecast_provider`

Reason:

- current and historical weather are tied to local-station or station-oriented data surfaces
- forecast behavior is tied to forecast-specific external providers

Those are separate responsibilities, not a single interchangeable provider slot.

Provider roles must remain:

- explicit
- narrow
- justified by current code reality

Multi-role domains are the exception, not the default.

## Boundary Shape

### What Crosses From Domain To Bridge

The domain should send:

- Oracle-native operation requests
- normalized Oracle-side intent data
- any domain-approved filters or provider-role selection
- session or user context only when the provider-backed operation genuinely needs it

The request should describe what Oracle needs, not how the provider works.

### What Crosses From Bridge To Domain

The bridge should return:

- Oracle-native objects
- Oracle-native result shapes
- domain-scoped normalized errors

The bridge may also include:

- an opaque bridge-owned or provider-owned reference

That reference may be carried by the domain, but the domain must not interpret provider internals inside it.

### What Must Never Cross The Boundary

The end-state boundary must not allow domains to depend on:

- provider-specific IDs
- provider-specific field names
- provider-specific payload nesting
- provider-specific response shape assumptions
- provider-specific retry semantics
- provider-specific error semantics

Temporary leakage may exist during migration, but it is not the intended architecture.

## Responsibilities

### Domains Own

- Oracle-side parsing
- Oracle-side ranking
- Oracle-side matching
- Oracle-side clarification
- Oracle-side confirmation policy
- Oracle-side session policy
- cross-domain fallback decisions
- final user-facing response shaping

### Bridges Own

- provider request construction
- provider auth and token usage
- provider endpoint selection
- provider retries and low-level fallback behavior
- provider-specific payload parsing
- light provider-side normalization
- translation of provider errors into domain-scoped Oracle errors

### Runtime And Control Plane Own

- playback authority
- satellite control
- interruption and resume
- reply-audio routing
- local runtime state
- command dispatch execution

Provider bridges must not absorb runtime or control-plane behavior.

## Oracle-Native Return Direction

Bridges return Oracle-native objects.

They do not:

- construct full domain-level outputs
- decide final user-facing structure
- choose ranking or clarification outcomes

The domain remains the owner of final Oracle behavior.

## Config Direction

Provider selection should be configured per domain or per provider role.

Examples:

- `music_provider = plex`
- `audiobook_provider = audiobookshelf`
- `calendar_provider = nextcloud`

For multi-role domains:

- `weather_current_provider = local_station`
- `weather_forecast_provider = nws`

Oracle should not default to:

- multiple bridges per role
- fine-grained provider splits such as separate search, metadata, and playback bridges

Those splits require current-code justification, not aesthetic preference.

## Error Direction

Bridge errors should be normalized into domain-scoped Oracle errors.

Examples of the intended pattern:

- music bridge returns music-scoped Oracle errors
- calendar bridge returns calendar-scoped Oracle errors
- audiobook bridge returns audiobook-scoped Oracle errors

The bridge should not pass raw provider errors upward as the normal contract.

Oracle also should not build one giant cross-domain provider error taxonomy.

## Migration Direction

The first extraction rule is:

1. preserve current domain behavior
2. reduce provider leakage
3. harden the boundary

The first bridge extraction is not the moment to:

- redesign the domain
- redesign replies
- redesign routing
- redesign session policy

Bridge work should tighten boundaries before it changes behavior.

## Anti-Patterns

The following are architectural anti-patterns:

- abstracting too early
- building universal provider layers
- leaking provider IDs into domains
- moving Oracle logic into bridges
- mixing bridge work with control-plane logic
- treating multi-bridge consultation as the default pattern
- redesigning domains during extraction

## Summary

Provider bridges are a boundary, not a new center of gravity.

The intended Oracle model is:

- domains stay Oracle-native
- bridges absorb provider details
- control-plane logic stays separate
- provider-role splits are explicit exceptions, not a new default architecture

## V2 Configuration Reconciliation

Every bridge receives a typed immutable domain/shared-role fragment and declared
secret handles. Provider selection is explicit per role. There is no universal
provider file, environment fallback, health-based precedence, or
deployment-defined bridge plugin registry.
