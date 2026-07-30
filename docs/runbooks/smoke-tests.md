# Brain Smoke Checks

This runbook records the current minimal smoke-check surface for the Oracle brain.

Use [incident-triage.md](incident-triage.md) when a smoke check fails and deeper
diagnosis is needed.

## What This Runbook Is For

Use this runbook for quick operational confirmation that the brain is up, its health surfaces respond, and basic command handling is reachable.

## Stop On Failure

Run the checks in order and stop at the first unexpected failure before moving deeper.

## Recommended Check Order

1. core brain health
2. config-report health
3. enabled-profile health
4. alerts surface when enabled
5. deterministic `/command` check

## Core Brain Health

- `GET /health`
  Expected result: `200 OK`

## Config-Report Health

- `GET /health/config`
  Expected result: `200 OK`

## Enabled-Profile Health

Check only the providers and facilities enabled by canonical configuration.
Common endpoints include `/health/home-assistant`, `/health/audiobook`,
`/health/calendar`, `/health/ollama`, `/health/music`, `/health/news`,
`/health/tts`, and `/health/stt`.

An enabled provider must report its real readiness. A canonically disabled
provider must report intentionally unavailable without making the Brain
unhealthy. The provider-free minimal profile requires no external provider,
LLM, STT, TTS, or audio hardware.

## Alerts Surface

- `GET /alerts/pending?source=<source>` when the selected profile and
  configuration expose alerts
  Expected result: `200 OK`

## Deterministic `/command` Check

- `POST /command`
  Expected result: `200 OK`

Use one already-supported provider-free deterministic request from the
installation's declared minimal validation surface. Do not use a household
device, external provider, or LLM-backed request as the base smoke proof.

Illustrative request shape:

```bash
curl -sS -X POST http://127.0.0.1:8011/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"what time is it","source":"manual"}'
```

Provider-backed checks are additional and run only when their provider and
profile are enabled. For example, facts checks may be appropriate after facts
or summarizer changes, but they are not part of the provider-free base gate:

```bash
curl -sS -X POST http://127.0.0.1:8011/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"How long do sloths live?","source":"manual"}'

curl -sS -X POST http://127.0.0.1:8011/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"Who wrote Frankenstein?","source":"manual"}'

curl -sS -X POST http://127.0.0.1:8011/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"Where is Machu Picchu?","source":"manual"}'

curl -sS -X POST http://127.0.0.1:8011/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"When was the Eiffel Tower built?","source":"manual"}'
```
