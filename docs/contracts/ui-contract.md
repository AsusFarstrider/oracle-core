# Oracle Alpha UI Contract

## Purpose

This contract defines the Alpha rules for `/api/ui`.

It exists to keep Oracle from hardening the wrong long-term architecture while
still making Alpha implementation concrete.

## Scope

This contract applies to Alpha `/api/ui` only.

It does not define the permanent final shape of all future structured UI behavior.

## Alpha Rules

### Page-Oriented

`/api/ui` is page-oriented for Alpha.

Preferred shape:

- one endpoint per major page or page-sized surface

Examples:

- `/api/ui/home`
- `/api/ui/weather`
- `/api/ui/calendar`
- `/api/ui/audio`
- `/api/ui/house`

### Snapshot-Based

Alpha UI endpoints return snapshots.

Snapshots should:

- be renderable without extra conversational interpretation
- include generation time
- include only app-safe summary fields

### Curated Actions

`POST /api/ui/action` uses curated public action IDs for Alpha.

The public UI contract must not require freeform user text.

Internal implementation may still map action IDs to internal helpers or Oracle-authored commands.

That mapping is not part of the public contract.

Some Alpha actions may also require explicit page context such as `source` for playback-targeted actions.

Rule:

- if an action needs playback targeting, the client must send the explicit Oracle source instead of inferring or hiding that routing in the browser or app

### No UI-Side Orchestration In Alpha

UI clients must not orchestrate multi-step behavior in Alpha.

Rules:

- `/api/ui` actions are single explicit UI actions
- browser and mobile clients must not compose multi-step workflows on their own
- orchestration logic belongs inside Oracle

Examples of disallowed Alpha behavior:

- chaining multiple house actions in the client because one button implies a workflow
- using `/api/ui/action` as a client-side macro engine
- recreating Oracle-side decision logic in browser or mobile code

### Source-Aware, Not Fully User-Aware

Canonical V2 UI requests carry authenticated request-source context and may
carry bounded temporary UI-session context.

They may provide:

- `ui_session_id`

Alpha `/api/ui` does not yet promise:

- full user-aware state shaping
- broad per-user household data models

`ui_session_id` is opaque temporary state scoping. It does not authenticate the
source, identify a household user, grant authorization, or supply audit actor
identity. Current `client_id` is a bounded deployed-client compatibility alias
owned by Stage 3 migration.

Satellite UI requests use exact configured canonical installation and source
identities. The canonical UI response returns separate `satellite_id` and
`source_id` values, and the client uses each only for its installation or
request-source role. Reusable runtime code does not rewrite household-derived
satellite names or accept arbitrary aliases, fallback lookups, or source claims.

Compatibility format guidance:

- lowercase
- hyphen-separated
- no spaces
- bounded to the authenticated source context

Examples:

- `example-session`
- `panel-session-2`

System Mode remains a configuration-service client. It does not read or write
runtime files directly and never becomes configuration authority.

### App-Safe Summaries Only

Alpha `/api/ui` responses expose app-safe summaries only.

They should not expose raw:

- `DispatchPlan`
- route reasoning
- trace internals
- provider-specific internals unless needed for the UI contract

### Polling Refresh

Alpha UI freshness uses:

- snapshot on load
- polling refresh
- refresh after action success

Alpha `/api/ui` does not assume:

- real-time subscriptions
- SSE
- websockets
- push-based sync

### Separation From `/api/conversation`

Conversation and chat belong in `/api/conversation`.

If a browser or mobile client supports chat:

- that chat surface uses `/api/conversation`
- it does not redefine `/api/ui` into a second conversation surface

### Alpha Constraints Are Temporary

Longer-term direction may later include:

- richer domain resources
- typed action families
- more dynamic UI updates
- stronger user-awareness

Those are later targets, not current Alpha contract guarantees.

### Alpha Versioning Stance

Alpha `/api/ui/*` is treated as implicit version 1.

Rules:

- no explicit version segment is required yet
- breaking contract changes still require documentation updates first
- Alpha flexibility does not justify undocumented contract drift
