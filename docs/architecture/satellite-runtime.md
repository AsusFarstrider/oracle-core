# Satellite Runtime Shape

This document records the current shared satellite runtime structure.

`satellite/pi_wake_satellite.py` is a thin entrypoint into
`satellite/pi_runtime/`. It resolves configuration authority first and then
calls the main runtime entrypoint with one frozen settings object.

## High-Level Runtime Flow

At a high level, the runtime flow is:

- startup and config
- wake loop
- capture and request pipeline
- reply and follow-up handling
- local playback control
- alert polling

## Runtime Package

The main runtime package lives under `satellite/pi_runtime/`.

Current package structure includes:

- `cli.py`: CLI parsing and related entry helpers
- `runtime.py`: main runtime orchestration loop
- `pipeline_runtime.py`: capture and request pipeline orchestration
- `request_runtime.py`: request-path helpers
- `reply_runtime.py`: reply playback and follow-up handling
- `replies.py`: reply construction and fallback helpers
- `oracle_client.py`: Oracle API client helpers
- `local_control.py`: local playback-control helpers
- `alerts_runtime.py`: alert polling helpers
- `session.py`: session helpers
- `models.py`: runtime data models
- `config_runtime.py`: runtime config reporting helpers
- `config_http.py`: local config HTTP surface helpers
- `wake.py`: wake-model and capture helpers
- `wake_loop.py`: wake-loop mechanics
- `wake_tuning.py`: wake-profile selection helpers

## Foreground Audio Handoff

Foreground audio now has one explicit satellite-local coordinator seam instead of separate open-coded paths.

That seam is centered on:

- `models.py`: `ForegroundAudioRequest` and `ForegroundHandoff`
- `local_control.py`: `begin_foreground_handoff(...)` and `finalize_foreground_handoff(...)`

The main entrypoints that use that seam are:

- `pipeline_runtime.py`: spoken reply handoff
- `request_runtime.py`: ack-tone handoff
- `reply_runtime.py`: follow-up cue handoff
- `alerts_runtime.py`: due alert and sleep-expiry handoff

This coordinator is intentionally thin.

It does:

- normalize borrow vs replace decisions
- carry explicit resume outcomes
- emit one canonical handoff decision log per foreground event

It does not:

- replace `playback_authority.py`
- become a second durable ownership layer
- absorb unrelated STT or routing behavior

## Audio Backend Boundary

The `audio/` subpackage is the explicit audio backend boundary for the runtime.

Current audio subpackage structure includes:

- `audio/config.py`
- `audio/playback.py`
- `audio/portaudio.py`
- `audio/alsa_arecord.py`

## Structure Notes

The runtime package separates the always-running wake/runtime loop from the request/reply pipeline and from local playback-control helpers.

The package also separates the audio backend boundary from the rest of the runtime so input and playback implementation details do not have to live in the top-level runtime modules.

## Optional Neighboring Subsystem

`satellite/wake_capture/` is an optional neighboring subsystem for wake-related clip collection.

It sits beside `pi_runtime` rather than inside the main runtime package.

## V2 Configuration Reconciliation

The runtime starts from one immutable projection/local-secret activation,
reports selected/applied projection IDs, and never rereads authored files or
behavioral environment/CLI fields. `satellite_id` and `source_id`
are separate. Current and previous validated pairs provide bounded offline
restart/fallback; credentials are unique and directional.

Projection delivery is initiated by the satellite. After authenticating its
lifecycle identity, it pulls only its desired projection/local-secret pair,
validates and installs the pair atomically, and acknowledges the resulting local
activation. Reconnection naturally retries delivery while offline startup uses
the last valid local pair. The optional configuration HTTP listener is
diagnostic infrastructure; its bind settings are deployment bootstrap metadata,
not projection fields.

`brain_client` is common installation-level projected configuration for every
enabled satellite, including display-only and playback-only installations. It
does not create a third compatibility component. The interaction runtime uses
the same connection when applicable, while the installation pull boundary uses
its directional credential to resolve only that satellite's selected activation.

