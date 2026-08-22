# Standard Debian Brain Installation

This runbook is the supported operator procedure for installing Oracle's
`minimal-brain` profile on Debian 13/amd64 from local release artifacts. It
also defines the corresponding update, recovery, rollback, verification, and
clean-reinstall paths.

The procedure uses one exact core artifact, one exact matching household
deployment artifact, and separately supplied secret material. The validated
minimal household profile requires no secret values. A target needs no Git
checkout, GitHub credential, or access to the private deployment authority.

Read alongside:

- [administration CLI reference](../reference/administration-cli.md);
- [dependency profiles](../reference/dependency-profiles.md);
- [canonical configuration setup](../config/setup.md); and
- [standard Debian service lifecycle](service-deployment.md).

## Validated Baseline And Authority

The physically validated Stage 4 tuple is Debian 13 on amd64, using systemd,
the `minimal-brain` profile, and host-local HTTP ingress. Other platforms are
experimental: preflight must identify the unsupported tuple, and mutation
requires the explicit `--allow-unsupported-platform` acknowledgement when no
concrete blocker exists.

The artifact manifests' exact core commit, Git tree, and household deployment
revision are authoritative. A release tag is only an operator-facing locator.
The target records the immutable identities resolved from the artifacts.

Installation may acquire declared dependencies from configured authenticated
Debian and Python package repositories. For `minimal-brain`, the installer
discovers and validates the host Python, installs a missing Debian `venv`
facility when required, and builds an immutable hash-locked production
environment. It does not modify the host Python package environment.

## Before Oracle Mutates The Host

Prepare:

- the uncompressed Oracle core tar artifact;
- the matching household deployment tar artifact;
- separately supplied expected SHA-256 values;
- a local non-root operator account intended for explicit enrollment in
  `oracle-admin`; and
- explicit sudo or root authority for the mutating steps.

The host must provide ordinary Debian tools used by bootstrap: `python3`,
`tar`, `sha256sum`, `dpkg`, `apt-get`, and systemd. Preflight reports missing
mandatory facilities and performs no mutation.

Set restrictive local defaults and identify the inputs:

```sh
umask 077
CORE_ARTIFACT=/path/to/oracle-core.tar
HOUSEHOLD_ARTIFACT=/path/to/oracle-household.tar
OPERATOR_ACCOUNT="$(id -un)"
```

Verify both received archives against the expected checksums supplied through
the operator-controlled transfer process. For example, when the supplied
checksum file names the two local archives:

```sh
sha256sum --check /path/to/oracle-artifacts.sha256
```

Stop on any mismatch. Do not repair or rewrite an artifact on the target.

## Disposable Bootstrap

The first trustworthy administration CLI is obtained through the ratified
operator-assisted bootstrap exception. Extract only the checksum-verified core
archive, and only into a newly created disposable directory:

```sh
BOOTSTRAP_DIRECTORY="$(mktemp -d /tmp/oracle-bootstrap.XXXXXXXX)"
tar -xf "$CORE_ARTIFACT" -C "$BOOTSTRAP_DIRECTORY"
BOOTSTRAP_CLI="$BOOTSTRAP_DIRECTORY/payload/scripts/oracle-admin.py"
HOST_PYTHON=/usr/bin/python3
test -f "$BOOTSTRAP_DIRECTORY/manifest.json"
test -f "$BOOTSTRAP_CLI"
```

Do not extract either artifact into `/srv/oracle`, `/etc/systemd/system`, or
another managed location. This extraction establishes no Oracle identity or
approval. The staged CLI performs the authoritative safe-inventory, path,
mode, symlink, content, core-tree, household-revision, core-pin, platform, and
profile validation before mutation.

Bootstrap commands deliberately use only Python's standard library:

```sh
"$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json preflight \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  > /tmp/oracle-preflight.json
```

Review the complete result. It must report no blockers, `mutation_performed`
must be false, and the two artifact identities must agree. On an experimental
platform, review every stated assumption before authorizing mutation.

## Protected Staging

Every mutation consumes the exact identity of a separately generated plan.
Generate and review the protected-staging plan without elevation:

```sh
"$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json stage-plan \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --operator-account "$OPERATOR_ACCOUNT" \
  > /tmp/oracle-stage-plan.json
```

Copy the exact `plan.identity` value from that JSON and apply it deliberately:

```sh
STAGE_PLAN='oracle-operation-plan-v1:sha256:<exact-digest>'
sudo "$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json stage \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --operator-account "$OPERATOR_ACCOUNT" \
  --approved-plan "$STAGE_PLAN" \
  > /tmp/oracle-stage-result.json
```

For an acknowledged experimental platform, add
`--allow-unsupported-platform` only to the `stage` command. Never use that flag
to bypass an actual blocker.

Staging may acquire declared host dependencies; create and validate the
`oracle` service account and primary group plus the `oracle-admin` operator
group; explicitly enroll the selected operator; create `/srv/oracle`; and
publish the exact immutable application, deployment, and Python-environment
components. It does not install the service or select an active activation.

