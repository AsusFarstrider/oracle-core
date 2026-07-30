# Oracle Runtime Boundary Contract

## Purpose

This document defines the runtime boundary contract between the Oracle brain and Oracle satellites.

It defines:

- the responsibility boundary between the brain and satellites
- the control-plane boundary for local playback systems
- the ownership boundary for reply, fallback, follow-up, and state
- the allowed assumptions for satellites and thin clients

## Contract Rule

The Oracle brain is the normal system of record.

Satellites are thin capture and playback endpoints plus minimal local playback control surfaces.

By default:

- the brain owns interpretation
- the brain owns routing
- the brain owns dispatch
- the brain owns reply shaping
- the brain owns confirmation and clarification state
- satellites capture audio, send requests, receive canonical results, play audio, and expose local playback adapters only where physically necessary

## Scope

This contract covers:

- brain-owned interpretation and reply behavior
- transcription and speech-rendering boundaries
- alert delivery boundaries
- satellite local control-surface boundaries
- reply ownership
- fallback ownership
- follow-up and session behavior
- brain-owned state versus satellite-owned state

## Brain Runtime Contract

### Configuration Snapshot

The Brain adopts one selected immutable configuration/secret activation
generation at startup and constructs a typed immutable `EffectiveConfig`.
Runtime and domain code do not reopen authored YAML, legacy JSON, or environment
configuration after adoption.

Reports distinguish the configuration service's selected generation from the
running process's applied generation. Restart is required by default. Startup
fails closed when the selected generation is invalid or incompatible; automatic
Brain rollback is forbidden.

### Command Interpretation

The brain is the primary interpreted entrypoint for user requests.

The brain:

- normalizes transcript text before routing
- chooses the route on the brain
- constructs dispatch on the brain
- executes capability logic on the brain
- returns canonical spoken output for normal success and failure paths
- returns enough structured result information to distinguish executed, pending, and failed outcomes

`reply_text` is the normal spoken source of truth.

`reply_text` is present for normal success paths and normal failure paths.

`reply_text` may be empty only for intentional silent no-op cases such as ignored junk transcripts.

Satellites do not reconstruct normal speech from `dispatch.result`.

Pending outcomes still produce canonical `reply_text`.

Failure outcomes still return canonical `reply_text` unless the intended result is silence.

If normalization reduces the transcript to nothing useful, the brain may return a silent no-op response.

### Speech To Text Boundary

`/stt` is a transcription surface only.

The brain keeps routing and capability interpretation out of `/stt`.

Satellites and clients may treat `/stt` output as input to the brain command path.

### Text To Speech Boundary

`/tts` is a rendering surface only.

The brain accepts canonical reply text and returns playable audio.

Clients do not use `/tts` as a substitute for missing brain-side reply construction.

### Alerts Boundary

Alert scheduling state is brain-owned.

Due-alert delivery is polling-based.

Satellites render due alerts through one explicit local foreground-audio handoff path.

Spoken alerts may use `/tts`.

Curated notification announcements use a distinct `notification` alert kind.
They borrow the speaker with pause-or-stronger interruption and resume prior
interruptible playback after speech completes. Timer, alarm, reminder, and
sleep-timer replacement semantics remain unchanged.

Timer or alarm cues may use local WAV assets, but they must still enter through the same foreground-audio handoff path as reply and follow-up audio.

Satellites do not reschedule, reinterpret, or persist alert truth on their own.

## Satellite Control-Plane Contract

The satellite control plane is a minimal authenticated local control surface for physically attached playback systems.

The control plane:

- authenticates non-health control requests
- executes explicit local transport and control operations authored by the brain
- treats command ids as idempotency keys for a short cache window
- returns structured action results

The control plane does not:

- own media search
- own ambiguity resolution
- decide what should play

The control plane executes explicit brain-authored commands against local hardware and software.

The Pi runtime may use one explicit local foreground-audio handoff coordinator to normalize reply, cue, and alert borrowing of the speaker.

That coordinator is not a second authority layer:

- it does not own durable playback truth
- it does not choose routing or capability semantics
- it does not replace playback authority
- it only makes the local foreground handoff explicit and auditable once per event

## Client Assumptions Allowed By Contract

Satellites and thin clients may assume:

- `reply_text` from the brain command path is the normal spoken source of truth
- route selection is brain-owned
- capability execution is brain-owned
- confirmation and clarification interpretation are brain-owned
- `session_id` may be reused across short follow-up turns
- polling the alert surface is the supported alert delivery path
- local control-plane endpoints reflect only local satellite playback state

Satellites and thin clients must not assume:

