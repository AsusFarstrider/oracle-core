# Runbook Lifecycle Contract

## Status

This contract defines lifecycle guarantees for the shared runbook kernel.
Recovery, composite routines, and home-automation controllers use
the kernel repository. Home Assistant supplies canonical entity-state evidence;
Oracle owns both door timing lifecycles.

## Definition

A runbook is a versioned, declarative, Brain-owned rule set that participates
in Oracle's durable run lifecycle.

Runbook kinds may use different domain languages. Sharing the lifecycle does
not require notification automation, network recovery, and composite routines
to share one step schema.

## Kernel Ownership

The shared lifecycle layer owns only cross-domain mechanics:

- stable run identity and definition identity;
- run kind and owning domain;
- activation idempotency and correlation;
- durable run and operation state;
- valid lifecycle transitions;
- durable waits, bounded lateness, and resumption scheduling;
- cancellation coordination;
- interrupted-run reconciliation;
- concurrency protection;
- audit correlation and sanitized progress records.

The lifecycle layer must not interpret domain entities, provider identifiers,
notification recipients, network targets, media users, or arbitrary commands.

## Domain Ownership

Each runbook kind owns and validates:

- its definition schema;
- accepted triggers and inputs;
- planning or operation generation;
- allowed domain actions and checks;
- approval and confirmation requirements;
- skip, retry, corrective-action, stop, and completion semantics;
- domain-specific presentation.

Domain controllers may invoke only registered Oracle capabilities. They must
not use the lifecycle layer to bypass an existing domain policy, confirmation,
allowlist, safety check, or provider bridge.

## Composite Runbooks

Cross-domain runbooks use a constrained composition language over registered,
typed domain capabilities. They may sequence capability calls, durable waits,
and registered checks with bounded failure policy.

They must not contain scripts, shell commands, arbitrary expressions, raw
URLs, provider-native service calls, credentials, or unregistered executable
text.

Existing task routines are the compatibility form of composite runbooks. Their
current configuration, activation, ordering, waits, correction behavior, and
audio handoff remain unchanged until an approved migration slice replaces the
compatibility path.

## Durable Safety Invariants

- A run and all planned operations are durable before the first mutating
  domain action begins.
- A running operation is never automatically replayed after Brain restart.
- Waiting runs may resume only from durable due-time and lateness evidence.
- A definition and resolved inputs used by an active run remain frozen for
  that run even if deployment configuration later changes.
- Duplicate activation must not create concurrent runs when the runbook kind
  defines a singleton or correlation-key constraint.
- Cancellation must not claim to stop a mutation that may already be active.
- Terminal runs are immutable except for explicitly additive delivery or audit
  receipts defined by a domain contract.
- Existing run history remains readable across additive schema migrations.

## Recovery Safety Invariants

Network recovery remains owned by the network domain. Kernel extraction must
not weaken immutable preview digests, single-use approval, shrink-only plan
reconciliation, fresh policy checks, preconditions, cooldowns, verification,
or stop-on-plan-expansion behavior.

## Notification Boundary

Notifications are a callable domain capability. Runbooks submit a curated
notification type, occurrence identity, bounded context, and run correlation.
They do not submit arbitrary text, recipients, satellite ids, phone ids,
provider services, or credentials.

The notifications domain owns rendering, audience resolution, recipient
preferences, channels, suppression, expiry, dispatch idempotency, and delivery
receipts. The existing satellite alert queue is one delivery adapter; it is not
the notification policy service.

## Historical Migration Constraints

- During the V2 migration, orchestration routes and UI payloads remained stable
  while canonical definitions replaced private V1 configuration inputs.
- Existing routine and recovery history remains readable where its persisted
  state format is still supported.
- The retired Home Assistant direct-notification ingress must not be restored
  alongside Oracle runbook ownership.
- Each door has one lifecycle owner: the Oracle home-automation controller.
- Satellite polling and foreground-audio contracts remain unchanged during
  kernel extraction.

Private V1 characterization is not a reusable core input.

## V2 Configuration Reconciliation

Canonical composite definitions live in `domains/routines.yaml`, Home
Assistant-owned definitions in `domains/home-assistant.yaml`, and network
recoveries in network policy. The kernel receives validated frozen definitions;
it never reads source configuration files itself. Obsolete V1 JSON loaders are
private migration material and are not reusable runtime inputs.

No canonical definition may reference scripts, URLs, provider-native commands,
service names, raw external entity identifiers, credentials, or unregistered
executable adapters. Configuration activation never activates or mutates a run.

An enabled composite routine may bind only to currently enabled canonical
capabilities. Home Assistant actions, state checks, and HA-backed remediations
require the Home Assistant role to be enabled. Audiobook start, sleep-timer, and
audiobook playback checks require the audiobook domain to be enabled, the user
to have an enabled canonical audiobook account where applicable, and the source
to be admitted by that domain's playback policy. A dormant mapping or a bare
satellite capability flag is not executable authority.

Canonical Home Assistant ingress authenticates with the event credential from
the same immutable Home Assistant runtime view that owns its event mappings and
automation definitions. Provider entity evidence resolves exactly one typed
event mapping and at most one enabled lifecycle owner. A new run records the
applied configuration revision and its resolved bounded definition before
waiting. Continuation uses an explicitly supplied Home Assistant state reader
and notifications capability; it cannot reopen V1 configuration or silently
fall back to the V1 notification service.

Canonical composite routines follow the same snapshot law. A new run records
the exact applied configuration revision and a frozen serialized definition
before its first step mutates state. The controller receives a finite immutable
adapter map whose entries are constructed from the same effective snapshot; it
does not resolve routine, provider, source, user, room, or action configuration
from V1 getters. A waiting canonical routine may continue only while the
currently applied revision exactly matches the revision recorded by the run.
After a revision change it fails closed rather than resolving its frozen action
IDs through new mappings.
