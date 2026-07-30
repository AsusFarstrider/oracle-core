# Oracle Network Control Contract

## Purpose

This document defines the minimum Stage 4 boundary for network/service control.
It does not authorize control by itself, and it does not change the Stage 3
read-only network status contract.

Stage 4 may add allowlisted, explicit, audited control requests after the
Oracle-owned network read model exists.

If this document conflicts with roadmap notes, this contract wins.

## Scope

Allowed in Stage 4:

- dry-run evaluation for allowlisted actions
- explicit confirmed execution for allowlisted actions
- audit records for every request, dry-run, confirmation, execution, denial,
  timeout, and provider result
- provider adapters that execute one specific allowlisted action on one named
  Oracle target

Forbidden:

- arbitrary commands
- generated shell commands
- shell commands derived from user text, LLM output, provider alerts, or monitor
  summaries
- automatic remediation
- self-healing from alerts, schedules, monitors, or background status checks
- provider-native hostnames, service names, entity ids, URLs, or alert text as
  the only control target
- credentials, tokens, or secrets in API payloads, UI payloads, or audit text

## Target Identity

Control targets must be Oracle-owned inventory/read-model objects.

Required control target fields:

- `target_type`: Oracle target type, such as `host`, `service`, or
  `power_target`
- `target_id`: stable Oracle id from the curated network inventory/read model
- `action_id`: stable Oracle-owned action id from an allowlist

Provider references are metadata only. They may help an adapter find the
provider object after Oracle policy approves a request, but they must not become
the primary identity presented to users or used by policy.

## Request Lifecycle

Minimum lifecycle:

1. request is created with actor, source, target, action, and reason
2. Oracle policy validates target, action, source, and configuration
3. dry-run returns the planned operation without changing external state
4. explicit confirmation is recorded before any mutating execution
5. provider adapter executes only the approved allowlisted action
6. Oracle records the provider result and final status in audit data

Only one confirmed disruptive network-control execution may hold the Brain's
process-local execution lease at a time. A second confirmed request must fail
closed with `network_control_action_in_progress`; it must not enter a provider
adapter. The active state may expose only Oracle target/action IDs and the
lease start time.

After an adapter attempt completes, Oracle applies a cooldown to that Oracle
target before another action for the same target may execute. The policy may
set bounded `execution.cooldown_seconds` from 0 through 3600. When omitted,
component/service restarts default to 60 seconds and host, router, or power
actions default to 300 seconds. Requests during cooldown fail closed with
`network_control_action_cooldown`.

The execution lease and cooldown registry are process-local safety state, not a
durable scheduler. A Brain restart clears them. They must never be treated as
authorization or as a replacement for confirmation, policy, preconditions, or
provider verification.

Dry-run must not change external state. A mutating action must not execute from
status refresh, provider alert ingestion, UI page load, voice interpretation, or
LLM output alone.

## Dry-Run Shape

The first Stage 4 surfaces are plan-only/non-executing:

- `POST /api/admin/network/control/dry-run`
- `POST /api/admin/network/control/confirm`

The dry-run response must include:

- `request_id`
- `requested_at`
- `actor`
- `source`
- `target_type`
- `target_id`
- `action_id`
- `mode`: always `dry_run`
- `allowed`: boolean policy result
- `policy_status`
- `confirmation_status`
- `provider`
- `result_status`
- `error_class`
- `summary`
- `target`: safe Oracle target metadata
- `preconditions`: provider/domain checks that must pass before execution can
  later be allowed
- `steps`: safe planned steps, empty when denied

PR 4.1 is allowed to deny every request after target validation. It exists to
stabilize Oracle-owned target identity and response shape before service-control
allowlists, confirmation, provider adapters, or audit persistence are added.
It must not return shell commands, provider credentials, confirmation tokens, or
provider-native target ids as the primary identity.

## Control Policy

Stage 4.2 introduces a dedicated local policy file:

