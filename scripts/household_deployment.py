#!/usr/bin/env python3
"""Resolve and materialize one committed Oracle household deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
from typing import Any


FORMAT_VERSION = 1
REVISION_PREFIX = "oracle-household-deployment-v1:sha256:"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")
SUPPORTED_MODES = {"100644", "100755", "120000"}


class DeploymentError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def _safe_relative(value: str, *, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise DeploymentError(f"unsafe {label}: {value!r}")
    return path


def _safe_symlink(path: PurePosixPath, target: str) -> None:
    target_path = PurePosixPath(target)
    if not target or target_path.is_absolute():
        raise DeploymentError(f"unsafe symlink target for {path}: {target!r}")
    resolved = list(path.parent.parts)
    for part in target_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise DeploymentError(f"symlink escapes deployment: {path} -> {target}")
            resolved.pop()
        else:
            resolved.append(part)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def deployment_revision_for_basis(basis: dict[str, Any]) -> str:
    return REVISION_PREFIX + hashlib.sha256(_json_bytes(basis)).hexdigest()


def _load_json_blob(repo: Path, commit: str, path: str) -> tuple[dict[str, Any], str, str]:
    _safe_relative(path, label="JSON source path")
    raw = _git(repo, "show", f"{commit}:{path}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DeploymentError(f"JSON object required at {path}")
    blob = _git(repo, "rev-parse", f"{commit}:{path}").decode().strip()
    return value, blob, hashlib.sha256(raw).hexdigest()


def _tracked_entries(repo: Path, commit: str) -> dict[str, dict[str, str]]:
    raw = _git(repo, "ls-tree", "-rz", "--full-tree", "-r", commit)
    entries: dict[str, dict[str, str]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, entry_type, object_id = metadata.decode().split()
        path = raw_path.decode("utf-8", "surrogateescape")
        _safe_relative(path, label="tracked path")
        entries[path] = {"entry_type": entry_type, "mode": mode, "object_id": object_id}
    return entries


def _roots(authority: dict[str, Any]) -> dict[str, PurePosixPath]:
    if authority.get("format_version") != FORMAT_VERSION:
        raise DeploymentError("unsupported household authority format")
    households = authority.get("households")
    if not isinstance(households, list) or not households:
        raise DeploymentError("household authority must declare at least one household")
    roots: dict[str, PurePosixPath] = {}
    for item in households:
        if not isinstance(item, dict):
            raise DeploymentError("household authority entries must be objects")
        household_id = item.get("household_id")
        root_value = item.get("root")
        if not isinstance(household_id, str) or IDENTIFIER.fullmatch(household_id) is None:
            raise DeploymentError(f"invalid household ID: {household_id!r}")
        if household_id in roots:
            raise DeploymentError(f"duplicate household ID: {household_id}")
        if not isinstance(root_value, str):
            raise DeploymentError(f"missing root for household {household_id}")
        roots[household_id] = _safe_relative(root_value, label="household root")
    values = list(roots.items())
    for index, (left_id, left) in enumerate(values):
        for right_id, right in values[index + 1 :]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise DeploymentError(f"household roots overlap: {left_id} and {right_id}")
    return roots


def _authored_revision(configuration_root: PurePosixPath, entries: list[dict[str, Any]], repo: Path) -> str:
    role_entries = [item for item in entries if PurePosixPath(item["destination"]).is_relative_to(configuration_root)]
    digest = hashlib.sha256()
    digest.update(b"oracle-authored-v1\0")
    for item in sorted(role_entries, key=lambda value: value["destination"]):
        role = PurePosixPath(item["destination"]).relative_to(configuration_root).as_posix()
        content = _git(repo, "cat-file", "blob", item["object_id"])
        path_bytes = role.encode()
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"oracle-authored-v1:sha256:{digest.hexdigest()}"


def resolve(repo: Path, source: str, authority_path: str, household_id: str) -> dict[str, Any]:
    commit = _git(repo, "rev-parse", f"{source}^{{commit}}").decode().strip()
    source_tree = _git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    tracked = _tracked_entries(repo, commit)
    authority, authority_blob, authority_sha256 = _load_json_blob(repo, commit, authority_path)
    roots = _roots(authority)
    if household_id not in roots:
        raise DeploymentError(f"unknown household: {household_id}")
    root = roots[household_id]
    definition_path = (root / "deployment.json").as_posix()
    definition, definition_blob, definition_sha256 = _load_json_blob(repo, commit, definition_path)
    if definition.get("format_version") != FORMAT_VERSION or definition.get("household_id") != household_id:
        raise DeploymentError("household definition identity mismatch")

    core = definition.get("core")
    if not isinstance(core, dict) or not all(isinstance(core.get(key), str) and HEX40.fullmatch(core[key]) for key in ("commit", "git_tree")):
        raise DeploymentError("household definition requires exact core commit and Git tree")
    profiles = definition.get("installation_profiles")
    if not isinstance(profiles, list) or not profiles or any(not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None for value in profiles):
        raise DeploymentError("installation profiles must be a non-empty unique identifier list")
    if len(set(profiles)) != len(profiles):
        raise DeploymentError("duplicate installation profile")
    ingress = definition.get("ingress")
    if not isinstance(ingress, dict) or ingress.get("posture") not in {"host-local", "household-lan"}:
        raise DeploymentError("unsupported ingress posture")
    secrets = definition.get("logical_secret_requirements")
    if not isinstance(secrets, list) or any(not isinstance(value, str) or not value or value != value.upper() for value in secrets):
        raise DeploymentError("logical secret requirements must be uppercase identifiers")
    if len(set(secrets)) != len(secrets):
        raise DeploymentError("duplicate logical secret requirement")

    template = definition.get("template")
    if not isinstance(template, dict):
        raise DeploymentError("template provenance is required")
    template_commit = template.get("source_commit")
    template_path = template.get("manifest_path")
    if not isinstance(template_commit, str) or HEX40.fullmatch(template_commit) is None or not isinstance(template_path, str):
        raise DeploymentError("template provenance requires an exact source commit and manifest path")
    resolved_template_commit = _git(repo, "rev-parse", f"{template_commit}^{{commit}}").decode().strip()
    template_manifest, template_blob, template_sha256 = _load_json_blob(repo, resolved_template_commit, template_path)
    if template_manifest.get("template_id") != template.get("template_id"):
        raise DeploymentError("template ID does not match recorded provenance")
    if template.get("manifest_git_blob") != template_blob or template.get("manifest_sha256") != template_sha256:
        raise DeploymentError("template manifest identity does not match recorded provenance")

    authority_files = definition.get("authority_files")
    payload = definition.get("payload")
    if not isinstance(authority_files, list) or not isinstance(payload, list):
        raise DeploymentError("authority_files and payload lists are required")
    authority_sources: set[str] = set()
    for relative in authority_files:
        if not isinstance(relative, str):
            raise DeploymentError("authority file paths must be strings")
        authority_sources.add((root / _safe_relative(relative, label="authority file")).as_posix())
    entries: list[dict[str, Any]] = []
    payload_sources: set[str] = set()
    destinations: set[str] = set()
    for mapping in payload:
        if not isinstance(mapping, dict) or not isinstance(mapping.get("source"), str) or not isinstance(mapping.get("destination"), str):
            raise DeploymentError("payload mappings require source and destination")
        relative_source = _safe_relative(mapping["source"], label="payload source")
        destination = _safe_relative(mapping["destination"], label="payload destination")
        source_path = (root / relative_source).as_posix()
        if source_path in payload_sources:
            raise DeploymentError(f"duplicate payload source: {source_path}")
        if destination.as_posix() in destinations:
            raise DeploymentError(f"duplicate payload destination: {destination}")
        if source_path not in tracked:
            raise DeploymentError(f"missing tracked payload source: {source_path}")
        payload_sources.add(source_path)
        destinations.add(destination.as_posix())
        tracked_entry = tracked[source_path]
        if tracked_entry["entry_type"] != "blob" or tracked_entry["mode"] not in SUPPORTED_MODES:
            raise DeploymentError(
                f"unsupported household payload entry {tracked_entry['mode']} "
                f"{tracked_entry['entry_type']} at {source_path}"
            )
        content = _git(repo, "cat-file", "blob", tracked_entry["object_id"])
        item: dict[str, Any] = {
            "destination": destination.as_posix(),
            "mode": tracked_entry["mode"],
            "object_id": tracked_entry["object_id"],
            "sha256": hashlib.sha256(content).hexdigest(),
            "source": source_path,
            "type": "symlink" if tracked_entry["mode"] == "120000" else "file",
        }
        if tracked_entry["mode"] == "120000":
            target = content.decode("utf-8", "surrogateescape")
            _safe_symlink(destination, target)
            item["target"] = target
        entries.append(item)

    tracked_in_root = {path for path in tracked if PurePosixPath(path).is_relative_to(root)}
    for path in tracked_in_root:
        item = tracked[path]
        if item["entry_type"] != "blob" or item["mode"] not in SUPPORTED_MODES:
            raise DeploymentError(f"unsupported tracked household entry {item['mode']} {item['entry_type']} at {path}")
    classified = authority_sources | payload_sources
    if tracked_in_root != classified:
        missing = sorted(tracked_in_root - classified)
        nonexistent = sorted(classified - tracked_in_root)
        raise DeploymentError(f"household root classification mismatch; unclassified={missing}, nonexistent={nonexistent}")
    if definition_path not in authority_sources:
        raise DeploymentError("deployment.json must be explicitly authority-only")

    configuration = definition.get("configuration")
    if not isinstance(configuration, dict) or not isinstance(configuration.get("root"), str):
        raise DeploymentError("configuration root and revision are required")
    configuration_root = _safe_relative(configuration["root"], label="configuration root")
    authored_revision = _authored_revision(configuration_root, entries, repo)
    if configuration.get("authored_revision") != authored_revision:
        raise DeploymentError("canonical configuration revision does not match payload")

    compatibility = definition.get("compatibility")
    deployment_metadata = definition.get("deployment_metadata")
    generated_configuration_inputs = definition.get("generated_configuration_inputs")
    migrations = definition.get("migrations")
    if not isinstance(compatibility, dict) or not isinstance(deployment_metadata, dict):
        raise DeploymentError("compatibility and deployment metadata objects are required")
    if not isinstance(generated_configuration_inputs, dict) or not isinstance(migrations, list):
        raise DeploymentError("generated configuration inputs and migrations are required")

    revision_input = {
        "compatibility": compatibility,
        "configuration": {"authored_revision": authored_revision, "root": configuration_root.as_posix()},
        "core": core,
        "deployment_metadata": deployment_metadata,
        "entries": [
            {
                key: value
                for key, value in item.items()
                if key in {"destination", "mode", "sha256", "target", "type"}
            }
            for item in sorted(entries, key=lambda value: value["destination"])
        ],
        "generated_configuration_inputs": generated_configuration_inputs,
        "household_id": household_id,
        "ingress": ingress,
        "installation_profiles": profiles,
        "logical_secret_requirements": secrets,
        "migrations": migrations,
        "template": {"manifest_git_blob": template_blob, "manifest_sha256": template_sha256, "template_id": template_manifest["template_id"]},
    }
    deployment_revision = deployment_revision_for_basis(revision_input)
    ledger = {
        "authority": {"git_blob": authority_blob, "path": authority_path, "sha256": authority_sha256},
        "configuration": revision_input["configuration"],
        "core": core,
        "deployment_revision": deployment_revision,
        "entries": sorted(entries, key=lambda value: value["destination"]),
        "format_version": FORMAT_VERSION,
        "household_id": household_id,
        "ingress": ingress,
        "installation_profiles": profiles,
        "logical_secret_requirements": secrets,
        "revision_basis": revision_input,
        "source_commit": commit,
        "source_tree": source_tree,
        "template": {**revision_input["template"], "source_commit": resolved_template_commit},
    }
    ledger["ledger_sha256"] = hashlib.sha256(_json_bytes(ledger)).hexdigest()
    return ledger


def materialize(repo: Path, ledger: dict[str, Any], output: Path) -> None:
    if output.exists():
        if any(output.iterdir()) if output.is_dir() else True:
            raise DeploymentError(f"output must be a new empty directory: {output}")
    else:
        output.mkdir(parents=True)
    for item in ledger["entries"]:
        destination = output.joinpath(*PurePosixPath(item["destination"]).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = _git(repo, "cat-file", "blob", item["object_id"])
        if item["type"] == "symlink":
            destination.symlink_to(item["target"])
        else:
            destination.write_bytes(content)
            destination.chmod(0o755 if item["mode"] == "100755" else 0o644)
        if item["type"] == "file" and hashlib.sha256(destination.read_bytes()).hexdigest() != item["sha256"]:
            raise DeploymentError(f"materialized content mismatch: {item['destination']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("resolve", "materialize"):
        command = subparsers.add_parser(name)
        command.add_argument("--source", required=True)
        command.add_argument("--authority", default="ops/households/authority.json")
        command.add_argument("--household", required=True)
        if name == "materialize":
            command.add_argument("--output", type=Path, required=True)
        command.add_argument("--ledger-output", type=Path)
    args = parser.parse_args()
    ledger = resolve(args.repo, args.source, args.authority, args.household)
    if args.command == "materialize":
        materialize(args.repo, ledger, args.output)
    if args.ledger_output is not None:
        args.ledger_output.parent.mkdir(parents=True, exist_ok=True)
        args.ledger_output.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(ledger, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
