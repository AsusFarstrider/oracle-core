# Oracle Administration CLI

`scripts/oracle-admin.py` is the standard host-local operator interface for a
managed Debian Brain installation. It is a non-daemon command surface separate
from the persistent Brain process. The [standard installation runbook](../runbooks/standard-installation.md)
provides the end-to-end procedure; this document defines command behavior and
authority.

## Invocation

Before first installation, invoke the CLI from a checksum-verified core
artifact extracted into a disposable directory. Only `preflight`,
`stage-plan`, and `stage` execute with the discovered host Python, and those
bootstrap commands import only Python standard-library installation modules.

After staging, commands re-execute through the exact immutable application and
Python environment selected by the target artifact or staged/active complete
activation. After activation, the stable invocation is:

```sh
/srv/oracle/selection/active/environment/bin/python -B \
  /srv/oracle/selection/active/application/scripts/oracle-admin.py COMMAND
```

Place global `--json` before the subcommand. JSON output uses the versioned
`oracle-admin-output-v1` format. Human output is for direct operator use and is
not a machine API.

## Plans And Mutation

Read-only commands never mutate the installation. Every installation,
assembly, service, activation, update, and rollback mutation has a matching
plan command. A plan records the relevant current and proposed identities,
compatibility, service interruption, validation, preservation, and recovery
posture and returns an immutable `plan.identity`.

The apply command requires that exact identity through `--approved-plan` and
rechecks the plan assumptions. A stale plan fails rather than applying against
changed artifacts, selections, configuration, or host state. Scripted use must
therefore parse the JSON plan, deliberately authorize its exact identity, and
retain both plan and result as evidence.

Automatic failed-activation recovery is part of the approved activation plan
and does not wait for a second confirmation.

## Authority

An enrolled `oracle-admin` operator may execute the managed CLI and inspect the
non-secret immutable and lifecycle metadata required by `status` and
`diagnostics-export`. That group does not grant mutation, raw secret access,
service-private data access, package management, account management, systemd
definition changes, or general host authority.

Use explicit sudo/root authority for commands documented as mutating. The CLI
does not leave behind a privileged daemon, setuid launcher, reusable root
shell, or broad authorization rule.

| Command family | Normal authority | Mutation |
| --- | --- | --- |
| `preflight`, non-secret `*-plan`, `status` | enrolled operator where an installation exists | none |
| `update-assemble-plan` | explicit elevation | none; reads the selected secret generation for complete-activation validation |
| `diagnostics-export` | enrolled operator | creates one redacted operator-owned output file outside `/srv/oracle` |
| `stage`, `assemble`, `update-assemble` | explicit elevation | managed host/install state |
| `service-install` | explicit elevation | fixed systemd definition and daemon reload |
| `activate`, `activate-recover` | explicit elevation | service and complete activation selection |
| `update`, `update-recover`, `rollback` | explicit elevation | service and complete activation selection |

## Command Reference

### `preflight`

Required arguments: `--core-artifact`, `--household-artifact`.

Performs read-only host discovery, complete artifact validation, core-pin
agreement, platform/profile compatibility, prerequisite checks, and proposed
dependency reporting. Unknown platforms are reported as experimental;
concrete incompatibilities remain blockers.

### `stage-plan` / `stage`

Required arguments: both artifact paths and `--operator-account`; `stage` also
requires `--approved-plan`. `stage` accepts
`--allow-unsupported-platform` only as explicit acknowledgement of a platform
with no concrete blocker.

Stages verified immutable application and household revisions, discovers or
acquires declared host dependencies, creates/reuses the isolated locked Python
environment, creates/validates Oracle identities and permissions, and enrolls
only the explicitly named operator. It does not activate Oracle.

### `assemble-plan` / `assemble`

Required arguments: both artifact paths and `--environment-identity`;
`assemble` also requires `--approved-plan`. The plan reports
`required_safety_acknowledgements`; pass each exact listed ID with a repeated
`--acknowledge` argument. Missing, additional, or unknown acknowledgements fail
before assembly mutates the installation.
The fixed full-production profile also requires `--runtime-compatibility-store`
on both commands. It must identify an existing canonical configuration store
whose exact accepted report set matches and validates every enabled satellite;
the reports remain runtime evidence and never become household policy.