- checked-in example: `config/network-control.example.json`
- local deploy file: `config/network-control.json`
- checked-in bridge example: `config/network-service-control.example.json`
- local bridge deploy file: `config/network-service-control.json`
- checked-in router example: `config/network-router-control.example.json`
- local router deploy file: `config/network-router-control.json`
- compatibility env override: `ORACLE_NETWORK_CONTROL_JSON`

The retired `network_control`, `network_service_control`, and
`network_router_control` keys in `server/config.local.json` are not read.

The policy is an Oracle-owned allowlist. Provider systems do not define Oracle
identity and do not authorize actions.

The network domain may map an Oracle host/service to a service-control bridge
reference such as `host_id` plus a bridge-facing `service_name`. The
service-control bridge owns platform and transport details: Linux, Windows,
local execution, SSH, Docker, systemd, and the concrete command mechanics. The
network-control policy only decides whether a named Oracle target/action pair
may be previewed or confirmed.

Router restart uses a separate router-control bridge profile keyed by the stable
Oracle router host ID. Router login details remain local bridge configuration;
they are not network inventory identity and must not appear in API responses.

Targets with more than one independently managed component may declare
action-specific bridge references under
`control_refs.service_control.actions.<action_id>`. This is intended for host
controls such as `restart_runtime` and `restart_ui`; it does not create fake
Oracle services for implementation processes.

Each action entry must use Oracle inventory IDs:

- `id`
- `target_type`: `host`, `service`, or `power_target`
- `target_id`
- `action_id`
- `provider`
- `adapter`
- `requires_confirmation`
- `required_preconditions`
- `enabled`
- `execution`: optional adapter-specific allowlist data, never user-provided
  command text
- `description`

Policy entries must not contain shell commands, scripts, raw credentials,
tokens, passwords, provider URLs, or provider-native target names as the primary
identity. An enabled action must require confirmation. Disabled entries are
allowed so future targets can be modeled before execution exists.

`required_preconditions` contains stable Oracle precondition IDs, not provider
commands or provider-native targets. The Brain owns selection and interpretation
of these checks; provider bridges own the fixed read-only observations needed to
evaluate them. Supported IDs are:

- `plex_no_active_streams`: blocks a Plex restart while active streams exist.
- `pihole_restart_continuity`: allows a restart when the alternate Pi-hole is
  healthy or when the target Pi-hole is already confirmed down. It blocks taking
  a healthy target offline without a healthy peer and fails closed when health
  cannot be determined.
- `host_storage_safe_for_restart`: requires the selected host's service-control
  adapter to pass its configured RAID, writable-mount, and storage-sharing
  service checks before Oracle permits a host restart.

Unknown IDs and IDs attached to incompatible targets are configuration errors.
Precondition results may expose stable IDs, status, summaries, and safe aggregate
values. They must not expose command text, credentials, provider-native service
targets, array names, or mount paths.

Host restart policy may set `requires_graceful_lifecycle: true`. This is a
mandatory execution contract, not an optional runbook. The corresponding
service-control bridge profile must declare `lifecycle.mode: graceful`.
Oracle must block the reboot before adapter execution when the lifecycle
profile is absent, invalid, or any mandatory preparation phase fails.

Graceful lifecycle phases are ordered:

1. evaluate target safety preconditions;
2. stop configured cross-host dependent services and release their client
   mounts;
3. stop configured services on the target host;
4. stop storage sharing, flush writes, unmount filesystems, and stop configured
   RAID arrays when storage closure is declared;
5. send the host restart;
6. verify transport recovery;
7. restart target-host services that were stopped during preparation;
8. verify host readiness;
9. restore client mounts read-write and restart cross-host dependent services.

The bridge owns concrete service targets, mount paths, RAID names, and fixed
commands. Policy and UI must contain only the lifecycle requirement and safe
phase summaries. A preparation failure must prevent reboot. If reboot dispatch
fails after preparation, Oracle must attempt bridge-owned rollback and report
whether rollback completed. A client mount that returns read-only is not
recovered; the bridge may attempt one fixed read-write remount and must fail
closed before restarting dependent services if verification still fails.
Automatic remediation, arbitrary commands, and operator-supplied lifecycle
steps remain forbidden.