- they may reconstruct normal reply speech from `dispatch.result`
- they may interpret media ambiguity locally
- they may classify commands locally
- they may execute Home Assistant, calendar, news, or Ollama logic locally
- local playback state is the source of truth for global conversation or clarification state

## Fallback Contract

Allowed fallback behavior:

- minimal spoken fallback when `reply_text` is absent
- minimal spoken fallback when `/tts` fails after the client already has text
- minimal transport-error messaging when playback fails locally
- silence for intentional ignored-command cases

Disallowed fallback behavior:

- local ambiguity resolution
- local capability execution
- broad local reply construction for normal success or failure cases
- local cross-domain routing decisions
- growing local business logic because the brain omitted `reply_text`

## Ownership Contract

### Brain-owned state

- routing
- dispatch planning
- confirmations
- clarifications
- conversation and session interpretation
- title and media resolution
- canonical reply shaping
- alert scheduling state
- audiobook playback registry or state known to the brain

### Satellite-owned state

- wake-word detector state
- microphone capture state
- local output and playback device state
- local music adapter state
- local long-form playback state
- local reply-audio state
- local foreground-audio handoff state

### Shared-but-not-equal surfaces

#### `source`

- the brain uses it to scope routing, alerts, and execution context
- authenticated ingress establishes it as request-source identity when calling
  the Brain

#### `satellite_id`

- identifies a managed installation/runtime for projection and lifecycle state
- is not interchangeable with request `source_id`, even when equal by
  deployment convention

#### `session_id`

- the brain owns conversation interpretation keyed by session
- the satellite or client owns reuse of the session identifier across short follow-up turns

## Follow-Up Contract

Satellites may reuse a short-lived `session_id` for same-session follow-ups.

The brain owns interpretation of that follow-up in context.

Satellites may open a short follow-up listening window after `pending_confirmation` or `pending_clarification`.

Satellites must not interpret a follow-up locally beyond sending it back to the brain with the same `session_id`.

## Projection And Offline Restart Contract

The Brain generates a minimal versioned projection for each enabled satellite.
The projection contains only that runtime's required configuration and is paired
atomically with a separate local secret generation. Satellites never receive the
full household bundle.

A satellite must restart while the Brain is unavailable using its last valid
projection pair. It retains current and previous compatible validated pairs and
may perform one bounded local fallback with durable degraded reporting. It does
not regain server-side authority for revoked credentials.

Projection delivery is asynchronous and satellite-pull after Brain activation.
An authenticated satellite may request only its own desired projection pair.
One versioned response carries the canonical projection payload and the separate
minimal local-secret payload, bound by the same selected activation and global
selection identity. The Brain resolves both from one selection snapshot; the
satellite does not fetch them independently. This response shape does not by
itself claim delivery, acknowledgement, application, or enrollment.
The executable pull endpoint is
`GET /api/satellite/projection/{satellite_id}`, authenticated with that
satellite's directional `brain_client` Bearer credential. The path ID selects
the lifecycle record but does not authenticate it. Successful secret-bearing
responses use `Cache-Control: no-store`; authentication and store failures are
sanitized and fail closed.
The satellite validates canonical encoding, lifecycle identity, projection
revision, exact runtime-compatibility evidence, and minimal secret-reference
closure before persistence. It stores projection and secret payloads separately
and atomically selects the Brain-issued activation ID; there is no second local
activation ID. Global selection operation/revision is pointer state so a newer
rollback transaction may reselect an existing immutable activation. Offline
startup loads only that validated selected pair and does not reopen legacy
configuration inputs.
One installation-level one-shot sync command owns pull and installation. It
uses only the selected projection's `brain_client` endpoint and credential,
updates selection without restarting for a selection-only or activation-only
change that reuses both payload generations, and reports a durable
restart-required result when projection or local-secret content changes.
Interaction and control processes are never competing writers. Network failure,
invalid response boundaries, and first-contact absence preserve the selected
pair. Platform scheduler cadence, service/task names, and restart commands are
deployment metadata rather than canonical fields.

V2 deployment uses one fixed refresh policy: run once after startup/network
availability and then once per minute. The schedule is not configurable through
the canonical bundle, projection, ordinary environment, or CLI. Runs do not
overlap. Failure preserves the selected local pair and retries on the next
ordinary interval; V2 adds no push delivery, watcher, resident sync daemon,
jitter, or exponential-backoff subsystem. Manual invocation remains available
for an operator who needs immediate refresh.

