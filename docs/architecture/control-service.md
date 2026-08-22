# Satellite Control Service

This document records the current satellite control-service structure.

The control service is the satellite-side control plane for media transport, long-form playback control, playback-authority state, and reply-audio interruption coordination.

## Structure

The current control-service surface is split across:

- `satellite/control_service.py` as the thin CLI entrypoint and server bootstrap
- `satellite/control_service_runtime/server.py` for the HTTP server, request handling, authorization, command dispatch, health surfaces, and command-result caching
- `satellite/control_service_runtime/adapters/` for backend-specific transport adapters
- `satellite/control_service_runtime/playback_authority.py` for active-session inspection and interrupt/resume coordination
- `satellite/control_service_runtime/longform.py` for long-form shell-controller support and long-form command/result shapes
- `satellite/control_service_runtime/reply_audio.py` for reply-audio state and stop signaling
- supporting runtime modules including `auth.py`, `cache.py`, `config_runtime.py`, `native_music.py`, and `system_volume.py`

## Runtime Shape

The current runtime is built around:

- a threaded HTTP server
- API-key authorization for control surfaces
- a single configured adapter implementation for playback-control execution
- command-result caching keyed by command id
- health and config-reporting surfaces alongside the authenticated control surfaces

## Current Control Surface

The current HTTP surface includes:

- `GET /health`
- `GET /health/config`
- `GET /playback-authority`
- `POST /control`

`POST /control` is the main command surface for transport control, media start, long-form playback control, reply-audio interruption, and Oracle interrupt/resume coordination.

## Adapter Boundary

The adapter layer is the execution boundary between the HTTP control plane and local playback backends.

The current adapter implementations are:

- a Plexamp HTTP adapter
- a shell-command adapter

## Playback Authority

The playback-authority subsystem builds a unified view of active playback sessions across:

- reply audio
- Oracle audiobook playback
- music playback backends

It also provides the current interrupt-for-Oracle and resume-after-Oracle coordination surface used when reply playback needs temporary output ownership.

## Long-Form Support

The control service includes a dedicated long-form support layer for:

- starting audiobook playback from a brain-prepared manifest
- pause, resume, stop, and seek control
- reading current long-form playback state

Passive long-form state reads are now short-cached inside the control service so repeated `GET /playback-authority` reads do not always spawn a fresh long-form state subprocess.

That cache is intentionally narrow:

- it applies to passive long-form state reads only
- long-form control actions invalidate it before acting
- command-confirmation polling bypasses it and still reads fresh state

## Boundary

The brain resolves music and audiobook requests, while the satellite control service executes local playback-control actions and reports current playback-authority state.

## V2 Configuration Reconciliation

The control service starts from one immutable satellite projection/local-secret
activation and never resolves behavior from environment or CLI fields after
canonical cutover. Brain-to-control and satellite-to-Brain credentials are
unique and directionally scoped. Health reports projection and applied
generation IDs without returning secrets.

The canonical V2 control service accepts only the Oracle native playback
adapter. Legacy Plexamp HTTP/client control and shell-command adapters are
retired migration behavior. Plex library/provider access required by native
playback remains independently owned by the music domain.
