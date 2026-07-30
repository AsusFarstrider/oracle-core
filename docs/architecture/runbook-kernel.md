# Runbook Kernel Architecture

## Status

This document describes the current kernel boundary and retained compatibility
architecture. Composite routines, network recovery, and live home-automation
door controllers use the shared repository while retaining their own domain
languages and policy.

The lifecycle invariants are normative in
[`contracts/runbook-kernel.md`](../contracts/runbook-kernel.md). Historical
migration sequencing remains outside the reusable architecture surface.

## Purpose

The runbook kernel gives independently designed runbook kinds one reliable,
durable lifecycle without forcing them into one domain language.

It is infrastructure for domain controllers. It is not:

- a routing domain;
- a provider bridge;
- a universal rules engine;
- an arbitrary script runner;
- the owner of domain policy;
- the owner of notification content or recipients;
- a replacement for network-control safety policy.

## Boundary

```text
domain trigger or user request
            |
            v
    domain runbook controller
      |                  |
      | lifecycle        | typed domain capabilities/providers
      v                  v
    runbook kernel     domain behavior
      |
      v
 durable run/operation repository + scheduler + audit correlation
```

The controller interprets its definition and decides the next valid domain
operation. The kernel records and coordinates that decision. The kernel never
invents a domain operation.

## Choosing The Owning Domain

Use the kernel when a workflow needs durable identity, transitions, waits,
correlation, cancellation, restart recovery, or common audit history. The
domain controller still owns the workflow language, triggers, decisions,
checks, retry/stop semantics, and typed capability calls.

A visible notification does not make a workflow notification-owned. The door
workflows are home-automation runbooks because they own door timing, fresh
state verification, repetition, and close-event cancellation. They call the
notifications domain only for the communication effect. A future
notification-internal runbook would instead own delivery escalation or another
workflow whose condition and decisions belong inside notifications.

## Kernel Responsibilities

### Registration

A runbook kind registers a stable kind id, owning domain, definition validator,
controller, and public presenter. Registration is code-owned and allowlisted;
configuration cannot register executable code.

### Activation

The kernel accepts a validated definition reference, trigger metadata,
correlation key, and activation idempotency key. It creates a durable run before
the controller executes a mutating operation.

Activation metadata must be sanitized. Provider credentials, raw request
headers, and unbounded provider payloads are not run state.

### Persistence

The kernel persists:

- run identity, kind, domain, definition identity/version, and timestamps;
- current status and sanitized summary;
- correlation and activation idempotency;
- ordered operations and their lifecycle;
- controller-owned bounded state needed to resume;
- approval provenance where required;
- cancellation, interruption, failure, and completion outcomes.

Domain-specific evidence may be stored only as bounded, sanitized operation
payload. Provider-native secrets are forbidden.

### Scheduling

The kernel owns durable due times, maximum lateness, and wake-up scheduling.
When a wait becomes due, the kernel asks the registered controller to continue.
It does not decide what domain action follows the wait.

### Cancellation

The kernel coordinates cancellation by run id or supported correlation key.
The controller decides whether its current domain operation is cancelable. A
running mutation is never reported canceled merely because the caller asked.

### Restart Reconciliation

Running mutations become interrupted after restart and are not replayed.
Durably waiting runs remain eligible for bounded resumption. The controller
must interpret interruption according to its domain contract.

### Presentation

The kernel supplies a sanitized common run envelope. A domain presenter adds
kind-specific progress, approval, findings, delivery, or remediation details.
The common layer does not flatten away meaningful domain semantics.

## Implemented Slice 1 Boundary

`server/oracle_app/runbook_kernel/` now contains:

- immutable definition and activation identity models;
- the current run-status set and transition validator;
- terminal-state immutability checks;
- `RunbookRepository`, a kernel-facing wrapper over the compatibility
  orchestration store.

The repository supports metadata-aware run creation, activation-idempotency
lookup, domain/correlation filtering, operation persistence, validated run
transitions, cancellation provenance, and restart reconciliation.

