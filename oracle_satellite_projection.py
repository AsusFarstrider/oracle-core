from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets as random_secrets
import shutil
from types import MappingProxyType
from typing import Any

import rfc8785


PULL_FORMAT = "oracle-satellite-projection-pull-v1"
LOCAL_STORE_FORMAT = "oracle-satellite-projection-local-store-v1"
LOCAL_ACTIVATION_FORMAT = "oracle-satellite-projection-local-activation-v1"
LOCAL_SELECTED_FORMAT = "oracle-satellite-projection-local-selected-v2"

_SATELLITE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
_SELECTION_OPERATION_ID = re.compile(r"^selection_op_[0-9a-f]{32}$")
_ACTIVATION_ID = re.compile(r"^sat_activation_[0-9a-f]{32}$")
_PROJECTION_GENERATION_ID = re.compile(r"^sat_projection_[0-9a-f]{32}$")
_SECRET_GENERATION_ID = re.compile(r"^sat_secret_[0-9a-f]{32}$")
_CONFIG_REVISION = re.compile(r"^oracle-config-v2:sha256:[0-9a-f]{64}$")
_PROJECTION_REVISION = re.compile(r"^oracle-projection-v1:sha256:[0-9a-f]{64}$")
_LOGICAL_SECRET_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SatelliteProjectionStoreError(ValueError):
    pass


class SatelliteProjectionIntegrityError(SatelliteProjectionStoreError):
    pass


class SatelliteProjectionCompatibilityError(SatelliteProjectionStoreError):
    pass


@dataclass(frozen=True)
class LocalSatelliteActivation:
    satellite_id: str
    activation_id: str
    source_config_revision: str
    projection_generation_id: str
    secret_generation_id: str
    projection_revision: str
    projection: Mapping[str, Any] = field(repr=False)
    _secrets: Mapping[str, str] = field(repr=False)

    @property
    def secret_ids(self) -> frozenset[str]:
        return frozenset(self._secrets)

    def resolve_secret(self, logical_id: str) -> str | None:
        return self._secrets.get(logical_id)


@dataclass(frozen=True)
class SelectedLocalSatelliteActivation:
    selection_operation_id: str
    selection_revision: int
    restart_required_activation_id: str | None
    activation: LocalSatelliteActivation

    @property
    def satellite_id(self) -> str:
        return self.activation.satellite_id

    @property
    def activation_id(self) -> str:
        return self.activation.activation_id

    @property
    def projection(self) -> Mapping[str, Any]:
        return self.activation.projection

    @property
    def secret_ids(self) -> frozenset[str]:
        return self.activation.secret_ids

    def resolve_secret(self, logical_id: str) -> str | None:
        return self.activation.resolve_secret(logical_id)


