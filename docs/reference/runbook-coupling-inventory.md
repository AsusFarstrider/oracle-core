# Runbook Coupling Inventory

Snapshot date: 2026-06-22.

This is a descriptive Slice 0 inventory of current implementation coupling. It
is not a target architecture or behavior contract. “Current” in the original
inventory sections means the 2026-06-22 snapshot; use
`architecture/runbook-kernel.md` and
`architecture/domains/notifications.md` for the deployed architecture.

## Durable Storage

`server/oracle_app/memory/orchestrations.py` directly exposes run and step
creation, update, completion, listing, deletion, and restart reconciliation.
Both current execution families write these records directly.

The shared tables are `memory_orchestration_runs` and
`memory_orchestration_steps`. Schema version `0003_orchestration_runs` created
them; Slice 1 schema version `0004_runbook_kernel_metadata` additively adds
definition domain/version, activation idempotency, correlation key, controller
version/state, and cancellation provenance.

`server/oracle_app/runbook_kernel/repository.py` now wraps the compatibility
store for future controller adoption. Existing routine and recovery modules
still call Memory functions directly, so those direct calls remain extraction
targets for Slices 2 and 3.

## Task Routine Coupling

`server/oracle_app/orchestration_routines.py` currently combines:

- wait scheduling and maximum-lateness behavior;
- cancellation;
- adapter registration and domain-call argument mapping;
- required/best-effort failure policy and remediation;
- run completion and audit events;
- definition lookup, input resolution, and voice-trigger matching.

Slice 2 removes its direct Memory storage calls. Routine lifecycle reads and
writes now go through `RunbookRepository`; interpretation and domain adapter
execution remain in the routine module.

The routine scheduler calls `resume_due_routines` directly. API startup calls
the scheduler and the shared interruption reconciler.

`server/oracle_app/api.py` owns concrete adapters for UI actions, audiobook
start, sleep timers, Home Assistant state checks, and playback checks, then
registers them with `configure_routine_adapters`.

## Network Recovery Coupling

`server/oracle_app/orchestration_recovery.py` combines network-domain behavior
with recovery lifecycle coordination:

- fresh network diagnosis and household read models;
- process-local preview storage, digest, expiry, and single-use claims;
- approved-plan persistence;
- shrink-only reconciliation and plan-expansion rejection;
- fresh per-step policy checks and network-control execution;
- recovery run/step completion and audit events.

Slice 3 removes its direct Memory storage calls. Approved run creation,
pre-mutation rollback, operation writes, and completion now use
`RunbookRepository`.

The preview and safety behavior remains in the network domain. The repository
does not select or execute a remediation action.

## Validation And Configuration Coupling

Canonical Pydantic role models validate recovery and routine definitions,
triggers, inputs, and the fixed routine step-type set before a generation can
activate. `domains/routines.yaml` is the sole authored role and immutable
`RoutineRuntimeSettings` is the execution input. The retired combined JSON
loader and repository config validator are gone.

## Route And Presentation Coupling

`server/oracle_app/admin_orchestration_routes.py` assumes exactly two kinds,
`recovery` and `routine`, when building inventory, detail, active-run, preview,
and execution fields.

`server/oracle_app/orchestration_routine_routes.py` calls routine start and
cancel functions directly and applies routine-specific source/UI trigger
checks.

Recovery preview and approval routes are registered from the recovery module.
System and House UI contracts currently consume these kind-specific payloads.

The future kernel may provide a common sanitized run view, but domain
presenters must retain kind-specific fields and compatibility responses.

## Notification Coupling

Slice 4 splits the notifications implementation into a domain package:

- catalog lookup;
- Home Assistant-backed suppression policy;
- provider-neutral submission service;
- satellite announcement channel;
- notification audit and compatibility exports.

The former Home Assistant-specific notification ingress was removed after both
door automations migrated. Domain controllers now invoke the provider-neutral
service directly; the HA event adapter does not submit notifications itself.

The existing `alerts.py` store remains the satellite delivery queue and
satellite occurrence-idempotency mechanism. External delivery uses the
channel-neutral `memory_notification_deliveries` receipt model, while the
satellite adapter preserves the established alert behavior.

## Characterization Test Map

- `tests/test_memory_orchestrations.py`: shared run/step persistence, kind
  filtering, and restart interruption.
- `tests/test_orchestration_routines.py`: ordered typed execution, frozen run
  definition, pending-before-action persistence, durable waits, lateness,
  cancellation, remediation, failure, restart behavior, kernel metadata, and
  legacy waiting-run resume through the repository.
- `tests/test_orchestration_recovery.py`: immutable preview, single-use
  approval, shrink-only execution, digest/plan-change safety,
  pending-before-action persistence, persistence-failure claim release, and
  real kernel-repository integration.
- `tests/test_orchestration_config.py`: current combined schema and rejection
  rules.
- `tests/test_orchestration_routine_routes.py`: existing UI activation and
  cancellation API behavior.
- `tests/test_notifications.py`: provider-neutral notification submission, the
  Home Assistant adapter, suppression, fan-out, idempotency, expiry, and
  satellite delivery decisions.
- `tests/test_home_automation_runbooks.py`: canonical HA event mapping,
  migration exclusivity, correlated start/cancel, durable verification,
  bounded repeat, provider retry, and notification capability calls.
- `tests/test_home_automation_config.py`: provider mapping validation, timing
  bounds, migration modes, and notification references.
- `tests/test_runbook_kernel.py`: kernel identities, transition rules, metadata,
  activation idempotency, correlation filtering, operation persistence, and
  cancellation provenance.
- `tests/test_memory_store.py`: additive migration from the legacy
  orchestration table while preserving existing rows and payloads.

## Extraction Gate

Slices 2 and 3 route both current execution families through the repository.
Before Slice 4 restructures notifications, routine, recovery, notification,
Memory, and kernel characterization tests must all pass together.

## V2 Configuration Forward Pointer

This dated coupling snapshot remains evidence for migration. Canonical composite
routines move to `domains/routines.yaml`, HA-owned definitions to
`domains/home-assistant.yaml`, and network recovery policy to the network role.
The current JSON loader remains compatibility evidence until equivalence and
complete Brain cutover; this inventory does not define runtime precedence.
