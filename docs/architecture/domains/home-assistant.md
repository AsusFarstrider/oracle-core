# Home Assistant Interactive Domain Architecture

This domain owns household voice commands and curated UI actions backed by
Home Assistant. It is separate from the durable home-automation runbook domain,
which consumes HA event evidence and owns delayed workflows.

## Boundaries

- `handlers/home_assistant.py` owns command confirmation, room context, dispatch
  status, and reply-facing behavior.
- `home_assistant_actions.py` owns curated UI action IDs, dynamic climate
  adjustments, provider error normalization, and verified action outcomes.
- `provider_bridges/home_assistant.py` owns HA conversation, service, entity
  state, polling, authentication, and payload translation mechanics.
- `api.py` exposes Oracle request/response contracts and delegates interactive
  HA work; it does not own HA service names or entity-action mappings.

`POST /api/ui/action` remains action-ID based. Browser clients never submit HA
entity IDs, service names, credentials, or provider-native payloads. Task
routines that use curated UI actions execute through the same Brain-owned
action path.

## Context and ambiguity

Strong Home Assistant context may resolve explicit referential follow-ups such
as `turn them off`, `set it to 72`, or `what about the bedroom`. It may claim a
room comparison only when the room is canonical and the prior HA command can be
reconstructed safely.

Explicit weather, calendar, music, audiobook, timer, time, network, news, and
facts/fallback requests retain their own deterministic route. Home Assistant
context must not hijack those requests. Unresolved room-sensitive commands use
the existing clarification state and never execute without a resolved room.

Provider-backed actions remain verified after execution. A successful HA
service request is not sufficient when the target fails to reach its expected
state.

## V2 Configuration Reconciliation

This domain owns `domains/home-assistant.yaml`: bridge configuration,
Oracle-to-provider room/entity/action and mode mappings, camera mappings, the
finite typed Home/House/Room view membership, and HA-owned automation
definitions. Provider-native identifiers remain only at the mapping edge;
views reference mapping IDs and never repeat raw entity IDs. Current provider
state, including household mode values, is operational truth and is never
changed by configuration activation.

The Stage 3 construction seam maps the optional applied role into frozen
`HomeAssistantRuntimeSettings`. Enabled interactive access resolves the selected
provider API credential and exposes the finite typed mapping registry at this
adapter edge. Only enabled automation definitions become operational runtime
entries, each already bound to its exact typed event mapping. The separate
event-ingress credential is resolved only when at least one such automation is
enabled; dormant definitions do not require or expose it. Disabled Home
Assistant selects no provider, mapping, automation, or secret. Raw credentials
remain absent from representations, while provider-native entity IDs remain
confined to the adapter-owned mappings.

The view surface externalizes only household-specific inventory, ordering,
canonical room association, optional labels, and camera snapshot references.
Oracle code retains the fixed page/section vocabulary, rendering, icons,
presentation defaults, state interpretation, actions, and response
serialization. This is not a dashboard, widget, layout, or theme system.
Camera references are confined relative logical paths beneath the selected
provider's deployment-owned snapshot root; they cannot be absolute host paths.

Canonical request composition now binds its route registry and Home Assistant
handler to the applied household room view. Room IDs, display names, aliases,
pending replies, implied-command normalization, and active-context follow-ups
read only canonical room vocabulary. Home Assistant entity discovery may still
help recognize provider entities, but it cannot override a configured room
term or create Oracle room identity.

Canonical handler and curated-action execution now construct the provider
bridge directly from that immutable view. Conversation, service, and entity
requests use the selected provider URL, credential, and timeout without calling
a compatibility settings getter. Curated public action IDs resolve exact typed action or
entity mappings; a missing role, missing mapping, unsupported operation, or
incomplete provider fails closed and never falls back to a hardcoded provider
target. Confirmed actions re-enter through the same installed handler,
so approval cannot switch configuration authority. Home, House, Room,
camera-snapshot, and HA-health reads now use the same immutable runtime view
under canonical composition and cannot consult hardcoded household inventories,
provider discovery, or compatibility getters. A separate immutable satellite-UI view
supplies only enabled UI definitions; the operational satellite fleet remains a
narrower control-edge view. No typed settings are converted into compatibility
dictionaries.

Canonical event ingress now selects its credential, provider-event mappings,
and enabled automation definitions from the installed application composition.
Incoming provider entity/state evidence resolves one typed event mapping;
ambiguous duplicate provider mappings or competing enabled lifecycle owners are
activation errors. Entry events produce a bounded controller definition tied to
the exact applied configuration revision, and the runbook kernel freezes that
definition for the active run. Canonical admin presentation reads the same
typed definitions.

The continuation scheduler can now construct its Home Assistant state reader
from the typed provider view, but canonical construction requires an explicitly
injected notifications capability. It never invents a default notification
service.