The pull result is one versioned envelope containing two distinct nested
payloads: the canonical projection JSON and its minimal local-secret snapshot.
One activation ID and one global selection identity bind both, preventing split
fetches from observing different selections. Receiving this envelope is not an
acknowledgement and does not prove that the satellite durably installed or
applied it.

The executable Brain surface is LAN-only
`GET /api/satellite/projection/{satellite_id}` with the directional
`brain_client` credential in the standard Bearer header. The lifecycle ID in
the path selects a candidate record but never authenticates it. This endpoint
is unrelated to the browser UI configuration surface.

`oracle_satellite_projection.py` provides the platform-neutral local adoption
foundation. One installation-level writer validates the canonical envelope,
identity, projection hash, exact runtime report, and secret closure; persists
projection and secret files separately; and atomically selects the Brain-issued
activation ID. The immutable activation is distinct from the local selected
pointer: the pointer owns the latest observed global selection operation and
revision, allowing a later rollback transaction to reselect existing immutable
content. Interaction-runtime and control-service adoption, scheduled fleet
refresh, acknowledgement, bounded retention/fallback, and applied-state
reporting are implemented over this same local selection.

`oracle_satellite_runtime_config.py` is the shared component-adoption boundary.
It loads the atomic local selection once and returns distinct immutable
interaction-runtime or control-service snapshots without retaining the broader
selected secret snapshot. The interaction view resolves only the Brain and
local control-client credentials. The control view resolves only its inbound
credential and optional music-provider credential. Raw values are excluded from
representations. The component block remains the exact immutable projected
mapping; component startup adapters own its interpretation rather than
duplicating the complete server schema on every platform.

`InteractionRuntimeSettings` is the frozen adapter between that interaction
snapshot and the wake/voice process. Its canonical constructor maps
the projected Brain/control edges, audio device union, VAD/follow-up/cue and
playback tuning, wake model/suppression/arbitration, and diagnostic-capture
policy. `InteractionRuntimeHostBootstrap` supplies only the config-report
listener, logging, reply-audio IPC paths, packaged logical-asset paths, default
capture storage, and explicit device-list command mode. The constructor resolves
logical model/cue assets to local paths once, retains credentials with redacted
representations, and does not read argv or environment. The shared interaction
Brain client now attaches the snapshot credential to every HTTP and multipart
request it owns. The snapshot separately retains installation `satellite_id`
and household `source_id`; wake arbitration uses the former. Interaction
entrypoint authority selection uses this seam.
Stage 3 validates this operational credential on the dedicated projection-pull
and wake-capture upload routes. The latter proves one selected satellite and
checks its projected source against the capture sidecar; it does not generalize
authentication across shared interaction ingress. Shared interaction endpoints
accept the header as preparatory client
plumbing but do not derive identity or authorization from it. This deliberately
avoids creating an incomplete browser/UI ingress and stable-source binding
system during satellite cutover.
The standalone sync helper uses the same installation authority resolver as the
interaction runtime. It loads the selected interaction snapshot, rejects
undeclared transport inputs, sends complete
capture pairs to the fixed Brain route, and uses projected cadence and local
retention. It is operational utility code, not a third compatibility component.

Canonical startup constructs settings only from the selected component snapshot
and typed host bootstrap. It rejects behavioral argv and environment inputs
instead of ignoring or blending them, and it does not translate projected
values into another runtime authority.

Canonical Linux unit templates use the projection-sync bootstrap file and
separate optional interaction/control host-bootstrap files. Matching Windows
runtime and control task installers emit the canonical selectors. Their
contract task names match the projection restart wrapper and health checker.
These definitions are installation artifacts only; registering or copying them
does not prove that a runtime has loaded and accepted its selected projection.

