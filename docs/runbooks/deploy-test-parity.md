# Oracle Deploy Test Parity

This runbook records how to judge parity between repo behavior and deployed behavior.

This document is for parity judgment only. It is not a smoke-test procedure or a deep-diagnosis runbook.

## What This Runbook Is For

Use this runbook when repo behavior and deployed behavior appear to disagree and you need to decide whether the mismatch is expected, environment-shaped, or a real parity failure.

## Base Rule

The repo is the source of truth for expected behavior.

Specifically:

- the repo workspace is the canonical full test baseline
- deployed hosts are deploy-only environments by default
- host-local repo-test parity is opt-in, not assumed

## Parity-Check Flow

1. verify the expected repo behavior
2. verify the deployed behavior on the relevant host
3. compare the two
4. classify the mismatch before treating it as a regression

Stop and classify before escalating any repo-vs-deploy difference into a product failure.

## Environment Types

### Repo Environment

- intended to run the tracked repo test surface
- used for code-change validation and behavior checks
- uses `python -m pytest` from the repository root as the sole canonical Python
  validation command
- installs test-only dependencies from `requirements-dev.txt`, separately from
  the Brain runtime dependencies in `server/requirements.txt`

### Deploy-Only Host

- intended to run Oracle services, not the full repo test surface
- used for service health, targeted command-path verification, and deploy-shaped checks

## Minimum Verification Surface

### Brain Host

- brain service status
- `GET /health`
- `GET /health/config`
- targeted live `/command` checks for the task-relevant path
- dependency health endpoints when the task touches those dependencies

### Satellite Host

- satellite service status
- satellite `GET /health/config`
- control-service `GET /health`
- targeted source-specific command-path checks when the task requires them

## Allowed Mismatch Categories

The following mismatch categories are allowed without automatically treating them as product regressions:

### 1. Host bootstrap prerequisites missing

- host-local virtualenv absent
- optional local tooling not installed
- non-production test prerequisites missing on the host

### 2. Hardware or audio-device dependent behavior

- capture device availability
- ALSA or PortAudio differences
- local speaker or microphone contention

### 3. Local secret or env absence

- local API keys not present
- host-specific env files missing
- dependency URLs unavailable because host-local config is incomplete

### 4. Deploy-only service wiring differences

- systemd env-file wiring differences
- tracked host override differences
- host-specific service names or runtime paths

### 5. External dependency availability differences

- Plex, Audiobookshelf, Ollama, or Home Assistant unreachable from that host
- local network dependency outages
- published feed availability differences

## Disallowed Mismatch Categories

The following mismatch categories must be treated as real parity failures unless they are intentional and explicitly documented:

### 1. Different routing decisions for the same tracked code without an intentional environment reason

- one environment routes a request to `music` while another routes the same request to `ollama`
- probable audiobook title routing differs for the same repo commit

### 2. Different clarification or rescue-policy outcomes for the same tracked code without an intentional environment reason

- one environment narrows or rescues while another hard-fails for the same modeled request
- deterministic clarification ordering differs without a documented reason

### 3. Host-local runtime trees drifting from the tracked repo without explicit acknowledgement

- private or stale code on the host
- partially synced runtime trees
- deployed services running code that no longer matches the tracked workspace

### 4. Deploy verification passing only because the host is using stale or private code instead of the tracked runtime

- a host appears healthy only because it is not actually running the current tracked code
- command behavior matches expectations for the wrong reason because the service is not using the repo state being reviewed

## How To Interpret Disagreement

When repo and deployed behavior disagree:

1. assume the repo is correct first
2. check whether the mismatch falls into an allowed mismatch category
3. if not, treat it as parity drift or product drift
4. only update the repo baseline after confirming that the repo is no longer modeling real Oracle behavior closely enough

## Related Docs

- [test-taxonomy.md](../architecture/test-taxonomy.md) for the repo-side test surface
- [incident-triage.md](incident-triage.md) for live-system diagnosis after a parity failure is identified
