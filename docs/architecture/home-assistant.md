# Home Assistant Integration

This document records the current Home Assistant integration shape in Oracle.

The integration is divided between brain-side interpretation support and handler-based execution.

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

Room and entity matching are built on the cached Home Assistant vocabulary together with room-context support.

## Handler Execution

Execution lives in the dedicated Home Assistant handler.

The handler is responsible for:

- executing the `home_assistant` dispatch target
- calling the Home Assistant conversation API
- handling returned results for Oracle's dispatch flow

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

## Conversation Continuity

The integration uses Home Assistant conversation-id continuity through Oracle conversation state.

That conversation-id continuity is part of the handler flow rather than a separate integration layer.

For bounded same-session follow-up recovery, brain-side routing may also consult recent Oracle conversation history when a Home Assistant-dependent follow-up phrase is plausible but the strong active context is unexpectedly absent.

That recovery is still brain-owned interpretation support.

It does not move Home Assistant execution into conversation storage, and it must still resolve back into canonical Home Assistant command text before dispatch.

## Pending Clarification

Pending room clarification is stored in Oracle state and resumes through the same Home Assistant handler path once room resolution is available.

## Confirmation And Verification Flow

Confirmations are part of the current Home Assistant integration flow for some requests before execution continues through the handler path.

The handler also performs a post-request verification pass for some successful actuator responses.

## Configuration Ownership

The canonical configuration owner is
`architecture/domains/home-assistant.md`. This document continues describing
the integration structure but does not define a competing config surface.
