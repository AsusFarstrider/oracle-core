# Oracle State Model

## Purpose

This document defines Oracle's state ownership, scope, lifecycle, and persistence contract.

It defines the ownership rules that govern Oracle state.

Related contract:

- [memory.md](memory.md) defines Oracle Memory ownership for durable structured
  operational storage.

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
| Conversation context | `server/oracle_app/conversation.py` | Module-owned session store with snapshot reads and explicit clear helpers | Brain conversation/session layer | `session_id` | Ephemeral | Includes turn history plus dispatch context. |
| Home Assistant conversation linkage | `server/oracle_app/conversation.py` | Stored and loaded through dedicated helpers | Brain conversation/session layer | `session_id` | Ephemeral | Must not leak across sessions; requests without `session_id` do not persist linkage. |
| Active audiobook playback registry | `server/oracle_app/state.py` | Helper-based register/load/clear by playback id and source mapping | Brain audiobook playback/session layer | `playback_id` plus `source` mapping | Restart-safe desirable | One active playback per source; replacement removes stale old ids. |
| Alerts / timers / alarms / reminders | `server/oracle_app/alerts.py` plus file-backed state under `data/alerts-state.json` | Module-owned store with lock plus file-backed persistence; create/list/consume/cancel helpers | Brain alerts layer | `alert_id`, `source`, and `session_id` when relevant | Restart-safe file-backed persistence; durable policy may still evolve | `list_alerts()` returns copies; metadata is copied on create. |
| Satellite local now-playing state | Satellite control adapter surface in `satellite/control_service.py` and local player adapter | Live adapter query only | Satellite playback control layer | Local satellite only | Ephemeral | No brain persistence. Brain may query it, but does not own it. |
| Satellite local longform playback state | `satellite/longform_player.py` | Local state file plus process state surfaced through control service | Satellite longform playback layer | Local satellite only | Restart-safe locally | Current state file under `/tmp/oracle-longform-player/state.json`. |
| Satellite reply-audio state | `satellite/control_service_runtime/reply_audio.py` with mirrored local reply-audio file state from the Pi runtime playback path | Authority-owned in-memory reply session state plus mirrored file-backed transport state and stop-request file | Satellite audio playback layer | Local satellite only | Ephemeral | Authority-owned reply session lifecycle is primary; mirrored file state is transitional transport-facing state only. |
| Satellite foreground-audio handoff state | `satellite/pi_runtime/models.py` plus `satellite/pi_runtime/local_control.py` coordinator helpers | Per-event handoff object created once for reply, cues, alerts, and sleep-expiry decisions | Satellite Pi runtime layer | Local satellite only | Ephemeral | This is not durable playback truth; it only normalizes one foreground borrowing or replacement decision at the local seam. |
| Satellite playback authority session | Local runtime authority model over music, audiobook, reply, and external playback backends | Local runtime state surfaced through control-service snapshots; brain may inspect but must not own truth | Satellite audio/runtime layer | Local satellite only | Ephemeral; restart-safe locally desirable | Minimum fields: `backend_type`, `state`, `resumable`, media/source identity, and position where applicable. |
| Satellite control command cache | `satellite/control_service.py` | In-memory command idempotency cache keyed by `command_id` with timestamp map | Satellite control-service layer | Local satellite only | Ephemeral | Used to dedupe repeated control requests for a short TTL window. |
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
- Clear: explicit conversation helpers in `conversation.py`.
- Forbidden: callers must not mutate live internal session dictionaries directly.

### Active audiobook playback registry

- Create/update: `handlers/audiobook.py` when local longform playback starts.
- Read: `api.py` stream proxy and `handlers/audiobook.py` transport/sleep-timer flows.
- Clear: `handlers/audiobook.py` on stop/close/failure and explicit state helpers.
- Forbidden: more than one active playback may remain registered for the same source.

### Alerts

- Create/update/cancel/list/consume: `alerts.py` and audiobook sleep-timer helpers that call into it.
- Read by clients: `GET /alerts/pending`.
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
