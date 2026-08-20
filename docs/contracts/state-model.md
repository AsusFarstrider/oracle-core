# Oracle State Model

## Purpose

This document defines Oracle's state ownership, scope, lifecycle, and persistence contract.

It defines the ownership rules that govern Oracle state.

Related contract:

- [memory.md](memory.md) defines Oracle Memory ownership for durable structured
  operational storage.

Current implementation reference:

- [state-ownership-ledger.md](../reference/state-ownership-ledger.md) and its
  machine-readable companion enumerate the source-reconciled mutable stores,
  caches, mirrors, and recovery markers governed by this contract.

## State Rules

- Brain-owned state must remain brain-owned unless a later contract change explicitly says otherwise.
- Satellite-owned state must remain local to the satellite unless the runtime contract explicitly promotes it to brain-owned state.
- State reads must return normalized snapshots where practical, not live mutable internals.
- Mutation ownership must be explicit. Every state category must have clear create, update, and clear paths.
- Global cross-user or cross-satellite confirmation or clarification state is forbidden.
- Hidden alias state for the same concept in multiple modules is forbidden.

Audio/runtime rules:

- the brain owns routing, intent resolution, backend-selection policy, and user-facing reply shaping
- the satellite runtime owns local playback state, active playback session truth, interruption outcome, and resumability status
- the brain may query, cache, and report local playback state, but must not own drifting shadow copies of satellite-local playback truth
- playback backend vocabulary:
  - `oracle_native_music`
  - `oracle_audiobook`
  - `plexamp_external`
  - `reply_audio`
- playback state vocabulary:
  - `playing`
  - `paused`
  - `stopped`
  - `starting`
  - `stopping`
  - `unknown`

## Inventory

