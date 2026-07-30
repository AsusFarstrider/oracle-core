# Audiobook Capability

The `audiobook` domain is a route target with execution centered in `server/oracle_app/handlers/audiobook.py`.

Audiobook requests are resolved on the brain and long-form playback is executed through the satellite control plane using brain-prepared payloads.

Audiobookshelf progress, item metadata, and playback-session payloads are
normalized by `AudiobookshelfAudiobookBridge` before domain, handler, UI, or
runtime code consumes them. Oracle code uses `library_item_id`,
`provider_session_id`, `current_time_seconds`, `duration_seconds`, normalized
tracks, and normalized chapters. Audiobookshelf camelCase fields and nested
provider response shapes remain bridge-local. Session IDs remain opaque because
the runtime must return them to the provider for sync and close operations.
New runtime and pending-sync state names that opaque value
`provider_session_id`; `abs_session_id` is accepted only when restoring legacy
in-flight state.

## Current Module Split

The current audiobook implementation is split across three layers:

- `server/oracle_app/audiobook.py`
- `server/oracle_app/audiobook_runtime/`
- `server/oracle_app/handlers/audiobook.py`
- `server/oracle_app/provider_bridges/audiobookshelf_audiobook.py`

`server/oracle_app/audiobook.py` remains part of the current domain surface.

`server/oracle_app/audiobook_runtime/` contains the focused runtime support modules for the audiobook domain.

`server/oracle_app/handlers/audiobook.py` contains the dispatch handler and execution entrypoint for the route target.

`server/oracle_app/provider_bridges/audiobookshelf_audiobook.py` owns Audiobookshelf request construction, auth, response parsing, stream fetch mechanics, and progress sync/close payload translation.

## audiobook_runtime Package Structure

The current `audiobook_runtime/` package is grouped around:

- parsing
- matching and series lookup support
- pending clarification
- playback-session support
- policy/support helpers
- provider bridge access

The current package modules are:

- `parsing.py`
- `matching.py`
- `pending.py`
- `playback.py`
- `policy.py`
- `client.py`

## Responsibility Split

At a high level, the current responsibility split is:

- parsing: audiobook and sleep-timer-related intent parsing
- matching and series lookup: search scoring, selection support, and series lookup support
- pending clarification: pending-choice matching and clarification support
- playback-session support: playback-session handling and long-form payload support
- Audiobookshelf bridge access: provider-facing Audiobookshelf mechanics
- handler execution: dispatch-target execution and result assembly

The domain keeps audiobook parsing, matching, clarification, sleep-timer policy, active playback state, and satellite control-plane behavior. The bridge does not decide playback authority or local runtime state.

## Brain Stream Surface

The audiobook domain also includes the brain-side audiobook stream surface used for prepared playback sessions.

## Current Intent Surface

The current intent surface includes:

- `play`
- `resume_current`
- `pause`
- `resume`
- `stop`
- `seek`
- `what_is_playing`
- sleep-timer-related intents
- series lookup

## V2 Configuration Reconciliation

Shared provider connection, library mapping, and policy move to
`domains/audiobooks.yaml`. User-specific logical credential references remain
with the owning household user capability. Provider selection is explicit; an
unselected definition or present token never creates precedence.

The canonical applied-runtime seam joins these ownership surfaces without
merging them. It resolves the one selected shared provider, the domain-owned
sleep-timer and playback-source policy, and only enabled users' explicitly
enabled audiobook account/credential references from `household.yaml`. Unknown
or unconfigured users fail without a global token or hardcoded-person fallback.
Playback sources bind only to applied audiobook-capable Brain-to-control edges,
and raw user/control credentials remain absent from representations.

Long-form manifests require an absolute Brain stream URL that the selected
satellite can fetch. For each audiobook-admitted playback source, the frozen
audiobook runtime therefore retains the authored
`satellites.yaml:brain_client.base_url` as a domain-scoped stream base URL.
This is a narrow second consumer of the endpoint, not a second endpoint
authority: it may only construct Brain-hosted audiobook stream URLs for that
same admitted target. It does not carry the satellite-to-Brain credential,
participate in projection refresh or authentication, or add the endpoint to
the general Brain fleet seam.

Canonical media planning carries `playback_target_source_id` separately from
the established request source. Target resolution validates the applied
audiobook-capable fleet edge and may default only an authenticated playback-
capable satellite to itself. The canonical audiobook handler now keeps user and
pending-clarification context keyed to request source while directing playback,
provider-session state, interruptions, and playback-scoped timers to the
resolved target. Explicit UI targets start directly; an authenticated satellite
defaulting to itself retains deferred audible start. The legacy handler
registry retains its combined source behavior.

Canonical provider and command execution uses one immutable
`CanonicalAudiobookExecution`. It resolves the selected shared provider and the
exact owning user's credential, constructs target-specific stream URLs, and
uses the admitted target's typed Brain-to-control edge. The explicit legacy
composition continues to use the historical getters. Canonical UI/media and
health routes use the same dependency for cover fetches, active-track streams,
and one authenticated ping per configured user account. The combined Audio UI
remains a shared music/audiobook adoption boundary; migrating only its
audiobook half would blend authorities on one read/control surface.
