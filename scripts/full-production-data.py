#!/usr/bin/env python3
"""Plan and apply the fixed production-data migration into /srv/oracle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
SERVER_DIRECTORY = SCRIPT_DIRECTORY.parent / "server"
for path in (SCRIPT_DIRECTORY, SERVER_DIRECTORY):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core_artifact import extract_verified
from oracle_app.configuration import parse_secret_companion
from oracle_app.full_production_data import (
    file_sha256,
    migrate_copy,
    source_identity,
    tts_cache_impact,
)


PLAN_FORMAT = "oracle-full-production-data-plan-v1"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _normalized(value: object):
    return json.loads(json.dumps(value, sort_keys=True))


def build_plan(
    household_artifact: Path,
    secret_companion: Path,
    source_database: Path,
    legacy_tts_cache: Path,
    *,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    clock = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    secret_bytes = secret_companion.read_bytes()
    secret_snapshot = parse_secret_companion(secret_bytes)
    with tempfile.TemporaryDirectory(prefix="oracle-full-production-data-plan-") as temporary:
        root = Path(temporary)
        manifest = extract_verified(household_artifact, root / "household")
        deployment = manifest.get("deployment")
        if not isinstance(deployment, dict) or deployment.get("installation_profiles") != ["full-production-brain"]:
            raise RuntimeError("household artifact does not select the fixed full-production profile")
        preview_database = root / "preview.sqlite3"
        preview = migrate_copy(
            source_database,
            root / "household" / "configuration",
            secret_snapshot,
            preview_database,
            observed_at=clock,
            apply_retention=False,
        )
    basis = {
        "format": PLAN_FORMAT,
        "household_artifact": {
            "sha256": file_sha256(household_artifact),
            "deployment_revision": manifest["deployment_revision"],
        },
        "secret_companion_identity": "oracle-secret-companion-v1:sha256:" + hashlib.sha256(secret_bytes).hexdigest(),
        "sources": {
            "memory": source_identity(source_database),
            "tts_cache": tts_cache_impact(legacy_tts_cache),
        },
        "observed_at": clock.isoformat(),
        "preview": preview,
        "destination": "/srv/oracle/data/oracle-memory.sqlite3",
    }
    normalized = _normalized(basis)
    return {**normalized, "identity": f"{PLAN_FORMAT}:sha256:{hashlib.sha256(_json_bytes(normalized)).hexdigest()}"}


def apply_plan(
    plan: dict[str, object],
    household_artifact: Path,
    secret_companion: Path,
    source_database: Path,
    legacy_tts_cache: Path,
    *,
    root: Path = Path("/srv/oracle"),
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("production data migration requires elevated maintenance authority")
    expected = build_plan(
        household_artifact,
        secret_companion,
        source_database,
        legacy_tts_cache,
        observed_at=datetime.fromisoformat(str(plan["observed_at"])),
    )
    if expected != plan:
        raise RuntimeError("production data migration plan is stale or does not match exact sources")
    destination = root / "data" / "oracle-memory.sqlite3"
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("production data destination is not empty")
    with tempfile.TemporaryDirectory(prefix="oracle-full-production-data-", dir=root / "tmp") as temporary:
        prepared = Path(temporary) / "oracle-memory.sqlite3"
        with tempfile.TemporaryDirectory(prefix="oracle-full-production-household-") as extracted:
            extract_verified(household_artifact, Path(extracted) / "household")
            report = migrate_copy(
                source_database,
                Path(extracted) / "household" / "configuration",
                parse_secret_companion(secret_companion.read_bytes()),
                prepared,
                observed_at=datetime.fromisoformat(str(plan["observed_at"])),
                apply_retention=True,
            )
        comparison = _normalized({**report, "retention_applied": None})
        if comparison != expected["preview"]:
            raise RuntimeError("applied migration differs from the approved preview")
        prepared.chmod(0o600)
        oracle = __import__("pwd").getpwnam("oracle")
        os.chown(prepared, oracle.pw_uid, oracle.pw_gid)
        os.replace(prepared, destination)
    return {
        "status": "migrated",
        "plan_identity": plan["identity"],
        "destination": str(destination),
        "sha256": file_sha256(destination),
        "report": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--household-artifact", type=Path, required=True)
        command.add_argument("--secret-companion", type=Path, required=True)
        command.add_argument("--source-database", type=Path, required=True)
        command.add_argument("--legacy-tts-cache", type=Path, required=True)
        command.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan = build_plan(args.household_artifact, args.secret_companion, args.source_database, args.legacy_tts_cache)
        args.plan.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = plan
    else:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        result = apply_plan(plan, args.household_artifact, args.secret_companion, args.source_database, args.legacy_tts_cache)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
