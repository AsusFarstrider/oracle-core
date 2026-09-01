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
stored in handler globals. A stable source may have an `associated_user_id`.
For a satellite this is the canonical default-user relationship: the person who
normally uses that satellite. The association supplies context and Stage 6
personal-reminder destination policy, but it does not authenticate a person,
prove the current speaker, or grant permission.

Required user-registry fields at a high level:

- canonical user id
- `display_name`
- `aliases`

V2 canonical users are real household members. Guest-user modeling is outside
V2. Service, technical, provider, and system identities are actors or
provider-owned references and must not be entered in `household.yaml:users`.
This keeps reminder `everyone` equal to every enabled canonical user without a
parallel eligibility list or role vocabulary.

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

Capability contracts may deliberately use a stricter subset of this order.
For reminder recipient “me,” the only allowed resolution is:

1. explicitly established active session user;
2. requesting authenticated satellite's `associated_user_id`; and
3. clarification.

The household default user is not a reminder identity fallback. On a common
satellite with no default user, an omitted reminder recipient must ask whether
the reminder is for everyone or a specific person.

## Execute-As And Session Switch Rules

An explicit user in the current utterance overrides session, association, and
household-default context for that request.

Explicit named-user requests update the active session user.

Explicit session switch changes the active user for the current session.

Session timeout clears active user context.

Explicit reset clears active user context.

## Capability Scope Contract

User-scoped capability resolution exists in Oracle.

Before Stage 6 feature implementation, user-scoped execution is limited to
audiobooks. The ratified Stage 6 target also uses canonical users as semantic
reminder recipients. This document does not claim that reminder execution is
implemented before its owning slice closes.

For audiobook execution:

- shared Audiobookshelf base URL remains audiobook-domain configuration
- shared Audiobookshelf library id remains audiobook-domain configuration
- shared Audiobookshelf timeout remains audiobook-domain configuration
- the effective user's audiobook capability supplies a logical credential
  reference resolved against the active secret generation
- audiobook search, progress, playback-session open, sync, close, and stream access resolve credentials from the effective user

For Stage 6 reminder execution:

- every enabled real household user is an eligible semantic recipient;
- personal delivery resolves all currently enabled and eligible satellites for
  which that user is the configured default user;
- a common satellite has no default user and receives no personal reminder or
  private-reminder indicator;
- an everyone-reminder creates independent recipient acknowledgement state and
  may additionally create a common household projection;
- a person with no eligible destination remains truthfully undelivered and
  outstanding; and
- future recurring occurrences re-resolve destinations from the current applied
  configuration while preserving historical occurrence/delivery records.

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
