# Apprise Notification Bridge Operations

## Ownership

Apprise and downstream transports such as ntfy are external provider systems.
Oracle does not install them, manage their users or topics, or own their
credentials. Oracle owns the reusable bridge, configuration contract,
sanitized health behavior, and receipt history for requests it submits.

Enabling the bridge requires both canonical configuration and any declared
Oracle-side optional profile facilities. Installing such facilities does not
install or enable Apprise itself.

## Configuration Boundary

Canonical configuration selects the bridge and declares:

- the provider endpoint;
- bounded request timeouts;
- a logical provider configuration key or route;
- the Oracle notification definitions allowed to request external delivery;
- secret references required by the selected provider arrangement.

Concrete downstream destinations, provider credentials, topic names, user
databases, and access policy remain in household deployment or the external
provider. They must not be embedded in reusable Oracle configuration.

Disabled external delivery must require neither provider reachability nor its
optional Oracle-side dependencies, and its absence must not make the Brain
unhealthy.

## Readiness

When enabled, readiness should verify:

- the selected bridge implementation and Oracle-side dependencies exist;
- configuration and logical secret references are complete;
- the configured endpoint is reachable;
- authentication and compatibility checks succeed where supported;
- provider health responses do not expose private destination material.

An unreachable enabled provider is degraded or unavailable according to the
notification contract. It must not be reported as healthy merely because the
bridge loaded.

## Diagnostics

Use the read-only notification administration surfaces to inspect sanitized
provider health, queue state, and bounded delivery receipts. Diagnostics may
include Oracle identifiers, logical route identifiers, timestamps, status, and
bounded error codes. They must omit message text when unnecessary and must
never expose provider URLs containing credentials, destination addresses,
topics, tokens, or raw provider responses.

Provider-native configuration endpoints that reveal routes or credentials are
not Oracle health probes.

## Verification

Safe verification proceeds in increasing-impact order:

1. validate canonical configuration and secret references;
2. inspect sanitized Oracle provider health;
3. verify the external provider through its own bounded health surface;
4. submit an explicitly approved canary to a non-sensitive destination;
5. verify the Oracle receipt and independent client delivery when required;
6. remove temporary canary configuration.

Do not enable a household notification definition solely because the provider
accepted a request. Where end delivery matters, obtain recipient-side evidence.

## Disable And Recovery

Disable external delivery through canonical configuration to stop new provider
requests without affecting native satellite announcements. Provider rollback
or repair remains an external-infrastructure operation. Oracle configuration,
secret, and activation changes use the ordinary generation, validation,
restart, recovery, and rollback lifecycle.
