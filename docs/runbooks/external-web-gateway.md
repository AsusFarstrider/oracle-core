# External Web Gateway Guidance

## Boundary

Oracle supports host-local and explicitly selected household-LAN HTTP ingress.
Neither posture authorizes direct public-Internet exposure. Remote access must
cross a separately configured boundary such as a VPN, authenticated tunnel,
reverse proxy, or equivalent gateway.

Oracle Stage 4 does not install or manage that gateway, public DNS, TLS
certificates, firewall rules, VPNs, tunnels, public users, browser login
sessions, password reset, or external identity policy.

See the [HTTP ingress contract](../contracts/external-web-access.md) for the
authoritative transport and access-policy requirements.

## Recommended Shape

A common arrangement is:

```text
remote client -> authenticated external boundary -> household-LAN Oracle listener
```

The external boundary terminates public transport and authenticates the remote
client before proxying to the explicitly selected Oracle LAN listener. The
Oracle listener still applies its canonical source, credential, trust, and
client-family policy; proxy authentication does not silently replace Oracle's
own access contract.

Do not expose the Brain listener directly to a public interface. Wildcard binds
are explicit broad-listening choices and require recorded interface, firewall,
and boundary assumptions.

## Protected Surface

An external gateway must default to protecting the complete reachable Oracle
HTTP surface, including:

- browser and mobile UI routes and their static assets;
- read-only administration and diagnostic routes;
- subsystem health and logs;
- API documentation and schema discovery;
- media proxy helpers;
- any future remote System Mode surface.

Public liveness is optional and must be deliberately shallow and non-sensitive.
Do not assume `/health` is safe for anonymous publication.

The host-local Unix-domain administration socket is not proxied. It must not be
converted into an HTTP maintenance endpoint merely to support remote access.

## Satellite And Local Clients

Satellites and other household components normally use the selected
household-LAN listener with their directional Oracle credentials. They do not
inherit browser sessions from the external gateway. A satellite control API key
or lifecycle credential must not be replaced with a public-web login session.

Browser and mobile clients may use the external boundary on both local and
remote networks when the household deliberately chooses that topology. Split
DNS or local bypass must not accidentally skip a required authentication
boundary.

## Trusted Proxy Information

Forwarded client, scheme, and host headers are untrusted unless the household
deployment explicitly enables them and prevents untrusted clients from reaching
the Brain listener outside the trusted proxy path. Record which proxy addresses
are trusted and which headers Oracle consumes.

Authentication secrets, user databases, password hashes, session keys, and
gateway configuration remain external deployment material. Do not place them in
the reusable core or Oracle household configuration merely because the gateway
fronts Oracle.

## Verification

Verify the boundary from outside and inside it:

1. unauthenticated remote requests cannot reach protected Oracle content;
2. authenticated browser/API requests and static assets work through the
   gateway;
3. public health behavior matches the declared policy;
4. direct public access to the Brain listener is unavailable;
5. LAN satellites still reach their intended listener using Oracle credentials;
6. forwarded-header trust matches the recorded proxy topology;
7. browser/PWA clients survive restart, network transition, and session expiry
   as required by their supported contract.

Gateway failure or rollback must not alter Oracle application revisions,
configuration generations, secrets, or activation selection. Disable remote
reachability at the external boundary while preserving the intentional local or
LAN posture.
