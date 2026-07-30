# Endpoint Exposure Inventory

## Role

This is a descriptive inventory of Oracle HTTP surface families and their
expected ingress posture. It does not grant access or define behavior.

Authority remains with the [client API contract](../contracts/client-api.md),
[HTTP ingress contract](../contracts/external-web-access.md),
[health contract](../contracts/health-ownership.md), and applicable domain or
UI contract.

## Exposure Vocabulary

- `host-local`: reachable only through loopback.
- `household-lan`: reachable through an explicitly selected household network
  listener and canonical access policy.
- `external-boundary-required`: remote Internet use requires a separately
  configured VPN, authenticated tunnel, reverse proxy, or equivalent boundary.
- `compatibility`: a retained alias whose removal has a separate evidence gate.

No label grants authorization by itself. Installation bind settings and
canonical trust, source, authentication, and credential policy must agree.

## Surface Families

| Family | Representative paths | Minimum exposure rule |
|---|---|---|
| Household browser UI | `/ui`, `/ui/*`, `/api/ui/*` | Host-local by default; household LAN by explicit choice. Remote Internet use requires an external boundary. |
| Operator diagnostics | `/admin`, `/admin/*`, read-only `/api/admin/*` | Same transport choices as browser UI, with operator-sensitive results protected by the applicable access policy. |
| Health and discovery | `/health`, `/health/*`, admin health aliases | Never assume anonymous public access. Any externally reachable liveness result must be separately bounded and non-sensitive. |
| Voice requests | `/api/voice/*` and retained aliases | Household LAN only when enabled clients have matching canonical source and credential policy. |
| Satellite lifecycle | `/api/satellite/*` | Household LAN, with the exact directional lifecycle or operational credential required by each route. Do not expose through a public browser boundary. |
| Provider integrations | `/api/integrations/{provider}/*` | Internal or household LAN only, narrowly authenticated, and limited to Oracle-native evidence or callbacks. |
| Media proxy | Brain-hosted artwork or prepared playback streams | Reachable only where the consuming authorized client requires it; provider credentials must never leak into URLs or responses. |
| Development introspection | `/docs`, `/redoc`, `/openapi.json` | Host-local by default; explicitly gate or disable before broader exposure. |

## Administration Boundary

The standard maintenance interface is not an HTTP family. Configuration,
secret, activation, recovery, and rollback operations use the protected
host-local Unix-domain Oracle control socket. Host installation, dependency,
service-definition, account, permission, and package changes remain
administration-CLI operations with bounded elevation.

Read-only HTTP diagnostics do not inherit control-socket authority. Later
System Mode mutation requires a separately ratified structured authorization
mechanism and must not be inferred from `/admin` or `/api/admin` reachability.

## Validation Requirements

Installation and readiness checks must record and reconcile:

- selected ingress posture and effective IPv4/IPv6 binds;
- enabled client families and their reachable paths;
- canonical trust, authentication, and credential requirements;
- wildcard-listener effects;
- firewall and external-boundary assumptions;
- local and, when enabled, household-LAN reachability results.

Contradictory bind and access policy fails readiness with an actionable
explanation. LAN exposure never implies direct public-interface exposure.