`GET /api/admin/network/status` may expose safe `control_actions` metadata for
Network tab affordances. This metadata is limited to Oracle action IDs,
enabled/confirmation state, adapter/provider labels, and descriptions. It must
not expose command strings, host login details, credentials, shell fragments,
raw provider URLs, or resolved service-control command targets.

The same metadata may include a safe `availability` object with `ready`,
`in_progress`, `blocked_by_active`, or `cooldown` status. Active state may
include Oracle target/action IDs and start time. Cooldown state may include
remaining seconds and expiry time. Provider identifiers, command details, and
credentials remain forbidden.

`GET /api/admin/network/control/actions` is a read-only control coverage
diagnostic. It reports every configured control policy action and whether it has
the expected Oracle inventory target, policy state, service-control inventory
reference, bridge host profile, bridge service profile, and bridge command
allowlist entry. It may report safe labels such as provider, adapter, transport,
platform, Oracle host id, Oracle service-control service key, and enabled state.
It must not execute any action, evaluate mutating preconditions, emit audit
records, or expose credentials, host login details, host addresses, command
arguments, systemd units, Docker container names, shell fragments, raw provider
URLs, or resolved service-control command targets.

The dry-run endpoint may return `allowed: true` only when the Oracle target
exists, the target/action pair is allowlisted, the policy entry is enabled, and
required preconditions pass. Even then, Stage 4.2 remains non-executing:
`result_status` stays `not_executed` and the response must contain only safe
planned steps.

## Confirmation

`POST /api/admin/network/control/confirm` is the first confirmed-control
contract. It exists before provider execution adapters so the UI, policy,
precondition rechecks, and audit records can stabilize safely.

Confirm requests must include:

- `target_type`
- `target_id`
- `action_id`
- `actor`
- `source`
- `reason`
- `confirmed`: must be `true`

The confirm endpoint must:

- re-run target/action policy checks;
- re-run required preconditions immediately before any attempted execution;
- reject requests without `confirmed: true`;
- return `result_status: not_implemented` when no provider adapter exists for
  the allowlist entry;
- never return shell commands, command arguments, secrets, raw provider URLs, or
  provider-native target IDs as the primary identity;
- record an audit event whether the request is allowed, denied, blocked, or
  executed.
- record `network_control_started` immediately after acquiring the execution
  lease so long-running actions are visible before completion;
- release the execution lease in a `finally` path and apply the target cooldown
  after every adapter attempt, including failed attempts.

Until execution adapters exist, a confirmed request may reach
`confirmation_status: confirmed` but still must not mutate provider state.

## Service Restart Adapter

The first executable adapter is intentionally narrow:

- `action_id`: `restart_service`
- preferred `adapter`: `service_control`
- legacy compatibility `adapter`: `service_restart` with `execution.method:
  systemd`

For the service-control path, Oracle sends the confirmed Oracle target/action to
the bridge with the target's `service_control` reference. The bridge owns the
host profile, login/transport, platform, service manager, and basic command
implementation. Network-control responses must not expose the resolved bridge
command target.

After a confirmed service-control restart, Oracle must verify availability
before returning success. Service-specific verifiers may be used when they
provide stronger evidence, such as Plex active-session/API availability checks.
Otherwise, the service-control bridge performs a read-only status check using
the same bridge host/service profile. The bridge may use platform/service
manager mechanics such as `systemctl is-active` or Docker inspection, but
network-control responses must expose only safe status summaries, adapter or
service-manager labels, bounded wait/timeout values, and verification status.
They must not expose command arguments, systemd unit names, Docker container
names, host addresses, credentials, stdout, or stderr.

The one allowed exception is a local self-restart of Oracle Brain. A process
cannot reliably restart its own systemd unit, wait, verify, and still return a
stable HTTP response to the UI. Such entries must be explicitly marked in the
service-control bridge config with `restart_mode: deferred_self_restart`.
Oracle may then return `result_status: executed` with
`execution.verification_status: deferred` and `execution.deferred: true` after
the bridge schedules the restart. The caller must verify Oracle Brain through
normal health evidence after the service comes back online. The deferred path is
still an allowlisted service-control action; it must not accept arbitrary
commands or expose the resolved systemd unit in network-control responses.

