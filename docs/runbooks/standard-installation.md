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

## Pre-Promotion Live Candidate Acceptance

Every completed stage that is intended for clean-core, GitHub, or production
promotion must pass one live candidate acceptance gate before protected core
history, a release tag, or a GitHub Release is changed. This is a managed
installation lifecycle operation, not permission to launch a private checkout
against production state.

The gate occurs in this order:

1. Complete and commit the private stage. Pass its full private regression,
   ownership classification with zero unclassified paths, and coverage gates.
2. Materialize an exact disposable clean-core candidate and matching household
   artifact, but do not update protected core history, create or move a release
   tag, publish a GitHub Release, or claim production promotion.
3. Pass clean-core CI, artifact round-trip and pair verification, supported-host
   preflight, and copied-production upgrade/migration, interrupted-recovery,
   rollback/downgrade, and re-upgrade rehearsal.
4. Record the current complete production activation and the exact candidate
   core, tree, household, environment, configuration, secret-generation, and
   migration/recovery identities. Review a managed rollback plan before live
   mutation. If candidate writes or schema changes make restoration of the
   previous complete activation uncertain, stop and return the architecture or
   migration decision to the operator.
5. Stage and assemble the exact candidate through the managed lifecycle. Use
   the managed `update` transaction to quiesce the current Brain and run the
   candidate on the normal production port. Do not start `uvicorn`, a repository
   virtual environment, or other checkout code directly against live stores.
6. Run a bounded real-household acceptance matrix. Satellites must reconnect
   through their normal paths. Exercise representative alert delivery and
   acknowledgement, media interruption/restoration, a uniquely named disposable
   calendar create/edit/delete cycle, one explicitly reversible safe device
   action, and relevant authenticated read/write paths. Use the household's
   designated disposable Windows lab mule first for disruptive Windows
   runtime, audio, display, kiosk, service, or power work; add only the Linux, personal/common,
   or integration representatives needed by the stage's actual changes.
   Leave a working candidate runtime on that disposable lab mule after testing
   unless rollback itself is under test, the candidate obstructs further work,
   or an older known baseline is required. This exception does not apply to the
   managed Brain rollback or to normal household satellites.
7. Verify cleanup, idempotency, projection/configuration health, reconnect
   behavior, no duplicate delivery, restored media/device state, and absence of
   test calendar or alert residue. Record exact request, occurrence, audit, and
   activation identities without exposing private payloads or secrets.
8. Roll back through the managed lifecycle to the previously recorded complete
   production activation. Verify installed status, configuration identity,
   Brain health, representative satellite health, and the rollback-compatible
   durable state before promotion. A failed acceptance or failed restoration
   returns the candidate to development.
9. Only after the gate passes may protected clean-core history, the immutable
   tag, and the GitHub Release be promoted. The final managed production
   activation of the exact released artifact is the re-upgrade/return leg and
   must still pass independent status, health, fleet, integration, and soak
   checks.
10. Before declaring the stage closed, determine whether the released core
    changed satellite-distributed runtime content. If it did, audit every
    enabled satellite against an exact manifest derived from the released
    artifact, roll the required runtime to the real fleet through the
    household's existing Linux or Windows deployment method, and record exact
    installed provenance. Each updated host must pass runtime/control process
    health, current projection acceptance, configuration health, bounded
    source-local behavior appropriate to the changed runtime, and cleanup.
    Representative pre-promotion acceptance proves the candidate architecture;
    it does not substitute for installing required runtime content on every
    enabled satellite installation. If the release has no satellite runtime
    delta, record that result explicitly instead of inventing a rollout.
    When the household artifact declares satellite asset overlays, build each
    satellite's exact installed manifest as the released core satellite
    payload plus those household-owned destinations. Copy the complete core
    runtime first, then overlay only the declared household assets at their
    canonical runtime destinations before restarting the host. Verify both
    source artifact identities and the composed manifest. Household-private
    assets must remain in the household artifact and must never be copied into
    clean core or a public release; the core files remain distributable
    fallbacks for households that do not declare an override.
    Before changing a Windows task's execution root, inventory every
    root-relative host dependency used by its preserved launcher. Native music
    and long-form audiobook playback require `ffplay` or `mpv`; if the launcher
    resolves it below `tools/ffmpeg/bin`, preserve that complete host-local
    payload at the same relative path in the new root or configure an explicit
    validated absolute player path. A clean control-service health response
    proves command availability, not executable availability: require
    executable/version/hash evidence and one representative real playback
    start/stop. Record these files separately from the released 62-file runtime
    manifest as installed host-local dependency provenance.
    When restarting a Windows scheduled task, do not assume a fixed delay after
    `Stop-ScheduledTask` is sufficient. Poll until the old task is definitively
    stopped/`Ready`, then start it and independently prove the new listener and
    owning process. With `MultipleInstances=IgnoreNew`, starting while the old
    instance is still stopping can be ignored and later resemble an application
    crash; `0xC000013A` is also the expected result of the deliberate hard stop.
    The tracked Windows projection-sync and startup-check wrappers enforce this
    wait and fail closed when a stopped task does not reach `Ready`; deployment
    tooling must not replace that with a fixed sleep.

