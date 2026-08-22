# `/api/ui/home` Contract

## Purpose

`GET /api/ui/home` returns the Alpha Home page snapshot.

This is the front-door household page.

## Request

Method:

- `GET`

Query/body:

- no request body

Optional query support may be added later if needed.

## Required Response Shape

The response must be renderable as a page snapshot without conversational interpretation.

Required top-level fields:

- `generated_at`
- `weather`
- `calendar`
- `actions`

## Field Requirements

### `generated_at`

- ISO-8601 timestamp
- represents when Oracle assembled the snapshot

### `weather`

Required fields:

- `summary`

Recommended Alpha fields:

- `temperature_f`
- `freshness_class`

### `calendar`

Required fields:

- `events`

Rules:

- `events` is always an array
- empty is valid

Recommended Alpha event fields:

- `summary`
- `start`
- `end`

### `actions`

Rules:

- always an array
- contains curated action definitions suitable for the Home page

Required action fields:

- `action_id`
- `label`

Recommended optional Alpha fields:

- `type`
- `icon`
- `requires_confirmation`

Optional field guidance:

- `type` may help the client render a simple action style such as `toggle`, `button`, or `scene`, but Alpha must not require a generic action framework
- `icon` may provide a stable presentational hint, but clients must still render cleanly without it
- `requires_confirmation` may be used when the UI should present an extra confirmation step before posting `/api/ui/action`

The Home page action definition must remain lightweight.

The UI must not guess action semantics from `action_id` alone when Oracle can provide an explicit optional hint.

## Optional Fields

Optional Alpha-safe fields may include:

- `refresh_after_seconds`
- `alerts`
- `notice`
- `network_health`

Optional fields must not replace required fields.

### `network_health`

When present, `network_health` should be a lightweight Home-safe summary block.

Recommended Alpha fields:

- `status`
- `label`
- `summary`
- `detail`
- `generated_at`

The Home snapshot must not expose raw provider payloads here.

## Freshness Expectations

Alpha expectation:

- fetch on page load
- refresh by polling
- refresh after successful UI actions

Recommended default polling:

- 60 seconds

## Example

```json
{
  "generated_at": "2026-04-15T13:05:00Z",
  "weather": {
    "summary": "61 degrees and cloudy",
    "temperature_f": 61,
    "freshness_class": "fresh"
  },
  "calendar": {
    "events": [
      {
        "summary": "Doctor appointment",
        "start": "2026-04-15T15:00:00-04:00",
        "end": "2026-04-15T16:00:00-04:00"
      }
    ]
  },
  "actions": [
    {
      "action_id": "reading_room_light_on",
      "label": "Reading Room Light",
      "type": "button",
      "icon": "lightbulb",
      "requires_confirmation": false
    },
    {
      "action_id": "living_room_lights_on",
      "label": "Living Room Lights",
      "type": "button"
    }
  ],
  "refresh_after_seconds": 60
}
```