For the legacy local systemd path, `execution` may contain:

- `method`: `systemd`
- `unit`: explicit systemd service unit name, such as
  `plexmediaserver.service`
- `restart_timeout_seconds`: bounded timeout for the systemd restart request
- `wait_seconds`: bounded wait before availability verification

The adapter must not accept user-provided commands, shell strings, hostnames,
service names, or unit names from the request payload. It must only use checked
Oracle inventory references, bridge config, and allowlist entries after Oracle
inventory, policy, confirmation, and precondition checks pass.

A typed Docker service adapter may declare a bounded ordered list of
`lifecycle_service_targets` in addition to its primary `service_target`. These
are fixed provider-native companion containers that stop before the primary
target and start after it in reverse order. They are adapter translation, not
Oracle service identity or a general workflow language. They must be unique,
must not repeat the primary target, and are invalid for non-Docker adapters.
This preserves the existing Nextcloud app/cron lifecycle during migration.

The adapter invokes systemd through noninteractive sudo (`sudo -n`) so a missing
sudo rule or password prompt fails quickly instead of blocking the admin
request. Restart timeouts are reported as `network_control_restart_timeout`
without returning stdout or stderr.

The Plex restart action must re-check `plex_no_active_streams` immediately
before execution. If Plex has any active streams, execution is blocked.

## Home Assistant Power Cycle Adapter

The `switch_power_cycle` adapter is limited to curated Oracle
`power_target` objects with:

- provider `home_assistant`
- an explicit `switch.*` entity reference
- capability `power_cycle`
- `enabled: true`

Execution requires explicit confirmation. Oracle calls Home Assistant to turn
the switch off, verifies the `off` state, waits the policy's bounded
`off_seconds`, restores power, and verifies the switch returned `on`. When the
power target references an Oracle host with an address, Oracle then polls host
reachability through the network probe bridge until the bounded recovery
timeout. Switch-on without host recovery is a failed action with
`power_restored: true`, not a successful power cycle. If the normal switch
sequence fails after power was turned off, Oracle makes a best-effort turn-on
request before returning failure.

Host recovery is necessary but not sufficient. Every enabled power target must
also declare an inventory-owned `readiness.checks` profile. The network probe
bridge owns the provider mechanics for the allowlisted check kinds:

- `host_reachable`: direct reachability for a fixed inventory address;
- `tcp_reachable`: a fixed address and port, such as a curated DNS endpoint;
- `internet`: the configured Oracle internet-health probe.

After power and optional host reachability recover, Oracle retries the complete
profile until the policy's bounded `readiness_timeout_seconds`. A restored plug
whose readiness profile does not pass is reported as
`network_control_power_readiness_failed` with `power_restored: true`. Results
and Activity may expose readiness status, aggregate counts, and stable failed
check IDs, but not addresses, ports, provider entity IDs, or raw probe details.
Control diagnostics expose only whether readiness is configured and its check
count.

The adapter does not accept entity IDs from API requests, UI state, user text,
LLM output, or provider discovery. The entity reference comes only from the
Oracle-owned local inventory. Dry-run and audit responses must not expose that
provider-native entity ID.

## Router Restart Adapter

The `router_control` adapter supports only the Oracle-owned `restart_router`
action for a curated router host. The local router profile must explicitly use
SSH transport and the fixed `ssh_reboot` bridge adapter. The bridge always
sends its built-in reboot operation; policy files, API requests, UI state, user
text, and model output cannot provide command text.

Router credentials belong in ignored local config or an environment reference.
They must not be committed, logged, returned by diagnostics, or exposed in
audit records. After the bridge accepts a restart request, Oracle must first
observe the router's SSH transport become unavailable through the network
probe bridge, then observe it return. A router that remains reachable
immediately after dispatch is not considered restarted. Failure to observe
shutdown is reported as `network_control_router_shutdown_not_observed`;
failure to recover within the bounded policy timeout is reported as
`network_control_router_recovery_failed`.

