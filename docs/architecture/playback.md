# Oracle Playback Surface

This document describes the current implementation shape of Oracle's playback surface.

For the canonical playback authority contract, see
[playback-authority.md](../contracts/playback-authority.md).

## Current State Summary

The current system is partly aligned with the playback authority model:

- the brain owns routing and command selection for music and audiobooks
- the control service exposes a normalized playback-authority read surface as the common local playback read path
- the satellite control service exposes separate local state surfaces for music, long-form audio, and reply audio
- the Pi runtime treats local interruption and resume as a satellite concern
- the Pi runtime now normalizes reply, cue, and alert borrowing of the speaker through one explicit foreground-audio handoff seam

The current system is also still fragmented:

- local playback truth historically came from separate backend-specific state shapes rather than one authority contract
- wake-time interruption behavior is hard-coded in the Pi runtime rather than declared by backend capability
- reply audio still mirrors file-backed state for transport compatibility, but authority now owns its session lifecycle
- backend state remains implementation-specific behind the normalized control-service playback-authority surface
- audiobook state has stronger session semantics than music, while music relies more on adapter-local behavior

## Audit Notes From Current Code

### Brain

- Music play and transport still operate through satellite control commands and local-state fetches rather than a single playback-authority contract:
  [music.py](../../server/oracle_app/handlers/music.py#L81),
  [control.py](../../server/oracle_app/music_runtime/control.py#L11),
  [transport.py](../../server/oracle_app/music_runtime/transport.py#L6)
- Bare transport routing already consults local audiobook, music, and reply-audio state before choosing a target:
  [route_refinement.py](../../server/oracle_app/route_refinement.py#L94)
- Audiobook playback already has a stronger brain-side playback record with sync or close behavior tied to local long-form status:
  [playback.py](../../server/oracle_app/audiobook_runtime/playback.py#L10)
- the brain now uses authority-normalized playback sessions for transport selection and `what is playing` style reads
- Oracle-native music policy is selected by the brain from host capability, not by host preference
- the old backend-specific GET state endpoints have been removed from the live control-service contract
- Oracle-native music supports single-track playback plus queue-shaped album, artist, and playlist handoff on the same authority model

### Satellite Control Service

- The control service exposes a normalized playback-authority surface, while the local adapter still bridges distinct music, long-form, and reply implementations:
  [server.py](../../satellite/control_service_runtime/server.py#L58)
- Reply audio state is now authority-managed with begin/finalize lifecycle, while retaining a minimal file-backed mirror and stop-request support:
  [reply_audio.py](../../satellite/control_service_runtime/reply_audio.py#L9)
- Local music and long-form audiobook playback sit behind one HTTP control server, but use separate implementations and state models:
  [plexamp_http.py](../../satellite/control_service_runtime/adapters/plexamp_http.py#L26),
  [longform.py](../../satellite/control_service_runtime/longform.py#L33)
- The control service also exposes a normalized playback-authority surface plus authority-owned Oracle interruption and recovery commands:
  [playback_authority.py](../../satellite/control_service_runtime/playback_authority.py),
  [server.py](../../satellite/control_service_runtime/server.py)

### Pi Runtime

- Wake detection asks the satellite-local layer whether any playback is active before choosing the wake profile:
  [runtime.py](../../satellite/pi_runtime/runtime.py#L153)
- Wake-time interruption is currently hard-coded as:
  - pause long-form if active, and escalate to stop if pause does not actually free the output device
  - pause music if active
  - later resume if the user did not issue a transport command, preserving audiobook resumability even after an interrupt-stop
  [local_control.py](../../satellite/pi_runtime/local_control.py#L93)
- Reply playback is still local, but it now registers and finalizes a real authority-owned reply session:
  [reply_runtime.py](../../satellite/pi_runtime/reply_runtime.py#L55),
  [playback.py](../../satellite/pi_runtime/audio/playback.py#L166)
- wake or capture ducking, reply-time pause promotion, and resume-after-Oracle still use the interrupted-playback model, but deferred audiobook start now uses a distinct post-reply start path instead of masquerading as interrupted playback

## Risks In Current Behavior

- wake-time interruption is policy baked into the Pi runtime instead of declared by backend capability
- reply audio remains transitional in transport state, but it is now modeled as a peer playback session in authority state
- the brain resolves transport by probing multiple local endpoints that can drift semantically
- audiobook has a stable notion of resumability; music resumability is more adapter-local and less explicit
- cue/output policy still lives partly in the Pi runtime, which is why handoff cleanup remains active work
- the new foreground-audio coordinator is intentionally thin and must not become a second hidden authority layer
- current product policy allows Oracle to interrupt all active conflicting playback, which means the playback surface must model both:
  - primary output ownership for state reporting
  - broader Oracle-overrides-all interruption authority

## Current Playback Semantics

The current local priority order is:

1. `reply_audio`
2. `audiobook`
3. `music`

Reply interruption behavior currently follows the shared local playback model:

- Oracle reply playback takes priority over user media playback
- local playback may duck when the current backend supports clean ducking for the requested interruption
- otherwise local playback pauses, and escalates to stop if pause does not actually release the device, so reply audio can take the output path
- local recovery after Oracle speech uses the same playback-authority and interrupted-playback model rather than a separate reply-only restore path
- audiobook interruption remains resumable after Oracle speech even when the runtime had to use stop rather than pause to free the device
- newly requested audiobook playback can now be prepared in a paused state and become audible only after Oracle finishes the spoken confirmation
- reply, ack, follow-up cue, due alert audio, and sleep-expiry foreground decisions now enter through the same explicit local handoff path instead of each open-coding their own interruption semantics

## V2 Configuration Reconciliation

Managed host capability is authored in `satellites.yaml` and delivered by
projection. `satellite_id` remains lifecycle identity; playback ownership uses
`source_id`. The Brain and control service consume immutable applied snapshots
and report desired/applied drift without treating local state as configuration.

Compatibility uses two independently versioned local components. The
interaction runtime implements voice, wake, cues, Brain calls, and TTS rendering;
the control service implements native media, normalized transport, playback
authority, and volume backends. Browser UI is not a playback component.

`audio.interaction_output` selects only conversational/TTS and cue rendering.
Native media follows its control-service/player deployment output until a real
cross-platform canonical selector is designed. Foreground handoff continues to
coordinate speaker access without making output-device configuration shared.

V2 projection-backed execution standardizes on the Oracle native player.
Plexamp client control remains part of the historical implementation description
above but is retired for canonical migration. Shared Plex provider access is not
retired; it remains domain-owned and may be projected in minimal form to the
native player.

System mixer control is projected separately from playback transport. Linux may
select one typed ALSA card/control pair; Windows may select the code-owned
default-endpoint controller without configurable commands or raw endpoint IDs.
The latter follows Windows default-output switching and must be advertised by
the target runtime before activation.

V2 intentionally defines no `domains/playback.yaml`. Shared authority and
interruption law remains code-owned, music and audiobook policy remains with
those domains, target/local-adapter settings remain satellite-owned, and active
playback/session truth remains operational state. A dedicated role requires
later evidence of substantial operator-owned cross-domain policy.

Canonical request composition now has a typed playback-target resolver over
the immutable applied fleet. It accepts an explicit
`playback_target_source_id`, or defaults an authenticated playback-capable
satellite request to itself. Ephemeral requests never acquire a target through
their claimed compatibility source. Resolution produces the target source,
satellite lifecycle identity, and `explicit` or
`authenticated_request_source` provenance without changing request identity.

The request schema and music/audiobook dispatch plans carry the target only for
media routes. Canonical handlers derive one immutable media-execution context
from the plan: request source owns conversation, pending clarification, user
context, logs, and memory, while playback target owns transport, volume,
playback state, interruptions, provider playback sessions, and playback-scoped
timers. Explicit targets execute directly; an authenticated satellite that
defaults playback to itself retains the deferred post-reply start path. The
runtime does not rewrite canonical source identity through a compatibility
adapter.
