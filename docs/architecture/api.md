# Oracle Brain API

Oracle exposes an HTTP API for satellites, CLI tools, and future UI clients.

The FastAPI app surface lives in `server/oracle_app/api.py`.

The API surface is the boundary between thin clients and the brain's internal routing, dispatch, and domain layers.

For the runtime contract between clients and the brain, see `docs/contracts/runtime.md`.

## API Surface

The current API surface is grouped into a small set of endpoint families.

### Health and Config/Reporting

- `GET /health`
- `GET /health/config`
- `GET /health/audiobook`
- `GET /health/calendar`
- `GET /health/home-assistant`
- `GET /health/music`
- `GET /health/news`
- `GET /health/ollama`
- `GET /health/tts`
- `GET /health/stt`
- `GET /api/admin/hooks`

### Routing and Command

- `POST /api/voice/route`
- `POST /command`
- `POST /api/voice/ingest/text`

`POST /api/voice/route` returns a `RouteResponse`.

`POST /command` returns a `CommandResponse`.

### Session and Alert Inspection

- `GET /api/voice/session`
- `GET /alerts/pending`

### Admin Diagnostics

- `GET /api/admin/home-automation/runbooks`
- `GET /api/admin/home-automation/runbooks/{runbook_id}`
- `GET /api/admin/notifications`
- `GET /api/admin/notifications/deliveries`

The home-automation admin endpoints are read-only operator diagnostics for
door-runbook soak and rollback verification. They report sanitized definition
identity, latest canonical state, active/latest run status, and operation
history. They do not submit notifications or query live Home Assistant state.

The notification admin endpoints expose sanitized Apprise health, external
policy/group summaries, exact receipt status counts, and bounded delivery
history. They cannot submit, retry, cancel, or configure notifications.

### Media and Speech

- `GET /audiobooks/stream/{playback_id}/{track_index}`
- `POST /tts`
- `POST /stt`

## Main Command Path

`POST /command` is the main composed API path.

At a high level, the current command path is:

1. resolve session
2. normalize text
3. route
4. build and execute dispatch
5. build reply
6. return `CommandResponse`

Request example:

```json
{
  "text": "turn on the kitchen lights",
  "source": "kitchen-satellite",
  "session_id": "demo-001"
}
```

Response example:

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
  "reply_text": "Turned on the lights"
}
```

Ignored normalized transcripts currently return a `system` route with empty `reply_text`.

## Structural Endpoint Notes

### Alerts

`GET /alerts/pending` exposes due alert inspection and delivery pickup by source.

### Audiobook Stream

`GET /audiobooks/stream/{playback_id}/{track_index}` exposes prepared audiobook track streaming through the brain API surface.

### TTS

`POST /tts` exposes speech synthesis through the configured TTS provider and returns audio bytes.

### STT

`POST /stt` exposes speech transcription through the configured STT provider and returns transcript text.

## V2 Configuration Reconciliation

The standard Stage 4 installation exposes configuration, secret, activation,
recovery, and rollback operations through the structured host-local Oracle
control plane over its protected Unix-domain socket. The HTTP API does not
provide a network-reachable configuration-maintenance family. Later System
Mode administration requires its own bounded authorization contract rather
than inheriting the host-local control plane's authority.

Canonical serialized request identity remains `source_id`; `source` and UI
`client_id` are bounded real-client migration aliases only.