Router restart profiles and policies remain disabled until the target router's
restart behavior is validated. Router and modem smart-plug actions remain
separate `switch_power_cycle` policies because a software restart and full
power interruption have different effects and recovery risks. A router power
target must remain disabled when turning the router off would also remove
Oracle's control path to turn the plug back on.

## Satellite Component Restarts

Satellite runtime and UI restarts are host-targeted service-control actions:

- `restart_runtime` restarts the allowlisted satellite runtime component;
- `restart_ui` restarts a separately managed Oracle UI/kiosk component.

These actions must use Oracle host identity, action-specific inventory
references, explicit confirmation, and bridge-owned platform mechanics. A
satellite without a separately managed Oracle UI must not receive a fabricated
`restart_ui` target. Host reboot, power cycle, arbitrary process termination,
and broad runtime refactors are outside this adapter.

Windows satellite components may use the bridge-owned
`windows_scheduled_task` adapter. Runtime tasks verify as `Running`; UI launcher
tasks may verify by a successful scheduled-task result because the launcher can
exit after starting the kiosk. Task names remain local bridge configuration and
must never be supplied by an API request or exposed in admin responses.
Dedicated Edge kiosk hosts may explicitly use bridge mode
`restart_edge_kiosk`, which stops only the `msedge` kiosk process before
starting the allowlisted UI task in interactive mode. Verification must confirm
an Edge process is running after the task's configured startup delay. This is
not a general process-control surface.

## Host Restart Adapter

`restart_host` is a host-level service-control action. The Oracle target must be
a curated host, and the local service-control host profile must separately
enable the action. The bridge owns the fixed platform command:

- Linux over SSH: fixed privileged reboot operation;
- Windows over SSH: fixed `shutdown.exe` restart operation;
- local Oracle Linux host: deferred privileged reboot after the response.

No policy, API request, UI state, user text, or model output may supply command
text or arguments. Remote restart success requires Oracle to observe the SSH
control transport go offline and then return within bounded shutdown and
recovery windows. ICMP alone is not sufficient because a managed host may block
ping while SSH and its Oracle runtime are healthy. A local Oracle host restart
reports deferred verification because the Brain cannot verify its own machine
after shutdown.

Remote host recovery is necessary but not sufficient for success. After SSH
returns, the service-control bridge must evaluate the host's allowlisted
readiness profile. A readiness profile may reference bridge-owned service keys
and fixed HTTP health checks. Linux services use existing systemd or Docker
status checks; Windows satellites use existing scheduled-task and Edge-process
checks. Satellite config health may use the fixed `8022` `/health/config`
endpoint. Oracle retries these read-only checks for a bounded policy timeout.

The network result records transport recovery and readiness separately:

- `verification_status`: host restart and transport recovery;
- `readiness_status`: expected services and health checks;
- `readiness_check_count` and `readiness_passed_count`: safe aggregate counts.

Provider URLs, units, container names, Windows task names, credentials, and
command arguments remain bridge-local and must not appear in network responses
or Activity records.

Host restart is intentionally separate from service restart and smart-plug
power cycle. It stops every service on the target machine and therefore
requires explicit confirmation plus target-specific operator warnings where
known storage or availability risks exist.

For a local Oracle Server reboot, the bridge must not explicitly stop Docker
containers that use `unless-stopped` restart policy. The operating system and
Docker perform graceful shutdown during reboot; an explicit pre-reboot stop
would persist as operator intent and prevent those containers from returning.
Remote hosts may still declare explicit `prepare_services` when their lifecycle
requires ordered shutdown and bridge-owned post-reboot restoration.

## Local Host Restart Completion

A local Oracle Server restart cannot return a verified result from the process
that schedules its own reboot. Before dispatch, Oracle must atomically persist
a pending local-restart record containing only the control request ID, Oracle
host/action identity, bounded readiness settings, lifecycle state, and the
current Linux boot ID. If that record cannot be written, reboot dispatch must
fail closed.

