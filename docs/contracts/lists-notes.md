# Lists And Notes Contract

## Status And Scope

This contract defines the ratified Stage 8 provider-backed Lists and Notes
semantics. Slice 8.1 established the law; Slice 8.9 implements the fixed roles,
runtime owners, deterministic voice and typed UI surfaces, Nextcloud Tasks and
Notes bridges, minimal registration, bounded caches, and health boundaries.
Slice 8.10 adds a Microsoft To Do Lists adapter. Its real-account gate passed
on 2026-09-22; the adapter remains unactivated in production.

Lists and Notes are separate Brain-owned domains. They share provider-neutral
identity, association, result, and safety principles but do not share content
storage or silently route into Facts, Calendar, Home Assistant, or Messaging.

## Authority And Ownership

Oracle owns provider-neutral interpretation, matching, clarification, replies,
stable canonical identities and aliases, household/person associations,
semantic risk and confirmation, bounded session selection, normalized results,
provider translation, and the minimum durable registration needed to address
objects created through Oracle.

The selected provider owns list, item, and note content and current object
truth. Oracle must not add a general list/note content database or treat a cache
as provider truth. Person association is organizational context, not
authentication, authorization, privacy, or provider permission.

Stage 8 House Mode is a household-visible surface. It may show configured
Lists/Notes titles and bounded content and offer the normal supported typed
actions, including their existing confirmation rules. It must not label an
associated object private or hide it from other household users merely because
of `user_ids`. Provider-account identity, canonical-user association, and
future visibility/authorization are independent concepts: several household
users may use one provider account, while separate personal provider accounts
may also exist. Neither account choice nor user association implies content
privacy, ownership, or an ACL. A future authenticated private view requires a
separate explicit design; Stage 8 does not implement one.

## Configuration And Provider Selection

`domains/lists.yaml` and `domains/notes.yaml` are optional fixed configuration
roles. Each present role declares `enabled` explicitly and selects exactly one
active provider. Multiple adapter implementations may be installed, but
credential presence, provider health, definition order, or discovery must not
select or multiplex providers.

Configured objects use Oracle IDs, display names, aliases, zero or more
canonical `user_ids`, and an opaque mapping owned by the selected bridge.
Runtime-created objects do not rewrite authored configuration. Oracle may
durably register only a stable Oracle ID, aliases needed for resolution,
association metadata, selected-provider identity, and opaque provider mapping.

Provider credentials, refresh tokens, endpoints, native IDs, ETags, and
payloads remain at the bridge/secret boundary. OAuth refresh-token rotation
must use canonical secret-generation authority; hidden token caches, mutable
credential files, and environment fallbacks are forbidden.

## List Semantics

The bounded neutral surface is: create one list; read one explicitly or
unambiguously resolved list; add, rename/edit, complete, reopen, or delete an
individual item; complete all items on one resolved list; reopen all applicable
items on one resolved list where supported; and delete all items from one
resolved list after consequential-action confirmation.

A bulk operation never crosses lists and never proceeds from an ambiguous list
name. Completing all items is ordinary; deleting all items is destructive. List
deletion and all-lists destructive commands are unsupported.

Microsoft To Do recurring tasks may be read and surfaced, but Stage 8 cannot
complete, reopen, edit, or delete one. Before a one-list bulk complete, reopen,
or delete, Oracle checks every item applicable to that operation. If any such
item is recurring, it refuses the entire operation before the first mutation,
explains the Stage 8 recurrence limit, and neither skips that item nor applies
partial work to ordinary items. A recurring item already in the requested
complete/reopen state is not applicable to that status change; delete-all
applies to every item. The bridge normalizes provider recurrence into a
provider-neutral recurring indicator; Oracle does not interpret recurrence
patterns or provider occurrence lifecycle.

## Note Semantics

The bounded neutral surface is: create, read, search, and select an individual
note; edit, append to, or replace bounded content; rename one note; and delete
one note through consequential-action handling.

Broad multi-note or notebook-wide deletion is unsupported. Provider notebooks,
sections, collections, folders, and equivalent hierarchies remain provider or
setup concerns and cannot be created or deleted through Oracle. Rich text,
attachments, labels/tags, and permission/sharing changes are not normalized.
`create list` is the sole container-like exception.

## Matching, Results, And Failure

Matching uses only canonical configured/runtime-registered identities and
aliases. Duplicate or ambiguous names clarify; provider discovery or a default
must not silently choose among genuine matches.

Reads may use bounded caches with explicit freshness/stale truth. Writes never
succeed from stale evidence. Bridges should use provider concurrency tokens
when available and return bounded Oracle-native results distinguishing verified
success, accepted-but-unverified, conflict, unavailable, rejected/unsupported,
and outcome unknown. HTTP acceptance alone is not verified success when a read
can prove the result.

Only safe/idempotent operations may receive bounded retries. An ambiguous
mutation must not be replayed blindly. Raw provider payloads, unrequested or
unbounded content, credentials, native IDs, concurrency tokens, and raw errors
must not enter logs, audits, UI, or general runbook results. A requested note
read may return its bounded normalized content to the requesting user; that is
the product behavior, not exposure of a raw provider payload.

## Safety And Composition

Semantic action risk is interface-independent. Ordinary item edits and
completion need no confirmation. Item/note deletion and one-list delete-all use
the shared consequential-action policy; delete-all has the larger blast radius.
Provider-specific risk rules are forbidden.

Runbooks may invoke only registered bounded Lists/Notes capabilities. They
cannot pass arbitrary provider payloads, select an unbounded runtime object, or
bypass confirmation/preauthorization law.

## Explicit Deferrals

Stage 8 does not authorize simultaneous active providers within one role,
Google Tasks/Keep, Apple Reminders/Notes, Microsoft OneNote, provider hierarchy
administration, sharing/permission management, general content storage, or
Communications/Messaging semantics. Microsoft To Do recurrence creation,
editing, occurrence-versus-series mutation, and provider-specific recurrence
lifecycle behavior are deferred beyond Stage 8.
