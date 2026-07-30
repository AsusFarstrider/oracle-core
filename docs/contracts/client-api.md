# Oracle Client API Contract

## Purpose

This contract defines Oracle's client-facing API families.

It defines:

- what belongs in `/api/voice`
- what belongs in `/api/ui`
- what belongs in `/api/admin`
- what does not belong in each
- why `/api/shared` is rejected
- how compatibility aliases are treated
- how static browser surfaces should be named conceptually
- the Alpha versioning stance for client-facing routes

## Public API Families

Oracle uses three client-facing API families:

- `/api/voice/...`
- `/api/ui/...`
- `/api/admin/...`

No public `/api/shared/...` namespace is allowed.

Provider callbacks are not client APIs. Narrow authenticated callbacks may use
`/api/integrations/{provider}/...` when a provider must submit a curated
Oracle-native event. Those routes must not expose internal modules, accept
freeform execution requests, or inherit browser-client semantics.

## Ownership Rule

Oracle's internal modules remain internal.

Public APIs are for client-facing contracts, not for exposing internal layering.

Internal examples that remain internal:

- routing internals
- handler internals
- provider bridges
- session-state internals
- playback-authority internals
- Home Assistant execution internals

## `/api/voice`

### Intended Clients

- satellites
- thin voice/chat clients
- command-style clients

### Contract Style

- conversation-shaped
- natural language in
- canonical reply text out
- session-aware

### Belongs Here

- command submission
- route preview
- interim command events for voice clients waiting on an in-flight command
- conversational session lookup
- voice alert pickup
- STT
- TTS

### Does Not Belong Here

- household page snapshots
- admin logs
- config reports
- operator diagnostics

## `/api/ui`

### Intended Clients

- browser household UI
- mobile app in structured app mode

### Contract Style

- app-shaped
- explicit snapshots
- explicit actions
- predictable JSON
- Alpha implicit version 1

### Belongs Here

- page snapshots
- household summaries
- explicit UI actions
- refresh metadata

### Does Not Belong Here

- freeform conversation/chat
- raw dispatch tracing
- logs
- config reports
- operator health dashboards
- client-driven orchestration workflows in Alpha

## `/api/admin`

### Intended Clients

- operator browser UI
- diagnostics tooling
- deployment inspection tools

### Contract Style

- operator-shaped
- diagnostic and explicitly administrative
- operator-facing

### Belongs Here

- health
- config inspection
- logs
- playback authority inspection
- source inventory
- diagnostics

### Does Not Belong Here

- household page data
- end-user actions
- natural-language command submission

The standard installation does not expose configuration, secret, activation,
recovery, rollback, package, service, or host mutation through `/api/admin`.
Those structured operations use the protected host-local Oracle control plane
and, for host-level changes, the administration CLI. Later System Mode mutation
requires a separately ratified bounded authorization mechanism; it does not
inherit the HTTP diagnostic family's reachability or authority.

## Rejection Of `/api/shared`

Public `/api/shared` is rejected.

Reasons:

- it becomes a junk drawer
- it weakens client-boundary clarity
- shared implementation belongs in internal code, not vague public HTTP

If multiple client families need the same behavior:

- share code internally
- expose purpose-specific routes under the correct public family

## Alpha Versioning Stance

For Alpha:

- `/api/ui/*` is treated as implicit version 1
- no explicit version segment is required yet
- breaking contract changes must still be documented carefully before implementation lands

Recommendation:

- keep Alpha contract evolution disciplined even without a formal versioning scheme
- do not introduce ad hoc per-endpoint version shapes unless there is a concrete need

## Compatibility Alias Rule

Compatibility aliases are allowed during migration.

Rules:

- aliases preserve existing clients while namespaced routes are introduced
- aliases are transitional
- new implementation work should target the namespaced routes
- documentation should treat namespaced routes as the intended architecture

For V2 request identity, serialized `source_id` is canonical. The current
`source` request field is a bounded compatibility alias only for deployed
satellite, browser, and mobile clients. It cannot establish trust by itself.
Stage 3 owns client migration, characterization, and removal after the complete
fleet/client cutover.

Likewise, current UI `client_id` fields are bounded aliases. Canonical APIs use
authenticated source context, `ui_session_id` for temporary UI state,
operation-specific IDs for idempotency, and trusted actor metadata for audit.
`client_id` cannot serve as source proof, authorization, or actor identity.

## Static Browser Surface Naming

Static browser path doctrine:

- `/ui` means end-user browser UI
- `/admin` means operator/debug browser UI

The old operator deep links under `/ui/trace.html` and `/ui/logs.html` are
retired. Operator-facing links use `/admin/...` directly.