In canonical mode, the interaction runtime and control service each load the
selected local activation directly at process startup. Their only configuration
bootstrap inputs are installation identity, local projection-store root, and
runtime-compatibility evidence. Each process retains one immutable component
snapshot for its lifetime, extracts only its own component block, and resolves
only secrets owned by that block; the interaction runtime additionally consumes
the shared `brain_client` edge. Canonical values are never rendered back into
legacy environment variables, generated `.env` files, or process arguments.
Absent, incompatible, or invalid required component configuration fails startup
closed. Installations that do not declare a component do not install or start
that process.

The interaction entrypoint resolves authority before parsing behavior inputs.
Its installation selectors identify the satellite, local projection store, and
runtime-compatibility evidence; they must be supplied together and select only
existing state. Ordinary command argv is empty. Known behavioral argv or
environment values are startup errors.

Each component has one typed internal runtime-settings object constructed only
from the canonical component snapshot plus a separate typed host-local
bootstrap. They are never field-level competing sources.
Host-local bootstrap is limited to deployment-owned mechanics such as listener,
logging, IPC paths, packaged asset/executable resolution, and native media-output
implementation details that the canonical schema deliberately does not own.
The interaction runtime's canonical constructor maps the complete projected
audio, wake, cue, capture, local-control, source, and Brain-client shape into one
frozen settings snapshot. Host bootstrap may resolve code-packaged logical asset
IDs, supply the default local diagnostic-capture directory, and select explicit
diagnostic command mode; it cannot replace projected behavior. The resolved
Brain credential remains part of the immutable component snapshot and is used
by the canonical Brain-client adapter. Projected
wake-capture sync policy likewise must be consumed by the code-owned V2 sync
path. That path sends one completed WAV/metadata pair to the fixed Brain upload
surface, uses the projected Brain endpoint and operational credential, and
applies projected deletion or retention only after durable Brain acceptance.
Failures remain pending. Host, user, SSH key, remote path, and arbitrary
transport inputs are not canonical configuration.
The interaction Brain client sends that credential as a standard Bearer header
on every Brain request it owns: STT, commands, TTS, wake arbitration, command-
event and alert polling, activity reporting, and Brain-routed silent playback
control. No environment or ordinary CLI value may fill in the credential.
Its typed snapshot retains both the managed `satellite_id` and the distinct
household `source_id`. Wake arbitration identifies the satellite installation;
commands, alerts, activity, and request context continue to identify the
configured source.
The dedicated projection-pull and wake-capture upload endpoints
authenticate the `brain_client` credential. Wake-capture authentication proves
the selected satellite and requires metadata `source_id` to equal that selected
projection; it does not create general source-authentication semantics. A Bearer
header on shared STT, command, TTS, wake,
alert, activity, or command-event endpoints does not establish satellite or
source identity and does not authorize a stable-source claim. Extending
enforcement to those shared ingress surfaces requires a separately reviewed
browser/UI and source-binding design; Stage 3 does not infer one from network
location, source parameters, or possession of a header that the endpoint does
not validate.

Canonical control-service volume execution supports exactly the configured
`alsa` mixer or Windows `windows_default_endpoint`. The Windows backend controls
only the current system default render endpoint's scalar master volume; it does
not select devices, enumerate endpoints, manage per-application sessions, or
form a fallback chain. Platform or dependency unavailability is a readiness/
startup error rather than silent fallback.

The Linux deployment wrapper restarts installed
`oracle-satellite-control.service` and `oracle-satellite.service` targets. The
Windows wrapper restarts installed `OracleSurfaceSatelliteControl` and
`OracleSurfaceSatelliteRuntime` scheduled tasks. Missing consumers are skipped
and the UI is excluded. The durable latch is cleared only after all installed
targets accept restart; failure or power loss leaves it for the next run. A
crash after successful restart but before clearing may repeat one harmless
restart. Clearing this retry latch is not an acknowledgement, applied-state
claim, or health proof.
Every enabled satellite's common projected `brain_client` edge supplies the
authoritative Brain URL and directional operational credential used for refresh;
it is not limited to voice-capable installations and is not a third compatibility
component. Fresh enrollment provisions that credential separately before the
first ordinary authenticated pull.
The one-shot first-contact command reads the per-satellite enrollment credential
only from a restricted local file and sends it to the dedicated enrollment
route at the installation-supplied Brain rendezvous URL. The response is the
same immutable selected projection envelope used by ordinary refresh. No
enrollment state is recorded, no credential is consumed or delivered, and
scheduled refresh never receives first-contact inputs.
Desired, delivered, acknowledged, and applied generations are reported
separately; Oracle does not claim distributed atomic activation. The legacy
satellite configuration listener is not a second delivery authority.

Satellite-to-Brain and Brain-to-control-service credentials are unique per
satellite and directionally scoped. Bootstrap identity alone never authenticates
enrollment.