Record `environment.environment_identity` from the successful stage result:

```sh
ENVIRONMENT_IDENTITY='oracle-python-environment-v1:sha256:<exact-digest>'
```

## Initial Assembly

Post-staging commands automatically re-execute through the exact installed
application and immutable Python environment required by the requested
operation. Continue to invoke the staged CLI during first installation; it is
only the trusted entrypoint into that re-execution.

Plan and apply the complete initial activation assembly:

```sh
RUNTIME_COMPATIBILITY_STORE='/path/to/recovered/configuration-store'
"$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json assemble-plan \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --environment-identity "$ENVIRONMENT_IDENTITY" \
  --runtime-compatibility-store "$RUNTIME_COMPATIBILITY_STORE" \
  > /tmp/oracle-assemble-plan.json

ASSEMBLE_PLAN='oracle-operation-plan-v1:sha256:<exact-digest>'
# Repeat this argument for every exact ID listed by the reviewed plan. Omit it
# when required_safety_acknowledgements is empty.
ASSEMBLE_ACKNOWLEDGEMENT='mutating_control_enablement'
sudo "$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json assemble \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --environment-identity "$ENVIRONMENT_IDENTITY" \
  --approved-plan "$ASSEMBLE_PLAN" \
  --acknowledge "$ASSEMBLE_ACKNOWLEDGEMENT" \
  --runtime-compatibility-store "$RUNTIME_COMPATIBILITY_STORE" \
  > /tmp/oracle-assemble-result.json
```

The result must identify one immutable configuration activation, complete
installation activation, and staged selector. Assembly does not start Oracle.
The validated `minimal-brain` household has no required secret values; a
household artifact with unsatisfied mandatory secret references must fail
instead of manufacturing values.

## Install The Systemd Service

Generate and inspect the fixed-unit plan, then apply it with elevation:

```sh
"$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json service-plan \
  > /tmp/oracle-service-plan.json

SERVICE_PLAN='oracle-operation-plan-v1:sha256:<exact-digest>'
sudo "$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json service-install \
  --approved-plan "$SERVICE_PLAN" \
  > /tmp/oracle-service-result.json
```

This installs the stable Oracle systemd definition and reloads systemd. It
does not itself enable or start the service.

## Activate And Verify

Plan and execute initial activation:

```sh
"$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json activate-plan \
  > /tmp/oracle-activate-plan.json

ACTIVATE_PLAN='oracle-initial-activation-plan-v1:sha256:<exact-digest>'
sudo "$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" --json activate \
  --approved-plan "$ACTIVATE_PLAN" \
  > /tmp/oracle-activate-result.json
```

Activation enables and starts Oracle through systemd, verifies process state,
readiness, health, configuration identity, the deterministic provider-free
request, and the declared web surfaces, and marks the activation known-good
only after those checks pass. A failure does not become known-good.

After successful activation, use the stable managed CLI:

```sh
ORACLE_PYTHON=/srv/oracle/selection/active/environment/bin/python
ORACLE_ADMIN=/srv/oracle/selection/active/application/scripts/oracle-admin.py
"$ORACLE_PYTHON" -B "$ORACLE_ADMIN" status
```

The enrolled operator runs `status` without sudo. Confirm independently:

```sh
systemctl is-enabled oracle-brain.service
systemctl is-active oracle-brain.service
curl --fail --silent http://127.0.0.1:8011/health
curl --fail --silent http://127.0.0.1:8011/health/config
curl --fail --silent http://127.0.0.1:8011/ui/ >/dev/null
curl --fail --silent http://127.0.0.1:8011/admin/ >/dev/null
curl --fail --silent http://127.0.0.1:8011/ui/satellite >/dev/null
```

Remove the disposable bootstrap directory only after successful installation:

```sh
rm -r -- "$BOOTSTRAP_DIRECTORY"
```

The transferred artifacts are operator inputs outside managed Oracle storage;
retain or remove them according to the household's evidence policy.

## Reboot Verification

Record the active identity and boot ID, reboot through ordinary host authority,
then run the same status and HTTP checks after the host returns:

```sh
cat /proc/sys/kernel/random/boot_id
sudo systemctl reboot
```

The boot ID must change. The service must be enabled and active, `status` must
be healthy with no integrity findings, and the exact activation,
configuration, secret-generation, and durable-state identities must remain.

## Update From New Local Artifacts

Verify the new archive checksums first. The new household artifact must pin the
exact new core commit and tree. Use the currently active managed CLI for
protected staging:

```sh
"$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json stage-plan \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --operator-account "$OPERATOR_ACCOUNT" \
  > /tmp/oracle-update-stage-plan.json

UPDATE_STAGE_PLAN='oracle-operation-plan-v1:sha256:<exact-digest>'
sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json stage \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --operator-account "$OPERATOR_ACCOUNT" \
  --approved-plan "$UPDATE_STAGE_PLAN" \
  > /tmp/oracle-update-stage-result.json
```