Schema version `0004_runbook_kernel_metadata` adds definition domain/version,
correlation key, activation idempotency key, controller version/state, and
cancellation reason/requester. Existing rows migrate additively with empty
defaults and retain their payload/history.

The existing table constraint still permits only compatibility kinds
`routine` and `recovery`. New kinds remain intentionally blocked until a later
slice migrates the store and registers a controller.

## Implemented Slice 2 Composite Compatibility Controller

`server/oracle_app/orchestration_routines.py` remains the task-routine domain
controller and the interpreter for the existing routine step schema. It now
uses `RunbookRepository` for:

- active-run lookup and duplicate-start protection;
- metadata-aware run creation;
- pending-before-action operation persistence;
- running/waiting/terminal transitions;
- durable wait completion and lateness failure;
- cancellation provenance;
- legacy waiting-run resumption.

New routine runs are recorded with domain `composite`, a deterministic
SHA-256 definition version, controller version `1`, and correlation key
`routine:<definition_id>`. Existing pre-kernel waiting runs have empty metadata
and remain resumable.

Routine configuration, step types, adapter arguments, execution order,
best-effort correction, audiobook deferred handoff, scheduler behavior, voice
matching, and UI routes are unchanged. Kernel-only metadata is removed from
the existing UI start/cancel response to preserve the public payload.

This is a compatibility composite controller, not the final registered
capability language. Slice 2 intentionally does not introduce a controller
registry or rewrite the existing routine definitions.

## Implemented Slice 3 Network Recovery Controller

`server/oracle_app/orchestration_recovery.py` remains the owner of network
diagnosis, previews, approval, shrink-only reconciliation, fresh policy checks,
execution, and verification. It now uses `RunbookRepository` for:

- approved run creation with network-domain metadata;
- pending-before-network-action operation persistence;
- running and terminal operation updates;
- terminal run transitions and controller progress;
- deletion of pre-mutation history when durable plan creation fails.

New recovery runs use domain `network`, controller version `1`, a deterministic
definition version derived from the recovery profiles, preview-scoped
correlation, and preview-scoped activation idempotency.

The preview remains process-local and single-use. A persistence failure releases
the preview claim, deletes any partial pre-mutation history, returns the same
`503` safety response, and executes no network action. Kernel metadata is not
added to the public recovery result.

The repository does not receive raw authority to execute network operations.
Every approved action still flows through the existing network-control
confirmation path with current allowlists, preconditions, cooldowns,
concurrency protection, provider adapters, and verification.

## Controller Interface

Controller registration is still planned. Conceptually a controller provides:

```text
validate_definition(definition) -> findings
activate(definition, trigger, context) -> controller decision
continue_run(run, due_operation) -> controller decision
cancel(run, reason) -> cancellation decision
present_definition(definition, runs) -> sanitized domain view
present_run(run) -> sanitized domain view
```

A controller decision may request the kernel to:

- record one or more planned operations;
- begin a typed operation;
- enter a durable wait;
- mark an operation skipped, failed, or completed;
- complete or fail the run;
- request a registered domain capability call.

The concrete interface must make invalid status transitions difficult and must
not expose direct SQL or raw table mutation to controllers.

## Runbook Kinds

### Composite

Composite runbooks coordinate registered capabilities across domains. Current
task routines are the compatibility implementation. Their constrained language
supports typed calls, waits, checks, and bounded corrective behavior.

### Network Recovery

Network recovery uses its native diagnosis, preview, approval, reconciliation,
and remediation semantics. The kernel stores lifecycle; the network domain
retains all safety and plan policy.

### Home Automation

Home-automation runbooks react to canonical household events from the Home
Assistant bridge. They own delayed verification, correlation, repetition, and
stop conditions, then call domains such as notifications through typed
capabilities.

### Notification-Internal

The notifications domain may use a notification-specific runbook when the
workflow concerns delivery escalation or channel behavior internal to that
domain. A household condition such as a door left open remains a
home-automation concern even though its visible action is a notification.

## Composite Capability Calls

The composite language addresses a capability by stable Oracle-owned id. The
capability registry defines its bounded arguments, result schema, safety
classification, and owning domain.

Conceptual examples:

