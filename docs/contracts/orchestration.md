# Oracle Orchestration Contract

## Purpose

Oracle orchestration is the Brain-owned mechanism for predefined multi-step
work. Recovery runbooks diagnose state and prepare bounded remediation plans;
task routines perform deterministic household actions and delayed follow-up.
Definitions are household deployment material interpreted by reusable Oracle
controllers.

## Definition Boundary

Every definition has a stable Oracle identity, explicit enablement, bounded
trigger metadata, and only registered typed operations. Definitions must not
contain shell commands, arbitrary arguments, credentials, provider URLs,
provider-native commands or entities, generated steps, or free-form LLM
instructions.

Task routines bind declared Oracle users and sources, bounded inputs, and
ordered typed steps. Source-local phrases resolve only for a bound authenticated
source; globally unique phrases may resolve from any authorized source.
Recognizing a step type in configuration does not make it executable unless a
registered controller provides that operation.

Registered composite operations include provider-neutral notification calls;
their text and audience remain owned by canonical notification definitions.
A registered timer-sound operation queues the existing source-scoped standard
timer alert and cannot select arbitrary audio or bypass alert delivery.
A step may select a path only through bounded comparisons against declared
typed inputs, never through arbitrary expressions. A declared spoken-duration
input reuses Oracle's session-scoped conversational input and shared duration
parser. A definition may map the exact response `no timer` to an in-range value;
that value selects the authored immediate path and does not imply cancellation.
An opt-in duration confirmation uses code-owned formatting rather than a
configuration-authored reply template.

## Recovery Preview And Approval

A recovery must:

1. collect a fresh curated diagnosis;
2. select only enabled allowlisted policy actions;
3. order proposed actions from lower to higher disruption;
4. freeze the exact proposal under an identity, expiry, and digest;
5. require explicit approval of that exact proposal;
6. recheck policy, safety, and current conditions before every action.

After approval, a plan may shrink because a target recovered. It may not add,
replace, reorder, or escalate an action without a new preview and approval.
Monitoring evidence never starts recovery automatically. Every mutation still
passes through its owning domain's allowlist, precondition, concurrency,
cooldown, adapter, verification, and audit boundary.

## Durable Lifecycle

The shared runbook kernel persists run identity, definition and controller
identity, approval provenance, status, sanitized summaries, correlations,
ordered operations, waits, and terminal outcomes. The kernel records lifecycle;
it does not interpret a domain definition or invent an operation.

All planned operations are recorded before the first mutation. After Brain
restart, a running mutation is marked interrupted and is not replayed.
Durably waiting runs may resume only within their declared lateness bound.
Cancellation is allowed only when Oracle can prove no active mutation is being
reported stopped merely because cancellation was requested.

Required failures stop a run. Explicit best-effort failures may continue only
under their declared bounded correction and recheck policy. Run and operation
records contain Oracle identities and sanitized outcomes, never credentials or
unbounded provider payloads.

## Activation

UI activation uses a stable orchestration identity, authenticated source
context, bounded UI-session context, and only declared input overrides. Voice
activation uses exact normalized configured phrases and authenticated source
context. Unknown sources, unbound sources, unknown inputs, disabled definitions,
unsupported trigger families, and duplicate active starts fail closed.

When a Run control omits a declared conversational input, Oracle stores the
bounded prompt under that UI session and begins no run or domain action until a
valid response is resolved. Invalid responses leave the prompt pending; only an
explicit cancellation response cancels it.

Compatibility request fields cannot establish trust, authorization, preview
ownership, or audit identity. A routine remains active while running or
waiting, and a second start of the same routine is rejected.

Typed actions preserve their owning domain contracts. For example, media start
uses the established playback target, session, deferred-audio, verification,
and cleanup boundaries; a routine does not acquire direct provider or satellite
control authority.

## Inspection And System Mode

Read-only administration may present normalized definitions, latest-run
summaries, bounded recent run and step history, and sanitized active state. It
must omit credentials, provider-native details, internal preview secrets, and
unbounded evidence.

System Mode may later request structured activation, cancellation, preview, or
approval through the same domain contracts. Configuration editing is separate:
it must use the canonical candidate, validation, generation, and activation
lifecycle through a separately authorized control mechanism. It never edits
installed definitions or runtime files directly.

## Canonical Configuration

Composite definitions belong to `domains/routines.yaml`; network recoveries
remain network-domain policy. Configuration activation does not start, resume,
cancel, or execute a run.

Definitions select only registered Oracle-native operations. Canonical
controllers consume one immutable applied definition and do not reread authored
files or legacy environment inputs. Compatibility sources remain limited to
migration and bounded characterization and become errors after the one-way
cutover.