| State category | Current location | Current access pattern | Verified owner | Required scope | Lifecycle class | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Pending confirmation | `server/oracle_app/state.py` | Helper-based create/load/clear keyed by `source + session_id` | Brain confirmation/system layer | `source + session_id` | Ephemeral | Missing context fails explicitly instead of returning an unresumable pending flow. |
| Pending music clarification | `server/oracle_app/state.py` | Helper-based create/load/clear keyed by `source + session_id`; read by routing and music handler | Brain music clarification layer | `source + session_id` | Ephemeral | Missing context fails explicitly instead of silently dropping pending state. |
| Pending audiobook clarification | `server/oracle_app/state.py` | Helper-based create/load/clear keyed by `source + session_id`; read by routing and audiobook handler | Brain audiobook clarification layer | `source + session_id` | Ephemeral | Missing context fails explicitly instead of silently dropping pending state. |
| Effective interaction session | `server/oracle_app/session_state.py` | Typed create/refresh/inspect/reset lifecycle under the shared interaction synchronization boundary | Brain conversation/session layer | `source + effective_session_id` | Ephemeral; 90-second inactivity timeout | Sole identity and lifecycle authority for pending, active, user, history, dispatch, Home Assistant linkage, audit, and interim-event compartments. |
| Conversation context | `server/oracle_app/conversation.py` | Separate history/dispatch compartment with snapshot reads; synchronized and cleared by the effective-session lifecycle | Brain conversation/session layer | `source + effective_session_id` | Ephemeral; owning session lifecycle | Includes six-turn history plus dispatch context. It has no independent TTL or identity authority. |
| Home Assistant conversation linkage | `server/oracle_app/conversation.py` | Stored and loaded through dedicated compartment helpers under the interaction synchronization boundary | Brain conversation/session layer | `source + effective_session_id` | Ephemeral; owning session lifecycle | Must not leak across sessions; requests without complete identity do not persist linkage. |
| Interim command events | `server/oracle_app/command_events.py` | Per-session bounded event compartment under the interaction synchronization boundary | Brain conversation/session layer | `source + effective_session_id` | Ephemeral; owning session lifecycle | At most 20 events per session; expiry/reset clears the owning event list atomically with session compartments. |
| Active audiobook playback registry | `server/oracle_app/audiobook_state.py` | Typed locked store with snapshot register/load/clear by playback id and source mapping | Brain audiobook playback/session layer | `playback_id` plus `source` mapping | Ephemeral; reconstructable from the owning playback/provider boundary | One active playback per source; replacement atomically removes stale old ids. This is not satellite playback truth. |
| Pending audiobook provider sync | `server/oracle_app/audiobook_state.py` | Typed locked upsert/status/load/clear store | Brain audiobook provider-sync layer | `sync_id` | Ephemeral retry state | Kept with audiobook runtime ownership rather than the interaction-state facade. Reads return deep snapshots. |
| UI calendar drafts | `server/oracle_app/ui_calendar_drafts.py` | Typed locked store with client-scoped create/load/clear and 15-minute prune | Brain structured UI/calendar layer | `client_id + draft_id` | Ephemeral | Drafts are UI workflow state, not conversation/session state; client isolation and snapshot reads are mandatory. |
| UI snapshot cache | `server/oracle_app/ui_snapshot_cache.py` | Locked get-or-build and prefix invalidation with deep snapshot reads | Brain structured UI layer | Purpose-specific cache key | Ephemeral/reconstructable | Concurrent misses for one key build once; cache data is not authoritative household state. |
| Alerts / timers / alarms / reminders | Memory SQLite `memory_alerts` and `memory_alert_transitions`, owned through `server/oracle_app/memory/alerts.py` | Transactional create/list/claim/acknowledge/cancel helpers with an append-only transition audit | Brain alerts domain over Memory storage | `alert_id`, canonical target `source_id`, and `session_id` when relevant | Active until acknowledged/completed, canceled, or expired; terminal rows retained 90 days | A valid lease is protected; expiry returns it to pending. Source retirement fails while active rows reference it. |
| Satellite local now-playing state | Satellite control adapter surface in `satellite/control_service.py` and local player adapter | Live adapter query only | Satellite playback control layer | Local satellite only | Ephemeral | No brain persistence. Brain may query it, but does not own it. |
| Satellite local longform playback state | `satellite/longform_player.py` | Local state file plus process state surfaced through control service | Satellite longform playback layer | Local satellite only | Restart-safe locally | Current state file under `/tmp/oracle-longform-player/state.json`. |
| Satellite reply-audio state | `satellite/control_service_runtime/reply_audio.py` with mirrored local reply-audio file state from the Pi runtime playback path | Authority-owned in-memory reply session state plus mirrored file-backed transport state and stop-request file | Satellite audio playback layer | Local satellite only | Ephemeral | Authority-owned reply session lifecycle is primary; mirrored file state is transitional transport-facing state only. |
| Satellite foreground-audio handoff state | `satellite/pi_runtime/models.py` plus `satellite/pi_runtime/local_control.py` coordinator helpers | Per-event handoff object created once for reply, cues, alerts, and sleep-expiry decisions | Satellite Pi runtime layer | Local satellite only | Ephemeral | This is not durable playback truth; it only normalizes one foreground borrowing or replacement decision at the local seam. |
| Satellite playback authority session | Local runtime authority model over music, audiobook, reply, and external playback backends | Local runtime state surfaced through control-service snapshots; brain may inspect but must not own truth | Satellite audio/runtime layer | Local satellite only | Ephemeral; restart-safe locally desirable | Minimum fields: `backend_type`, `state`, `resumable`, media/source identity, and position where applicable. |
| Satellite control command cache | `satellite/control_service_runtime/cache.py` plus the server-wide runtime boundary in `satellite/control_service_runtime/server.py` | Locked in-memory command idempotency cache keyed by `command_id`; duplicate in-flight IDs execute once through atomic get-or-store, while one re-entrant server lock serializes adapter and playback-authority operations across distinct commands | Satellite control-service layer | Local satellite only | Ephemeral | Deduplicates retries for a 60-second TTL window; reads return snapshots. The server lock, rather than the cache, protects shared mutable adapters across threaded requests. |
| Satellite wake/session state | `satellite/pi_wake_satellite.py` | In-process local variables tracking wake cooldown, active conversation session, alert poll timing, and duck/restore state | Satellite wake/capture layer | Local satellite process only | Ephemeral | Includes `active_session_id`, `last_conversation_activity_at`, `next_wake_time`, `next_alert_poll_at`, and duck-volume restore state. |
| Home Assistant cache | `data/home-assistant-cache.json` | File-backed cache loaded by HA routing helpers | Brain Home Assistant integration layer | Global brain cache | Restart-safe cache; not durable truth | Cache is operational convenience only, not source of truth. |
| Oracle Memory | `data/oracle-memory.sqlite3` | Memory-owned SQLite schema and helpers | Brain Memory layer | Global operational store | Durable operational memory | Records structured operational reality only; does not route, dispatch, execute, or generate replies. |
| Suggestions operational records | Memory-owned SQLite, with previous provisional store at `data/openclaw_suggestions.sqlite3` treated as migration input | Specialized tables may remain, but storage ownership belongs to Memory | Brain Memory layer plus Suggestions admin domain | Global operational store | Durable operational memory | Suggestion runs, packets, responses, suggestions, and reviews are operational memory records. Suggestions remain advisory and non-executing. |

## Mutation Ownership

### Pending confirmation