class SatelliteProjectionLocalStore:
    """Single-writer immutable projection store for one satellite installation."""

    def __init__(
        self,
        root: Path,
        *,
        satellite_id: str,
        runtime_compatibility: Mapping[str, Any],
    ) -> None:
        if not isinstance(satellite_id, str) or _SATELLITE_ID.fullmatch(satellite_id) is None:
            raise SatelliteProjectionStoreError("Local satellite identity is invalid.")
        try:
            compatibility_bytes = rfc8785.dumps(dict(runtime_compatibility))
            compatibility = json.loads(compatibility_bytes)
        except Exception as exc:
            raise SatelliteProjectionCompatibilityError("Local runtime compatibility is invalid.") from exc
        if not isinstance(compatibility, dict):
            raise SatelliteProjectionCompatibilityError("Local runtime compatibility must be an object.")
        self.root = Path(root).expanduser().resolve(strict=False)
        self.satellite_id = satellite_id
        self.runtime_compatibility = compatibility
        self._initialize()

    def install(self, response_bytes: bytes) -> SelectedLocalSatelliteActivation:
        payload = self._validate_response(response_bytes)
        activation = payload["activation"]
        projection_entry = payload["projection"]
        secret_entry = payload["local_secrets"]
        projection = projection_entry["payload"]
        values = secret_entry["values"]
        activation_id = activation["activation_id"]

        current = self.load_selected(optional=True)
        selection = payload["selection"]
        if current is not None:
            if selection["revision"] < current.selection_revision:
                raise SatelliteProjectionIntegrityError("Pulled selection revision is older than local state.")
            if selection["revision"] == current.selection_revision and (
                selection["operation_id"] != current.selection_operation_id
                or activation_id != current.activation_id
            ):
                raise SatelliteProjectionIntegrityError("Pulled selection conflicts with local state.")

        self._install_activation(
            metadata={
                "format": LOCAL_ACTIVATION_FORMAT,
                "satellite_id": self.satellite_id,
                "activation_id": activation_id,
                "source_config_revision": activation["source_config_revision"],
                "projection_generation_id": projection_entry["generation_id"],
                "secret_generation_id": secret_entry["generation_id"],
                "projection_revision": projection["projection_revision"],
                "logical_secret_ids": sorted(values),
            },
            projection=projection,
            values=values,
        )
        installed = self.load_activation(activation_id)
        payload_changed = current is None or (
            installed.projection_generation_id != current.activation.projection_generation_id
            or installed.secret_generation_id != current.activation.secret_generation_id
        )
        restart_required_activation_id = activation_id if payload_changed else None
        if current is not None and current.restart_required_activation_id is not None:
            restart_required_activation_id = activation_id
        pointer = {
            "format": LOCAL_SELECTED_FORMAT,
            "satellite_id": self.satellite_id,
            "activation_id": activation_id,
            "selection_operation_id": selection["operation_id"],
            "selection_revision": selection["revision"],
            "restart_required_activation_id": restart_required_activation_id,
        }
        _atomic_replace(self.root / "selected.json", rfc8785.dumps(pointer), mode=0o600)
        _fsync_directory(self.root)
        selected = self.load_selected()
        if selected is None:  # pragma: no cover - required by the optional return type
            raise SatelliteProjectionIntegrityError("Local selection was not persisted.")
        return selected

    def load_selected(self, *, optional: bool = False) -> SelectedLocalSatelliteActivation | None:
        path = self.root / "selected.json"
        if not path.exists() and not path.is_symlink():
            if optional:
                return None
            raise SatelliteProjectionIntegrityError("No local satellite activation is selected.")
        pointer = _read_exact_json(
            path,
            {
                "format",
                "satellite_id",
                "activation_id",
                "selection_operation_id",
                "selection_revision",
                "restart_required_activation_id",
            },
            "local selected pointer",
        )
        restart_required = pointer["restart_required_activation_id"]
        if (
            pointer["format"] != LOCAL_SELECTED_FORMAT
            or pointer["satellite_id"] != self.satellite_id
            or not _matches(pointer["activation_id"], _ACTIVATION_ID)
            or not _matches(pointer["selection_operation_id"], _SELECTION_OPERATION_ID)
            or not _positive_int(pointer["selection_revision"])
            or (
                restart_required is not None
                and (
                    not _matches(restart_required, _ACTIVATION_ID)
                    or restart_required != pointer["activation_id"]
                )
            )
        ):
            raise SatelliteProjectionIntegrityError("Local selected pointer identity is invalid.")
        installed = self.load_activation(pointer["activation_id"])
        return SelectedLocalSatelliteActivation(
            selection_operation_id=pointer["selection_operation_id"],
            selection_revision=pointer["selection_revision"],
            restart_required_activation_id=restart_required,
            activation=installed,
        )

    def mark_restarted(self) -> SelectedLocalSatelliteActivation:
        selected = self.load_selected()
        if selected is None:  # pragma: no cover - required by the optional return type
            raise SatelliteProjectionIntegrityError("No local satellite activation is selected.")
        if selected.restart_required_activation_id is None:
            return selected
        pointer = {
            "format": LOCAL_SELECTED_FORMAT,
            "satellite_id": self.satellite_id,
            "activation_id": selected.activation_id,
            "selection_operation_id": selected.selection_operation_id,
            "selection_revision": selected.selection_revision,
            "restart_required_activation_id": None,
        }
        _atomic_replace(self.root / "selected.json", rfc8785.dumps(pointer), mode=0o600)
        _fsync_directory(self.root)
        cleared = self.load_selected()
        if cleared is None:  # pragma: no cover - required by the optional return type
            raise SatelliteProjectionIntegrityError("Local restart latch was not persisted.")
        return cleared

    def load_activation(self, activation_id: str) -> LocalSatelliteActivation:
        if not _matches(activation_id, _ACTIVATION_ID):
            raise SatelliteProjectionIntegrityError("Local activation identity is invalid.")
        directory = self.root / "activations" / activation_id
        if directory.is_symlink() or not directory.is_dir():
            raise SatelliteProjectionIntegrityError("Local activation is unavailable.")
        metadata = _read_exact_json(
            directory / "metadata.json",
            {
                "format",
                "satellite_id",
                "activation_id",
                "source_config_revision",
                "projection_generation_id",
                "secret_generation_id",
                "projection_revision",
                "logical_secret_ids",
            },
            "local activation metadata",
        )
        self._validate_activation_metadata(metadata, activation_id)
        projection_bytes = _read_bytes(directory / "projection.json")
        projection = _parse_canonical_json(projection_bytes, "local projection")
        values = _read_exact_json_values(directory / "secrets.json")
        self._validate_projection_and_secrets(projection, values)
        if (
            metadata["projection_revision"] != projection["projection_revision"]
            or metadata["logical_secret_ids"] != sorted(values)
        ):
            raise SatelliteProjectionIntegrityError("Local activation metadata does not match its payloads.")
        return LocalSatelliteActivation(
            satellite_id=self.satellite_id,
            activation_id=activation_id,
            source_config_revision=metadata["source_config_revision"],
            projection_generation_id=metadata["projection_generation_id"],
            secret_generation_id=metadata["secret_generation_id"],
            projection_revision=metadata["projection_revision"],
            projection=_freeze_json(projection),
            _secrets=MappingProxyType(dict(values)),
        )

    def _initialize(self) -> None:
        self.root.mkdir(mode=_private_directory_mode(), parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise SatelliteProjectionIntegrityError("Local projection store root is invalid.")
        marker = self.root / "store.json"
        if not marker.exists() and not marker.is_symlink():
            _write_new(
                marker,
                rfc8785.dumps({"format": LOCAL_STORE_FORMAT, "satellite_id": self.satellite_id}),
                mode=0o600,
            )
            _fsync_directory(self.root)
        stored = _read_exact_json(marker, {"format", "satellite_id"}, "local projection store")
        if stored != {"format": LOCAL_STORE_FORMAT, "satellite_id": self.satellite_id}:
            raise SatelliteProjectionIntegrityError("Local projection store identity is invalid.")
        activations = self.root / "activations"
        activations.mkdir(mode=_private_directory_mode(), exist_ok=True)
        if activations.is_symlink() or not activations.is_dir():
            raise SatelliteProjectionIntegrityError("Local activation store is invalid.")

    def _validate_response(self, response_bytes: bytes) -> dict[str, Any]:
        payload = _parse_canonical_json(response_bytes, "satellite projection pull response")
        _require_exact_keys(
            payload,
            {"format", "satellite_id", "selection", "activation", "projection", "local_secrets"},
            "satellite projection pull response",
        )
        _require_exact_keys(payload["selection"], {"operation_id", "revision"}, "pull selection")
        _require_exact_keys(
            payload["activation"], {"activation_id", "source_config_revision"}, "pull activation"
        )
        _require_exact_keys(payload["projection"], {"generation_id", "payload"}, "pull projection")
        _require_exact_keys(payload["local_secrets"], {"generation_id", "values"}, "pull secrets")
        if (
            payload["format"] != PULL_FORMAT
            or payload["satellite_id"] != self.satellite_id
            or not _matches(payload["selection"]["operation_id"], _SELECTION_OPERATION_ID)
            or not _positive_int(payload["selection"]["revision"])
            or not _matches(payload["activation"]["activation_id"], _ACTIVATION_ID)
            or not _matches(payload["activation"]["source_config_revision"], _CONFIG_REVISION)
            or not _matches(payload["projection"]["generation_id"], _PROJECTION_GENERATION_ID)
            or not _matches(payload["local_secrets"]["generation_id"], _SECRET_GENERATION_ID)
        ):
            raise SatelliteProjectionIntegrityError("Satellite projection pull identity is invalid.")
        projection = payload["projection"]["payload"]
        values = payload["local_secrets"]["values"]
        if not isinstance(values, dict):
            raise SatelliteProjectionIntegrityError("Satellite projection secret payload is invalid.")
        self._validate_projection_and_secrets(projection, values)
        return payload

    def _validate_projection_and_secrets(
        self,
        projection: Any,
        values: Mapping[str, Any],
    ) -> None:
        _require_exact_keys(
            projection,
            {
                "kind",
                "projection_schema_version",
                "satellite_id",
                "source_id",
                "projection_revision",
                "runtime_compatibility",
                "configuration",
            },
            "satellite projection",
        )
        if (
            projection["kind"] != "oracle-satellite-projection"
            or projection["projection_schema_version"] != 1
            or projection["satellite_id"] != self.satellite_id
            or not _matches(projection["source_id"], _SATELLITE_ID)
            or not _matches(projection["projection_revision"], _PROJECTION_REVISION)
        ):
            raise SatelliteProjectionIntegrityError("Satellite projection identity is invalid.")
        if projection["runtime_compatibility"] != self.runtime_compatibility:
            raise SatelliteProjectionCompatibilityError(
                "Satellite projection targets different runtime compatibility."
            )
        revision_payload = dict(projection)
        claimed_revision = revision_payload.pop("projection_revision")
        expected_revision = "oracle-projection-v1:sha256:" + hashlib.sha256(
            rfc8785.dumps(revision_payload)
        ).hexdigest()
        if claimed_revision != expected_revision:
            raise SatelliteProjectionIntegrityError("Satellite projection content does not match its revision.")
        required_ids = _projection_secret_ids(projection["configuration"])
        if set(values) != required_ids or not all(
            _matches(logical_id, _LOGICAL_SECRET_ID)
            and isinstance(value, str)
            and bool(value)
            for logical_id, value in values.items()
        ):
            raise SatelliteProjectionIntegrityError(
                "Satellite projection secrets are not minimal and complete."
            )

    def _install_activation(
        self,
        *,
        metadata: dict[str, Any],
        projection: dict[str, Any],
        values: dict[str, str],
    ) -> LocalSatelliteActivation:
        activation_id = metadata["activation_id"]
        final = self.root / "activations" / activation_id
        if final.exists() or final.is_symlink():
            installed = self.load_activation(activation_id)
            if (
                _thaw_json(installed.projection) != projection
                or installed.secret_ids != frozenset(values)
                or any(installed.resolve_secret(key) != value for key, value in values.items())
                or installed.source_config_revision != metadata["source_config_revision"]
                or installed.projection_generation_id != metadata["projection_generation_id"]
                or installed.secret_generation_id != metadata["secret_generation_id"]
                or installed.projection_revision != metadata["projection_revision"]
            ):
                raise SatelliteProjectionIntegrityError("Existing local activation has conflicting content.")
            return installed
        staging = self.root / "activations" / f".install-{random_secrets.token_hex(16)}"
        staging.mkdir(mode=_private_directory_mode())
        try:
            _write_new(staging / "metadata.json", rfc8785.dumps(metadata), mode=0o600)
            _write_new(staging / "projection.json", rfc8785.dumps(projection), mode=0o600)
            _write_new(staging / "secrets.json", rfc8785.dumps(values), mode=0o600)
            _fsync_directory(staging)
            os.replace(staging, final)
            _fsync_directory(final.parent)
        except BaseException:
            if staging.exists() and not staging.is_symlink():
                shutil.rmtree(staging)
            raise
        return self.load_activation(activation_id)

    def _validate_activation_metadata(self, metadata: dict[str, Any], activation_id: str) -> None:
        logical_ids = metadata["logical_secret_ids"]
        if (
            metadata["format"] != LOCAL_ACTIVATION_FORMAT
            or metadata["satellite_id"] != self.satellite_id
            or metadata["activation_id"] != activation_id
            or not _matches(metadata["source_config_revision"], _CONFIG_REVISION)
            or not _matches(metadata["projection_generation_id"], _PROJECTION_GENERATION_ID)
            or not _matches(metadata["secret_generation_id"], _SECRET_GENERATION_ID)
            or not _matches(metadata["projection_revision"], _PROJECTION_REVISION)
            or not isinstance(logical_ids, list)
            or logical_ids != sorted(set(logical_ids))
            or not all(_matches(item, _LOGICAL_SECRET_ID) for item in logical_ids)
        ):
            raise SatelliteProjectionIntegrityError("Local activation metadata is invalid.")


def _projection_secret_ids(configuration: Any) -> set[str]:
    _require_exact_keys(
        configuration,
        {"brain_client", "interaction_runtime", "control_service"},
        "projected configuration",
    )
    brain = configuration["brain_client"]
    _require_exact_keys(brain, {"base_url", "credential_secret"}, "projected Brain client")
    ids = {_logical_secret_id(brain["credential_secret"])}
    interaction = configuration["interaction_runtime"]
    if interaction is not None:
        if not isinstance(interaction, dict) or "control_service_client" not in interaction:
            raise SatelliteProjectionIntegrityError("Projected interaction runtime is invalid.")
        client = interaction["control_service_client"]
        _require_exact_keys(client, {"local_client_url", "credential_secret"}, "projected control client")
        ids.add(_logical_secret_id(client["credential_secret"]))
    control = configuration["control_service"]
    if control is not None:
        if not isinstance(control, dict) or "credential_secret" not in control or "music" not in control:
            raise SatelliteProjectionIntegrityError("Projected control service is invalid.")
        ids.add(_logical_secret_id(control["credential_secret"]))
        music = control["music"]
        if music is not None:
            if not isinstance(music, dict) or "provider" not in music:
                raise SatelliteProjectionIntegrityError("Projected music configuration is invalid.")
            provider = music["provider"]
            if not isinstance(provider, dict) or "credential_secret" not in provider:
                raise SatelliteProjectionIntegrityError("Projected music provider is invalid.")
            ids.add(_logical_secret_id(provider["credential_secret"]))
    return ids


def _logical_secret_id(value: Any) -> str:
    if not _matches(value, _LOGICAL_SECRET_ID):
        raise SatelliteProjectionIntegrityError("Projected logical secret reference is invalid.")
    return value


def _read_exact_json(path: Path, keys: set[str], artifact: str) -> dict[str, Any]:
    payload = _parse_canonical_json(_read_bytes(path), artifact)
    _require_exact_keys(payload, keys, artifact)
    return payload


def _read_exact_json_values(path: Path) -> dict[str, str]:
    payload = _parse_canonical_json(_read_bytes(path), "local secret payload")
    if not isinstance(payload, dict):
        raise SatelliteProjectionIntegrityError("Local secret payload must be an object.")
    return payload


def _parse_canonical_json(data: bytes, artifact: str) -> Any:
    try:
        payload = json.loads(data)
        canonical = rfc8785.dumps(payload)
    except Exception as exc:
        raise SatelliteProjectionIntegrityError(f"{artifact.capitalize()} is invalid JSON.") from exc
    if canonical != data:
        raise SatelliteProjectionIntegrityError(f"{artifact.capitalize()} is not canonical JSON.")
    return payload


def _require_exact_keys(value: Any, keys: set[str], artifact: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise SatelliteProjectionIntegrityError(f"{artifact.capitalize()} has an invalid shape.")


def _read_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SatelliteProjectionIntegrityError("Local projection artifact is unavailable.")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SatelliteProjectionIntegrityError("Local projection artifact cannot be read.") from exc


def _write_new(path: Path, data: bytes, *, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise


def _private_directory_mode() -> int:
    # Python 3.13+ translates 0700 into a protected Windows ACL. That loses the
    # deployment parent's explicit interactive-user grant when an elevated
    # first-contact process is owned by Administrators. Other mode bits remain
    # advisory on Windows, so inherit the already restricted parent ACL there.
    return 0o777 if os.name == "nt" else 0o700


def _atomic_replace(path: Path, data: bytes, *, mode: int) -> None:
    temporary = path.parent / f".{path.name}.tmp-{random_secrets.token_hex(16)}"
    try:
        _write_new(temporary, data, mode=mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _matches(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
