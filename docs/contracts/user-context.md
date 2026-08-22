# Oracle User Context

## Purpose

This document defines Oracle's user-context contract.

It defines:

- the authoritative user truth model
- the session boundary for active user continuity
- the precedence rules for user resolution
- the execute-as and session-switch rules
- the capability scope boundary for user-scoped execution
- the failure rules for unknown or unconfigured users
- the secret-storage boundary for user-scoped credentials

## Household User Contract

Oracle owns canonical household users in `household.yaml`. The selected
canonical configuration generation is the only reusable runtime user authority.

The selected canonical configuration generation is authoritative for:

- known users
- display names
- aliases
- default-user designation
- capability-specific access configuration

User truth is not stored in source entries, inferred from source identity, or
stored in handler globals. A stable source may have an `associated_user_id`, but
that association is context only and does not authenticate a person or grant
permission.

Required user-registry fields at a high level:

- canonical user id
- `display_name`
- `aliases`

## Session User Context Contract

User context is session-owned.

Session state stores:

- the resolved active user id
- lifecycle metadata

Session state stores only resolved user context.

Session state must not store API keys or other copied secrets.

## User Resolution Precedence

The precedence order is:

1. explicit user in the current utterance
2. active session user
3. authenticated stable source `associated_user_id`
4. configured household default user
5. safe failure

Rules:

- explicit current-utterance wording always wins
- explicit execute-as for the current utterance also refreshes session user context
- session user context persists until session timeout or explicit reset
- session timeout clears user context
- explicit reset clears user context

## Execute-As And Session Switch Rules

An explicit user in the current utterance overrides session, association, and
household-default context for that request.

Explicit named-user requests update the active session user.

Explicit session switch changes the active user for the current session.

Session timeout clears active user context.

Explicit reset clears active user context.

## Capability Scope Contract

User-scoped capability resolution exists in Oracle.

User-scoped capability resolution is currently limited to audiobook execution.

For audiobook execution:

- shared Audiobookshelf base URL remains audiobook-domain configuration
- shared Audiobookshelf library id remains audiobook-domain configuration
- shared Audiobookshelf timeout remains audiobook-domain configuration
- the effective user's audiobook capability supplies a logical credential
  reference resolved against the active secret generation
- audiobook search, progress, playback-session open, sync, close, and stream access resolve credentials from the effective user

## Fallback-Router Advisory User Contract

`fallback_router` may propose an advisory `user_id`.

That proposal:

- is brain-validated only
- is limited to the current request only
- is ignored for domains that do not support user-scoped execution
- is ignored if invalid

It must not:

- override explicit current-utterance user wording
- mutate session user context
- bypass the configured user registry

## Permission Boundary

Any configured user may explicitly execute in-scope audiobook requests as any other configured user.

## Unknown And Unconfigured User Rules

Unknown users must fail cleanly.

Known users without audiobook capability enabled must fail cleanly.

Known users with audiobook enabled but without a configured token must fail cleanly.

Oracle must not silently fall back for unknown or unconfigured users.

## Secret Storage Boundary

User-scoped logical secret references belong to the owning user capability in
`household.yaml`. Raw values belong only to the active secret generation.

Session state must never duplicate secrets.

Documentation and deploy-state must not record secret values.

Environment, legacy JSON, and source-default fields cannot override canonical
user configuration after Brain cutover. Migration maps current default-user and
source-default-user vocabulary into household defaults and source associations.
