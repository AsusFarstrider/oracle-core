#!/usr/bin/env python3
"""Build and verify Oracle's simple uncompressed core tar artifact."""

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


FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
PAYLOAD_PREFIX = "payload/"


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


def verify(archive_path: Path) -> dict[str, object]:
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
        if manifest.get("format_version") != FORMAT_VERSION or manifest.get("artifact_kind") != "oracle-core":
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
        with tempfile.TemporaryDirectory(prefix="oracle-core-artifact-") as temp:
            root = Path(temp)
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
            tree = _tree_identity(root)
            if tree != manifest.get("core_git_tree"):
                raise ArtifactError(f"Git tree mismatch: expected {manifest.get('core_git_tree')}, got {tree}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--repo", type=Path, default=Path.cwd())
    build_parser.add_argument("--source", default="HEAD")
    build_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    result = build(args.repo, args.source, args.output) if args.command == "build" else verify(args.archive)
    summary = {
        "artifact_kind": result["artifact_kind"],
        "core_commit": result["core_commit"],
        "core_git_tree": result["core_git_tree"],
        "payload_entries": len(result["inventory"]),
        "result": "built" if args.command == "build" else "verified",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
