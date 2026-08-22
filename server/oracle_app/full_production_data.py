"""Deterministic Slice 10 migration into the standard full-production data root."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from .configuration import SecretSnapshot, inspect_candidate
from .memory.identity_reconciliation import reconcile_identities
from .memory.retention import retention_policy_from_configuration
from .memory.retention_executor import run_retention
from .memory.schema import ensure_schema


class FullProductionDataError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise FullProductionDataError(f"required production data source is absent or unsafe: {path}")
    return {"path": str(path), "size": path.stat().st_size, "sha256": file_sha256(path)}


def tts_cache_impact(path: Path) -> dict[str, object]:
    files = [] if not path.is_dir() or path.is_symlink() else [item for item in path.rglob("*") if item.is_file() and not item.is_symlink()]
    return {
        "path": str(path),
        "disposition": "discard_identity_unsafe_legacy_cache",
        "files": len(files),
        "bytes": sum(item.stat().st_size for item in files),
    }


def _sqlite_backup(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FullProductionDataError("migration destination must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def migrate_copy(
    source_database: Path,
    configuration_root: Path,
    secret_snapshot: SecretSnapshot,
    destination_database: Path,
    *,
    observed_at: datetime,
    apply_retention: bool,
) -> dict[str, object]:
    clock = observed_at.astimezone(timezone.utc)
    _sqlite_backup(source_database, destination_database)
    ensure_schema(destination_database)
    inspection = inspect_candidate(configuration_root, secret_snapshot=secret_snapshot)
    if not inspection.report.activation_eligible or inspection.bundle is None:
        raise FullProductionDataError("canonical full-production configuration is not activation eligible")
    bundle = inspection.bundle
    household = SimpleNamespace(
        users={item.id: item for item in bundle.household.users},
        sources={item.id: item for item in bundle.household.sources},
    )
    satellites = SimpleNamespace(
        satellites={item.id: item for item in bundle.satellites.satellites},
    )
    identities = reconcile_identities(
        household,
        satellites,
        db_path=destination_database,
        now=clock,
    )
    policy = retention_policy_from_configuration(
        bundle.roles["brain.yaml"].storage.memory.retention  # type: ignore[attr-defined]
    )
    dry_run = run_retention(policy, db_path=destination_database, now=clock, dry_run=True)
    if dry_run.blocked:
        raise FullProductionDataError("retention is blocked by unknown or incomplete data classes")
    applied = None
    if apply_retention:
        applied = run_retention(policy, db_path=destination_database, now=clock, dry_run=False)
        if applied.changed_count != dry_run.changed_count:
            raise FullProductionDataError("retention apply differs from the approved dry-run")
    with sqlite3.connect(destination_database) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        schema_versions = [str(row[0]) for row in connection.execute(
            "SELECT version FROM memory_schema_migrations ORDER BY version"
        )]
    if integrity != "ok" or foreign_keys:
        raise FullProductionDataError("migrated Memory database integrity validation failed")
    return {
        "configuration": {
            "authored_revision": inspection.authored_revision,
            "config_revision": inspection.normalized_candidate_revision,
        },
        "identity_reconciliation": asdict(identities),
        "retention": dry_run.as_dict(),
        "retention_applied": None if applied is None else applied.as_dict(),
        "database": {
            "integrity_check": integrity,
            "foreign_key_defects": len(foreign_keys),
            "schema_versions": schema_versions,
        },
    }