- Create: `handlers/home_assistant.py` when a risky HA action needs confirmation.
- Read/clear: `handlers/system.py` for `confirm_pending` and `cancel_pending`.
- Forbidden: any write path that is not keyed by `source + session_id`.
- Required failure behavior: if `source` or `session_id` is missing, do not emit `pending_confirmation`; fail explicitly instead.

### Pending music clarification

- Create: `handlers/music.py` when Plex results require clarification or best-guess follow-up.
- Read: `capabilities/plugins.py` and `handlers/music.py`.
- Clear: `handlers/music.py` after selection, execution, or hard failure.
- Forbidden: global clarification state shared across sessions.
- Forbidden: storing playback-authority truth, service health, deploy/config truth, or global alerts inside pending payloads.
- Required failure behavior: if `source` or `session_id` is missing, do not emit `pending_clarification`; fail explicitly instead.

### Pending audiobook clarification

- Create: `handlers/audiobook.py` when Audiobookshelf results require clarification.
- Read: `capabilities/plugins.py` and `handlers/audiobook.py`.
- Clear: `handlers/audiobook.py` after selection, execution, or hard failure.
- Forbidden: global clarification state shared across sessions.
- Forbidden: storing playback-authority truth, service health, deploy/config truth, or global alerts inside pending payloads.
- Required failure behavior: if `source` or `session_id` is missing, do not emit `pending_clarification`; fail explicitly instead.

### Conversation context and HA conversation linkage

- Create/update: `api.py` via `append_turn()` and `set_dispatch_context()`, plus `handlers/home_assistant.py` for HA linkage.
- Read: `handlers/ollama.py` for prompt shaping and `handlers/home_assistant.py` for HA reuse.
- Clear: the effective-session lifecycle clears conversation, Home Assistant
  linkage, interim events, and prior audit data atomically on expiry or explicit
  reset; direct conversation clear remains a narrow compartment operation.
- Synchronization: session and conversation/event compartment operations share
  `interaction_synchronization.py`'s re-entrant transaction boundary.
- Lifecycle: 90-second session inactivity, 30-second pending state, and a
  six-turn history bound. There is no independent conversation TTL.
- Forbidden: callers must not mutate live internal session dictionaries directly.

### Active audiobook playback registry

- Create/update: `handlers/audiobook.py` when local longform playback starts.
- Read: the satellite-media proxy and audiobook/music/UI/orchestration control
  flows through `audiobook_state.py` snapshots.
- Clear: audiobook/music/UI control on stop/close/failure through the typed
  audiobook owner.
- Forbidden: more than one active playback may remain registered for the same source.

### Pending audiobook provider sync

- Create/update/clear: `audiobook_runtime/playback.py` through
  `audiobook_state.py`.
- Read: audiobook diagnostics and focused tests through snapshots.
- Forbidden: interaction/session state or provider bridge ownership of retry
  lifecycle.

### UI drafts and snapshots

- Calendar draft create/load/clear: `ui_calendar.py` through the typed
  `ui_calendar_drafts.py` store.
- Snapshot get/build/invalidate: structured UI builders through
  `ui_snapshot_cache.py`.
- Both owners synchronize compound operations and return copies.
- Forbidden: storing drafts in conversation state or treating cached snapshots
  as authoritative household/provider state.

### Alerts

- Create/cancel/list: the alert domain through transactional Memory helpers.
- Reliable client delivery: authenticated leased claim plus explicit acknowledgement
  under `/api/satellite/alerts/*`.
- Notification alert outcomes reconcile to channel-neutral delivery receipts;
  local timer/alarm/reminder/sleep-timer records do not create receipts.
- Brain audiobook sleep-timer expiry calls the typed internal stop operation.
- Forbidden: callers must not mutate stored alert objects or metadata outside the alerts module.

### Satellite playback authority session

- Create/update: satellite-local playback/runtime layer as backends start, pause, resume, stop, or are interrupted.
- Read: control-service `GET /playback-authority` snapshots and local runtime decision points.
- Clear: backend stop, session completion, runtime reset, or explicit interruption outcome.
- Forbidden:
  - brain-owned shadow state that drifts from local playback truth
  - forcing music into audiobook-shaped long-form semantics
  - forcing reply audio into music-shaped semantics
  - implicit Plexamp-first assumptions that bypass the generic authority model

### Satellite foreground-audio handoff state

- Create/update: `satellite/pi_runtime/local_control.py` when a foreground event begins and completes.
- Read: Pi runtime reply, cue, alert, and pipeline flows that need one explicit handoff decision.
- Clear: immediately after foreground completion and explicit finalize.
- Forbidden:
  - treating foreground handoff state as durable playback ownership truth
  - using it as a shadow authority layer
  - reconstructing resume or replacement outcomes later from side effects instead of carrying the explicit handoff result forward

