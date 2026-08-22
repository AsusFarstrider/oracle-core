# Notifications Domain Architecture

Notifications is a Brain-owned capability for communicating a curated Oracle
event through approved channels. Current behavior is governed by the
[notification contract](../../contracts/notifications.md); durable workflow
mechanics belong to the separate
[runbook-kernel architecture](../runbook-kernel.md).

## Domain Boundary

The domain separates:

- why and when an owning domain requests communication;
- the stable semantic notification type and occurrence identity;
- eligible audiences and logical recipients;
- channel-specific rendering and dispatch;
- deduplication, suppression, retry, expiry, and audit evidence.

The notification domain does not own the condition or workflow that caused a
request. Home automation, network recovery, composite routines, and other
typed callers retain their own policy and outcomes.

```text
typed domain or runbook caller -> notifications.submit(...)
  -> type and policy lookup -> audience resolution -> suppression
  -> satellite queue and/or durable external receipt
  -> provider adapter
```

## Core Concepts

A notification type is the stable identifier callers use. It owns approved
content, default style, audience, channels, suppression, expiry, and delivery
success semantics. Callers do not provide final arbitrary text or provider
destinations.

An occurrence is one idempotent request. Reusing an occurrence identity must
not create another delivery. A later legitimate repeat uses a new occurrence
under the same optional workflow correlation.

Satellite endpoints and logical external-recipient groups are distinct. A
speaker is an endpoint, not a person. Provider-native destination identifiers
and credentials remain hidden from callers and reusable configuration.

Durable external receipt states distinguish pending work, provider acceptance,
retry delay, failure, expiry, and suppression. Provider acceptance does not
claim client display or human acknowledgement. Likewise, satellite queue
consumption does not prove acoustic hearing.

## Caller Contract

All callers use one structured provider-neutral operation with a registered
notification type, stable occurrence identity, bounded allowed context,
trusted internal caller identity, and optional run correlation.

Callers may not supply arbitrary message text, raw satellite IDs, provider
device IDs, credentials, unregistered adapters, or suppression bypasses. A
delivery failure changes the caller's domain outcome only when that caller's
contract explicitly makes notification success mandatory.

## Suppression And Delivery

Workflow conditions, notification suppression, recipient preferences, and
channel availability are separate policy layers. Suppression is evaluated by
the notification domain and rechecked before delivery when evidence can become
stale. Callers cannot disable it.

The satellite channel creates source-scoped expiring alert work and lets the
satellite borrow foreground audio under the shared playback policy. Satellites
do not resolve audiences or suppression.

The external channel creates restart-safe logical-recipient receipts. A
provider bridge translates those logical targets into an independently
configured delivery system without exposing concrete destination URLs to the
notification domain.

## Implementation Boundary

The reusable implementation separates catalog, policy, service, audit,
receipts, background delivery, canonical runtime binding, and channel adapters.
Provider bridges remain under `provider_bridges/`; they do not move into domain
policy modules. Read-only admin routes expose sanitized definition, provider,
and receipt status without accepting delivery or configuration mutations.

Canonical `domains/notifications.yaml` owns notification types, templates,
audiences, suppression, channels, retry, and logical recipient mappings.
Secret-bearing provider destinations remain whole logical secrets. Disabled
definitions do not resolve recipients, adapters, or credentials, and
configuration activation never submits a notification.
