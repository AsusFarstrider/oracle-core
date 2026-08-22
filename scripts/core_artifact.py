#!/usr/bin/env python3
"""Build and verify Oracle's simple uncompressed distribution artifacts."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tarfile
import tempfile
import shutil


FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
PAYLOAD_PREFIX = "payload/"
HOUSEHOLD_REVISION_PREFIX = "oracle-household-deployment-v1:sha256:"


class ArtifactError(RuntimeError):
    pass


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def _safe_relative(path: str) -> PurePosixPath:
    raw_parts = path.split("/")
    value = PurePosixPath(path)
    if not path or value.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
        raise ArtifactError(f"unsafe relative path: {path!r}")
    return value


def _safe_symlink(path: PurePosixPath, target: str) -> None:
    target_path = PurePosixPath(target)
    if not target or target_path.is_absolute():
        raise ArtifactError(f"unsafe symlink target for {path}: {target!r}")
    resolved: list[str] = list(path.parent.parts)
    for part in target_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise ArtifactError(f"symlink escapes payload: {path} -> {target}")
            resolved.pop()
        else:
            resolved.append(part)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _household_revision(basis: dict[str, object]) -> str:
    return HOUSEHOLD_REVISION_PREFIX + hashlib.sha256(_json_bytes(basis)).hexdigest()


def _git_entries(repo: Path, revision: str) -> tuple[str, str, list[dict[str, str]]]:
    commit = _git(repo, "rev-parse", f"{revision}^{{commit}}").decode().strip()
    tree = _git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    raw = _git(repo, "ls-tree", "-rz", "--full-tree", "-r", commit)
    entries: list[dict[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, entry_type, object_id = metadata.decode().split()
        path = raw_path.decode("utf-8", "surrogateescape")
        _safe_relative(path)
        if entry_type != "blob" or mode not in {"100644", "100755", "120000"}:
            raise ArtifactError(f"unsupported Git entry {mode} {entry_type} at {path}")
        content = _git(repo, "cat-file", "blob", object_id)
        item = {
            "path": path,
            "type": "symlink" if mode == "120000" else "file",
            "mode": mode,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if mode == "120000":
            target = content.decode("utf-8", "surrogateescape")
            _safe_symlink(PurePosixPath(path), target)
            item["target"] = target
        entries.append(item)
    return commit, tree, entries


def build(repo: Path, revision: str, output: Path) -> dict[str, object]:
    commit, tree, entries = _git_entries(repo, revision)
    manifest: dict[str, object] = {
        "format_version": FORMAT_VERSION,
        "artifact_kind": "oracle-core",
        "core_commit": commit,
        "core_git_tree": tree,
        "inventory": entries,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(manifest_bytes)
        info.mode = 0o644
        info.mtime = 0
        archive.addfile(info, io.BytesIO(manifest_bytes))
        for item in entries:
            path = str(item["path"])
            tar_info = tarfile.TarInfo(PAYLOAD_PREFIX + path)
            tar_info.mtime = 0
            if item["type"] == "symlink":
                tar_info.type = tarfile.SYMTYPE
                tar_info.mode = 0o777
                tar_info.linkname = str(item["target"])
                archive.addfile(tar_info)
            else:
                content = _git(repo, "show", f"{commit}:{path}")
                tar_info.size = len(content)
                tar_info.mode = 0o755 if item["mode"] == "100755" else 0o644
                archive.addfile(tar_info, io.BytesIO(content))
    return manifest


def _payload_inventory(root: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        _safe_relative(relative.as_posix())
        if path.is_symlink():
            target = path.readlink().as_posix()
            _safe_symlink(relative, target)
            content = target.encode("utf-8", "surrogateescape")
            item = {"path": relative.as_posix(), "type": "symlink", "mode": "120000", "sha256": hashlib.sha256(content).hexdigest(), "target": target}
        elif path.is_file():
            content = path.read_bytes()
            item = {
                "path": relative.as_posix(),
                "type": "file",
                "mode": "100755" if path.stat().st_mode & stat.S_IXUSR else "100644",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        else:
            raise ArtifactError(f"unsupported payload entry: {path}")
        entries.append(item)
    return entries


def _write_archive(output: Path, manifest: dict[str, object], payload_root: Path) -> None:
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(manifest_bytes)
        info.mode = 0o644
        info.mtime = 0
        archive.addfile(info, io.BytesIO(manifest_bytes))
        for item in manifest["inventory"]:
            assert isinstance(item, dict)
            path = str(item["path"])
            source = payload_root.joinpath(*PurePosixPath(path).parts)
            tar_info = tarfile.TarInfo(PAYLOAD_PREFIX + path)
            tar_info.mtime = 0
            if item["type"] == "symlink":
                tar_info.type = tarfile.SYMTYPE
                tar_info.mode = 0o777
                tar_info.linkname = str(item["target"])
                archive.addfile(tar_info)
            else:
                content = source.read_bytes()
                tar_info.size = len(content)
                tar_info.mode = 0o755 if item["mode"] == "100755" else 0o644
                archive.addfile(tar_info, io.BytesIO(content))


def build_household(ledger_path: Path, payload_root: Path, output: Path) -> dict[str, object]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    basis = ledger.get("revision_basis")
    if not isinstance(basis, dict):
        raise ArtifactError("household ledger lacks revision_basis")
    revision = _household_revision(basis)
    if revision != ledger.get("deployment_revision"):
        raise ArtifactError("household ledger revision does not match revision basis")
    expected_entries = basis.get("entries")
    if not isinstance(expected_entries, list):
        raise ArtifactError("household revision basis lacks entries")
    inventory = _payload_inventory(payload_root)
    normalized_expected = []
    for item in expected_entries:
        if not isinstance(item, dict) or not isinstance(item.get("destination"), str):
            raise ArtifactError("malformed household revision inventory")
        normalized_expected.append({"path" if key == "destination" else key: value for key, value in item.items()})
    if inventory != normalized_expected:
        raise ArtifactError("household payload does not match deployment revision inventory")
    core = basis.get("core")
    if not isinstance(core, dict):
        raise ArtifactError("household revision basis lacks core pin")
    deployment = {key: value for key, value in basis.items() if key != "entries"}
    manifest: dict[str, object] = {
        "format_version": FORMAT_VERSION,
        "artifact_kind": "oracle-household",
        "deployment_revision": revision,
        "required_core_commit": core.get("commit"),
        "required_core_git_tree": core.get("git_tree"),
        "deployment": deployment,
        "inventory": inventory,
    }
    _write_archive(output, manifest, payload_root)
    return manifest


def _blob_identity(content: bytes) -> bytes:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).digest()


def _tree_identity(root: Path) -> str:
    def walk(directory: Path) -> bytes:
        body = bytearray()
        children = sorted(
            directory.iterdir(),
            key=lambda p: p.name.encode("utf-8", "surrogateescape") + (b"/" if p.is_dir() and not p.is_symlink() else b""),
        )
        for child in children:
            name = child.name.encode("utf-8", "surrogateescape")
            if child.is_symlink():
                mode = b"120000"
                object_id = _blob_identity(child.readlink().as_posix().encode("utf-8", "surrogateescape"))
            elif child.is_dir():
                mode = b"40000"
                object_id = walk(child)
            elif child.is_file():
                mode = b"100755" if child.stat().st_mode & stat.S_IXUSR else b"100644"
                object_id = _blob_identity(child.read_bytes())
            else:
                raise ArtifactError(f"unsupported staged entry: {child}")
            body.extend(mode + b" " + name + b"\0" + object_id)
        return hashlib.sha1(b"tree " + str(len(body)).encode() + b"\0" + body).digest()

    return walk(root).hex()


def _verify_into(archive_path: Path, root: Path) -> dict[str, object]:
    with tarfile.open(archive_path, "r:") as archive:
        members = archive.getmembers()
        names: set[str] = set()
        for member in members:
            if member.name in names:
                raise ArtifactError(f"duplicate archive member: {member.name}")
            names.add(member.name)
            if member.name == MANIFEST_NAME:
                if not member.isfile():
                    raise ArtifactError("manifest.json must be a regular file")
                continue
            if not member.name.startswith(PAYLOAD_PREFIX):
                raise ArtifactError(f"entry outside manifest.json and payload/: {member.name}")
            relative = _safe_relative(member.name[len(PAYLOAD_PREFIX):])
            if member.issym():
                _safe_symlink(relative, member.linkname)
            elif not member.isfile():
                raise ArtifactError(f"unsupported archive entry type: {member.name}")
        if MANIFEST_NAME not in names:
            raise ArtifactError("missing manifest.json")
        raw_manifest = archive.extractfile(MANIFEST_NAME)
        if raw_manifest is None:
            raise ArtifactError("unreadable manifest.json")
        manifest = json.load(raw_manifest)
        kind = manifest.get("artifact_kind")
        if manifest.get("format_version") != FORMAT_VERSION or kind not in {"oracle-core", "oracle-household"}:
            raise ArtifactError("unsupported artifact manifest")
        inventory = manifest.get("inventory")
        if not isinstance(inventory, list):
            raise ArtifactError("manifest inventory must be a list")
        declared = {str(item.get("path")): item for item in inventory if isinstance(item, dict)}
        if len(declared) != len(inventory):
            raise ArtifactError("duplicate or malformed manifest inventory")
        actual_names = {name[len(PAYLOAD_PREFIX):] for name in names if name.startswith(PAYLOAD_PREFIX)}
        if actual_names != set(declared):
            raise ArtifactError("archive payload does not match declared inventory")
        for path, item in declared.items():
            relative = _safe_relative(path)
            member = archive.getmember(PAYLOAD_PREFIX + path)
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                if item.get("type") != "symlink" or item.get("mode") != "120000" or item.get("target") != member.linkname:
                    raise ArtifactError(f"symlink inventory mismatch: {path}")
                destination.symlink_to(member.linkname)
                content = member.linkname.encode("utf-8", "surrogateescape")
            else:
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ArtifactError(f"unreadable payload member: {path}")
                content = extracted.read()
                expected_mode = "100755" if member.mode & 0o111 else "100644"
                if item.get("type") != "file" or item.get("mode") != expected_mode:
                    raise ArtifactError(f"file inventory mismatch: {path}")
                destination.write_bytes(content)
                destination.chmod(0o755 if expected_mode == "100755" else 0o644)
            if item.get("sha256") != hashlib.sha256(content).hexdigest():
                raise ArtifactError(f"content hash mismatch: {path}")
        if kind == "oracle-core":
            tree = _tree_identity(root)
            if tree != manifest.get("core_git_tree"):
                raise ArtifactError(f"Git tree mismatch: expected {manifest.get('core_git_tree')}, got {tree}")
        else:
            deployment = manifest.get("deployment")
            if not isinstance(deployment, dict):
                raise ArtifactError("household artifact lacks deployment identity")
            basis = {**deployment, "entries": [{"destination" if key == "path" else key: value for key, value in item.items()} for item in inventory]}
            if _household_revision(basis) != manifest.get("deployment_revision"):
                raise ArtifactError("household deployment revision mismatch")
            core = deployment.get("core")
            if not isinstance(core, dict) or core.get("commit") != manifest.get("required_core_commit") or core.get("git_tree") != manifest.get("required_core_git_tree"):
                raise ArtifactError("household core pin metadata mismatch")
    return manifest


def extract_verified(archive_path: Path, destination: Path) -> dict[str, object]:
    """Safely materialize one verified payload into a new disposable directory."""

    if destination.exists() or destination.is_symlink():
        raise ArtifactError(f"verified extraction destination already exists: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    try:
        return _verify_into(archive_path, destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def verify(archive_path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="oracle-core-artifact-") as temp:
        return _verify_into(archive_path, Path(temp))


def verify_pair(core_archive: Path, household_archive: Path) -> dict[str, object]:
    core = verify(core_archive)
    household = verify(household_archive)
    if core.get("artifact_kind") != "oracle-core" or household.get("artifact_kind") != "oracle-household":
        raise ArtifactError("artifact pair must contain one core and one household artifact")
    if household.get("required_core_commit") != core.get("core_commit") or household.get("required_core_git_tree") != core.get("core_git_tree"):
        raise ArtifactError("household artifact core pin does not match supplied core artifact")
    return {"core": core, "household": household}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--repo", type=Path, default=Path.cwd())
    build_parser.add_argument("--source", default="HEAD")
    build_parser.add_argument("--output", type=Path, required=True)
    household_parser = subparsers.add_parser("build-household")
    household_parser.add_argument("--ledger", type=Path, required=True)
    household_parser.add_argument("--payload-root", type=Path, required=True)
    household_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("archive", type=Path)
    pair_parser = subparsers.add_parser("verify-pair")
    pair_parser.add_argument("core_archive", type=Path)
    pair_parser.add_argument("household_archive", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.repo, args.source, args.output)
    elif args.command == "build-household":
        result = build_household(args.ledger, args.payload_root, args.output)
    elif args.command == "verify-pair":
        pair = verify_pair(args.core_archive, args.household_archive)
        print(json.dumps({
            "result": "verified",
            "core_commit": pair["core"]["core_commit"],
            "core_git_tree": pair["core"]["core_git_tree"],
            "deployment_revision": pair["household"]["deployment_revision"],
        }, indent=2, sort_keys=True))
        return 0
    else:
        result = verify(args.archive)
    summary = {
        "artifact_kind": result["artifact_kind"],
        "payload_entries": len(result["inventory"]),
        "result": "built" if args.command.startswith("build") else "verified",
    }
    for key in ("core_commit", "core_git_tree", "deployment_revision", "required_core_commit", "required_core_git_tree"):
        if key in result:
            summary[key] = result[key]
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
