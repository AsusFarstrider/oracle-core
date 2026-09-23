# Lists And Notes Domain Architecture

The normative behavior is owned by
[`lists-notes.md`](../../contracts/lists-notes.md). This page records the
Nextcloud implementation and the live-proven but unactivated Microsoft To Do
provider boundary.

Lists and Notes are separate optional Brain domains. Each accepts one explicit
provider selection from its own fixed configuration role. Neither borrows the
Calendar runtime, cache, provider selection, identity policy, or write path.
The Nextcloud Tasks bridge reuses only low-level HTTP Basic-auth and DAV
conventions; the Nextcloud Notes bridge owns Notes REST capability negotiation.

The public semantic flow is:

```text
voice or typed UI operation
  -> deterministic Lists/Notes intent and canonical object selection
  -> domain-owned risk, confirmation, bounds, cache, and result shaping
  -> selected provider bridge with native ID and concurrency token
  -> post-write provider verification
```

Provider bridges own CalDAV `VTODO`, DAV collection URLs, native task/note IDs,
ETags, Notes API versions, authentication, HTTP payloads, and provider error
translation. Public routes and dispatch results expose canonical list/note IDs
or stable opaque item/note references, never provider IDs, ETags, credentials,
or raw provider errors.

## State And Identity

Provider content remains provider truth. Reads may use independent bounded
reconstructable caches: one key per canonical list and a separate Notes title
projection. Every write refreshes provider truth and invalidates only its own
domain cache.

The Stage 8 House page is household-visible and consumes the existing typed
snapshots and operation routes. It may display bounded titles/content and
perform supported actions with the same confirmation and provider-verification
law as voice. This is not a user-scoped private view. Provider-account identity,
canonical-user association, and any future visibility/authorization are
independent; neither a shared nor a separate provider account creates an ACL
in Oracle. This does not add simultaneous provider/account selection to either
Stage 8 role.

Configured objects come from `domains/lists.yaml` or `domains/notes.yaml`.
Objects created at runtime write only minimal canonical identity, aliases,
canonical user associations, and the opaque provider mapping to the existing
Memory current-projection repository. Task titles, note titles/content, task
completion, ETags, and provider response bodies are not stored there. A
deleted runtime-created note tombstones the registration; list deletion is not
a public capability.

## Interfaces And Safety

Deterministic voice routing and `GET/POST /api/ui/lists*` or
`GET/POST /api/ui/notes*` share the same canonical executions. Exact matching
is required before mutation. Ambiguous selections return bounded choices and
perform no write. Individual item/note deletion and one-list delete-all require
the shared confirmation lifecycle. Bulk complete/reopen/delete iterates only
the one resolved list; no cross-list command exists.

Provider writes use current ETags when supported, map precondition failure to a
conflict result, and verify provider state after mutation. Unsafe
delete/recreate emulation is forbidden. Notes API major version 1 with ETag
support at 1.2 or newer is required. The Tasks bridge's `icalendar` package is
an explicit bridge-only dependency in `requirements-nextcloud-tasks.txt` and
the full-production profile, not an Oracle base dependency.

Microsoft To Do is the alternative Lists provider, not a second active Lists
authority. Its bridge owns delegated token refresh against the immutable
canonical secret-generation view and holds short-lived access tokens only in
memory. It discards replacement refresh credentials rather than persisting them;
an invalid or expired canonical refresh credential fails with
`lists_reauthorization_required` and requires an operator-owned canonical
secret transaction. Graph task/list IDs, paging links, and OAuth responses stay
inside the bridge. Runtime-created identities are filtered by selected provider
when reloaded and are keyed by provider, so a provider switch cannot overwrite
or reinterpret another backend's mapping. Legacy Nextcloud registrations remain
readable. The Graph bridge converts non-null task recurrence to a neutral
`recurring` flag. The Lists owner exposes that flag for reads and preflights
every applicable item of a Microsoft To Do bulk complete/reopen/delete before
writing any item; one-item recurring mutations are rejected at both the domain
and bridge boundaries. Existing ETags still protect against concurrent provider
changes after preflight. No recurrence pattern, occurrence, or series lifecycle
is implemented. This implementation does not add a content store, list deletion, note
hierarchy, attachments, sharing,
permission management, or a general file editor.
