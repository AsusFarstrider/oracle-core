# Oracle Room Context Surface

This document describes the current implementation shape of Oracle's room-context surface.

For the canonical room-context contract, see
[room-context.md](../contracts/room-context.md).

## Current Codebase Fit

The current room-context model fits the existing Oracle architecture:

- requests remain `text + source + session_id`
- routing and normalization remain brain-owned
- dispatch remains thin
- the Home Assistant execution path remains text-driven
- sessions remain short-lived continuity rather than durable truth

This preserves the current contract in:

- [schemas.py](../../server/oracle_app/schemas.py)
- [dispatch.py](../../server/oracle_app/dispatch.py)
- [session_state.py](../../server/oracle_app/session_state.py)
- [home_assistant.py](../../server/oracle_app/handlers/home_assistant.py)

## Room-Resolution Seam

Oracle uses a shared brain-side room-resolution seam.

This seam is not embedded only inside:

- [routing_helpers.py](../../server/oracle_app/routing_helpers.py)
- [home_assistant.py](../../server/oracle_app/handlers/home_assistant.py)

### Required Components

The room-context surface includes:

- a room vocabulary accessor
- a room-sensitive home-command classifier
- a shared room resolver

Implementation grouping:

- a small `room_context` namespace under `server/oracle_app/`
- only the shared room or source seam modules needed for room-context behavior

## Inspection Surfaces

Room-context behavior is currently visible through:

- `GET /health/config` for config-report findings tied to room-context inputs such as source-registry shape and room-vocabulary checks
- `GET /api/voice/session` for session-scoped room-context state such as
  `active_room_ref`
- request-path logs for room-resolution behavior, including resolved room and resolution source where logged

## V2 Configuration Reconciliation

Stable sources and `associated_room_id` move to `household.yaml`; arbitrary
caller strings never create source truth. Canonical resolution provenance uses
`deictic_source_association` and `source_association_fallback`. The deictic and
ordinary non-deictic branches follow the room-context contract.

The canonical household consumer seam indexes typed enabled rooms and exposes
an enabled fixed source's configured `associated_room_id` as context only. The
ingress layer must already have established that stable source; the lookup does
not authenticate a caller or treat an arbitrary source string as proof.

The shared room resolver and home-routing seam now accept that household view
as an explicit canonical input. Canonical room names, session room references,
and fixed source associations resolve only against the applied typed household
snapshot, without consulting an external source registry or the Home Assistant
cache-derived vocabulary. Canonical provenance uses
`deictic_source_association` and `source_association_fallback`; the deictic and
ordinary branches retain their different precedence laws. Canonical route and
command composition now supplies the typed view only after request ingress has
established the source; uncredentialed HTTP and internal callers receive an
unassociated ephemeral source.

Canonical application composition now also owns a separately constructed route
capability registry bound to that same household view. Implied and keyword Home
Assistant routing, pending-room replies, active-context follow-ups, command
normalization, room injection, and handler clarification all use enabled typed
room IDs, display names, and aliases. `resolved_room` remains the canonical ID;
provider-facing freeform command text uses the configured display name.

The synced Home Assistant entity registry remains operational discovery input
for recognizing provider entities. Canonical room terms win collisions, so an
entity discovery alias cannot redefine or override household room vocabulary.
This does not make the HA cache a second configuration authority.
