# Server Design

This document records the current server-side package structure.

`server/app.py` is a thin compatibility entrypoint. It imports the FastAPI app from `server/oracle_app/api.py`.

At a high level, the server pipeline is:

- routing
- dispatch
- handlers

## Package Layout

The core server package lives under `server/oracle_app/`.

Current top-level structure:

- `api.py`: FastAPI routes and app surface
- `schemas.py`: request and response models
- `command_processing.py`: command-path orchestration helpers
- `routing.py`: route selection entrypoint
- `route_refinement.py`: post-route refinement helpers
- `dispatch.py`: dispatch-plan construction and dispatch execution entrypoints
- `replies.py`: reply shaping helpers
- `health.py`: health endpoint helpers
- `tracing.py`: request-tracing helpers
- `config.py`: fail-closed boundary for the few not-yet-simplified internal
  call sites plus reconstructible cache loading; never configuration authority
- `config_reporting.py`: config reporting helpers
- `configuration/`: executable schema, generation, projection, activation, and
  immutable runtime composition
- `constants.py`: shared constants
- `system_intents.py`: system-intent classification helpers
- `routing_helpers.py`: shared routing helper functions
- `conversation.py`: conversation-context helpers
- `session_state.py`: session-state storage and lifecycle helpers
- `state.py`: additional runtime state helpers
- `user_context.py`: user-context helpers
- `alerts.py`: alert and reminder helpers
- `calculations.py`: deterministic calculation helpers
- `calendar.py`: calendar domain module
- `music.py`: music domain entry module
- `audiobook.py`: audiobook domain entry module
- `news.py`: news domain module
- `weather.py`: weather domain module
- `media_rescue_policy.py`: shared media fallback/rescue helpers

## Subpackages

The package also contains several focused subpackages.

### Capabilities

- `capabilities/`: routing capability plugins and the routing registry

### Handlers

- `handlers/`: dispatch handlers and the handler registry

### Runtime Modules

- `music_runtime/`: music runtime support modules
- `audiobook_runtime/`: audiobook runtime support modules

### Context

- `room_context/`: room-context resolution and related helpers

## Domain Modules And Runtime Subpackages

Some domains use both top-level modules and focused runtime subpackages.

- `music.py` coexists with `music_runtime/`
- `audiobook.py` coexists with `audiobook_runtime/`

The top-level domain files remain part of the current package surface, while the runtime subpackages hold additional domain-specific support modules.

## V2 Configuration Reconciliation

One Brain-side configuration service and shared offline engine replace runtime
file/env resolution as the target architecture. Startup constructs immutable
typed `EffectiveConfig` and `SecretSnapshot` objects; package/domain factories
receive owned fragments through injection and never open config files.
