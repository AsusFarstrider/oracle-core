# Oracle HTTP Ingress Contract

## Purpose

This contract defines supported HTTP exposure postures and their relationship
to canonical access policy. It does not select a firewall, reverse proxy, VPN,
TLS arrangement, DNS name, port, or interface-selection implementation.

## Supported Postures

Standard Debian Brain installations support two first-class postures:

- `host_local`: listen only on loopback. This is the safe default and the
  Stage 4 minimal-profile posture.
- `household_lan`: accept connections from an explicitly selected household
  network address or interface so authorized Oracle clients can reach the
  Brain.

LAN ingress is supported, not experimental, but it requires an explicit
installation or maintenance choice. Installation must not broaden a new Brain
from loopback to network reachability merely because the host has a LAN
interface. Wildcard IPv4 or IPv6 binds are explicit broad-listening choices and
must be explained and recorded.

## Separate Authorities

Transport exposure and canonical access policy are separate authorities:

- installation and deployment settings determine where HTTP listens;
- canonical access configuration determines which sources and client families
  may use Oracle and what trust, authentication, and credential requirements
  apply.

Readiness must reject contradictions, including remote listening with a
host-local-only policy, configured LAN clients with only a loopback listener,
missing credentials required by enabled sources, or a client family with no
reachable ingress path.

Validation and deployment evidence record effective bind addresses, ports,
IPv4 and IPv6 exposure, posture classification, canonical access-policy
compatibility, credential expectations, reachability results, and declared
firewall or external-boundary assumptions.

## Client And Administration Boundaries

HTTP client families retain their own contracts. Satellites, browsers, mobile
clients, and integrations do not gain access merely because a listener is
reachable. Provider callbacks remain narrowly authenticated Oracle-native
integration surfaces.

The standard online administration boundary is not HTTP. Configuration,
secret, activation, recovery, and rollback operations use the protected
host-local Unix-domain control socket. Host-level mutation remains with the
administration CLI or a later separately ratified bounded mechanism. LAN or
public HTTP reachability must never grant that maintenance authority.

## Public Internet Boundary

Household LAN exposure is distinct from direct public Internet exposure.
Oracle must never infer or configure direct public-interface exposure from LAN
enablement.

Remote Internet access requires an explicitly configured external boundary,
such as a VPN, authenticated tunnel, or reverse proxy. That boundary owns its
authentication, public transport, certificates, and session behavior. Passing
an external gateway does not by itself prove that an Oracle operation is safe
or authorized.

Oracle's Stage 4 installer does not install or modify firewalls, reverse
proxies, VPNs, tunnels, DNS, TLS certificates, or public ingress
infrastructure. Those require separate deployment or profile contracts.

## Proxy And Health Safety

Oracle must not trust proxy-supplied identity, scheme, host, or client-address
headers unless the exact proxy relationship and accepted headers are declared
and direct bypass is prevented.

Any externally reachable health surface must be deliberately bounded. It must
not expose configuration, secrets, provider posture, route inventories, logs,
diagnostics, or secret-derived state. A deployment may keep all health routes
behind its external boundary or expose only a separately reviewed shallow
liveness result.

## Non-Goals

This contract does not create Oracle-managed household accounts, browser
sessions, OAuth, password reset, route-by-route permission scopes, remote
administration, or a universal API-key matrix. Those require separate evidence
and ratification.