Brain startup may complete the pending request only when the Linux boot ID has
changed. A normal Brain service restart on the same boot must leave the request
pending and must not claim host recovery. After a changed boot, the
service-control bridge runs the configured host readiness profile. Readiness
may include curated services, fixed health endpoints, and fixed
`read_write_mounts`. A mount check passes only when the configured path is
mounted and a private temporary file can be created and removed there. Provider
mount flags alone are not sufficient because network filesystems may report
flags that do not reflect effective write access.

Startup writes a second final `network_control_confirm` event under the original
request ID:

- `executed` with verification and readiness `passed` when all checks pass;
- `failed` with `network_control_local_host_readiness_failed` when checks do not
  pass within the bounded timeout.

The pending record is cleared only after the final event is persisted. Startup
completion never retries the reboot or any provider action.

## Local Service Restart Completion

A local service restart that terminates the Brain process must persist a
pending restart record before scheduling the restart. The record contains only
the control request identity, Oracle target and bridge references, and the
current process start identity. If the record cannot be persisted, execution
fails closed and the restart is not scheduled.

After a new Brain process completes application startup, Oracle may complete
the pending request only when the process identity changed. It appends a second
final `network_control_confirm` event under the original request ID with
verification `passed`, then clears the pending record. A startup in the same
process must leave the record pending, and completion never retries the
service restart.

## Preconditions

Control policy may require provider-backed preconditions before an action can
be planned or executed. Preconditions are evidence checks, not actions.

Examples:

- Plex restart requires `plex_no_active_streams`
- future audiobook service restart may require no active audiobook playback
- future power-cycle actions may require the host to already be down and a
  confirmed power target to exist

A host restart must inherit the required preconditions from every curated
`restart_service` policy whose service belongs to that host. Host-specific
preconditions are combined with inherited service preconditions and duplicate
IDs are evaluated once. This prevents a host reboot from bypassing a
service-level safety policy. For example, Oracle Server restart must fail while
Plex has active streams even though the operator selected the host rather than
the Plex service. Provider-discovered services outside Oracle's curated
inventory do not create inherited policy.

Precondition result fields:

- `id`: Oracle-owned precondition id
- `provider`: provider used for evidence, when applicable
- `status`: `passed`, `failed`, `unknown`, or `unavailable`
- `observed_value`: safe scalar diagnostic value
- `summary`: safe human-readable explanation

Dry-run must block when a required precondition is failed or unavailable. A
mutating execution path must re-check required preconditions immediately before
execution, because state can change after dry-run.

## Audit Record

Every control request must produce an audit record with safe diagnostic detail.

Required audit fields:

- `request_id`
- `requested_at`
- `actor`
- `source`
- `target_type`
- `target_id`
- `action_id`
- `mode`: `dry_run` or `execute`
- `policy_status`
- `confirmation_status`
- `provider`
- `result_status`
- `started_at`
- `finished_at`
- `error_class`
- `summary`

Audit summaries must be safe for System Mode display and logs. They must not
contain secrets, command strings, raw provider URLs, or provider payload dumps.

`network_control_started` and its final `network_control_confirm` event must use
the Oracle control `request_id` as their Memory `correlation_id`. The final
event may persist safe precondition summaries, lifecycle phase summaries,
verification state, failure class, and rollback/recovery summaries. It must not
persist bridge command text, credentials, resolved systemd/container targets,
mount paths, RAID identifiers, stdout, stderr, or raw provider observations.

During Brain startup, a durable `network_control_started` event with no final
`network_control_confirm` event for the same request ID must be reconciled into
one synthetic final outcome:

- `result_status: interrupted`;
- `error_class: network_control_interrupted_by_restart`;
- `execution.verification_status: unknown`;
- a safe summary stating that the provider outcome is unknown;
- safe lifecycle phase and precondition summaries when they were present on
  the started event.

Interruption reconciliation is reporting only. Oracle must not retry the
provider action, restore its old execution lease, restore its old cooldown, or
assume the action failed. A later attempt must use a new request ID, fresh
precondition evidence, and explicit confirmation. Reconciliation must be
idempotent: once the synthetic final event exists, later startups must not
create another one.

## Recent Result Read Model

