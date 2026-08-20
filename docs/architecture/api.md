# Oracle Brain API

Oracle exposes an HTTP API for satellites, CLI tools, and future UI clients.

The FastAPI application composition lives in `server/oracle_app/api.py`.
Purpose-owned route registration lives in conversation, speech, UI, admin,
satellite, and integration family modules.

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

### Canonical Conversation

- `POST /api/conversation/route`
- `POST /api/conversation/command`
- `GET /api/conversation/session`
- `GET /api/conversation/command-events`

`POST /api/conversation/route` returns a `RouteResponse`.

`POST /api/conversation/command` returns the finite public
`ConversationResult`; it never exposes raw route or dispatch envelopes. The
old `/api/voice/*` and selected root routes remain temporary Slice 9 consumer
compatibility surfaces.

### Session and Transitional Alert Inspection

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

### Satellite Media and Speech

- `POST /api/satellite/alerts/claim`
- `POST /api/satellite/alerts/{alert_id}/acknowledge`
- `GET /api/satellite/media/audiobooks/{playback_id}/tracks/{track_index}`
- `POST /api/satellite/deferred-resume`
- `POST /api/speech/tts`
- `POST /api/speech/stt`

## Main Command Path

`POST /api/conversation/command` is the canonical composed API path.

At a high level, the current command path is:

1. resolve session
2. normalize text
3. route
4. build and execute dispatch
5. build reply
6. shape the finite public `ConversationResult`

Request example:

```json
{
  "text": "turn on the kitchen lights",
  "source": "kitchen-satellite",
  "session_id": "demo-001"
}
```

Canonical response example:

```json
{
  "reply_text": "Turned on the lights",
  "session_id": "demo-001",
  "source_id": "kitchen-satellite",
  "status": "executed",
  "failure_code": null,
  "trace_id": "example-trace",
  "effects": {
    "follow_up": null,
    "satellite_playback": null,
    "deferred_satellite_playback": null,
    "ui_presentation": null
  }
}
```

Ignored normalized transcripts currently return a `system` route with empty `reply_text`.

## Structural Endpoint Notes

### Alerts

Authenticated satellite claim derives source identity from the Bearer
projection credential, validates that the managed satellite is alert-capable,
and returns bounded leases. Acknowledgement requires the same source and lease.
The root pending route is a temporary Slice 9 compatibility surface.

### Audiobook Stream

The canonical satellite media route exposes prepared audiobook track streaming
through the Brain. The old root stream remains only for Slice 9 migration.

### TTS

`POST /api/speech/tts` exposes speech synthesis through the configured TTS provider and returns audio bytes.

### STT

`POST /api/speech/stt` exposes speech transcription through the configured STT provider and returns transcript text.

## V2 Configuration Reconciliation

The standard Stage 4 installation exposes configuration, secret, activation,
recovery, and rollback operations through the structured host-local Oracle
control plane over its protected Unix-domain socket. The HTTP API does not
provide a network-reachable configuration-maintenance family. Later System
Mode administration requires its own bounded authorization contract rather
than inheriting the host-local control plane's authority.

Canonical serialized request identity remains `source_id`; `source` and UI
`client_id` are bounded real-client migration aliases only.
