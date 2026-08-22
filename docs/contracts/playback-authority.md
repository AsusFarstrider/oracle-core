# Oracle Playback Authority

## Purpose

This document defines Oracle's playback authority contract.

It defines:

- the playback ownership boundary between the brain and the satellite runtime
- the required playback authority surface
- the required shared playback session model
- the arbitration rules for conflicting playback
- Oracle's override authority for listening and speaking
- the host capability vocabulary for playback backends

## Ownership Rule

### Brain Owns

- intent resolution
- route selection
- backend-selection policy
- user-facing reply shaping
- source-to-host targeting
- host capability interpretation
- requests for playback control

The brain may cache local playback state for one request flow, but it must not become the durable owner of satellite-local playback truth.

Request-source identity and playback destination are separate concepts. A
canonical media request may carry `playback_target_source_id`; that field
selects an enabled playback-capable Oracle source and never authenticates the
caller or supplies user/room association. An authenticated playback-capable
satellite may default its target to its own source when the field is absent.
An ephemeral or non-playback request source requires an explicit target for
media execution. Invalid explicit targets fail and never fall back to the
request source.

Conversation sessions, contextual identity, request logs, and memory remain
owned by the established request source. Playback commands and local playback
truth use the resolved playback target. This separation does not introduce
roles, permissions, or a generalized target-authorization system.

Audible-start coordination follows the resolved target provenance. A media
request explicitly targeted by a UI or other client may begin directly on the
selected target. A request whose target defaults to the same authenticated
satellite defers newly selected media until that satellite finishes the
conversational reply. Target selection does not move the conversation session
or pending clarification to the playback target.

### Satellite Runtime Owns

- active local playback truth
- current backend in use
- current playback session state
- interruption outcome
- resumability
- local output-device contention outcome
- local recovery outcome

The satellite runtime is the authority for what actually happened on the host.

Playback authority remains the durable center of gravity for local playback ownership truth even when the Pi runtime uses a foreground-audio handoff coordinator.

## Playback Authority Contract

Oracle uses one satellite-local playback authority surface.

That authority surface must:

- report the current active playback sessions
- report which session, if any, currently owns the output path
- execute interruption according to declared backend capability
- report the actual interruption result
- execute resume or recovery for sessions Oracle interrupted
- report whether a session is resumable
- provide stable control actions for the active session or a targeted session

Foreground-audio borrowing and replacement may be normalized by the Pi runtime, but they must still be anchored to authority-owned playback truth and actual interruption results.

## Minimum Shared Playback Session Model

Every local playback backend must report a minimum common shape.

Required fields:

- `session_id`
- `backend_type`
- `media_kind`
- `state`
- `resumable`
- `owner_priority`
- `can_duck`
- `can_pause`
- `can_stop`
- `can_resume`
- `title` when meaningful
- `artist_or_author` when meaningful
- `position_seconds` when meaningful
- `duration_seconds` when meaningful
- `updated_at`

Vocabulary:

- `backend_type`: `reply_audio`, `plexamp_external`, `oracle_audiobook`, `oracle_native_music`
- `media_kind`: `reply`, `music`, `audiobook`
- `state`: `playing`, `paused`, `stopped`, `starting`, `stopping`, `unknown`

## Arbitration Rule

The local playback priority order is:

1. `reply_audio`
2. `audiobook`
3. `music`

The interruption decision order is:

1. duck if the current backend supports clean ducking for the requested interruption
2. otherwise pause if supported
3. otherwise stop if supported
4. otherwise report `interruption_failed`

The runtime must report the actual result, not the desired result.

If a pause path claims success but does not actually release the shared output path, the runtime must treat that as a failed interruption attempt and continue to the next stronger action.

## Oracle Audio Boss Rule

Oracle may interrupt any active local playback required for wake capture or spoken reply.

Interruption is not limited to one currently preferred output owner.

If multiple playback sessions are active or contending, Oracle may interrupt all conflicting sessions.

The authority layer may report one primary output owner for status purposes, but ownership reporting does not limit Oracle's interruption authority.

## Foreground Borrowing Rule

The runtime may treat reply, acknowledgement cues, follow-up cues, due alerts, and sleep-expiry decisions as foreground-audio events.

For those events, the runtime must make one explicit handoff decision per event:

- what is asking for the speaker
- which sessions were interrupted
- whether the foreground event is borrowing the speaker or replacing the prior foreground use
- whether interrupted media should resume, remain stopped, or be replaced by a deferred new owner

That handoff model must not become a second playback authority.

Durable playback ownership truth still belongs to the authority layer and backend session state.

## Host Capability And Config Contract

Host capability and runtime health are separate concepts.

The playback capability vocabulary includes:

- `supports_oracle_native_music`
- `supports_oracle_audiobook`
- interaction-runtime conversational output adapters
- control-service volume backends

`supports_*` means the host is configured and intended to support that backend class.

Runtime health remains a separate concern exposed through health or authority state.

Host configuration describes capability, not preference.

## Backend-Specific Rules

### Reply Audio

Reply audio participates in the shared playback authority model.

Reply playback reports whether it was:

- started successfully
- interrupted by wake
- interrupted by explicit stop request
- blocked by device contention

Oracle reply playback must either obtain the local output path or report a real playback failure.

The runtime must not mark a failed reply playback as completed.

### Audiobook

Audiobook local state is exposed through the shared authority shape.

Audiobook manifest and progress semantics remain domain-specific.

When Oracle must speak during audiobook playback, the runtime may escalate from pause to stop if stop is required to free the device.

That interruption must still preserve resumability so the audiobook can resume after Oracle finishes speaking.

### Legacy Plexamp External Music

Plexamp client control is V1 compatibility behavior and is not a canonical V2
backend. Shared Plex provider access may remain for Oracle-native music, but a
compatibility report cannot advertise Plexamp as satisfying native-player
requirements.

## V2 Configuration Reconciliation

Conversational/TTS and cue output selection belongs to the interaction runtime.
Native-media output remains a control-service/player deployment concern in V2;
there is no ratified shared canonical output-device selector. Playback authority
and foreground handoff still arbitrate the local speaker without owning or
merging those separate configuration concerns.

Desired host capability lives in the managed satellite record and reaches the
runtime through its immutable projection. `satellite_id` identifies the managed
runtime; `source_id` may identify either the established request source or the
separately resolved playback target depending on the typed field. Playback APIs
and policy must not infer one from the other except for the authenticated
same-satellite default, and neither may be assumed to equal the lifecycle ID.