Validates the complete initial combination, installs canonical configuration
and secret generations, publishes one immutable complete activation record,
and selects it as `staged`. It does not install or start the systemd service.

### `update-assemble-plan` / `update-assemble`

Arguments match initial assembly. These commands build a complete update
activation around already staged protected components while preserving the
currently selected configuration activation when compatible. The candidate is
selected only as `staged`. `update-assemble-plan` requires explicit elevation:
although read-only, it validates the existing selected secret generation and
must not broaden `oracle-admin` access to raw secret material.

### `service-plan` / `service-install`

`service-plan` takes no arguments. `service-install` requires
`--approved-plan`.

Installs the fixed stable `oracle-brain.service` definition and reloads
systemd. It never embeds a particular core, environment, household, or
configuration identity in the unit and does not independently enable/start the
service.

### `activate-plan` / `activate`

`activate-plan` takes no arguments. `activate` requires `--approved-plan`.

Performs first activation of the complete staged record, enables/starts the
systemd service, and verifies process state, readiness, runtime health,
configuration identity, deterministic provider-free interaction, and required
web surfaces. Known-good selectors advance only after verification.

### `activate-recover`

Takes no arguments. Recovers an interrupted initial-activation transaction by
validating durable transaction evidence and either completing the candidate or
restoring the prior safe selection. It requires elevation because selection
and service state may change.

### `update-plan` / `update`

`update-plan` takes no arguments and plans activation of the already assembled
`staged` record. `update` requires `--approved-plan`.

The operation quiesces Oracle, atomically selects the complete candidate,
restarts through systemd, and performs required verification. Failure triggers
the plan's automatic complete-record recovery. Result `verified` approves the
candidate; `recovered_failed` records that the candidate failed and the prior
known-good activation was restored.

### `update-recover`

Takes no arguments. Recovers the durable transaction for an interrupted update
or rollback. It never reconstructs a combination from independent component
pointers.

### `rollback-plan` / `rollback`

Both require `--activation-id`; `rollback` also requires `--approved-plan`.

Validates and selects one intact compatible prior complete activation. It does
not copy files over the current revision. Migration compatibility and any
rollback limitation must be resolved by the plan before mutation.

### `status`

Takes no arguments and performs no mutation. It distinguishes systemd state,
Oracle readiness and health, active/staged/approved/previous-known-good
records, exact core and household identities, configuration readiness,
platform/profile state, and managed-content drift. Raw secret values are never
returned.

### `diagnostics-export`

Requires `--output` naming a new file in an existing directory outside
`/srv/oracle`. The export includes redacted status and non-secret evidence
inventory, is mode `0600`, and excludes secrets, durable service-private data,
caches, temporary contents, and journal contents.

## Serialization, Exit, And Evidence

Mutating operations are serialized through the managed maintenance lock.
Concurrent mutation fails rather than interleaving installation, activation,
recovery, or rollback.

Successful JSON results identify the command, status, mutation disposition,
and applicable immutable identities. A command failure returns a nonzero exit
and reports whether mutation may have occurred. When mutation may have
occurred, use the applicable recovery command before preparing another plan.

Plans, staging records, dependency/environment evidence, activation results,
recovery results, and rollback results are retained beneath the declared
installation/control evidence surfaces. `diagnostics-export` is the supported
way to copy their non-secret diagnostic representation out of managed storage.

## Boundaries Not Supplied By This CLI

The CLI does not download releases, discover a `latest` version, access the
private household authority, install external providers, edit arbitrary files,
expose host administration over the network, or merge downstream code changes.
Artifacts arrive through a separate operator-controlled delivery mechanism and
are validated identically regardless of transport.

Routine online configuration/secret operations use Oracle's structured
host-local control plane and shared transaction engine. They do not grant the
persistent Brain package, systemd, account, or general host authority. The
Stage 4 `minimal-brain` installation requires no secret values and does not
claim a comprehensive backup/restore product.
