"""Standard-installation layout and complete activation selection.

This module deliberately contains no installer or privilege-elevation behavior.
It models the fixed Stage 4 layout, publishes complete activation records, and
atomically selects one already-validated record.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Mapping

from .installation_identity import environment_directory_name


STANDARD_INSTALLATION_ROOT = Path("/srv/oracle")
ACTIVATION_FORMAT = "oracle-installation-activation-v1"
ACTIVATION_ID_PREFIX = f"{ACTIVATION_FORMAT}:sha256:"

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256_ID = re.compile(r"^[a-z][a-z0-9-]*-v[0-9]+:sha256:[0-9a-f]{64}$")
_SAFE_COMPONENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SELECTION_NAMES = frozenset({"active", "staged", "approved", "previous-known-good"})


class InstallationLayoutError(ValueError):
    """The managed installation layout or an activation is invalid."""


@dataclass(frozen=True)
class InstallationLayout:
    root: Path = STANDARD_INSTALLATION_ROOT

    @property
    def revisions(self) -> Path:
        return self.root / "revisions"

    @property
    def environments(self) -> Path:
        return self.root / "environments"

    @property
    def deployments(self) -> Path:
        return self.root / "deployments"

    @property
    def configuration(self) -> Path:
        return self.root / "configuration"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets"

    @property
    def activations(self) -> Path:
        return self.root / "activations"

    @property
    def selection(self) -> Path:
        return self.root / "selection"

    @property
    def installation_state(self) -> Path:
        return self.root / "state" / "installation"

    @property
    def control_state(self) -> Path:
        return self.root / "state" / "control"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def temporary(self) -> Path:
        return self.root / "tmp"

    def required_directories(self) -> tuple[Path, ...]:
        return (
            self.revisions,
            self.environments,
            self.deployments,
            self.configuration,
            self.secrets,
            self.activations,
            self.selection,
            self.installation_state,
            self.control_state,
            self.data,
            self.cache,
            self.temporary,
        )


@dataclass(frozen=True)
class ActivationRequest:
    core_commit: str
    core_git_tree: str
    application_revision_identity: str
    python_environment_identity: str
    household_deployment_revision: str
    configuration_activation_identity: str
    service_definition_identity: str
    persistent_state_checkpoint: str | None = None


@dataclass(frozen=True)
class InstalledActivation:
    activation_id: str
    directory: Path
    record: Mapping[str, object]


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


def _validate_component_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or _SAFE_COMPONENT_ID.fullmatch(value) is None or "/" in value:
        raise InstallationLayoutError(f"{label} identity is not path-safe.")


def _record_basis(request: ActivationRequest) -> dict[str, object]:
    if not isinstance(request.core_commit, str) or _HEX_40.fullmatch(request.core_commit) is None:
        raise InstallationLayoutError("Core commit identity must be a full SHA-1 object ID.")
    if not isinstance(request.core_git_tree, str) or _HEX_40.fullmatch(request.core_git_tree) is None:
        raise InstallationLayoutError("Core Git-tree identity must be a full SHA-1 object ID.")
    for value, label in (
        (request.application_revision_identity, "Application revision"),
        (request.python_environment_identity, "Python environment"),
        (request.configuration_activation_identity, "Configuration activation"),
        (request.service_definition_identity, "Service definition"),
    ):
        _validate_component_identity(value, label)
    if (
        not isinstance(request.household_deployment_revision, str)
        or _SHA256_ID.fullmatch(request.household_deployment_revision) is None
    ):
        raise InstallationLayoutError("Household deployment revision is invalid.")
    if request.persistent_state_checkpoint is not None:
        _validate_component_identity(request.persistent_state_checkpoint, "Persistent-state checkpoint")
    return {
        "format": ACTIVATION_FORMAT,
        "core": {"commit": request.core_commit, "git_tree": request.core_git_tree},
        "application_revision_identity": request.application_revision_identity,
        "python_environment_identity": request.python_environment_identity,
        "household_deployment_revision": request.household_deployment_revision,
        "configuration_activation_identity": request.configuration_activation_identity,
        "service_definition_identity": request.service_definition_identity,
        "persistent_state_checkpoint": request.persistent_state_checkpoint,
    }


def activation_record(request: ActivationRequest) -> dict[str, object]:
    basis = _record_basis(request)
    identity = ACTIVATION_ID_PREFIX + hashlib.sha256(_json_bytes(basis)).hexdigest()
    return {**basis, "activation_id": identity}


def activation_directory_name(activation_id: str) -> str:
    if not activation_id.startswith(ACTIVATION_ID_PREFIX):
        raise InstallationLayoutError("Installation activation identity is invalid.")
    digest = activation_id.removeprefix(ACTIVATION_ID_PREFIX)
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise InstallationLayoutError("Installation activation identity is invalid.")
    return f"activation-{digest}"


def _component_targets(layout: InstallationLayout, request: ActivationRequest) -> dict[str, Path]:
    return {
        "application": layout.revisions / request.application_revision_identity,
        "environment": layout.environments / environment_directory_name(request.python_environment_identity),
        "deployment": layout.deployments / request.household_deployment_revision,
        "configuration": layout.configuration / "activations" / request.configuration_activation_identity,
    }


def _require_direct_child(path: Path, parent: Path, label: str) -> None:
    if path.parent != parent or path.name in {"", ".", ".."}:
        raise InstallationLayoutError(f"{label} must be one direct managed child.")
    if path.is_symlink() or not path.is_dir():
        raise InstallationLayoutError(f"{label} is absent or not an installed directory.")
    try:
        resolved = path.resolve(strict=True)
        expected_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise InstallationLayoutError(f"{label} cannot be resolved safely.") from exc
    if resolved.parent != expected_parent or resolved != expected_parent / path.name:
        raise InstallationLayoutError(f"{label} escapes or aliases its managed subtree.")


def validate_component_targets(layout: InstallationLayout, request: ActivationRequest) -> dict[str, Path]:
    targets = _component_targets(layout, request)
    _require_direct_child(targets["application"], layout.revisions, "Application revision")
    _require_direct_child(targets["environment"], layout.environments, "Python environment")
    _require_direct_child(targets["deployment"], layout.deployments, "Household deployment")
    configuration_activations = layout.configuration / "activations"
    _require_direct_child(targets["configuration"], configuration_activations, "Configuration activation")
    return targets


def publish_activation(layout: InstallationLayout, request: ActivationRequest) -> InstalledActivation:
    """Publish one complete record from already-installed validated components."""
    targets = validate_component_targets(layout, request)
    record = activation_record(request)
    name = activation_directory_name(str(record["activation_id"]))
    destination = layout.activations / name
    if destination.exists() or destination.is_symlink():
        return load_activation(layout, destination)
    staging = layout.activations / f".staging-{name}-{secrets.token_hex(8)}"
    staging.mkdir(mode=0o700)
    try:
        record_path = staging / "activation.json"
        with record_path.open("xb") as stream:
            stream.write(_json_bytes(record))
            stream.flush()
            os.fsync(stream.fileno())
        for link_name, target in targets.items():
            relative = os.path.relpath(target, start=staging)
            if Path(relative).is_absolute():
                raise InstallationLayoutError("Activation component reference must be relative.")
            (staging / link_name).symlink_to(relative, target_is_directory=True)
        record_path.chmod(0o400)
        staging.chmod(0o500)
        _fsync_directory(staging)
        os.rename(staging, destination)
        _fsync_directory(layout.activations)
    except BaseException:
        if staging.exists() and not staging.is_symlink():
            for child in staging.iterdir():
                child.unlink()
            staging.rmdir()
        raise
    return load_activation(layout, destination)


def load_activation(layout: InstallationLayout, directory: Path) -> InstalledActivation:
    if directory.parent != layout.activations or directory.is_symlink() or not directory.is_dir():
        raise InstallationLayoutError("Activation must be one immutable direct child of activations/.")
    try:
        raw = (directory / "activation.json").read_bytes()
        record = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationLayoutError("Activation record is absent or invalid.") from exc
    if not isinstance(record, dict):
        raise InstallationLayoutError("Activation record must be a JSON object.")
    expected_fields = {
        "format", "activation_id", "core", "application_revision_identity",
        "python_environment_identity", "household_deployment_revision",
        "configuration_activation_identity", "service_definition_identity",
        "persistent_state_checkpoint",
    }
    if set(record) != expected_fields:
        raise InstallationLayoutError("Activation record has an invalid shape.")
    request = ActivationRequest(
        core_commit=record.get("core", {}).get("commit") if isinstance(record.get("core"), dict) else "",
        core_git_tree=record.get("core", {}).get("git_tree") if isinstance(record.get("core"), dict) else "",
        application_revision_identity=record.get("application_revision_identity"),
        python_environment_identity=record.get("python_environment_identity"),
        household_deployment_revision=record.get("household_deployment_revision"),
        configuration_activation_identity=record.get("configuration_activation_identity"),
        service_definition_identity=record.get("service_definition_identity"),
        persistent_state_checkpoint=record.get("persistent_state_checkpoint"),
    )
    expected = activation_record(request)
    if record != expected or directory.name != activation_directory_name(str(expected["activation_id"])):
        raise InstallationLayoutError("Activation record identity does not match its canonical contents.")
    targets = validate_component_targets(layout, request)
    for link_name, expected_target in targets.items():
        link = directory / link_name
        if not link.is_symlink():
            raise InstallationLayoutError(f"Activation reference {link_name!r} is missing or not a symbolic link.")
        raw_target = os.readlink(link)
        if Path(raw_target).is_absolute():
            raise InstallationLayoutError(f"Activation reference {link_name!r} must be relative.")
        try:
            resolved = link.resolve(strict=True)
        except OSError as exc:
            raise InstallationLayoutError(f"Activation reference {link_name!r} is dangling.") from exc
        if resolved != expected_target.resolve(strict=True):
            raise InstallationLayoutError(f"Activation reference {link_name!r} disagrees with its record.")
    return InstalledActivation(str(record["activation_id"]), directory, record)


def select_activation(layout: InstallationLayout, selection: str, activation: InstalledActivation) -> Path:
    if selection not in _SELECTION_NAMES:
        raise InstallationLayoutError("Selection name is not part of the lifecycle contract.")
    validated = load_activation(layout, activation.directory)
    destination = layout.selection / selection
    temporary = layout.selection / f".{selection}-{secrets.token_hex(8)}.tmp"
    relative = os.path.relpath(validated.directory, start=layout.selection)
    try:
        temporary.symlink_to(relative, target_is_directory=True)
        if temporary.resolve(strict=True) != validated.directory.resolve(strict=True):
            raise InstallationLayoutError("Temporary activation selector resolves incorrectly.")
        os.replace(temporary, destination)
        _fsync_directory(layout.selection)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


def load_selected_activation(layout: InstallationLayout, selection: str = "active") -> InstalledActivation:
    if selection not in _SELECTION_NAMES:
        raise InstallationLayoutError("Selection name is not part of the lifecycle contract.")
    link = layout.selection / selection
    if not link.is_symlink():
        raise InstallationLayoutError("Activation selection is absent or not a symbolic link.")
    raw_target = os.readlink(link)
    raw_path = Path(raw_target)
    if raw_path.is_absolute():
        raise InstallationLayoutError("Activation selection must use a relative reference.")
    if raw_path.parts != ("..", "activations", raw_path.name):
        raise InstallationLayoutError("Activation selection must target one direct activation record.")
    try:
        resolved = link.resolve(strict=True)
    except OSError as exc:
        raise InstallationLayoutError("Activation selection is dangling.") from exc
    if resolved.parent != layout.activations.resolve(strict=True):
        raise InstallationLayoutError("Activation selection does not target one direct activation record.")
    return load_activation(layout, resolved)
