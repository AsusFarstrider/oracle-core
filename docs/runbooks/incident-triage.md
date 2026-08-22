# Oracle Incident Triage

This runbook is the primary operator triage path for Oracle incidents.

## What This Runbook Is For

Use this runbook when Oracle is up but misbehaving, partially degraded, or failing somewhere along the request, playback, or dependency path.

## Triage Order

Run the stages in this order and stop on the first clear error before moving deeper:

1. installed activation status and drift
2. runtime configuration identity
3. brain `/api/conversation/command` trace
4. satellite trace
5. control-service trace
6. dependency health
7. local playback and wake-capture behavior

## Runtime Config Surfaces

Current config-report endpoints:

- brain: `GET /api/admin/health/config`
- Pi satellite: `GET /health/config` on the satellite config-report port
- control service: `GET /health/config`

Format support:

- JSON by default
- plain text with `?format=text`
- plain text with `Accept: text/plain`

What to look for:

- exact applied configuration/secret generation identity
- installed status health and managed-drift findings
- provider/model asset identity and service-account readability
- audio-input open failures on Pi satellites

## Brain Trace Stage

If `/api/conversation/command` behavior is wrong, inspect the brain request trace for:

- `command_received`
- `route_chosen`
- `dispatch_planned`
- `dispatch_executed`
- `reply_built`
- `failure_path_selected` when present

Stop on the first clear failure in this path before moving to lower layers.

What to look for:

- `failure_class`
- `owning_component`

## Satellite Trace Stage

If wake activates but the request fails afterward, inspect the satellite trace for:

- `transcript_obtained`
- `command_response_received`
- `stt_failed`
- `brain_request_failed`
- `brain_dispatch_failed`
- `tts_failed`

Stop on the first clear failure in this path before moving to lower layers.

## Control-Service Trace Stage

Inspect the control-service trace for:

- `control_command_received`
- `control_command_sent`
- `control_command_result`
- `playback_authority_degraded` when present
- `playback_authority_interrupt_started`
- `playback_authority_interrupt_finished`
- `playback_authority_resume_lookup`
- `playback_authority_resume_skipped`
- `playback_authority_resume_decision`
- `foreground_handoff`

Stop on the first clear failure in this path before moving to dependency or local playback checks.

What to look for:

- `failure_class`
- `owning_component`

## Dependency Health Stage

Brain dependency probes:

- `/api/admin/health/home-assistant`
- `/api/admin/health/music`
- `/api/admin/health/audiobook`
- `/api/admin/health/calendar`
- `/api/admin/health/news`
- `/api/admin/health/ollama`
- `/api/admin/health/tts`
- `/api/admin/health/stt`

Use these after config and request-path checks, not before.

## Fast Failure Map

- wake word not triggering:
  Pi service status -> Pi `/health/config` -> audio input errors -> wake/capture path
- heard wake, then local failure tone:
  `stt_failed` -> `brain_request_failed` -> `tts_failed`
- heard spoken failure from Oracle:
  brain trace -> `failure_class` / `owning_component` -> target dependency health
- music or audiobook transport failed:
  control-service `/health` -> control-service `/health/config` -> control trace -> playback authority trace when present

## Failure Class Map

- `router_failure`:
  inspect brain fallback-router events first
- `contract_failure`:
  inspect the seam entrypoint that produced the failure
- `transport_failure`:
  inspect network reachability or upstream HTTP failures before domain logic
- `control_service_failure`:
  inspect control-service command/result path first
- `authority_mismatch`:
  inspect playback-authority and foreground-handoff events first
- `domain_failure`:
  inspect the selected handler or dependency after the request path is confirmed
- `stt_failure` or `tts_failure`:
  inspect speech stack health and local runtime logs first

## Signal To Domain Mapping

- wrong identity or unhealthy `/api/admin/health/config`:
  config surface
- bad route with sane dispatch:
  routing surface
- good route with failed dispatch:
  `failure_class` / `owning_component` decide whether to inspect handler, transport, control service, or authority first
- no incoming control trace:
  brain-to-satellite transport path
- incoming control trace with failed result:
  local playback or adapter surface
- audio input open failure:
  local capture or device surface

## Notes

- configuration-health output is diagnostic and sanitized; it never contains
  secret values.
- the selected activation's `oracle-admin.py status` is the host-level doctor;
  the private repository is not an operational fallback.
