# Oracle User Context Surface

This document describes the reusable user-context implementation shape. The
canonical behavioral rules are in
[user-context.md](../contracts/user-context.md).

## Architecture Boundary

Oracle preserves these responsibilities:

- requests carry text, source, and session identity;
- the Brain owns normalization, routing, and session continuity;
- dispatch remains thin;
- canonical household configuration declares enabled users, aliases, source
  associations, defaults, and logical capability credential references;
- capability implementations own user-specific execution.

The reusable implementation seams include:

- [schemas.py](../../server/oracle_app/schemas.py)
- [dispatch.py](../../server/oracle_app/dispatch.py)
- [session_state.py](../../server/oracle_app/session_state.py)
- [audiobook.py](../../server/oracle_app/handlers/audiobook.py)

## Resolution

Canonical user resolution proceeds in contract order: explicit user, valid
session user, authenticated source association, household default, then safe
failure. A source association supplies context; it is not authentication or
authorization.

The typed household view indexes enabled users by stable ID, display name, and
alias. Disabled records may remain available for stable historical references,
while ambiguous names do not resolve. Canonical execution does not reconstruct
the legacy user-registry shape.

## Supported Interaction Shape

The reusable command and state surface supports:

- explicit user naming;
- default-user capability requests;
- explicit execute-as rewrites where the owning contract permits them;
- explicit session user switching;
- session inspection that reports effective user context without exposing
  credentials;
- clear failures for unknown, ambiguous, disabled, or unconfigured users.

Actual household members, aliases, associations, defaults, and secret values
belong to household deployment material and are not reusable core content.
