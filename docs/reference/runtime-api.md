# Oracle Runtime API Reference

This is the current canonical interface summary. The executable OpenAPI surface
and `tests/fixtures/api_family_contract.json` remain the exact route authority.
See [runtime.md](../contracts/runtime.md) for the normative client/runtime
responsibility boundary.

## Brain Families

Only `GET /health` is a permanent root route. All other Brain interfaces live
under one purpose-owned family:

- `/api/conversation`: route, command, session, and interim command events;
- `/api/speech`: STT and TTS modality conversion;
- `/api/ui`: curated browser and household UI data/actions;
- `/api/admin`: health, hooks, diagnostics, configuration identity, caches,
  network, orchestration, and Suggestions operator surfaces;
- `/api/satellite`: projection lifecycle, wake capture, alert delivery,
  deferred playback, local playback, and media proxy mechanics;
- `/api/integrations`: authenticated provider callbacks.

The retired root command/STT/TTS/alert/media routes and `/api/voice/*` are not
registered.

## Conversation

`POST /api/conversation/command` accepts text plus the client session/source
claim fields defined by `CommandRequest`. Ingress authentication and projection
identity, not a claimed name alone, establish the effective source.

It returns only the finite `ConversationResult`:

```json
{
  "reply_text": "Turned on the lights.",
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

The public result never exposes the internal route or dispatch envelopes.
Status is one of `executed`, `pending_confirmation`,
`pending_clarification`, `failed`, or `ignored`.

## Speech

- `POST /api/speech/stt` accepts multipart field `audio` and returns normalized
  transcript text plus provider identity.
- `POST /api/speech/tts` accepts Brain-authored text and returns audio bytes.

Speech conversion owns no routing, session, reply, or playback policy.

## Durable Alert Delivery

`POST /api/satellite/alerts/claim` leases due alerts to the authenticated
satellite source. `POST /api/satellite/alerts/{alert_id}/acknowledge` records
`acknowledged` or `completed` against the active lease. Duplicate acknowledgement
of the same lease is idempotent; source mismatch, lease mismatch, and expiry
fail closed.

## Satellite Media

`GET /api/satellite/media/audiobooks/{playback_id}/tracks/{track_index}` proxies
an already prepared long-form track to a playback target. Canonical audiobook
execution owns provider access and maps provider failures at this HTTP boundary.

## Configuration And Identity

- `GET /api/satellite/projection/{satellite_id}` returns only that authenticated
  satellite's selected projection.
- `POST /api/satellite/enrollment/{satellite_id}` uses the separate selected
  enrollment credential for first installation.
- `POST /api/satellite/wake-captures/{satellite_id}` accepts authenticated,
  bounded metadata plus a mono PCM WAV and stores it beneath the
  deployment-owned archive root.

Canonical request identity serializes as `source_id`; lifecycle operations use
`satellite_id`. Payload claims never override authenticated source authority.

## Satellite Control Plane

The local control service exposes unauthenticated `GET /health`; all other
surfaces require its Bearer API key.

- `GET /playback-authority` reports normalized local playback ownership and
  resumability state.
- `POST /control` accepts an idempotent `command_id`, a finite action, and
  action-specific `args`.

Current actions are pause/resume/stop, next/previous, volume operations,
music playback, long-form playback/control/state, and reply-audio stop. The
control service owns finite local adapter mechanics; Brain/domain policy remains
outside it.