The admin Network status payload may include the latest confirmed control
outcome for a matching Oracle target/action pair. Oracle Memory
`network_control_confirm` events are the durable source of truth. The bounded
process-local read model is restored from those events during Brain startup.

Rules:

- dry-run requests must not update the recent-result read model;
- confirm requests may update the recent-result read model after the final
  control result is known, including executed, failed, blocked, denied, or
  not-executed outcomes;
- results are keyed by `target_type`, `target_id`, and `action_id`;
- startup first reconciles unmatched `network_control_started` events, then
  selects only the newest final event for each key;
- `GET /api/admin/network/status` may expose `last_control_result` on the
  matching host/service row and matching `control_actions` entry;
- the read model must remain diagnostics-safe and expose only Oracle identity,
  request/result status, timestamps, safe summary text, adapter/provider labels,
  and safe execution status fields such as verification status.

The recent-result read model must not expose command arguments, systemd unit
names, Docker container names, host addresses, credentials, stdout, stderr,
tokens, raw provider URLs, or arbitrary provider payloads.

Execution leases and cooldowns remain process-local safety state. They are not
reconstructed from audit history after Brain restart.

## Control Coverage

`GET /api/admin/network/control/actions` classifies every configured policy
action for Stage 4 closure:

- `verified`: enabled, correctly configured, and backed by a durable executed
  final event with `execution.verification_status: passed`;
- `enabled_unverified`: enabled and correctly configured, but no qualifying
  durable verification exists;
- `disabled`: present in policy but disabled;
- `misconfigured`: inventory, confirmation, bridge, readiness, lifecycle, or
  power-target requirements are incomplete or invalid.

When an action reports `execution.readiness_status`, it qualifies as verified
only when readiness also passed. Deferred self-restarts, failed, blocked,
denied, interrupted, not-executed, and confirmation-only outcomes do not count
as verification. A newer failure or interruption does not erase an earlier
durable successful verification; coverage reports whether the action has ever
completed its required verification path.

Coverage may expose the stable Oracle request ID and verification timestamp.
It must not expose credentials, commands, provider-native targets, host
addresses, systemd units, container names, provider output, or raw evidence.
Coverage is diagnostic only and must not execute, enable, disable, retry, or
otherwise mutate a control action.

## Stage 3 Boundary

`GET /api/admin/network/status` remains read-only. It must not return executable
action URLs, shell commands, restart buttons, confirmation tokens, or provider
credentials.

Read-only status payloads expose descriptive readiness metadata only. Network
control uses separate planned, allowlisted, confirmed, and audited execution
surfaces defined by this contract.

## V2 Configuration Reconciliation

Private network-control, service-control, and router-control JSON files and
environment selectors are obsolete V1 migration inputs, not reusable runtime
authority. Canonical ownership is inventory identity in
`domains/network/inventory.yaml`, allowlist and safety policy in `policy.yaml`,
and host/provider translation in `adapters.yaml`.

Policy cannot contain shell commands, scripts, raw credentials, provider URLs,
provider-native targets, systemd units, container names, or Windows task names.
Provider-native identifiers may exist only at the adapter edge and never become
caller-supplied execution text.

Canonical adapter construction follows enabled inventory and policy edges. An
enabled graceful lifecycle pulls its explicitly typed readiness, prepare,
client-release, and storage service adapters into the same required adapter and
secret closure; activation must not defer discovery of a missing supporting
credential until execution. A Home Assistant power edge reuses the enabled
canonical Home Assistant provider connection and may not create a second HA
credential authority. Constructing these typed edges performs no action and
does not bypass confirmation, plan approval, preconditions, or verification.

Canonical policy construction retains only enabled actions and recoveries.
Each action binds its exact Oracle inventory target and selected typed adapter;
an enabled power-cycle action cannot bind an individually disabled power
target. Each recovery retains its `plan` approval requirement, profile
identities, and enabled UI/voice trigger policy. Profile IDs remain definition
identity in this seam and do not imply an executable profile registry.
Construction grants no control capability, creates no recovery preview or run,
and performs no diagnosis or remediation.
