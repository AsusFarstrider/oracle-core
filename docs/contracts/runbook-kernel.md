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

Slice 8.6 admitted an exclusive `composition` definition form in
`domains/routines.yaml`; Slice 8.7 connects it to the same durable repository
through its separately registered bounded controller. The existing flat
`steps` form retains its compatibility interpreter and behavior. Controller
selection is explicit by definition format, so a composite can never fall
through to an empty compatibility run.

Before any capability dispatch, the controller durably records the frozen
definition, resolved bounded inputs, exact configuration revision, run state,
and complete operation plan. A capability exception after dispatch is an
uncertain outcome: the operation and run stop as interrupted and are never
blindly replayed. A restart at a proven pre-dispatch boundary may continue;
durable waits, polls, or safe retries resume only against the same applied
configuration revision. Child runs carry durable parent run/operation links.

The ready set is evaluated in stable authored order without unnecessary
parallelism. Failed dependencies block only descendants. Explicit failure and
cancellation compensation uses authored operations and records its own outcome;
it does not rewrite the original operation result or claim transactional
rollback. Cancellation stops pending work, propagates to an active child, and
reports a still-active provider operation honestly when its capability cannot
be canceled.

### Bounded Stage 8 Composition Law

The ratified target extends the same composite controller and repository with:

- explicit dependencies, with failed prerequisites blocking only dependent
  work while independent work follows the declared run policy;
- deterministic three-state conditions (`true`, `false`, `unknown`), where
  unknown never becomes false and defaults to fail closed unless the definition
  explicitly chooses stop, skip, a fallback branch, or proceed;
- bounded fixed waits and bounded wait-until checks with timeout as an explicit
  semantic result;
- bounded retries only for operations whose registered semantics and
  idempotency/reconciliation make retry safe;
- bounded repetition with an explicit finite count, duration, or deterministic
  condition;
- explicitly named child Oracle runbooks with acyclic bounded nesting;
- explicit on-failure/on-cancel compensation, never inferred inverse actions or
  a household snapshot;
- deterministic configured triggers with stable activation idempotency; and
- simple bounded typed inputs and limited Oracle-owned results, never a general
  variable, expression, payload traversal, or dataflow language.

Required run policies are fail-fast and best-effort. Steps may be critical or
noncritical; no generalized failure-count threshold is part of Stage 8. Run
outcomes distinguish success, partial success, stopped failure, cancellation,
and compensation/cleanup outcome where applicable.

Cancellation prevents pending work, propagates to active child Oracle runbooks,
and attempts cancellation only where the capability supports it. Completed or
noncancelable provider work remains truthfully reported. Cancellation is not
rollback. Restart reconciliation never blindly replays an uncertain mutation,
and the existing exact-applied-revision fail-closed rule remains unchanged.

### Semantic Capability And Preauthorization Law

Each callable capability has one code-owned semantic description shared by
voice, UI, and runbooks: stable ID, owner, typed bounded arguments/results,
semantic direction and blast-radius inputs, confirmation class, retry,
idempotency/reconciliation and cancellation traits, and sanitized outcome
vocabulary. Interfaces and providers cannot reclassify the same action.

A runbook may execute consequential actions without fresh runtime confirmation
only when its reviewed enabled definition explicitly preauthorizes those exact
semantic powers. The reviewed safety manifest is deterministic and bound to the
definition/configuration review. Material expansion or change requires a new
safety acknowledgement. Underlying risk does not disappear, and absent valid
preauthorization the normal confirmation boundary applies. Deterministic
automatic triggers are authorized only as part of that same reviewed envelope.

Automatic activation is limited to code-owned `schedule`, `home_event`,
`presence`, `network_event`, and `alert_event` trigger kinds. Each binding names
an exact configured Oracle evidence identity, expected normalized state, and
bounded static inputs. Provider identifiers and provider operations never enter
the public trigger envelope. Household-local schedules reuse the canonical
gap/fold temporal resolver and identify an occurrence by its intended local
calendar date and wall minute, so a spring gap resolves once at the first valid
instant and a fall fold fires only at the earlier instant.

Every automatic occurrence has a stable activation idempotency key. Accepted
event transitions are persisted before controller activation and remain pending
across restart until the activation attempt is recorded. A missing required
input produces a durable stopped/skipped occurrence and never opens a human
clarification. A distinct occurrence while the same singleton routine is
active is durably rejected/skipped; it is neither queued nor allowed to cancel
and restart the active run.

Network evidence is observation-only. It may activate an explicitly reviewed
routine definition but can never select, preview, approve, or execute a network
recovery definition. Monitoring therefore cannot start `Fix Internet` or
bypass its confirmation boundary.

Provider-owned unit actions are authorized as opaque mapped units; Oracle does
not recursively inspect provider internals. Preauthorization cannot bypass a
domain allowlist, safety prerequisite, supported mechanism, verification,
idempotency, or honest result contract.

The composite safety review binds fixed Oracle targets and semantic selectors,
including an opaque mapped action identity, bounded non-target arguments,
maximum invocations, and the exact configured automatic-trigger envelope.
A changed binding behind the same Oracle target is a material review change.
The definition's execution-relevant shape and manual trigger scope are bound
to a deterministic review digest; presentation wording alone is not a power
change. Acknowledgement is required before a new or materially changed enabled
preauthorization can be selected. A declaration in authored configuration is
not itself evidence that the operator reviewed or activated it.

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
fall back to a V1 notification service.

Canonical composite routines follow the same snapshot law. A new run records
the exact applied configuration revision and a frozen serialized definition
before its first step mutates state. The controller receives a finite immutable
adapter map whose entries are constructed from the same effective snapshot; it
does not resolve routine, provider, source, user, room, or action configuration
from V1 getters. A waiting canonical routine may continue only while the
currently applied revision exactly matches the revision recorded by the run.
After a revision change it fails closed rather than resolving its frozen action
IDs through new mappings.
