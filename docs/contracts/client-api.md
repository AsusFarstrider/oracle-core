# Oracle Client API Contract

## Purpose

This contract defines Oracle's client-facing API families.

It defines:

- what belongs in `/api/conversation` and `/api/speech`
- what belongs in `/api/ui`
- what belongs in `/api/admin`
- what belongs in `/api/satellite` and `/api/integrations`
- what does not belong in each
- why `/api/shared` is rejected
- how compatibility aliases are treated
- how static browser surfaces should be named conceptually
- the Alpha versioning stance for client-facing routes

## Public API Families

Oracle uses six purpose-owned API families:

- `/api/conversation/...`
- `/api/speech/...`
- `/api/ui/...`
- `/api/admin/...`
- `/api/satellite/...`
- `/api/integrations/...`

No public `/api/shared/...` namespace is allowed.

Provider callbacks are not client APIs even though they share this public
family classification. Narrow authenticated callbacks use
`/api/integrations/{provider}/...` only when a provider must submit a curated
Oracle-native event. They must not expose internal modules, accept freeform
execution requests, or inherit browser-client semantics.

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

## `/api/conversation`

### Intended Clients

- satellites and thin conversation clients
- direct text/chat and terminal-like clients
- command-style clients

### Contract Style

- conversation-shaped
- natural language in
- canonical reply text out
- session-aware

### Belongs Here

- command submission
- route preview
- interim command events for clients waiting on an in-flight command
- conversational session lookup
- opaque deferred conversation effects returned by command execution

### Does Not Belong Here

- household page snapshots
- admin logs
- config reports
- operator diagnostics
- speech transcoding or synthesis
- satellite-local alert pickup, media, or appliance control

The canonical surfaces are:

- `POST /api/conversation/command`
- `POST /api/conversation/route`
- `GET /api/conversation/session`
- `GET /api/conversation/command-events`

The old text-ingest endpoint is not retained. Text is a modality of command
submission, not a second command architecture.

### Command Result

`POST /api/conversation/command` returns a finite public result rather than the
internal route and dispatch envelope.

Required fields:

- `reply_text`: canonical client-safe reply text; it may be empty only when
  `status` is `ignored`
- `session_id`: the effective conversation session identity
- `source_id`: the effective authenticated request-source identity
- `status`: exactly `executed`, `pending_confirmation`,
  `pending_clarification`, `failed`, or `ignored`
- `failure_code`: a sanitized stable failure code or `null`
- `trace_id`: the request trace/correlation identity
- `effects`: the finite typed effect object described below

The effect object has four optional typed members and no arbitrary extension
dictionary:

- `follow_up`: expectation for a same-session client follow-up
- `satellite_playback`: the foreground playback disposition a satellite must
  apply
- `deferred_satellite_playback`: an opaque continuation token that a satellite
  may return through the satellite-control boundary after the reply
- `ui_presentation`: an app-safe UI DTO or stable reference

Absent effects are `null`. Internal `RouteResponse`, `DispatchPlan`, provider
payloads, handler results, and routing explanations are not public command
result fields. A generic safety reply is permitted only when malformed internal
output reaches the public shaper; it is not a normal substitute for
target-owned reply behavior.

## `/api/speech`

### Intended Clients

- satellites and clients that need Oracle-owned speech conversion

### Belongs Here

- `POST /api/speech/stt`
- `POST /api/speech/tts`

Speech endpoints convert modalities. They do not own routing, sessions,
commands, replies, alerts, or satellite appliance policy.

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

`GET /api/admin/caches` is the read-only V2-native facts/TTS cache diagnostic.
It reports lifecycle health, counts, bytes, bounds, expired/malformed/legacy
entries, and a non-mutating cutover impact. It cannot prune, warm, or otherwise
mutate either cache.

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

## `/api/satellite`

This family owns authenticated satellite mechanics, not every request that
happens to originate from a satellite. It includes wake arbitration, activity,
configuration projection/enrollment, wake-capture upload, reliable alert
claim/acknowledgement, opaque deferred playback continuation, and canonical
satellite media delivery.

Canonical Stage 5 additions are:

- `POST /api/satellite/alerts/claim`
- `POST /api/satellite/alerts/{alert_id}/acknowledge`
- `POST /api/satellite/deferred-resume`
- `GET /api/satellite/media/audiobooks/{playback_id}/tracks/{track_index}`

Alert claim requires `Authorization: Bearer <projection-credential>` and a
claimed `source_id`; the credential-derived source is authoritative. A claim
returns zero or more records with `alert_id`, `lease_id`, `lease_expires_at`,
`kind`, `message`, `due_at`, `source_id`, optional `session_id`, and metadata.
Acknowledgement supplies the same `source_id` and `lease_id` plus status
`acknowledged` or `completed`. Constructing a claim response never completes a
record, and an expired lease returns it to pending.

Conversational satellite commands still use `/api/conversation`; satellite STT
and TTS still use `/api/speech`.

## `/api/integrations`

This family terminates narrow authenticated provider callback and integration
ingress. Provider translation occurs at the owning integration/bridge boundary.
It does not accept generic Oracle commands or expose provider schemas as Oracle
domain contracts.

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

The root aliases and `/api/voice/*` are obsolete migration inputs, not retained
contract surfaces. Stage 5 migrates every known Oracle-controlled consumer to
the semantic family, verifies the migration, and removes the old path. Unknown
hypothetical consumers do not justify an alias or dual response shape. Root
`/health` is the sole intentional minimal Brain liveness/recovery endpoint.

For V2 request identity, serialized `source_id` is canonical. The current
`source` request field is a bounded compatibility alias only for deployed
satellite, browser, and mobile clients. It cannot establish trust by itself.
Stage 5 owns client migration, characterization, and removal after the complete
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
