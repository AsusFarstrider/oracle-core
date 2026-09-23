# Home Assistant Integration

This document records the current Home Assistant integration shape in Oracle.

The integration is divided between Brain-side interpretation support and finite
semantic execution through configured provider mappings.

Home Assistant is represented as the `home_assistant` dispatch target and is handled by a dedicated dispatch handler.

## Structural Pieces

The current integration has three main structural pieces:

- routing helpers
- room-context modules
- handler execution

## Interpretation Support

Brain-side interpretation support lives in shared routing helpers and room-context modules.

This support includes:

- cached room and entity vocabulary matching
- room-name and alias support
- room-sensitive request resolution support

Canonical room interpretation may use the cached Home Assistant vocabulary
together with room-context support. The cache is reconstructable discovery
evidence only: it cannot create a callable target, operation, or alias. Mutation
selection reads only the applied typed mappings and their Oracle-owned target
terms.

## Handler Execution

Execution lives in the dedicated Home Assistant handler.

The handler and shared action executor are responsible for:

- executing the `home_assistant` dispatch target
- resolving a closed Oracle capability, canonical target, bounded arguments,
  risk, and expected result before provider dispatch
- translating only the resolved configured mapping through exact typed bridge
  methods
- verifying readable provider state after mutation and preserving unknown or
  rejected outcomes honestly

The bridge exposes no natural-language Conversation mutation method. Public
voice, UI, and runbook paths share the semantic risk/execution machinery and
cannot submit provider entity IDs or service names.

Read-only presence queries use separately typed `person_presence` mappings.
They resolve Oracle users before reading HA, normalize only `home`, `away`, or
`unknown`, and never consult the entity cache or device trackers as a fallback.

The authenticated HA event ingress may also feed reviewed automatic routine
triggers. The HA boundary converts configured entry-state mappings to
`home_event` evidence and configured person mappings to coarse `presence`
evidence; only canonical Oracle IDs, normalized state, and stable event
occurrence identity cross into orchestration. Accepted delivery is durably
staged before activation so a Brain restart can retry it. This path cannot use
discovery/cache entries as authority and cannot expose HA entity/service
concepts to a routine definition.

## Integration Surfaces

The current integration surfaces include:

- cache file: `data/home-assistant-cache.json`
- typed cache refresh owner: `server/oracle_app/home_assistant_cache.py`
- camera still snapshot helper: `server/oracle_app/home_assistant_camera.py`

The canonical system handler invokes the cache owner with its injected, immutable
Home Assistant runtime settings. The owner fetches the provider state and
atomically replaces the reconstructable cache; it does not read configuration
or secrets independently.

## Camera Still Snapshots

House Mode camera stills are a Home Assistant domain concern, not a browser reach-around.

For the current Beta path, Home Assistant produces scheduled Eufy snapshot files under `/config/www/snapshots`, which HA serves through `/local/snapshots/...`.

Oracle should fetch those HA-served still files through the Home Assistant integration and expose Oracle-owned `/api/ui/...` snapshot URLs to browser clients.

Rules:

- use HA `/local/snapshots/*_latest.jpg` for the scheduled production still images
- do not use HA `/api/camera_proxy/...` for this scheduled-still contract
- do not make browser clients fetch HA URLs directly
- treat this as still-image support, not live camera streaming

## Interaction Continuity

Oracle's bounded room/action clarification and semantic session context remain
authoritative. Provider Conversation identity is not part of mutation
execution.

For bounded same-session follow-up recovery, brain-side routing may also consult recent Oracle conversation history when a Home Assistant-dependent follow-up phrase is plausible but the strong active context is unexpectedly absent.

That recovery is still brain-owned interpretation support.

It does not move Home Assistant execution into conversation storage. The
ratified target resolves to a finite typed Oracle semantic action before
dispatch, never provider natural-language command text.

## Pending Clarification

Pending room clarification is stored in Oracle state and resumes through the same Home Assistant handler path once room resolution is available.

## Confirmation And Verification Flow

Confirmation follows the resolved semantic direction and configured climate
bounds, identically across voice, UI, and runbooks. A provider-accepted action
with readable but unchanged state is not success.

## Configuration Ownership

The canonical configuration owner is
`architecture/domains/home-assistant.md`. This document continues describing
the integration structure but does not define a competing config surface.
