# Network

The `network` domain is a single user-facing route target for Oracle network awareness.

## Scope

The domain owns:

- user-facing network and internet-health questions
- normalized summary status
- short spoken summary language
- action availability boundaries
- safety rules for approved restart/control actions

The domain does not split into user-facing subdomains such as monitoring, probing, router control, or service control.

## Current Structure

The current domain is split across:

- `server/oracle_app/network.py` for request parsing, summary assembly, and Oracle-side status interpretation
- `server/oracle_app/handlers/network.py` for dispatch-target execution
- `server/oracle_app/provider_bridges/network_probe.py` for Oracle-owned direct DNS/HTTP reachability checks
- `server/oracle_app/provider_bridges/librenms.py` for optional LibreNMS visibility
- `server/oracle_app/provider_bridges/service_control.py` for typed approved service-control adapters
- `server/oracle_app/provider_bridges/router_control.py` for typed approved router-control adapters
- capability routing in `server/oracle_app/capabilities/plugins.py`, which recognizes simple network-health questions

## Provider Bridge Roles

The current bridge roles are intentionally narrow:

- `network_probe` verifies connectivity directly through Oracle-owned checks
- `librenms` provides monitoring visibility only
- `service_control` executes only planned and allowlisted Oracle service actions
- `router_control` executes only planned and allowlisted router actions

These bridges do not interpret overall health, decide user actions, or combine multiple systems. The `network` domain owns that work.

The direct-probe and LibreNMS read bridges return immutable Oracle-owned
observation DTOs from `provider_bridges/network_observations.py`. Provider
payload parsing and provider field aliases remain inside their bridges. The
network domain converts those DTOs to ordinary dictionaries before building the
read model, so public network snapshot and voice response shapes remain
unchanged. DTO constructors defensively copy nested rows and preserve the
existing successful/unconfigured collection contracts.

## Current Behavior

The capability remains deliberately bounded:

- Oracle can answer simple network-health questions such as whether the network or internet looks okay
- the Home UI can show a normalized network-health block
- no self-healing is performed
- approved control actions use explicit bounded routes and confirmation policy
- no arbitrary shell execution exists

## Summary Shape

The domain returns a normalized summary object shaped around:

- top-level `status`
- `internet` direct-check status
- `monitoring` provider-visibility status
- `problems`
- `actions_available`
- `generated_at`

Top-level and sub-status values are constrained to:

- `healthy`
- `pending`
- `degraded`
- `down`
- `unknown`

## Safety Boundary

Control bridges remain allowlist-driven.

Required boundary:

- approved hosts only
- approved services or routers only
- approved actions only

Forbidden:

- arbitrary shell execution from user text
- direct UI calls into provider/control mechanisms
- provider bridges deciding whether Oracle should act
- automatic restart/remediation based on monitoring alone

## V2 Configuration Reconciliation

Canonical network configuration uses fixed `inventory.yaml`, `policy.yaml`, and
`adapters.yaml` roles. Inventory anchors explicit domain enablement. Policy
remains Oracle-native and command-free; provider/host translation remains at the
adapter edge. The split changes configuration ownership, not control safety.

Inventory also owns the optional domain-level
`internet_health_probe_adapter_id`. When present on an enabled inventory, it
selects exactly one compatible `direct_probe` adapter for Oracle's aggregate
internet-health observation. It is separate from object-specific monitors.
Absence means no domain-level internet-health probe; Oracle does not infer one,
activate orphan adapters, or invent a synthetic Internet host.

The first Stage 3 network construction seam maps only the enabled inventory
anchor into frozen `NetworkInventoryRuntimeSettings`. It binds hosted objects,
service groups, monitor targets, dependency endpoints, and power-target hosts
to exact Oracle-owned identity records. Adapter IDs remain unresolved at this
layer, and no policy, probe, health interpretation, or action authority is
constructed. Device-host and dependency-endpoint references now fail candidate
validation when unresolved. A disabled inventory contributes no operational
topology. Adapter and policy construction remain separate typed seams in the
installed composition.

The second seam maps only adapters reached from enabled inventory or policy
edges into frozen `NetworkAdaptersRuntimeSettings`. The reachability closure is
finite and schema-owned: monitors, enabled power targets, enabled actions, and
typed service-control readiness/lifecycle references. Required LibreNMS, SSH,
and router credentials resolve from the adopted secret generation and remain
redacted. Home Assistant power reuses the enabled canonical HA connection rather
than creating another credential boundary. No adapter method is called during
construction; observation, action policy, confirmation, execution, and live
state remain outside this seam. Policy construction is the following seam in
the same installed composition.

The third seam maps only enabled actions and recoveries into frozen
`NetworkPolicyRuntimeSettings`. Actions bind to exact enabled inventory targets
and the typed adapters selected by the adapter seam. Enabled power actions
cannot treat a disabled power target as executable. Recoveries retain plan
approval, profile identity, and normalized voice-trigger ownership, but policy
construction does not interpret profile IDs as an execution registry. It does
not create previews or runs, execute controls, diagnose state, or remediate
anything. Brain adoption composes this seam with the inventory and admitted
adapter views before any operation begins.

The complete Stage 3 execution seam is `CanonicalNetworkExecution`. It receives
those three immutable views and the optional canonical music execution once at
composition construction. Voice summaries, the fixed Internet and Network read
models, LibreNMS health, admin diagnostics, recovery previews/runs, startup
restart completion, and confirmed control select that object whenever canonical
authority is installed. A missing canonical network execution fails closed; it
does not reopen V1 network files.

Observation remains typed at the provider edge. The selected domain probe and
each object monitor execute only through its admitted `direct_probe` or
`librenms` adapter. Connections may be reused within the snapshot, but each
monitor retains its own canonical target and matching rule. The three retired
`oracle_satellite_control` monitor records are not recreated: satellite health
belongs to the satellite operational relationship rather than network
inventory.

Control retains the existing finite lifecycle instead of creating a second
policy system. Dry-run and confirmation resolve exactly one enabled canonical
target/action owner, evaluate the code-owned preconditions, acquire the
process-local execution guard, call the bound typed adapter, verify the result,
and persist the existing safe result/audit record. Graceful host restart keeps
the contract order: release cross-host clients, stop target-host services,
close declared storage, restart, restore target-host services, verify
readiness, then restore read-write client storage and its dependents. Partial
preparation is rolled back. Pi-hole continuity still permits restarting an
already-down target or a target whose alternate is healthy. No raw provider
target, credential, command, or mount detail enters the public control model.

Canonical requests and workers use only the installed typed network view; no
field-level precedence or alternate runtime authority exists.
