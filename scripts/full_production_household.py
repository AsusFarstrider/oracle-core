#!/usr/bin/env python3
"""Materialize the fixed full-production household artifact with pinned local assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat

from household_deployment import (
    DeploymentError,
    _git,
    _json_bytes,
    _safe_relative,
    deployment_revision_for_basis,
    materialize,
    resolve,
)


PROFILE = "full-production-brain"
ASSET_FORMAT = 1


def _load_asset_manifest(repo: Path, commit: str, path: str) -> tuple[dict[str, object], str]:
    relative = _safe_relative(path, label="full-production asset manifest")
    raw = _git(repo, "show", f"{commit}:{relative.as_posix()}")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeploymentError("full-production asset manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("format_version") != ASSET_FORMAT:
        raise DeploymentError("unsupported full-production asset manifest")
    return manifest, hashlib.sha256(raw).hexdigest()


def _validated_assets(manifest: dict[str, object]) -> list[dict[str, object]]:
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise DeploymentError("full-production asset manifest has no assets")
    assets: list[dict[str, object]] = []
    ids: set[str] = set()
    destinations: set[str] = set()
    for raw in raw_assets:
        if not isinstance(raw, dict):
            raise DeploymentError("full-production asset entry must be an object")
        asset_id = raw.get("id")
        source_value = raw.get("source_path")
        destination_value = raw.get("destination")
        digest = raw.get("sha256")
        size = raw.get("size")
        mode = raw.get("mode")
        if (
            not isinstance(asset_id, str)
            or not asset_id
            or asset_id in ids
            or not isinstance(source_value, str)
            or not Path(source_value).is_absolute()
            or not isinstance(destination_value, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or not isinstance(size, int)
            or size < 1
            or mode != "100644"
        ):
            raise DeploymentError("invalid full-production asset declaration")
        destination = _safe_relative(destination_value, label="asset destination").as_posix()
        if destination in destinations:
            raise DeploymentError("duplicate full-production asset destination")
        source = Path(source_value)
        if source.is_symlink():
            source = source.resolve(strict=True)
        if not source.is_file() or stat.S_IMODE(source.stat().st_mode) & 0o022:
            raise DeploymentError(f"full-production asset is absent or writable by an untrusted group: {asset_id}")
        content_digest = hashlib.sha256()
        observed_size = 0
        with source.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                observed_size += len(chunk)
                content_digest.update(chunk)
        if observed_size != size or content_digest.hexdigest() != digest:
            raise DeploymentError(f"full-production asset identity differs: {asset_id}")
        ids.add(asset_id)
        destinations.add(destination)
        assets.append(
            {
                "destination": destination,
                "id": asset_id,
                "mode": mode,
                "sha256": digest,
                "size": size,
                "source_path": str(source),
                "type": "file",
            }
        )
    return sorted(assets, key=lambda item: str(item["destination"]))


def resolve_full_production(
    repo: Path,
    source: str,
    authority_path: str,
    household_id: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    ledger = resolve(repo, source, authority_path, household_id)
    if ledger.get("installation_profiles") != [PROFILE]:
        raise DeploymentError("full-production materialization requires its exact installation profile")
    basis = ledger.get("revision_basis")
    if not isinstance(basis, dict):
        raise DeploymentError("household ledger has no revision basis")
    production = basis.get("full_production_brain")
    if not isinstance(production, dict) or not isinstance(production.get("asset_manifest"), str):
        raise DeploymentError("household deployment lacks full-production asset authority")
    manifest, manifest_sha256 = _load_asset_manifest(
        repo,
        str(ledger["source_commit"]),
        str(production["asset_manifest"]),
    )
    assets = _validated_assets(manifest)
    existing_destinations = {str(item["destination"]) for item in ledger["entries"]}
    if existing_destinations & {str(item["destination"]) for item in assets}:
        raise DeploymentError("full-production assets overlap committed household payload")
    external_entries = [
        {
            "destination": item["destination"],
            "mode": item["mode"],
            "sha256": item["sha256"],
            "source": f"external:{item['id']}",
            "type": "file",
        }
        for item in assets
    ]
    ledger["entries"] = sorted([*ledger["entries"], *external_entries], key=lambda item: item["destination"])
    basis["entries"] = sorted(
        [
            {key: value for key, value in item.items() if key in {"destination", "mode", "sha256", "target", "type"}}
            for item in ledger["entries"]
        ],
        key=lambda item: item["destination"],
    )
    production["asset_manifest_sha256"] = manifest_sha256
    production["provider_asset_count"] = len(assets)
    production["provider_asset_bytes"] = sum(int(item["size"]) for item in assets)
    ledger["deployment_revision"] = deployment_revision_for_basis(basis)
    ledger["ledger_sha256"] = hashlib.sha256(_json_bytes({key: value for key, value in ledger.items() if key != "ledger_sha256"})).hexdigest()
    return ledger, assets


def materialize_full_production(
    repo: Path,
    ledger: dict[str, object],
    assets: list[dict[str, object]],
    output: Path,
) -> None:
    external_destinations = {str(item["destination"]) for item in assets}
    tracked = {
        **ledger,
        "entries": [item for item in ledger["entries"] if str(item["destination"]) not in external_destinations],
    }
    materialize(repo, tracked, output)
    for asset in assets:
        destination = output.joinpath(*PurePosixPath(str(asset["destination"])).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(asset["source_path"]), destination)
        destination.chmod(0o644)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != asset["sha256"]:
            raise DeploymentError(f"materialized full-production asset differs: {asset['id']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source", required=True)
    parser.add_argument("--authority", default="ops/households/authority.json")
    parser.add_argument("--household", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger-output", type=Path, required=True)
    args = parser.parse_args()
    ledger, assets = resolve_full_production(args.repo, args.source, args.authority, args.household)
    materialize_full_production(args.repo, ledger, assets, args.output)
    args.ledger_output.parent.mkdir(parents=True, exist_ok=True)
    args.ledger_output.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"deployment_revision": ledger["deployment_revision"], "asset_count": len(assets)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
