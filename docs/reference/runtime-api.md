# Oracle Runtime API Reference

This document is a reference-only summary of the runtime interfaces used between the Oracle brain, satellites, and the satellite control plane.

For the runtime boundary contract, see [runtime.md](../contracts/runtime.md).

## Brain Interfaces

### `POST /command`

Purpose:

- primary brain entrypoint for interpreted user requests

Request shape:

```json
{
  "text": "turn on the kitchen lights",
  "source": "kitchen-satellite",
  "session_id": "demo-001"
}
```

Required request fields:

- `text`

Optional but commonly expected request fields:

- `source`
- `session_id`

Response shape:

```json
{
  "route": {
    "target": "home_assistant",
    "confidence": 0.82,
    "reason": "Matched home automation keyword: turn on",
    "normalized_text": "turn on the kitchen lights"
  },
  "dispatch": {
    "target": "home_assistant",
    "hook": "home_assistant.execute",
    "payload": {
      "text": "turn on the kitchen lights",
      "source": "kitchen-satellite",
      "session_id": "demo-001"
    },
    "status": "executed",
    "result": {}
  },
  "reply_text": "Turned on the lights."
}
```

Required top-level fields:

- `route`
- `dispatch`
- `reply_text`

Required `route` fields:

- `target`
- `confidence`
- `reason`
- `normalized_text`

Required `dispatch` fields:

- `target`
- `hook`
- `payload`
- `status`

Optional `dispatch` fields:

- `result`

Allowed `dispatch.status` values:

- `planned`
- `pending_integration`
- `pending_confirmation`
- `pending_clarification`
- `executed`
- `failed`

Notes:

- `reply_text` is the normal spoken source of truth
- pending outcomes still return canonical `reply_text`
- failure outcomes still return canonical `reply_text` unless the intended result is silence
- intentional silent ignore may return empty `reply_text`

### `POST /stt`

Purpose:

- transcribe uploaded audio into normalized text input for the brain

Request detail:

- multipart upload field name: `audio`

Response shape:

```json
{
  "text": "turn on the lights in the mancave",
  "provider": "fast-whisper"
}
```

Required response fields:

- `text`
- `provider`

### `POST /tts`

Purpose:

- synthesize brain-authored spoken reply text into playable audio

Request shape:

```json
{
  "text": "Hello, I am Oracle."
}
```

Response detail:

- raw audio bytes
- media type set in the response
- provider may be exposed in response headers

### `POST /api/satellite/alerts/claim`

Purpose:

- lease due alerts to an authenticated managed satellite without completing them

Request detail:

- Bearer projection credential
- JSON `source_id`, `lease_seconds`, and `limit`; credential identity is authoritative

Response shape:

```json
{
  "alerts": [
    {
      "alert_id": "abc123",
      "lease_id": "lease-abc123",
      "lease_expires_at": "2026-03-17T21:00:30Z",
      "kind": "timer",
      "message": "Your pasta timer is done.",
      "due_at": "2026-03-17T21:00:00Z",
      "source_id": "kitchen-satellite",
      "session_id": "kitchen-session",
      "metadata": {}
    }
  ]
}
```

Required top-level field:

- `alerts`

Required alert fields:

- `alert_id`
- `kind`
- `message`
- `due_at`

Optional alert fields:

- `session_id`
- `metadata`

### `POST /api/satellite/alerts/{alert_id}/acknowledge`

The authenticated source supplies the active `lease_id` and an
`acknowledged` or `completed` status. Duplicate acknowledgement with the same
lease is idempotent. A source mismatch, different lease, or expired lease is
rejected. The former `GET /alerts/pending` surface remains only for bounded
Slice 9 client migration.

## Satellite Control Plane

Purpose:

- minimal authenticated local control surface for physically attached playback systems

### Auth

Control-plane auth boundary:

- `GET /health` is unauthenticated
- all other control-plane endpoints require `Authorization: Bearer <api-key>`

### `GET /health`

Purpose:

- liveness plus adapter-level local health summary

Response shape:

```json
{
  "ok": true,
  "service": "oracle-satellite-control",
  "adapter": {}
}
```

Required fields:

- `ok`
- `service`
- `adapter`

### `GET /playback-authority`

Purpose:

- expose the satellite-local playback authority model used for routing, interruption, and recovery

