#!/usr/bin/env python3
"""Protected immutable-component staging for standard Oracle installations."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
from typing import Mapping

from core_artifact import (
    ArtifactError,
    _payload_inventory,
    _tree_identity,
    extract_verified,
)
from oracle_app.installation_identity import (
    ENVIRONMENT_FORMAT,
    ENVIRONMENT_PREFIX,
    environment_directory_name,
)


ENVIRONMENT_BUILD_MARKER = ".oracle-environment-building.json"
SUPPORTED_PROFILE_LOCKS = {"minimal-brain": Path("server/requirements.lock")}


class InstallationStagingError(RuntimeError):
    """A protected component could not be staged without weakening its contract."""


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o550)
        elif path.is_file():
            path.chmod(0o550 if path.stat().st_mode & 0o111 else 0o440)
        else:
            raise InstallationStagingError(f"unsupported installed entry: {path}")
    root.chmod(0o550)


def _publish_tree(prepared: Path, parent: Path, identity: str) -> tuple[Path, bool]:
    if not parent.is_dir() or parent.is_symlink():
        raise InstallationStagingError(f"managed component parent is absent or unsafe: {parent}")
    destination = parent / identity
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise InstallationStagingError(f"installed component identity is not a directory: {identity}")
        return destination, True
    staging = parent / f".staging-{identity}-{secrets.token_hex(8)}"
    try:
        shutil.copytree(prepared, staging, symlinks=True)
        _make_read_only(staging)
        _fsync_directory(staging)
        os.rename(staging, destination)
        _fsync_directory(parent)
    except BaseException:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination, False


def _require_inventory(root: Path, manifest: Mapping[str, object]) -> None:
    actual = _payload_inventory(root)
    if actual != manifest.get("inventory"):
        raise InstallationStagingError("published component inventory differs from the verified artifact")


def stage_artifact_pair(
    core_archive: Path,
    household_archive: Path,
    *,
    revisions: Path,
    deployments: Path,
) -> dict[str, object]:
    """Verify both local inputs completely, then publish their immutable payloads."""

    initial_hashes = {
        "core": _archive_sha256(core_archive),
        "household": _archive_sha256(household_archive),
    }
    with tempfile.TemporaryDirectory(prefix="oracle-install-staging-") as temporary:
        disposable = Path(temporary)
        try:
            core = extract_verified(core_archive, disposable / "core")
            household = extract_verified(household_archive, disposable / "household")
        except (ArtifactError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise InstallationStagingError(str(exc)) from exc
        if core.get("artifact_kind") != "oracle-core" or household.get("artifact_kind") != "oracle-household":
            raise InstallationStagingError("artifact pair must contain one core and one household payload")
        if (
            household.get("required_core_commit") != core.get("core_commit")
            or household.get("required_core_git_tree") != core.get("core_git_tree")
        ):
            raise InstallationStagingError("household artifact core pin does not match supplied core artifact")
        if initial_hashes != {
            "core": _archive_sha256(core_archive),
            "household": _archive_sha256(household_archive),
        }:
            raise InstallationStagingError("an artifact changed during validation")

        application_identity = f"core-{core['core_commit']}"
        deployment_identity = str(household["deployment_revision"])
        existing_application = revisions / application_identity
        existing_deployment = deployments / deployment_identity
        if existing_application.exists() or existing_application.is_symlink():
            if existing_application.is_symlink() or not existing_application.is_dir():
                raise InstallationStagingError("application revision identity is occupied by an invalid entry")
            _require_inventory(existing_application, core)
            if _tree_identity(existing_application) != core.get("core_git_tree"):
                raise InstallationStagingError("existing application revision has drifted")
        if existing_deployment.exists() or existing_deployment.is_symlink():
            if existing_deployment.is_symlink() or not existing_deployment.is_dir():
                raise InstallationStagingError("deployment revision identity is occupied by an invalid entry")
            _require_inventory(existing_deployment, household)
        application, application_reused = _publish_tree(disposable / "core", revisions, application_identity)
        _require_inventory(application, core)
        if _tree_identity(application) != core.get("core_git_tree"):
            raise InstallationStagingError("published application does not match its exact core Git tree")
        deployment, deployment_reused = _publish_tree(
            disposable / "household", deployments, deployment_identity
        )
        _require_inventory(deployment, household)
    return {
        "application_revision_identity": application_identity,
        "application_path": str(application),
        "application_reused": application_reused,
        "core_commit": core["core_commit"],
        "core_git_tree": core["core_git_tree"],
        "household_deployment_revision": deployment_identity,
        "deployment_path": str(deployment),
        "deployment_reused": deployment_reused,
        "artifact_sha256": initial_hashes,
    }


def interpreter_facts(interpreter: Path) -> dict[str, str]:
    probe = (
        "import json,platform,sys,sysconfig;"
        "print(json.dumps({'implementation':platform.python_implementation(),"
        "'version':platform.python_version(),'abi':sysconfig.get_config_var('SOABI') or '',"
        "'platform':sysconfig.get_platform(),'system':platform.system(),"
        "'architecture':platform.machine()}))"
    )
    try:
        completed = subprocess.run(
            [str(interpreter), "-c", probe],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        facts = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        raise InstallationStagingError(f"Python interpreter validation failed: {interpreter}") from exc
    required = {"implementation", "version", "abi", "platform", "system", "architecture"}
    if set(facts) != required or not all(isinstance(facts[key], str) for key in required):
        raise InstallationStagingError("Python interpreter returned an incomplete identity")
    return facts


def environment_record(
    interpreter: Path,
    profile: str,
    lock: Path,
    *,
    facts: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if profile not in SUPPORTED_PROFILE_LOCKS:
        raise InstallationStagingError(f"unsupported production dependency profile: {profile}")
    if not lock.is_file() or lock.is_symlink():
        raise InstallationStagingError(f"dependency lock is absent or unsafe: {lock}")
    lock_sha256 = hashlib.sha256(lock.read_bytes()).hexdigest()
    basis: dict[str, object] = {
        "format": ENVIRONMENT_FORMAT,
        "python": dict(facts or interpreter_facts(interpreter)),
        "profile": profile,
        "lock_sha256": lock_sha256,
    }
    identity = ENVIRONMENT_PREFIX + hashlib.sha256(_json_bytes(basis)).hexdigest()
    return {**basis, "environment_identity": identity}


def _locked_packages(lock: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^ \\]+)(?:\s|\\|$)", line)
        if match:
            packages[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    if not packages:
        raise InstallationStagingError("dependency lock contains no exact package requirements")
    return packages


def _installed_packages(environment_python: Path) -> dict[str, str]:
    probe = (
        "import importlib.metadata,json,re;"
        "norm=lambda v:re.sub(r'[-_.]+','-',v).lower();"
        "print(json.dumps({norm(d.metadata['Name']):d.version for d in importlib.metadata.distributions()}))"
    )
    completed = subprocess.run(
        [str(environment_python), "-c", probe],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return json.loads(completed.stdout)


def _environment_tree_sha256(root: Path) -> str:
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in {"oracle-environment.json", ENVIRONMENT_BUILD_MARKER} or (path.is_dir() and not path.is_symlink()):
            continue
        if path.is_symlink():
            entries.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "executable": bool(path.stat().st_mode & 0o111),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        else:
            raise InstallationStagingError(f"unsupported Python environment entry: {path}")
    return hashlib.sha256(_json_bytes(entries)).hexdigest()


def _validate_environment(directory: Path, expected: Mapping[str, object], lock: Path) -> dict[str, object]:
    try:
        stored = json.loads((directory / "oracle-environment.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise InstallationStagingError("Python environment identity record is unreadable") from exc
    stored_basis = {key: value for key, value in stored.items() if key != "environment_tree_sha256"}
    if stored_basis != expected:
        raise InstallationStagingError("existing Python environment identity record is invalid")
    recorded_tree = stored.get("environment_tree_sha256")
    if recorded_tree != _environment_tree_sha256(directory):
        raise InstallationStagingError("existing Python environment content has drifted")
    environment_python = directory / "bin" / "python"
    if platform.system() == "Windows":
        environment_python = directory / "Scripts" / "python.exe"
    if interpreter_facts(environment_python) != expected["python"]:
        raise InstallationStagingError("existing Python environment interpreter identity differs")
    locked = _locked_packages(lock)
    installed = _installed_packages(environment_python)
    bootstrap = {name for name in ("pip", "setuptools") if name not in locked}
    actual = {name: version for name, version in installed.items() if name not in bootstrap}
    if actual != locked:
        raise InstallationStagingError("installed Python packages differ from the complete dependency lock")
    subprocess.run([str(environment_python), "-m", "pip", "check"], check=True)
    return stored


def build_python_environment(
    application: Path,
    environments: Path,
    interpreter: Path,
    *,
    profile: str = "minimal-brain",
) -> dict[str, object]:
    """Build, validate, and publish one immutable native Python environment."""

    relative_lock = SUPPORTED_PROFILE_LOCKS.get(profile)
    if relative_lock is None:
        raise InstallationStagingError(f"unsupported production dependency profile: {profile}")
    lock = application / relative_lock
    record = environment_record(interpreter, profile, lock)
    identity = str(record["environment_identity"])
    destination = environments / environment_directory_name(identity)
    if destination.is_dir() and not destination.is_symlink():
        marker = destination / ENVIRONMENT_BUILD_MARKER
        identity_record = destination / "oracle-environment.json"
        if marker.is_file():
            try:
                marked = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise InstallationStagingError("incomplete Python environment marker is invalid") from exc
            if marked != record:
                raise InstallationStagingError("incomplete Python environment belongs to another identity")
            shutil.rmtree(destination)
        elif identity_record.is_file():
            stored = _validate_environment(destination, record, lock)
            _make_read_only(destination)
            return {**stored, "path": str(destination), "reused": True}
        else:
            raise InstallationStagingError("unmarked incomplete Python environment requires explicit repair")
    if destination.exists() or destination.is_symlink():
        raise InstallationStagingError("Python environment identity is occupied by an invalid entry")

    destination.mkdir(mode=0o700)
    marker = destination / ENVIRONMENT_BUILD_MARKER
    marker.write_bytes(_json_bytes(record))
    try:
        subprocess.run([str(interpreter), "-m", "venv", str(destination)], check=True)
        environment_python = destination / "bin" / "python"
        if platform.system() == "Windows":
            environment_python = destination / "Scripts" / "python.exe"
        subprocess.run(
            [str(environment_python), "-m", "pip", "install", "--require-hashes", "-r", str(lock)],
            check=True,
        )
        locked = _locked_packages(lock)
        installed = _installed_packages(environment_python)
        bootstrap = {name for name in ("pip", "setuptools") if name not in locked}
        actual = {name: version for name, version in installed.items() if name not in bootstrap}
        if actual != locked:
            raise InstallationStagingError("installed Python packages differ from the complete dependency lock")
        subprocess.run([str(environment_python), "-m", "pip", "check"], check=True)
        stored_record = {**record, "environment_tree_sha256": _environment_tree_sha256(destination)}
        record_path = destination / "oracle-environment.json"
        with record_path.open("xb") as stream:
            stream.write(_json_bytes(stored_record))
            stream.flush()
            os.fsync(stream.fileno())
        marker.unlink()
        _make_read_only(destination)
        _fsync_directory(destination)
        _fsync_directory(environments)
    except BaseException:
        if destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    return {**stored_record, "path": str(destination), "reused": False}


def validate_python_environment(
    application: Path,
    environment: Path,
    *,
    profile: str = "minimal-brain",
) -> dict[str, object]:
    """Validate one already-published environment against its application lock."""

    relative_lock = SUPPORTED_PROFILE_LOCKS.get(profile)
    if relative_lock is None:
        raise InstallationStagingError(f"unsupported production dependency profile: {profile}")
    if environment.is_symlink() or not environment.is_dir():
        raise InstallationStagingError("Python environment is absent or unsafe")
    environment_python = environment / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    expected = environment_record(environment_python, profile, application / relative_lock)
    if environment.name != environment_directory_name(str(expected["environment_identity"])):
        raise InstallationStagingError("Python environment directory identity differs from its declared inputs")
    return _validate_environment(environment, expected, application / relative_lock)