Schedule the gate inside a bounded maintenance window. Account for alerts due
before, during, and immediately after Brain downtime; do not create broad
whole-house effects merely to increase coverage. Every external mutation needs
a known initial state, a typed reversible action where available, verification,
and explicit cleanup or restoration evidence.

This gate reconciles existing evidence rather than duplicating or weakening it:

- the copied-production migration/recovery/rollback rehearsal remains a
  prerequisite and is not replaced by live success;
- the temporary live candidate activation and rollback satisfy the existing
  pre-promotion managed update/rollback exercise;
- the final released activation supplies the existing return/re-upgrade proof;
- clean-core CI, artifact verification, failed/interrupted recovery coverage,
  final post-activation health, fleet checks, and soak remain mandatory;
- ordinary fleet health cannot prove satellite runtime version parity, so an
  exact released-artifact manifest/provenance audit and any required satellite
  rollout are separate closure evidence; and
- architectural changes that make live rollback, data restoration, exact
  provenance, or bounded external cleanup questionable require fresh operator
  approval rather than silent modification of this sequence.

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

If `update-assemble-plan` reports `implicit_configuration_change_forbidden`,
do not bypass the blocker. A normal update must keep installation and
configuration selectors coherent. For the exceptional case where the running
older core cannot normalize the new household schema, create one explicit
cross-version recovery capsule before changing canonical configuration. The
capsule pins the exact staged core, tree, environment, household deployment,
known-good installation, complete selected configuration and secret
generations, and every satellite projection selection:

```sh
TARGET_ENVIRONMENT_IDENTITY='oracle-python-environment-v1:sha256:<exact-digest>'

sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json schema-transition-plan \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --environment-identity "$TARGET_ENVIRONMENT_IDENTITY" \
  > /tmp/oracle-schema-transition-plan.json

SCHEMA_TRANSITION_PLAN='oracle-schema-transition-plan-v1:sha256:<exact-digest>'
sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" --json schema-transition-prepare \
  --core-artifact "$CORE_ARTIFACT" \
  --household-artifact "$HOUSEHOLD_ARTIFACT" \
  --environment-identity "$TARGET_ENVIRONMENT_IDENTITY" \
  --approved-plan "$SCHEMA_TRANSITION_PLAN" \
  > /tmp/oracle-schema-transition-prepare.json
```

The plan and preparation are explicitly elevated because the complete
selection includes separately protected secret-generation identity. Only after
the approved capsule is durable, stop the service and use the exact newly
staged application and environment to perform the canonical transaction
offline against both standard installed stores:

```sh
sudo systemctl stop oracle-brain.service
sudo /usr/bin/setpriv --reuid=oracle --regid=oracle \
  --groups=oracle-admin,<exact-profile-supplementary-groups> -- \
  "$TARGET_ENVIRONMENT/bin/python" -B \
  "$TARGET_APPLICATION/scripts/oracle-config.py" \
  --offline-store /srv/oracle/configuration \
  --offline-secret-store /srv/oracle/secrets \
  activate --candidate "$TARGET_DEPLOYMENT/configuration" \
  --expected-secret-generation "$SELECTED_SECRET_GENERATION"
```

Use the exact supplementary groups already validated by the selected
installation profile. Review the candidate online or offline first. The next
selection change must be exactly one canonical activation to the capsule's
target revision with the same selected secret generation. Then regenerate
`update-assemble-plan`; it accepts the temporary selector interval only when
the capsule, selected configuration, staged artifact pair, and environment all
match exactly.

```sh
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
verification fails during an explicit schema transition, the approved plan
first restores the captured configuration and every projection selection,
then restores the prior complete installation selection, restarts it, and
verifies the recovered runtime before sealing recovery as successful. Inspect
the result: `verified` means the candidate became known-good;
`recovered_failed` means the complete prior compatible set was restored and
verified, and the candidate did not become known-good.

If any operation fails after `schema-transition-prepare` but before a managed
`update` transaction begins, recover the captured set with:

```sh
sudo "$ORACLE_PYTHON" -B "$ORACLE_ADMIN" schema-transition-recover
```

Do not delete or edit the capsule, independently move either selector, use it
for a configuration no-op, or use it to authorize rollback across a
configuration boundary. Those remain rejected normal-lifecycle states.

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
operation or restores the prior complete activation. For an explicit schema
transition, `update-recover` also restores the capsule's exact configuration,
secret generation, and complete projection map before the installation, then
verifies the recovered runtime before writing successful recovery evidence. It
must not guess a new combination from separate component histories. After
recovery, run `status`, inspect `journalctl -u oracle-brain.service`, and repeat
the health checks.

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