Response shape:

- `ok`
- `sessions`
- `active_sessions`
- `output_owner`
- optional normalized session metadata such as `backend_type`, `media_kind`, `state`, `title`, `artist_or_author`, `queue_count`, and resumability fields

### `POST /control`

Purpose:

- execute an explicit local playback control command authored by the brain

Request shape:

```json
{
  "command_id": "abc123",
  "action": "pause",
  "args": {}
}
```

Required request fields:

- `command_id`
- `action`

Optional request field:

- `args`

Response shape:

```json
{
  "ok": true,
  "command_id": "abc123",
  "state": "accepted"
}
```

Required response fields:

- `ok`
- `command_id`
- `state`

Optional response fields:

- `detail`
- action-specific payload fields

Current explicit actions:

- `pause`
- `resume`
- `stop`
- `next`
- `previous`
- `volume_up`
- `volume_down`
- `set_volume`
- `play_media`
- `play_longform_audio`
- `pause_longform_audio`
- `resume_longform_audio`
- `stop_longform_audio`
- `seek_longform_audio`
- `get_longform_state`
- `stop_reply_audio`

## V2 Configuration And Identity Surfaces

The canonical runtime API direction adds authenticated operator configuration
service operations and satellite projection delivery/acknowledgement surfaces.
Exact endpoints are fixed with the executable Stage 3 API schema.

Projection delivery uses authenticated satellite pull. A satellite requests
only the desired pair for its own `satellite_id`; the Brain does not discover or
store a satellite configuration-listener URL as canonical configuration. The
legacy port `8022` listener remains a bounded compatibility/diagnostic surface
until the pull/install/acknowledgement path meets its removal gate.

### `GET /api/satellite/projection/{satellite_id}`

The caller supplies its directional `brain_client` value as
`Authorization: Bearer <credential>`. The path selects one lifecycle identity;
the credential must prove that same selected satellite. Success returns the
`oracle-satellite-projection-pull-v1` canonical JSON envelope with
`Cache-Control: no-store`. Missing, malformed, unknown, disabled, unselected,
and incorrect authentication all use the same generic `401` response. An
unavailable or inconsistent canonical store returns a generic `503` response.
Neither response exposes secret values or internal failure detail.

This LAN-only endpoint is separate from browser UI
`GET /api/satellites/config`. A successful response does not record delivery,
acknowledgement, application, or enrollment.

### `POST /api/satellite/enrollment/{satellite_id}`

Fresh installation supplies its selected per-satellite enrollment value as
`Authorization: Bearer <credential>` to the installation's single
`brain_bootstrap_url`. Success returns the same
`oracle-satellite-projection-pull-v1` envelope and `Cache-Control: no-store`
boundary as ordinary pull. Authentication is distinct: the enrollment value
cannot authenticate ordinary refresh, and the projected operational value
cannot authenticate enrollment. The route records no enrollment state, consumes
no credential, and does not alter configuration selection. Missing, malformed,
unknown, disabled, unselected, or incorrect authentication is one generic
enrollment `401`; canonical-store failure is the same generic `503` used by
ordinary projection delivery.

### `POST /api/satellite/wake-captures/{satellite_id}`

The canonical wake-capture helper supplies the selected satellite's
`brain_client` value as `Authorization: Bearer <credential>` and one multipart
request containing `metadata` JSON plus one `audio` WAV. The Brain proves the
same selected satellite, requires the sidecar `source_id` to match its projected
source, validates bounded metadata and mono PCM WAV structure, and ignores the
client filename. Success means the WAV and completion sidecar are durable under
the deployment-owned archive root and returns a content-derived `capture_id`
with `Cache-Control: no-store`. Identical retry is successful. Invalid input is
a generic `400`; authentication failure is a generic `401`; unavailable or
ambiguous archive/store state is a generic `503`.

The endpoint accepts no remote path, transport selector, chunk, session,
delivery state, or separate upload credential. It is LAN-only and must not be
routed through the public browser gateway.

Canonical request identity serializes as `source_id`; lifecycle operations use
`satellite_id`. Satellite UI configuration returns both. Existing `source` and
UI `client_id` fields remain bounded compatibility aliases for current clients
and do not establish trust. Configuration reports expose selected/applied
generation IDs and no raw secret values.