```text
home.lights.set
audiobooks.start_current
notifications.submit
```

Provider-specific details resolve behind the owning domain. Composite
definitions must not include Home Assistant entity ids, satellite URLs,
Audiobookshelf credentials, shell commands, or provider service names.

## Current Compatibility Architecture

- `orchestration_routines.py` owns routine interpretation and typed adapter
  calls while using `RunbookRepository` for lifecycle persistence;
- `orchestration_recovery.py` owns recovery interpretation and network safety
  while using `RunbookRepository` for lifecycle persistence;
- `memory/orchestrations.py` is the shared durable store;
- `admin_orchestration_routes.py` presents exactly `routine` and `recovery`;
- the retired V1 `config/orchestration.json` compatibility input contains both
  definition families for migration/characterization only.
- `runbook_kernel.RunbookRepository` is used by task routines and network
  recovery. No current execution controller writes orchestration Memory tables
  directly.
- `home_automation/controller.py` owns the live entry workflows and uses the
  repository under domain `home_automation`;
- the retired V1 `config/home-automation-runbooks.json` compatibility input
  carries HA mappings, bounded timing policy, and mutually exclusive migration
  modes for migration/characterization only;
- the home-automation scheduler, not the composite scheduler, resumes domain
  `home_automation` runs.
- both door definitions are in runbook mode; the direct HA notification ingress
  and repeat loops are removed.

The V2 canonical event seam consumes typed Home Assistant event mappings and
automation definitions directly. It freezes one bounded
`EntryRunbookDefinition` plus the applied configuration revision into each new
run. Canonical continuation constructs provider-state reads from the immutable
Home Assistant view and requires an injected notifications capability; missing
injection fails construction rather than consulting another configuration
authority.

The extraction inventory is recorded in
[`reference/runbook-coupling-inventory.md`](../reference/runbook-coupling-inventory.md).

## Related Documentation

- Normative lifecycle contract:
  [`contracts/runbook-kernel.md`](../contracts/runbook-kernel.md)
- Notification caller boundary:
  [`architecture/domains/notifications.md`](domains/notifications.md)
- Home-automation lifecycle contract:
  [`contracts/home-automation-runbooks.md`](../contracts/home-automation-runbooks.md)

## Documentation Maintenance

Every extraction slice must update this document to distinguish the current
runtime from the remaining compatibility path. The final migration cannot be
marked complete while this document describes planned interfaces that differ
from deployed code.

## V2 Configuration Reconciliation

Composite routines, HA-owned definitions, and network recovery policy move to
their fixed canonical owners. The kernel receives validated frozen definitions
through typed configuration and never reads files. Definitions cannot contain
scripts, URLs, provider commands, service names, raw external IDs, credentials,
or executable adapter details.

The Stage 3 composite construction seam maps `domains/routines.yaml` into
frozen `RoutineRuntimeSettings` without changing the kernel lifecycle. Only
enabled definitions become executable or own trigger phrases. Each definition
is bound to its enabled owner and sources; Home Assistant steps bind to exact
adapter mappings, while audiobook steps bind to the canonical user account and
an audiobook-domain-admitted applied playback target. A disabled capability,
dormant mapping, or unadmitted satellite target blocks configuration activation
instead of failing during a later run. Configuration construction does not
create, resume, cancel, or mutate kernel runs.

`CanonicalRoutineExecution` is the proportional execution boundary over the
existing compatibility controller. It supplies the exact serialized typed
definition, applied configuration revision, and an immutable finite adapter map
for Home Assistant and audiobook operations. Voice requests, UI run requests,
and satellite routine buttons select this object from the installed Brain
composition and cannot fall back to V1 settings in canonical mode. The existing
durable run stores the frozen definition and revision; no second routine store
or historical configuration loader is introduced. Continuation under a
different selected revision fails closed because reinterpreting frozen IDs
against new provider mappings would violate snapshot ownership.

The shared orchestration admin read model remains a bounded compatibility-shaped
surface spanning routine and network-recovery records. Canonical application
lifespan starts the routine scheduler with the immutable adapter map and exact
required revision; network recovery reads and execution use the same installed
network composition.
