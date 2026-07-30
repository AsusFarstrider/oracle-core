# `/api/ui/action` Contract

## Purpose

`POST /api/ui/action` executes an Alpha household UI action.

This is the structured UI action surface.

It is not a freeform conversation endpoint.

## Request

Method:

- `POST`

Required request fields:

- `action_id`
- `ui_session_id`

Optional request fields:

- `target_source_id`

## Request Rules

### `action_id`

- required
- string
- must be a curated public UI action identifier

Examples:

- `living_room_light_on`
- `living_room_lights_on`
- `bedroom_lights_toggle`

### `ui_session_id`

- required
- string
- scopes bounded temporary UI state under the authenticated request source

Normalization guidance:

- lowercase
- hyphen-separated
- no spaces
- opaque and non-authoritative

Examples:

- `example-session`
- `panel-session-2`

### `target_source_id`

- optional at the top-level request shape
- required for curated source-scoped actions such as audio transport actions
- identifies the Oracle playback-capable source the action should target

Examples:

- `living_room_voice`

The authenticated request `source_id` is established by ingress and is separate
from this action target. `ui_session_id` and `target_source_id` do not establish
authorization or audit actor identity. Current `client_id` and `source` target
fields are bounded Stage 3 aliases for deployed UIs.

Rules:

- clients must provide `target_source_id` when the action definition or page context requires explicit playback targeting
- clients must not guess or synthesize hidden source routing rules on their own
- Oracle validates that a provided `target_source_id` is a known enabled playback-capable source before execution

### Public Contract Boundary

The public `/api/ui/action` contract must not require:

- raw freeform user text
- raw route targets
- raw handler names

Internal implementation may map `action_id` to:

- Oracle-authored commands
- direct internal helpers
- curated domain execution paths

That mapping is internal and not part of the public contract.

Some curated action IDs are global and do not require `target_source_id`.

Examples:

- `living_room_light_on`
- `bedroom_lights_off`

Some curated action IDs are source-scoped and do require `target_source_id`.

Examples:

- `pause_audiobook`
- `resume_music`
- `stop_music`

## Required Response Shape

Required top-level fields:

- `ok`
- `action_id`
- `result`
- `refresh`

## Field Requirements

### `ok`

- boolean

### `action_id`

- echoes the requested action id

### `result`

Required fields:

- `status`

Recommended Alpha fields:

- `message`
- `reply_text`

Rules:

- `message` is the primary UI-facing status text for structured clients
- clients should render `message` as the main action outcome text when present
- `reply_text` is optional carryover from Oracle's conversation system
- `reply_text` may be useful for parity with voice behavior or optional speech/readback
- clients must not depend on `reply_text` as the primary rendering field
- `/api/ui/action` remains a UI action contract, not a full conversation contract

### `refresh`

Required field describing post-action refresh expectations.

Required subfields:

- `refresh_pages`

Rules:

- `refresh_pages` is always an array
- page names should be the UI page identifiers Oracle expects the client to refresh

Examples:

- `["home"]`
- `["home", "house"]`
- `["audio"]`

## Failure Behavior

Failures should be explicit and app-safe.

Recommended failure shape:

- `ok: false`
- `action_id`
- `error`
- `detail`
- `refresh.refresh_pages`

Failure responses should not require the client to parse Oracle internals.

## Alpha Orchestration Boundary

`/api/ui/action` is not a client-side workflow composition surface.

Rules:

- one request represents one explicit UI action
- clients must not sequence multiple requests to recreate Oracle-side orchestration
- if a button or card implies a multi-step behavior, Oracle should own that orchestration internally

## Freshness Expectations

UI clients should refresh affected pages immediately after successful action execution.

The server should help by returning explicit refresh guidance.

## Example Request

```json
{
  "action_id": "living_room_light_on",
  "ui_session_id": "example-session"
}
```

## Example Source-Scoped Request

```json
{
  "action_id": "pause_audiobook",
  "ui_session_id": "example-session",
  "target_source_id": "living_room_voice"
}
```

## Example Success Response

```json
{
  "ok": true,
  "action_id": "living_room_light_on",
  "result": {
    "status": "executed",
    "message": "Living room light turned on.",
    "reply_text": "Turned on the living room light."
  },
  "refresh": {
    "refresh_pages": ["home", "house"]
  }
}
```

## Example Failure Response

```json
{
  "ok": false,
  "action_id": "living_room_light_on",
  "error": "action_failed",
  "detail": "The target did not reach the expected state.",
  "result": {
    "status": "failed"
  },
  "refresh": {
    "refresh_pages": ["home", "house"]
  }
}
```
