# Oracle State Ownership Ledger

This reference records the current implementation of Oracle's mutable state.
The normative ownership and lifecycle rules remain in
[`state-model.md`](../contracts/state-model.md); this ledger supplies the
source-reconciled implementation detail that changes more frequently.

The machine-readable ledger is
[`state-ownership-ledger.json`](state-ownership-ledger.json). Each state entry
inherits lifecycle fields from its named profile and may override any field.
The resolved entry therefore records:

- a stable ID, semantic owner, implementation location, and scope;
- lifecycle, authority/cache/mirror role, and durability;
- create, read, update, clear/prune/recover, clock/retention, concurrency,
  snapshot, and restart behavior;
- consumers, tests, governing contracts, and linked findings.

The ledger is descriptive, not a second contract. A path move updates the
ledger; an ownership or contract change requires the normal architectural
decision process.

## Coverage Boundary

The current ledger explicitly covers:

- all effective-interaction compartments, UI drafts, and UI snapshots;
- active audiobook and pending provider-sync state;
- every table in the canonical Memory database, including Suggestions;
- alerts, notifications, runbook execution, network control/recovery state,
  and restart markers;
- provider, UI, facts, Home Assistant, and TTS caches;
- Brain application/runtime composition state;
- satellite playback authority, reply state, command idempotency, player
  files, wake state, and wake-capture artifacts;
- immutable configuration generations and their mutable selection/runtime
  observations.

`tests/test_state_ownership_ledger.py` validates the schema, source paths,
stable IDs, profile resolution, all canonical SQLite tables, and the known
runtime-path bindings. New durable storage or a new canonical Memory table
must therefore receive a ledger disposition before the gate passes.

## Lifecycle Profiles

Profiles remove repetition without hiding semantics:

| Profile | Meaning |
| --- | --- |
| `interaction` | Brain-owned effective-session compartment under one shared re-entrant synchronization boundary. |
| `brain_locked_ephemeral` | Brain-owned in-memory state with explicit locking and snapshot reads. |
| `brain_cache` | Reconstructable Brain cache; never authoritative provider or household truth. |
| `brain_restart_marker` | Bounded file state used to reconcile a deliberate local restart. |
| `memory_sqlite` | Durable operational Memory state protected by SQLite transactions and approved retention law. |
| `satellite_runtime` | Satellite-local in-memory playback/control authority under the control runtime boundary. |
| `satellite_file` | Satellite-local reconstructable player/transport state; atomic-write debt remains assigned to Stage 9. |
| `managed_configuration` | Immutable generated configuration artifacts plus explicit mutable selection/observation records. |

## Updating The Ledger

Update the JSON entry and its evidence whenever mutable state is introduced,
moved, removed, or changes lifecycle. Change `state-model.md` only when the
normative ownership or state law changes. Historical migration explanation
belongs in the appropriate archived stage record, not here.
