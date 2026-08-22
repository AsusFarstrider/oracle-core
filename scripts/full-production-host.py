#!/usr/bin/env python3
"""Plan and reconcile the fixed full-production Brain host boundary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
SERVER_DIRECTORY = SCRIPT_DIRECTORY.parent / "server"
for path in (SCRIPT_DIRECTORY, SERVER_DIRECTORY):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core_artifact import extract_verified
from oracle_app.full_production_host import build_host_plan

SUDOERS_ARTIFACT_PATH = Path("assets/host/oracle-full-production.sudoers")


def _inputs(household_artifact: Path, root: Path) -> tuple[dict[str, object], Path]:
    household = root / "household"
    manifest = extract_verified(household_artifact, household)
    deployment = manifest.get("deployment")
    if not isinstance(deployment, dict):
        raise RuntimeError("household artifact has no deployment authority")
    sudoers = household / SUDOERS_ARTIFACT_PATH
    return deployment, sudoers


def build_plan(household_artifact: Path, secret_asset: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="oracle-full-production-host-") as temporary:
        deployment, sudoers = _inputs(household_artifact, Path(temporary))
        return build_host_plan(deployment, secret_asset, sudoers)


def apply_plan(plan: dict[str, object], household_artifact: Path, secret_asset: Path) -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("full-production host reconciliation requires elevated maintenance authority")
    with tempfile.TemporaryDirectory(prefix="oracle-full-production-host-") as extracted:
        deployment, sudoers_source = _inputs(household_artifact, Path(extracted))
        current = build_host_plan(deployment, secret_asset, sudoers_source)
        if current != plan:
            raise RuntimeError("full-production host plan is stale or does not match exact inputs")
        missing_groups = list(plan["groups"]["missing_for_oracle"])
        if missing_groups:
            subprocess.run(["usermod", "-a", "-G", ",".join(missing_groups), "oracle"], check=True)
        account = pwd.getpwnam("oracle")
        destination = Path(str(plan["secret_asset"]["destination"]))
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chown(destination.parent, account.pw_uid, account.pw_gid)
        destination.parent.chmod(0o700)
        temporary = destination.parent / f".{destination.name}.tmp-{os.getpid()}"
        try:
            shutil.copyfile(secret_asset, temporary)
            temporary.chmod(0o600)
            os.chown(temporary, account.pw_uid, account.pw_gid)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        subprocess.run(["visudo", "-cf", str(sudoers_source)], check=True)
        sudoers_destination = Path("/etc/sudoers.d/oracle-full-production")
        sudoers_temporary = sudoers_destination.parent / f".{sudoers_destination.name}.tmp-{os.getpid()}"
        try:
            shutil.copyfile(sudoers_source, sudoers_temporary)
            sudoers_temporary.chmod(0o440)
            os.chown(sudoers_temporary, 0, 0)
            subprocess.run(["visudo", "-cf", str(sudoers_temporary)], check=True)
            os.replace(sudoers_temporary, sudoers_destination)
        finally:
            sudoers_temporary.unlink(missing_ok=True)
    return {
        "status": "reconciled",
        "plan_identity": plan["identity"],
        "groups_added": missing_groups,
        "secret_asset": str(destination),
        "sudoers": str(sudoers_destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--household-artifact", type=Path, required=True)
        command.add_argument("--secret-asset", type=Path, required=True)
        command.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        result = build_plan(args.household_artifact, args.secret_asset)
        args.plan.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        result = apply_plan(plan, args.household_artifact, args.secret_asset)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