### Satellite reply-audio state

- Create/update: `satellite/control_service_runtime/reply_audio.py` as reply playback begins, finalizes, or is invalidated as stale.
- Read: control-service `GET /playback-authority`, `stop_reply_audio`, and local runtime reply/playback decisions that consult authority.
- Clear: explicit reply finalization, stale-session invalidation, runtime reset, or superseding replacement when a newer reply session starts.
- Mirrored transport state: `satellite/pi_runtime/audio/playback.py` may write mirrored reply file state for transport compatibility and stop-request coordination, but that file-backed state is not the ownership source of truth.
- Forbidden:
  - treating the mirrored file-backed reply state as the canonical ownership record when authority state exists
  - allowing a new reply playback to start without an explicit authority-side create path
  - leaving reply session cleanup implicit instead of having clear finalize or stale-clear behavior

### Satellite control command cache

- Create/update: `satellite/control_service.py` when a control action completes and is cached by `command_id`.
- Read: `satellite/control_service.py` request handler before redispatching a duplicated command.
- Clear: TTL pruning in `prune_cache()`.
- Synchronization: atomic cache get-or-store deduplicates the same command ID;
  the control server's re-entrant runtime lock serializes all adapter,
  reply-audio, interruption, and playback-authority operations across request
  threads.
- Forbidden: treating the cache as playback truth or allowing it to outlive its short idempotency window.

### Satellite wake/session state

- Create/update: `satellite/pi_wake_satellite.py` main loop and capture handlers.
- Read: same process only.
- Clear/reset: process restart, timeout rollover, or explicit local state transitions.
- Forbidden: brain-side ownership of these wake/cooldown/session-control variables.

### Home Assistant cache

- Create/update: HA integration cache refresh path.
- Read: HA routing helpers and cache-backed lookups.
- Clear: cache refresh or explicit operational maintenance.
- Forbidden: treating cache contents as durable truth instead of refreshable convenience data.

### Facts result cache

- Create/update: the facts domain after a cacheable normalized provider result.
- Read: the facts domain by its provider/settings/request identity.
- Clear/prune: locked startup and post-write maintenance removes expired,
  malformed, old-version, and oldest excess entries; maximum 512 entries.
- Recovery: delete and regenerate; the cache is never facts or provider truth.

### TTS clip cache

- Create/update: the Brain speech synthesis boundary using atomic file replace.
- Read: exact synthesis text plus provider, model, configuration, and cache
  version identity; successful reads refresh access time.
- Clear/prune: locked startup and post-write maintenance removes identity-unsafe
  legacy files and clips idle over 90 days, then evicts least-recently-used
  clips to at most 4,096 clips and 256 MiB.
- Recovery: discard and regenerate; no fixed-filename or normalized-text cache
  is canonical authority.

## Forbidden Scopes

- One global pending confirmation shared across all users or satellites.
- One global music clarification shared across all users or satellites.
- One global audiobook clarification shared across all users or satellites.
- Direct mutation of live conversation or alert internals by external callers.
- Multiple active audiobook playback entries for the same source remaining live at once.
- Brain-owned shadow copies of satellite-local now-playing, longform, or reply-audio state that can drift from the satellite.
- Treating one backend-specific local state surface as the universal playback authority model for all local audio.
- Treating satellite control command-cache entries as durable command truth.
- Promoting wake-loop local variables into implicit cross-process state without an explicit contract change.

## Canonical Configuration Artifacts

Canonical configuration is not domain runtime state. Its owned artifacts are:

| Artifact | Mutability | Owner | Runtime role |
| --- | --- | --- | --- |
| Authored candidate bundle | Editable | Configuration service/authoring workspace | No direct runtime reads |
| Normalized config generation | Immutable | Configuration service | Builds `EffectiveConfig` |
| Secret generation | Immutable and restricted | Configuration service | Supplies declared logical secret values |
| Activation generation | Immutable pairing | Configuration service | Binds exact config and secret generations |
| Selected pointer | Atomic mutable pointer | Configuration service | Chooses next generation for adoption |
| Applied-generation record | Process observation | Owning runtime | Reports the snapshot currently held |
| Satellite projection pair | Immutable local pair | Brain generator and satellite installer | Builds satellite-local effective config |
| Configuration audit | Append-only sanitized record | Configuration service | Records lifecycle decisions, not secret values |

Configuration activation does not rewrite operational domain state. Memory may
retain sanitized audit and observation records but is not configuration
authority. Runtime caches, provider state, sessions, active playback, and
runbook execution state remain governed by their existing owners.
