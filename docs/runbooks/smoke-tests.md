# Brain Smoke Checks

Use these checks for quick confirmation of the selected canonical Brain. Stop
at the first unexpected result and continue with `incident-triage.md`.

## Installed State First

Run the selected activation's `oracle-admin.py status` with its locked Python
environment. Require healthy status, exact selected identities, and no material
managed drift before interpreting application checks.

## HTTP Check Order

1. `GET /health` must return `200` liveness.
2. `GET /api/admin/health/config` must report the selected canonical
   configuration/secret activation identity.
3. Enabled providers under `/api/admin/health/*` must report their actual
   readiness. A disabled provider is intentionally unavailable and does not
   make the Brain unhealthy.
4. `POST /api/conversation/command` must return the finite public result for the
   provider-free deterministic request declared by the installation.
5. When speech is enabled, `POST /api/speech/stt` and
   `POST /api/speech/tts` must use the selected providers/assets.
6. When durable alerts are enabled, exercise authenticated satellite claim and
   acknowledgement; do not create or consume a household alert merely as a
   liveness probe.

Example deterministic conversation request:

```bash
curl -sS -X POST http://127.0.0.1:8011/api/conversation/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"what time is it","source":"manual-smoke","session_id":"manual-smoke"}'
```

Expected fields are exactly `reply_text`, `session_id`, `source_id`, `status`,
`failure_code`, `trace_id`, and `effects`. The response must not expose raw
route or dispatch envelopes.

Provider-backed facts, Home Assistant, media, or household behavior and other
external provider systems are
additional profile-specific gates. Run them only after base health and only
when the selected configuration declares the provider/capability enabled.