The control service has a typed internal seam:
`ControlServiceSettings` is the frozen object consumed by existing control
behavior, and `ControlServiceHostBootstrap` contains only deployment-owned bind,
logging, IPC, packaged-player, and long-form adapter mechanics. The canonical
constructor maps the immutable control snapshot and its
owned credentials, forces retired Plexamp external control off, and fills only
the separate bootstrap-owned mechanics. It never invokes the parser or reads
behavioral environment/argv values. The process entrypoint selects authority
before parsing behavior. Canonical startup derives the native-player selector
and every long-form command from Oracle's installed player modules; arbitrary
arbitrary command hooks and executable selections are rejected rather than
becoming a second configuration authority. Listener, logging, and shared
reply-audio IPC values remain finite host bootstrap.

`SystemVolumeController` now implements the two schema-v1 volume adapters. ALSA
continues to use the configured card/control. Windows lazily uses the pinned
Windows-only `pycaw` dependency to resolve `AudioUtilities.GetSpeakers()` and
operate that device's `EndpointVolume` scalar. Readiness proves Windows platform,
dependency import, default endpoint resolution, and one scalar read before the
control service starts. The controller deliberately has no endpoint ID, device
enumeration, per-session mixer, or fallback behavior.

Projection refresh is performed by the shared one-shot
`oracle_satellite_projection_sync.py` command. It is scheduled by deployment
infrastructure and is not a third compatibility component or a child authority
of either runtime process. For established installations it reads the selected
Brain edge, performs one authenticated pull, installs atomically, and exits.
Exit `0` means no restart latch is pending, exit `3` means projection or local-
secret content requires consumer restart, and exit `1` means failure; output is
sanitized JSON. Selection-only changes and lightweight activations that reuse
both payload generations exit `0` unless an older latch remains pending.

The checked-in Linux systemd wrapper restarts installed
`oracle-satellite-control.service` and `oracle-satellite.service` units in that
order. The Windows wrapper likewise restarts installed
`OracleSurfaceSatelliteControl` and `OracleSurfaceSatelliteRuntime` scheduled
tasks. Missing targets are skipped and neither wrapper restarts the UI. Only
after every installed target accepts restart does the wrapper atomically clear
the latch. Failure or interruption retains it for retry; a crash after restart
but before clear may repeat one harmless restart. This handoff does not claim
consumer health, delivery acknowledgement, or applied-state reporting. The
checked-in deployment schedule is fixed: startup/network availability plus one
run per minute, with overlapping runs suppressed. Linux uses a persistent
systemd timer and Windows a SYSTEM Scheduled Task. Ordinary failure leaves the
selected pair untouched and retries at the next interval. Operators may invoke
the one-shot wrapper manually; V2 adds no push, watcher, resident sync daemon,
jitter, or exponential backoff. Installers default to installed-but-disabled,
and enablement waits until the interaction runtime and control service actually
consume their projected fields. These assets are retained implementation, not a
Stage 4 validated satellite installation profile.

First contact uses one installation-supplied `brain_bootstrap_url` and one
restricted local enrollment-credential file. A one-shot POST authenticates the
declared satellite against the selected bundle and returns the ordinary
immutable projection envelope. Neither value enters effective configuration;
the credential is never accepted through argv or environment. Once the
projection is validated, refresh uses only its projected
`brain_client.base_url` and operational credential, and offline restart uses the
cached pair. With no cached pair, failed first contact leaves the store
unselected and fails closed. V2 has no discovery protocol, ordered fallback,
credential consumption, automatic delivery, or enrollment-state subsystem.

The deployment-owned local-store parent is the permission boundary. POSIX
store directories are created with mode `0700`. On Windows, store directories
inherit the restricted parent ACL because the interactive runtime user and the
SYSTEM refresh task must both read the same selected activation; forcing
Python's protected `0700` Windows ACL can accidentally retain only an elevated
Administrators owner. Windows deployment therefore grants the finite runtime
user and SYSTEM on the parent before first contact. The scheduler installer
accepts only absolute drive-root or UNC paths and remains compatible with the
Windows PowerShell/.NET version installed on the Surface fleet.
