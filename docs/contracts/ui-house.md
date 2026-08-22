# `/api/ui/house` Contract

## Purpose

`GET /api/ui/house` returns the Alpha House page snapshot.

This is a curated household status and control page.

It is not a generic Home Assistant dashboard.

## Request

Method:

- `GET`

Query/body:

- no request body for Alpha

## Required Response Shape

Required top-level fields:

- `generated_at`
- `temperatures`
- `climate`
- `lights`
- `cameras`
- `actions`

Recommended Alpha fields:

- `home_assistant`
- `notice`
- `refresh_after_seconds`

## Field Requirements

### `temperatures`

Rules:

- always an array
- contains curated household temperature helpers only

Recommended Alpha item fields:

- `entity_id`
- `label`
- `available`
- `value_f`
- `unit`

### `climate`

Rules:

- always an array
- contains curated climate entities only

Recommended Alpha item fields:

- `entity_id`
- `label`
- `available`
- `state`
- `target_temperature_f`
- `current_temperature_f`
- `hvac_action`

### `lights`

Rules:

- always an array
- contains only curated day-to-day light controls relevant to Alpha

Recommended Alpha item fields:

- `entity_id`
- `label`
- `available`
- `state`
- `brightness_pct`

### `cameras`

Rules:

- always an array
- may expose camera inventory and app-safe state summaries
- may expose Oracle-proxied still snapshots when the Home Assistant domain has a curated snapshot source
- Alpha does not promise live camera streams through `/api/ui`
- browser clients must not fetch Home Assistant camera URLs directly

Recommended Alpha item fields:

- `camera_id`
- `entity_id`
- `label`
- `available`
- `state`
- `view_supported`
- `snapshot_supported`
- `snapshot_available`
- `snapshot_url`
- `snapshot_last_modified`
- `snapshot_content_type`
- `snapshot_content_length`

Alpha rule:

- `view_supported` should remain `false` unless Oracle exposes a supported live-view surface
- `snapshot_url`, when present, must point to an Oracle `/api/ui/...` URL, not to Home Assistant
- scheduled Eufy still images are sourced from Home Assistant's `/local/snapshots/*_latest.jpg` files through Oracle; do not use HA `/api/camera_proxy/*` for this scheduled-snapshot contract

### `actions`

Rules:

- always an array
- contains curated House-safe actions only
- action items follow the same lightweight action-definition rules used by `/api/ui/home`

## Failure And Partial Data Behavior

Recommended Alpha behavior:

- prefer a usable curated page snapshot when Oracle can still assemble one
- make unavailable sections explicit with `available: false` item fields or a `home_assistant.detail` message
- do not silently turn this endpoint into a raw provider dump just because some state fetches fail

## Scope Boundary

`/api/ui/house` must not become:

- a generic entity browser
- a full Home Assistant replacement
- a camera streaming surface
- a settings-heavy device management UI

## Freshness Expectations

Alpha expectation:

- fetch on page load
- poll while visible
- refresh after successful House-related actions

Recommended default polling:

- 15 to 30 seconds

## Example

```json
{
  "generated_at": "2026-04-15T13:05:00Z",
  "home_assistant": {
    "ok": true,
    "detail": null
  },
  "temperatures": [
    {
      "entity_id": "sensor.downstairs_temperature",
      "label": "Downstairs Temperature",
      "available": true,
      "value_f": 68.0,
      "unit": "F",
      "state": "68"
    }
  ],
  "climate": [
    {
      "entity_id": "climate.downstairs_thermostat",
      "label": "Downstairs Thermostat",
      "available": true,
      "state": "heat",
      "target_temperature_f": 69,
      "current_temperature_f": 68,
      "hvac_action": "heating"
    }
  ],
  "lights": [
    {
      "entity_id": "light.reading_room",
      "label": "Reading Room Light",
      "available": true,
      "state": "on",
      "brightness_pct": 100
    }
  ],
  "cameras": [
    {
      "camera_id": "doorbell",
      "entity_id": "camera.doorbell",
      "label": "Doorbell",
      "available": true,
      "state": "recording",
      "view_supported": false,
      "snapshot_supported": true,
      "snapshot_available": true,
      "snapshot_url": "/api/ui/house/cameras/doorbell/snapshot?v=Tue%2C%2021%20Apr%202026%2001%3A20%3A04%20GMT",
      "snapshot_last_modified": "Tue, 21 Apr 2026 01:20:04 GMT",
      "snapshot_content_type": "image/jpeg",
      "snapshot_content_length": 102579
    }
  ],
  "actions": [
    {
      "action_id": "reading_room_light_on",
      "label": "Reading Room Light",
      "type": "button",
      "icon": "lightbulb",
      "requires_confirmation": false
    }
  ],
  "notice": "Camera still snapshots are proxied through Oracle from Home Assistant. Live camera streams are not exposed through Alpha /api/ui yet.",
  "refresh_after_seconds": 30
}
```

## Camera Snapshot Endpoint

`GET /api/ui/house/cameras/{camera_id}/snapshot` returns the current curated still snapshot for a House camera.

Rules:

- `camera_id` must be one of the curated House camera ids returned by `GET /api/ui/house`
- the endpoint proxies the scheduled still snapshot file served by Home Assistant under `/local/snapshots/...`
- the endpoint must not proxy HA `/api/camera_proxy/...` for this contract
- responses should use `Cache-Control: no-store` because the HA files have fixed names and are overwritten
- failure should be app-safe, for example `404` for unknown cameras and `502` when HA cannot serve the snapshot
