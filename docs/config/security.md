# Configuration Security

## Secret Boundary

Canonical YAML stores uppercase logical secret references only. Raw values live
in the separate strict `secrets.env` generation and never enter config hashes,
reports, logs, Memory, audit, exports, examples, or APIs.

System Mode may create, replace, remove, or rotate a value but never reads it
back. Failed submissions are deleted from staging and require re-entry. Unused
secrets warn; missing secrets required by enabled configuration block activation.

The configuration engine enforces these write-only operations with expected
secret-generation concurrency. Every successful submission creates a new generation,
even when the submitted raw value is unchanged; Oracle does not compare or
fingerprint raw equality. Secret-only activation preserves the selected config
revision, permanently revokes the predecessor, and keeps transaction journals
and audit records value-free. On a standard Debian installation, the host-local
CLI calls this engine through the protected Oracle administration Unix socket.
Filesystem admission is limited to the Oracle service identity, root, and the
dedicated Oracle operator group; the server validates kernel peer credentials
instead of client-supplied identity claims. Secret values come from a hidden
prompt or stdin, never argv, and are absent from responses. The trusted-boundary
System Mode client remains later work.

Secret-bearing URLs are whole secret values. Credential-free provider URLs may
appear only at their owning provider/adapter edge. Raw secret generations use
bounded retention and revoked generations are permanently non-activatable.

## Access Boundary

`access.yaml` declares Oracle-facing trust expectations without configuring the
external gateway, VPN, tunnel, firewall, or identity provider. Browser mutation
requires both the configured trusted authentication boundary and CSRF-resistant
proof. Host-local CLI uses a local process boundary.

The host-local protocol is a finite operation-specific schema with bounded
messages. It transports fixed-role UTF-8 candidate content rather than asking
the service to open caller-selected paths, rejects extra fields, sanitizes
unexpected failures, and never creates a TCP listener. Systemd creates the
boot-lifetime runtime directory with the declared service-owner/operator-group
boundary before the control plane accepts requests. The socket is removed and
safely recreated across service restart and reboot; stale socket files are not
trusted. The socket path and service presence lock are bootstrap/deployment
state. Systemd supplies the service with the narrowly scoped `oracle-admin`
supplementary group; the unprivileged service publishes its own runtime
directory to that group and validates the exact boundary before binding.
Brain bootstrap recovers durable transactions before binding the socket,
and partial or invalid bootstrap fails startup. Resolved authored and installed
configuration trees cannot overlap, and the socket cannot live under authored
input.

Offline CLI use is explicit and mutually exclusive with socket mode. It refuses
an uninitialized store, holds the same service-presence lock to prove the socket
authority is stopped, and reaches mutation only through the ordinary exclusive
store transaction lock. It never creates, repairs, or overlays a store. Initial
offline activation may read the actual candidate companion, but raw values
remain governed by the same write-only reporting and audit boundary. Later
manual companion drift is rejected rather than silently ignored or blended
with the selected configuration.

Oracle does not add application-level user accounts or treat household users
and source associations as authorization. Standard local online administration
uses the host's Unix identities and the single Oracle operator group; this does
not grant general Debian host-administration authority.

The canonical access runtime seam resolves active non-satellite source
credentials from the same immutable secret generation as `access.yaml` and
keeps them out of object representations. It performs only presented-credential
authentication; it does not compare configured credentials with each other or
persist raw-value equality. Disabled-source bindings remain inactive even when
their logical reference is retained. Ingress-specific protected transport,
ephemeral-source assignment, and the eventual request integration remain
separate from this settings seam.

## Satellite Credentials

Enrollment, satellite-to-Brain, and Brain-to-control credentials are unique per
satellite and directionally scoped. Satellite-local secret generations contain
only values needed by that projection. Revoked credentials never regain
server-side authority through offline fallback.

## Migration

Legacy env files, `config.local.json`, JSON blobs, and shared API keys are
migration evidence only after complete process cutover. Import extracts their
raw values into logical references without logging or reporting them. A
household may preserve retired inputs outside executable paths as private
migration evidence; canonical startup reads fixed legacy locations only to
reject a reintroduced authority.
