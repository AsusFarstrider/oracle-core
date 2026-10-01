# Home Assistant Interactive Domain Architecture

This domain owns household voice commands and curated UI actions backed by
Home Assistant. It is separate from the durable home-automation runbook domain,
which consumes HA event evidence and owns delayed workflows.

## Boundaries

- `handlers/home_assistant.py` owns command confirmation, room context, dispatch
  status, and reply-facing behavior.
- `home_assistant_actions.py` owns deterministic finite resolution, shared
  voice/UI/runbook semantic risk, configured action IDs, climate policy, error
  normalization, and verified action outcomes.
- `provider_bridges/home_assistant.py` owns exact typed HA operations, entity
  state, polling, authentication, and payload translation mechanics.
- `api.py` assembles the public route families; `application_command.py` and
  `application_ui.py` delegate interactive HA work. None owns HA service names
  or entity-action mappings.

`POST /api/ui/action` remains action-ID based. Browser clients never submit HA
entity IDs, service names, credentials, or provider-native payloads. Task
routines that use curated UI actions execute through the same Brain-owned
action path.

## Ratified Finite Interactive Target

Stage 8 retires Home Assistant Conversation as mutating authority. Every public
voice, UI, and runbook mutation must resolve a finite Oracle-owned semantic
action, Oracle target, typed bounded arguments, semantic risk, and expected
result before the provider bridge is called. The bridge translates only that
typed request into an allowlisted provider service operation. There is no
mutating natural-language Conversation fallback.

Read-only provider discovery may remain an implementation aid, but discovered
entities, services, scripts, scenes, or automations are not callable authority.
Provider-owned unit actions become callable only through explicit configured
Oracle mappings; Home Assistant owns their internals and Oracle authorizes the
mapped unit rather than recursively inspecting its steps.

Risk is semantic and interface-independent. Lock/close/arm directions are
normally low friction; unlock/open/disarm require confirmation. Environmental
changes within configured normal bounds are ordinary, changes outside normal
but within actual provider/device bounds require confirmation, and changes
outside provider/device capability fail. Scope participates in risk without
making every multi-target operation consequential.

Typed results distinguish verified state change, accepted but unverified,
rejected/unsupported, unavailable, failed, and outcome unknown. Provider
acceptance is not success when state can be verified. Ambiguous callable names
clarify and produce a persistent non-blocking warning; Oracle never silently
chooses between a runbook and provider action.

Presence is implemented as read-only current provider evidence. Only explicitly configured HA
`person` mappings may associate a provider entity with a canonical Oracle user,
and Oracle normalizes only `home`, `away`, or `unknown`. Raw device trackers,
coordinates, non-home zones, history, room inference, and find-device behavior
remain provider-only. Source `associated_user_id` is context, never presence
proof.

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
Public Home/House and satellite room-control/environment serialization emits
`target_id` from the mapping's Oracle `oracle_id`; cameras retain Oracle
`camera_id`. Native `entity_id` is used only to fetch provider state and is not
copied into public read models, including unavailable items. Provider remapping
preserves public identity, labels, actions, order, and confirmation behavior.
No separate identity registry or provider-ID compatibility field is created.
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

Canonical handler and shared semantic execution construct the provider bridge
directly from that immutable view. Exact typed operations and entity-state
reads use the selected provider URL, credential, and timeout without calling a
compatibility settings getter. Curated public action IDs and finite voice
grammar resolve exact typed action or entity mappings; a missing role, missing
mapping, unsupported operation, ambiguous target, or incomplete provider fails
closed and never falls back to a hardcoded or discovered provider target.
Voice resolution, composite validation, and typed execution share the mapping
equivalence check in `configuration/home_assistant_action_semantics.py`.
Different mapping IDs or lexical aliases for an identical provider binding and
execution policy count once; stable mapping-ID order selects the representative.
Distinct bindings or policies remain ambiguous. Curated UI IDs still select
their exact configured mapping, with unchanged semantic risk and verification.
Configured aliases and normal climate bounds live on mappings; actual provider
minimum/maximum and unit evidence is checked before climate dispatch. Confirmed
actions re-enter through the same installed handler,
so approval cannot switch configuration authority. Home, House, Room,
camera-snapshot, and HA-health reads now use the same immutable runtime view
under canonical composition and cannot consult hardcoded household inventories,
provider discovery, or compatibility getters. The reconstructable entity cache
remains available to interpretation/read/advisory consumers but cannot create a
callable target or alias and is not consulted for mutation authorization. A
separate immutable satellite-UI view
supplies only enabled UI definitions; the operational satellite fleet remains a
narrower control-edge view. No typed settings are converted into compatibility
dictionaries.

The same applied mapping registry now admits `person_presence` entries. Whole-
bundle validation requires each entry to bind one unique enabled canonical user
to one unique provider `person` entity with read-only operation. System-owned
presence queries resolve people through household user vocabulary, read only
those mappings, collapse provider-specific non-home zones to `away`, and expose
no entity ID, zone name, coordinate, tracker, or history value.

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
