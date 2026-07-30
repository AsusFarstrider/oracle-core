# Music Capability

The `music` domain is a route target with execution centered in `server/oracle_app/handlers/music.py`.

Music requests are resolved on the brain and local playback is executed through the satellite control plane.

## Current Module Split

The current music implementation is split across three layers:

- `server/oracle_app/music.py`
- `server/oracle_app/music_runtime/`
- `server/oracle_app/handlers/music.py`

`server/oracle_app/music.py` remains part of the current domain surface.

`server/oracle_app/music_runtime/` contains the focused runtime support modules for the music domain.

`server/oracle_app/handlers/music.py` contains the dispatch handler and execution entrypoint for the route target.

## music_runtime Package Structure

The current `music_runtime/` package is grouped around:

- parsing
- matching
- pending clarification
- policy/support helpers
- transport
- control
- client access
- Ollama-assisted support helpers

The current package modules are:

- `parsing.py`
- `matching.py`
- `pending.py`
- `policy.py`
- `transport.py`
- `control.py`
- `client.py`
- `ollama.py`

## Responsibility Split

At a high level, the current responsibility split is:

- parsing: request-to-intent parsing
- matching: candidate scoring, selection support, and result shaping support
- pending clarification: pending-choice matching and clarification support
- transport and control integration: playback transport execution and control-plane access
- Plex and client access: library lookup and client-facing Plex access helpers
- handler execution: dispatch-target execution and result assembly

Music selections cross the domain/runtime boundary with an Oracle-owned
`provider_ref` containing `provider`, `item_id`, `item_path`, and `parent_path`.
Plex identity parsing remains inside `PlexMusicBridge`. Legacy `plex_key`,
`rating_key`, and `parent_key` inputs are accepted by the selection compatibility
adapter for old in-flight choices, then reconstructed at the existing satellite
command transport edge where deployed runtimes still require them.

## Current Intent Surface

The current intent surface includes:

- `play`
- `pause`
- `resume`
- `stop`
- `next`
- `previous`
- `set_volume`
- `volume_up`
- `volume_down`
- `what_is_playing`
- `lookup_album`

## Satellite Playback Integration

The music domain integrates with the satellite control service for local playback commands and playback-state reads.

The current structure also includes playback-authority reads as part of the music transport/control integration surface.

## V2 Configuration Reconciliation

Music provider selection, library mapping, matching/clarification policy, and
Oracle-native playback policy belong to `domains/music.yaml`. Playback targets
are enabled `source_id` references, not satellite lifecycle IDs. Provider
definitions, credentials, or health never create implicit selection.

The canonical applied-runtime seam resolves exactly that selected Plex
definition and its logical credential, retains the bounded matching policy, and
joins each configured playback `source_id` to the applied fleet's validated
music-capable Brain-to-control edge. Unknown sources have no fallback. The seam
does not absorb the Brain-owned global control timeout or satellite-owned native
player, volume, audio, and local-client behavior, and raw credentials remain
absent from representations.

Canonical media planning carries `playback_target_source_id` separately from
the established request source. Target resolution validates the applied
music-capable fleet edge and may default only an authenticated playback-capable
satellite to itself. The canonical music handler now keeps pending
clarification and fallback observations keyed to request source while directing
transport, volume, now-playing reads, interruptions, playback state, and media
commands to the resolved target. Explicit UI targets start directly; an
authenticated satellite defaulting to itself retains deferred audible start.
The legacy handler registry retains its combined source behavior.

Stage 3 now binds provider and control execution through one
`CanonicalMusicExecution` constructed from that applied view. The Plex bridge
accepts a typed provider connection directly; canonical code does not recreate
an untyped music-settings dictionary. Search, native queue construction, transport,
volume, now-playing, reply/long-form arbitration, and audiobook interruption
use the selected provider and exact admitted control edge. Canonical V2 music
targets use the Oracle native player; retired Plexamp selection is not inferred
from legacy satellite flags. Cross-media fallbacks receive the canonical
audiobook execution object and fail closed when that domain is disabled.

The shared Audio UI and music artwork route now select that same execution from
the installed composition. Tokenized artwork URL construction stays inside the
Plex bridge rather than leaking provider credentials into the route. Provider
health/diagnostics and network precondition reads retain their separate owners.
Music health now reports configuration readiness from the selected typed
execution. Generic admin source, log-target, and playback-authority diagnostics
derive fleet membership from the immutable satellite view rather than treating
music policy as fleet inventory; each live authority read uses the enabled media
domain that admits that source. Canonical diagnostics never infer Plexamp
support. Network-control preconditions remain with the network migration because
their provider-session checks participate in recovery policy.
