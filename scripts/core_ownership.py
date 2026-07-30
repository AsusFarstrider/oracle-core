#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any
import unicodedata


FORMAT_VERSION = 1
MATERIALIZATION_FORMAT_VERSION = 1
PROMOTED_CLASSES = frozenset({"core_direct", "core_curated_derivative"})
CLASSES = frozenset(
    {
        *PROMOTED_CLASSES,
        "household_deployment",
        "secret",
        "runtime_generated",
        "private_development",
        "obsolete_private_compatibility",
        "archive_history",
        "private_third_party",
        "external_dependency",
        "forbidden",
    }
)
ALLOWED_SOURCE_MODES = frozenset({"100644", "100755", "120000", "160000"})


class OwnershipError(RuntimeError):
    pass


def _git(repo: Path, *arguments: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise OwnershipError(message or f"git {' '.join(arguments)} failed")
    return process.stdout


def _decode_path(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OwnershipError("tracked paths must be valid UTF-8") from exc


def _parse_tree_entry(raw_entry: bytes) -> dict[str, str]:
    try:
        metadata, raw_path = raw_entry.split(b"\t", 1)
        mode, entry_type, object_id = metadata.decode("ascii").split(" ")
    except (ValueError, UnicodeDecodeError) as exc:
        raise OwnershipError("git returned an invalid tree entry") from exc
    path = _decode_path(raw_path)
    if mode not in ALLOWED_SOURCE_MODES:
        raise OwnershipError(f"unsupported Git mode {mode} for {path}")
    if entry_type not in {"blob", "commit"}:
        raise OwnershipError(f"unsupported Git entry type {entry_type} for {path}")
    if (mode == "160000") != (entry_type == "commit"):
        raise OwnershipError(f"inconsistent Git mode/type for {path}: {mode} {entry_type}")
    return {
        "path": path,
        "mode": mode,
        "type": "gitlink" if mode == "160000" else "symlink" if mode == "120000" else "file",
        "object_id": object_id,
    }


def resolve_commit(repo: Path, revision: str) -> str:
    return _git(
        repo,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{revision}^{{commit}}",
    ).decode("ascii").strip()


def inventory(repo: Path, revision: str) -> dict[str, Any]:
    commit = resolve_commit(repo, revision)
    tree = _git(repo, "rev-parse", f"{commit}^{{tree}}").decode("ascii").strip()
    raw_tree = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries = [_parse_tree_entry(item) for item in raw_tree.split(b"\0") if item]
    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    return {
        "format_version": FORMAT_VERSION,
        "source_commit": commit,
        "source_tree": tree,
        "entries": entries,
    }


def _validate_manifest_path(path: str, *, label: str) -> str:
    if not isinstance(path, str) or not path:
        raise OwnershipError(f"{label} must be a non-empty string")
    if "\\" in path or "\x00" in path:
        raise OwnershipError(f"{label} contains a prohibited character: {path!r}")
    candidate = PurePosixPath(path)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != path
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise OwnershipError(f"{label} must be a normalized relative path: {path!r}")
    return candidate.as_posix()


def _validate_destination(path: str) -> str:
    destination = _validate_manifest_path(path, label="destination")
    if any(part.casefold() == ".git" for part in PurePosixPath(destination).parts):
        raise OwnershipError(f"destination enters prohibited Git metadata: {destination}")
    return destination


def _manifest_blob(repo: Path, commit: str, manifest_path: str) -> tuple[str, bytes]:
    raw = _git(repo, "ls-tree", "-z", commit, "--", f":(literal){manifest_path}")
    records = [item for item in raw.split(b"\0") if item]
    if len(records) != 1:
        raise OwnershipError(f"manifest is not one tracked leaf in {commit}: {manifest_path}")
    entry = _parse_tree_entry(records[0])
    if entry["path"] != manifest_path or entry["type"] != "file":
        raise OwnershipError(f"manifest must be a tracked regular file: {manifest_path}")
    return entry["object_id"], _git(repo, "cat-file", "blob", entry["object_id"])


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OwnershipError(f"manifest contains duplicate JSON field {key!r}")
        result[key] = value
    return result


def _load_manifest(repo: Path, commit: str, manifest_path: str) -> tuple[dict[str, Any], dict[str, str]]:
    normalized_path = _validate_manifest_path(manifest_path, label="manifest path")
    blob_id, raw_manifest = _manifest_blob(repo, commit, normalized_path)
    try:
        parsed = json.loads(raw_manifest, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipError(f"manifest is not valid UTF-8 JSON: {normalized_path}") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"format_version", "entries"}:
        raise OwnershipError("manifest must contain exactly format_version and entries")
    if parsed["format_version"] != FORMAT_VERSION:
        raise OwnershipError(f"unsupported manifest format_version: {parsed['format_version']!r}")
    if not isinstance(parsed["entries"], list):
        raise OwnershipError("manifest entries must be a list")
    identity = {
        "path": normalized_path,
        "git_blob": blob_id,
        "sha256": hashlib.sha256(raw_manifest).hexdigest(),
    }
    return parsed, identity


def _reviewed_sources(
    raw_review: object,
    *,
    source_entries: dict[str, dict[str, str]],
    derivative_path: str,
) -> dict[str, Any]:
    if not isinstance(raw_review, dict) or set(raw_review) != {"status", "sources"}:
        raise OwnershipError(
            f"curated derivative {derivative_path} requires review status and sources"
        )
    if raw_review["status"] != "approved":
        raise OwnershipError(f"curated derivative {derivative_path} is not explicitly approved")
    sources = raw_review["sources"]
    if not isinstance(sources, list) or not sources:
        raise OwnershipError(f"curated derivative {derivative_path} has no reviewed sources")
    normalized_sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"path", "git_blob"}:
            raise OwnershipError(
                f"curated derivative {derivative_path} has an invalid reviewed source"
            )
        source_path = _validate_manifest_path(source["path"], label="reviewed source path")
        if source_path in seen:
            raise OwnershipError(
                f"curated derivative {derivative_path} repeats reviewed source {source_path}"
            )
        seen.add(source_path)
        tracked = source_entries.get(source_path)
        if tracked is None or tracked["type"] == "gitlink":
            raise OwnershipError(
                f"curated derivative {derivative_path} source is missing or unsupported: {source_path}"
            )
        expected_blob = source["git_blob"]
        if not isinstance(expected_blob, str) or tracked["object_id"] != expected_blob:
            raise OwnershipError(
                f"curated derivative {derivative_path} is stale for source {source_path}"
            )
        normalized_sources.append({"path": source_path, "git_blob": expected_blob})
    normalized_sources.sort(key=lambda item: item["path"].encode("utf-8"))
    return {"status": "approved", "sources": normalized_sources}


def _normalize_manifest_entries(
    raw_entries: list[object],
    *,
    source_entries: dict[str, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    destinations: dict[str, str] = {}
    portable_destinations: dict[str, str] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise OwnershipError("each manifest entry must be an object")
        allowed = {"path", "classification", "destination", "review"}
        unexpected = set(raw_entry) - allowed
        if unexpected:
            raise OwnershipError(f"manifest entry has unknown fields: {sorted(unexpected)}")
        source_path = _validate_manifest_path(raw_entry.get("path"), label="source path")
        if source_path in normalized:
            raise OwnershipError(f"source path is classified more than once: {source_path}")
        classification = raw_entry.get("classification")
        if classification not in CLASSES:
            raise OwnershipError(
                f"source path {source_path} has unknown classification {classification!r}"
            )
        tracked = source_entries.get(source_path)
        if tracked is None:
            raise OwnershipError(f"classification references a nonexistent source: {source_path}")
        normalized_entry: dict[str, Any] = {
            "path": source_path,
            "classification": classification,
        }
        has_destination = "destination" in raw_entry
        destination = raw_entry.get("destination")
        has_review = "review" in raw_entry
        review = raw_entry.get("review")
        if classification in PROMOTED_CLASSES:
            if not isinstance(destination, str):
                raise OwnershipError(f"promoted source lacks a destination: {source_path}")
            destination = _validate_destination(destination)
            if tracked["type"] == "gitlink":
                raise OwnershipError(f"Gitlink cannot be promoted: {source_path}")
            prior_source = destinations.get(destination)
            if prior_source is not None:
                raise OwnershipError(
                    f"core destination {destination} is targeted by {prior_source} and {source_path}"
                )
            for existing_destination, existing_source in destinations.items():
                if destination.startswith(f"{existing_destination}/") or existing_destination.startswith(
                    f"{destination}/"
                ):
                    raise OwnershipError(
                        f"core destinations overlap as file and descendant: "
                        f"{existing_destination} from {existing_source}, {destination} from {source_path}"
                    )
            portable_key = unicodedata.normalize("NFC", destination).casefold()
            prior_portable = portable_destinations.get(portable_key)
            if prior_portable is not None and prior_portable != destination:
                raise OwnershipError(
                    f"core destinations alias on case-insensitive filesystems: "
                    f"{prior_portable} and {destination}"
                )
            destinations[destination] = source_path
            portable_destinations[portable_key] = destination
            normalized_entry["destination"] = destination
            if classification == "core_curated_derivative":
                normalized_entry["review"] = _reviewed_sources(
                    review,
                    source_entries=source_entries,
                    derivative_path=source_path,
                )
            elif has_review:
                raise OwnershipError(f"direct promotion cannot declare derivative review: {source_path}")
        elif has_destination or has_review:
            raise OwnershipError(
                f"non-promoted source cannot declare destination or review: {source_path}"
            )
        normalized[source_path] = normalized_entry
    return normalized


def _blob_bytes(repo: Path, object_id: str) -> bytes:
    return _git(repo, "cat-file", "blob", object_id)


def _validate_symlink_target(*, destination: str, raw_target: bytes) -> str:
    try:
        target = raw_target.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OwnershipError(f"symlink target for {destination} is not UTF-8") from exc
    if not target or "\x00" in target or "\\" in target or posixpath.isabs(target):
        raise OwnershipError(f"symlink target for {destination} is unsafe: {target!r}")
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(destination), target))
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        raise OwnershipError(f"symlink target for {destination} escapes the core tree: {target!r}")
    return target


def _resolve_symlink_destination(
    destination: str,
    target: str,
    symlinks: dict[str, str],
) -> str:
    pending = (PurePosixPath(destination).parent / PurePosixPath(target)).parts
    hops = 0
    while True:
        resolved: list[str] = []
        index = 0
        restarted = False
        while index < len(pending):
            part = pending[index]
            index += 1
            if part in {"", "."}:
                continue
            if part == "..":
                if not resolved:
                    raise OwnershipError(f"symlink resolution escapes the core tree: {destination}")
                resolved.pop()
                continue
            resolved.append(part)
            candidate = "/".join(resolved)
            nested_target = symlinks.get(candidate)
            if nested_target is None:
                continue
            hops += 1
            if hops > len(symlinks):
                raise OwnershipError(f"symlink cycle detected from {destination}")
            pending = (
                PurePosixPath(candidate).parent / PurePosixPath(nested_target)
            ).parts + pending[index:]
            restarted = True
            break
        if not restarted:
            return "/".join(resolved)


def _validate_promoted_symlinks(
    repo: Path,
    ledger_entries: list[dict[str, Any]],
) -> None:
    promoted_paths = {entry["destination"] for entry in ledger_entries if "destination" in entry}
    symlinks: dict[str, str] = {}
    for entry in ledger_entries:
        if entry.get("source_type") != "symlink" or "destination" not in entry:
            continue
        symlinks[entry["destination"]] = _validate_symlink_target(
            destination=entry["destination"],
            raw_target=_blob_bytes(repo, entry["source_object"]),
        )
    for destination, target in symlinks.items():
        resolved = _resolve_symlink_destination(destination, target, symlinks)
        if destination == resolved or destination.startswith(f"{resolved}/"):
            raise OwnershipError(
                f"promoted symlink {destination} resolves to itself or an ancestor: {target!r}"
            )
        if resolved not in promoted_paths and not any(
            path.startswith(f"{resolved}/") for path in promoted_paths
        ):
            raise OwnershipError(
                f"promoted symlink {destination} is dangling after export: {target!r}"
            )


def resolve_ledger(repo: Path, revision: str, manifest_path: str) -> dict[str, Any]:
    source_inventory = inventory(repo, revision)
    commit = source_inventory["source_commit"]
    source_entries = {entry["path"]: entry for entry in source_inventory["entries"]}
    manifest, manifest_identity = _load_manifest(repo, commit, manifest_path)
    classifications = _normalize_manifest_entries(
        manifest["entries"],
        source_entries=source_entries,
    )
    missing = sorted(set(source_entries) - set(classifications), key=lambda path: path.encode("utf-8"))
    if missing:
        preview = ", ".join(missing[:8])
        suffix = "" if len(missing) <= 8 else f" (+{len(missing) - 8} more)"
        raise OwnershipError(f"tracked paths are unclassified: {preview}{suffix}")

    ledger_entries: list[dict[str, Any]] = []
    for source_path in sorted(source_entries, key=lambda path: path.encode("utf-8")):
        source = source_entries[source_path]
        classification = classifications[source_path]
        ledger_entry: dict[str, Any] = {
            **classification,
            "source_mode": source["mode"],
            "source_type": source["type"],
            "source_object": source["object_id"],
        }
        ledger_entries.append(ledger_entry)
    _validate_promoted_symlinks(repo, ledger_entries)

    ledger: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "source_commit": commit,
        "source_tree": source_inventory["source_tree"],
        "manifest": manifest_identity,
        "entries": ledger_entries,
    }
    normalized = json.dumps(
        ledger,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    ledger["ledger_sha256"] = hashlib.sha256(normalized).hexdigest()
    return ledger


def _git_blob_identity(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _promoted_entries(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (entry for entry in ledger["entries"] if "destination" in entry),
        key=lambda entry: entry["destination"].encode("utf-8"),
    )


def _write_candidate_entry(repo: Path, root: Path, entry: dict[str, Any]) -> None:
    destination = root / PurePosixPath(entry["destination"])
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    content = _blob_bytes(repo, entry["source_object"])
    if entry["source_type"] == "symlink":
        target = _validate_symlink_target(
            destination=entry["destination"],
            raw_target=content,
        )
        os.symlink(target, destination)
        return
    if entry["source_type"] != "file":
        raise OwnershipError(
            f"promoted source has unsupported materialization type: {entry['path']}"
        )
    with destination.open("xb") as handle:
        handle.write(content)
    destination.chmod(0o755 if entry["source_mode"] == "100755" else 0o644)


def _candidate_inventory(root: Path) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        retained_directories: list[str] = []
        for name in directory_names:
            candidate = base / name
            relative = candidate.relative_to(root).as_posix()
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(candidate)
                content = target.encode("utf-8")
                entries[relative] = {
                    "mode": "120000",
                    "type": "symlink",
                    "object_id": _git_blob_identity(content),
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "symlink_target": target,
                }
            elif stat.S_ISDIR(mode):
                retained_directories.append(name)
            else:
                raise OwnershipError(f"candidate contains unsupported entry type: {relative}")
        directory_names[:] = retained_directories
        for name in file_names:
            candidate = base / name
            relative = candidate.relative_to(root).as_posix()
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(candidate)
                content = target.encode("utf-8")
                entries[relative] = {
                    "mode": "120000",
                    "type": "symlink",
                    "object_id": _git_blob_identity(content),
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "symlink_target": target,
                }
            elif stat.S_ISREG(mode):
                content = candidate.read_bytes()
                executable = bool(mode & 0o111)
                entries[relative] = {
                    "mode": "100755" if executable else "100644",
                    "type": "file",
                    "object_id": _git_blob_identity(content),
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                }
            else:
                raise OwnershipError(f"candidate contains unsupported entry type: {relative}")
    return entries


def _candidate_git_tree(root: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="oracle-core-tree-") as temporary:
        git_dir = Path(temporary) / "git"
        init = subprocess.run(
            ["git", "init", "--quiet", "--bare", str(git_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if init.returncode:
            raise OwnershipError(
                init.stderr.decode("utf-8", errors="replace").strip()
                or "could not initialize temporary Git tree verifier"
            )
        environment = {
            **os.environ,
            "GIT_DIR": str(git_dir),
            "GIT_WORK_TREE": str(root),
        }
        for key, value in (
            ("core.autocrlf", "false"),
            ("core.filemode", "true"),
            ("core.symlinks", "true"),
        ):
            configured = subprocess.run(
                ["git", "config", key, value],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if configured.returncode:
                raise OwnershipError("could not configure temporary Git tree verifier")
        indexed = subprocess.run(
            ["git", "add", "--all", "--"],
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if indexed.returncode:
            raise OwnershipError(
                indexed.stderr.decode("utf-8", errors="replace").strip()
                or "could not index materialized candidate"
            )
        written = subprocess.run(
            ["git", "write-tree"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if written.returncode:
            raise OwnershipError(
                written.stderr.decode("utf-8", errors="replace").strip()
                or "could not write materialized candidate tree"
            )
        return written.stdout.decode("ascii").strip()


def _verify_candidate(
    root: Path,
    promoted: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    actual = _candidate_inventory(root)
    expected = {entry["destination"]: entry for entry in promoted}
    missing = sorted(set(expected) - set(actual), key=lambda path: path.encode("utf-8"))
    extra = sorted(set(actual) - set(expected), key=lambda path: path.encode("utf-8"))
    if missing or extra:
        raise OwnershipError(
            f"candidate path inventory differs from ledger; missing={missing[:8]}, extra={extra[:8]}"
        )
    exported: list[dict[str, Any]] = []
    for destination in sorted(expected, key=lambda path: path.encode("utf-8")):
        source = expected[destination]
        materialized = actual[destination]
        if materialized["mode"] != source["source_mode"]:
            raise OwnershipError(f"candidate mode differs from ledger: {destination}")
        if materialized["type"] != source["source_type"]:
            raise OwnershipError(f"candidate type differs from ledger: {destination}")
        if materialized["object_id"] != source["source_object"]:
            raise OwnershipError(f"candidate content differs from ledger: {destination}")
        record: dict[str, Any] = {
            "source_path": source["path"],
            "destination": destination,
            "classification": source["classification"],
            "mode": materialized["mode"],
            "type": materialized["type"],
            "git_blob": materialized["object_id"],
            "content_sha256": materialized["content_sha256"],
        }
        if materialized["type"] == "symlink":
            record["symlink_target"] = materialized["symlink_target"]
        exported.append(record)
    normalized = json.dumps(
        exported,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return exported, hashlib.sha256(normalized).hexdigest()


def materialize_candidate(
    repo: Path,
    revision: str,
    manifest_path: str,
    output: Path,
    ledger_output: Path,
) -> dict[str, Any]:
    resolved_repo = repo.resolve()
    resolved_output = output.resolve(strict=False)
    resolved_ledger_output = ledger_output.resolve(strict=False)
    if resolved_output.exists():
        raise OwnershipError(f"candidate output already exists: {resolved_output}")
    if resolved_ledger_output.exists():
        raise OwnershipError(f"candidate ledger already exists: {resolved_ledger_output}")
    if resolved_output == resolved_repo or resolved_repo in resolved_output.parents:
        raise OwnershipError("candidate output must be outside the private repository")
    if resolved_ledger_output == resolved_output or resolved_output in resolved_ledger_output.parents:
        raise OwnershipError("candidate ledger must remain outside the candidate tree")
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_ledger_output.parent.mkdir(parents=True, exist_ok=True)

    ledger = resolve_ledger(resolved_repo, revision, manifest_path)
    promoted = _promoted_entries(ledger)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{resolved_output.name}.staging-", dir=resolved_output.parent)
    )
    try:
        for entry in promoted:
            _write_candidate_entry(resolved_repo, staging, entry)
        exported, exported_tree_sha256 = _verify_candidate(staging, promoted)
        git_tree = _candidate_git_tree(staging)
        regular_files = sum(entry["type"] == "file" for entry in exported)
        executable_files = sum(entry["mode"] == "100755" for entry in exported)
        symlinks = sum(entry["type"] == "symlink" for entry in exported)
        top_level = sorted(
            {PurePosixPath(entry["destination"]).parts[0] for entry in exported},
            key=lambda path: path.encode("utf-8"),
        )
        candidate: dict[str, Any] = {
            "format_version": MATERIALIZATION_FORMAT_VERSION,
            "source_commit": ledger["source_commit"],
            "source_tree": ledger["source_tree"],
            "manifest": ledger["manifest"],
            "ownership_ledger_sha256": ledger["ledger_sha256"],
            "exported_tree_sha256": exported_tree_sha256,
            "git_tree": git_tree,
            "counts": {
                "exported_paths": len(exported),
                "regular_files": regular_files,
                "executable_files": executable_files,
                "symlinks": symlinks,
            },
            "top_level": top_level,
            "entries": exported,
        }
        identity_input = json.dumps(
            candidate,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        candidate["candidate_identity"] = (
            "oracle-core-candidate-v1:sha256:" + hashlib.sha256(identity_input).hexdigest()
        )
        serialized = (
            json.dumps(candidate, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        ledger_staging = resolved_ledger_output.with_name(
            f".{resolved_ledger_output.name}.staging-{os.getpid()}"
        )
        try:
            with ledger_staging.open("xb") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, resolved_output)
            os.replace(ledger_staging, resolved_ledger_output)
        finally:
            if ledger_staging.exists():
                ledger_staging.unlink()
        return candidate
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _print_json(value: dict[str, Any]) -> None:
    json.dump(value, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory and resolve Oracle core-promotion ownership from committed Git objects."
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Private repository path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory_parser = subparsers.add_parser("inventory", help="Print one committed tree inventory.")
    inventory_parser.add_argument("--source", required=True, help="Committed source revision.")

    resolve_parser = subparsers.add_parser("resolve", help="Resolve and validate the complete ledger.")
    resolve_parser.add_argument("--source", required=True, help="Committed source revision.")
    resolve_parser.add_argument("--manifest", required=True, help="Manifest path in that source revision.")

    materialize_parser = subparsers.add_parser(
        "materialize",
        help="Materialize one committed ownership ledger into a new empty core candidate tree.",
    )
    materialize_parser.add_argument("--source", required=True, help="Committed source revision.")
    materialize_parser.add_argument(
        "--manifest", required=True, help="Manifest path in that source revision."
    )
    materialize_parser.add_argument(
        "--output", required=True, type=Path, help="New candidate tree path. Must not exist."
    )
    materialize_parser.add_argument(
        "--ledger-output",
        required=True,
        type=Path,
        help="New path for the complete exported-path ledger. Must not exist.",
    )

    args = parser.parse_args()
    try:
        if args.command == "inventory":
            _print_json(inventory(args.repo.resolve(), args.source))
        elif args.command == "resolve":
            _print_json(resolve_ledger(args.repo.resolve(), args.source, args.manifest))
        else:
            candidate = materialize_candidate(
                args.repo.resolve(),
                args.source,
                args.manifest,
                args.output,
                args.ledger_output,
            )
            _print_json(
                {
                    key: value
                    for key, value in candidate.items()
                    if key != "entries"
                }
                | {
                    "candidate_path": str(args.output.resolve()),
                    "ledger_path": str(args.ledger_output.resolve()),
                }
            )
    except OwnershipError as exc:
        print(f"core ownership error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