Take the target environment identity from the stage result, then assemble the
new complete staged activation. Planning this update assembly is explicitly
elevated because it validates the currently selected secret generation while
building the complete activation; membership in `oracle-admin` does not grant
raw secret traversal.

```sh
TARGET_ENVIRONMENT_IDENTITY='oracle-python-environment-v1:sha256:<exact-digest>'

sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json update-assemble-plan \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --environment-identity "$TARGET_ENVIRONMENT_IDENTITY" \
  > /tmp/oracle-update-assemble-plan.json

UPDATE_ASSEMBLE_PLAN='oracle-operation-plan-v1:sha256:<exact-digest>'
sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json update-assemble \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --environment-identity "$TARGET_ENVIRONMENT_IDENTITY" \
  --approved-plan "$UPDATE_ASSEMBLE_PLAN" \
  > /tmp/oracle-update-assemble-result.json
```

Finally plan and activate the already assembled candidate:

```sh
"$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json update-plan \
  > /tmp/oracle-update-plan.json

UPDATE_PLAN='oracle-update-activation-plan-v1:sha256:<exact-digest>'
sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json update \
  --approved-plan "$UPDATE_PLAN" \
  > /tmp/oracle-update-result.json
```

The update quiesces Oracle, atomically selects the complete candidate, restarts
through systemd, and performs the same required verification. If required
verification fails, the approved plan automatically restores and verifies the
prior complete known-good activation. Inspect the result: `verified` means the
candidate became known-good; `recovered_failed` means the prior activation was
restored and the candidate did not become known-good.

Refresh `ORACLE_PYTHON` and `ORACLE_ADMIN` through `selection/active` after any
successful update or rollback.

## Interrupted Operations And Recovery

Automatic failed-activation recovery does not wait for new approval. If the
operator process or host is interrupted during initial activation, retain or
recreate the checksum-verified disposable bootstrap and run:

```sh
sudo "$HOST_PYTHON" -S -B "$BOOTSTRAP_CLI" activate-recover
```

For an interrupted update or rollback, run:

```sh
sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" update-recover
```

Recovery reads the durable transaction and either completes the validated
operation or restores the prior complete activation. It must not guess a new
combination from separate component histories. After recovery, run `status`,
inspect `journalctl -u oracle-brain.service`, and repeat the health checks.

When the installed CLI cannot be trusted or executed, repeat the checksum-
verified disposable bootstrap procedure with an approved core artifact. The
staged CLI remains subject to the same authoritative validation before any
managed mutation.

## Explicit Rollback

Use `status --json` to identify an intact compatible prior activation, normally
`previous-known-good`. Plan and apply rollback as one complete selection:

```sh
ROLLBACK_ACTIVATION='oracle-installation-activation-v1:sha256:<exact-digest>'

"$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json rollback-plan \
  --activation-id "$ROLLBACK_ACTIVATION" \
  > /tmp/oracle-rollback-plan.json

ROLLBACK_PLAN='oracle-rollback-activation-plan-v1:sha256:<exact-digest>'
sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json rollback \
  --activation-id "$ROLLBACK_ACTIVATION" \
  --approved-plan "$ROLLBACK_PLAN" \
  > /tmp/oracle-rollback-result.json
```

Rollback validates and selects the complete prior activation; it does not copy
old files over the current revision. A state migration that limits rollback
must be identified by the plan before mutation.

## Diagnostics, Drift, And Repair Boundary

An enrolled operator may inspect non-secret state and create a redacted
diagnostic export without elevation:

```sh
"$ORACLE_PYTHON" -B "$ORACLE_ADMIN" status
"$ORACLE_PYTHON" -B "$ORACLE_ADMIN" diagnostics-export \
  --output /tmp/oracle-diagnostics.json
```

The output path must be a new file outside `/srv/oracle`. Diagnostics exclude
raw secrets and service-private data. Direct modification of application
revisions, environments, deployments, activation records, selectors, systemd
definitions, or protected permissions is unsupported drift. Do not normalize
such drift by editing managed files in place.

Use explicit elevated maintenance to repair ownership or permissions. If a
managed installation cannot be trusted, preserve household-owned backups and
evidence, restore a clean Debian baseline, and perform this procedure again
from verified artifacts and separately supplied secrets. Stage 4 does not
promise an in-place merge of modified managed code or a comprehensive backup
product.

## Preservation And Support Boundary

Application revisions and environments are replaceable immutable components.
Household deployment revisions, authored configuration, secrets, installed
configuration/secret generations, activation history, deployment state, and
durable data follow their declared preservation and migration contracts.
Caches are reconstructible; temporary files are never recovery inputs.

The supported administration path never silently preserves or overwrites local
modifications to managed code. Apache-2.0 permits downstream modification, but
a modified installation is a custom posture outside Oracle's standard update,
recovery, and rollback guarantee.
