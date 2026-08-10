from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from types import MappingProxyType
from typing import Any, Mapping

from .normalization import CONFIG_FORMAT, CONFIG_REVISION_PREFIX, NormalizedBundle, canonicalize_json
from .reporting import CandidateInspection
from .secrets import SecretSnapshot, collect_secret_references


STORE_FORMAT = "oracle-configuration-store-v1"
CONFIG_GENERATION_FORMAT = "oracle-config-generation-v1"
SECRET_GENERATION_FORMAT = "oracle-secret-generation-v1"
SECRET_STATUS_FORMAT = "oracle-secret-status-v1"
ACTIVATION_GENERATION_FORMAT = "oracle-activation-generation-v1"
SELECTED_POINTER_FORMAT = "oracle-selected-v1"

_GENERATION_ID = re.compile(r"^(config|secret|activation)_[0-9a-f]{32}$")
_CONFIG_ID = re.compile(r"^config_[0-9a-f]{32}$")
_SECRET_GENERATION_ID = re.compile(r"^secret_[0-9a-f]{32}$")
_ACTIVATION_ID = re.compile(r"^activation_[0-9a-f]{32}$")
_SELECTION_OPERATION_ID = re.compile(r"^selection_op_[0-9a-f]{32}$")
_SATELLITE_ACTIVATION_ID = re.compile(r"^sat_activation_[0-9a-f]{32}$")
_BUNDLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
_LOGICAL_SECRET_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")


class GenerationStoreError(ValueError):
    pass


class StoreLineageConflict(GenerationStoreError):
    pass


class GenerationIntegrityError(GenerationStoreError):
    pass


class GenerationCompatibilityError(GenerationStoreError):
    pass


class SecretGenerationRevokedError(GenerationStoreError):
    pass


class SecretGenerationPrunedError(GenerationStoreError):
    pass


@dataclass(frozen=True)
class ConfigGeneration:
    generation_id: str
    config_revision: str
    bundle_id: str
    schema_version: int
    required_secret_ids: frozenset[str]
    configuration: Mapping[str, Any]


@dataclass(frozen=True)
class SecretGeneration:
    generation_id: str
    snapshot: SecretSnapshot
    state: str
    raw_present: bool


@dataclass(frozen=True)
class ActivationGeneration:
    generation_id: str
    config_generation_id: str
    secret_generation_id: str


@dataclass(frozen=True)
class SelectedActivation:
    activation: ActivationGeneration
    config: ConfigGeneration
    secrets: SecretGeneration
    selection_operation_id: str | None = None
    selection_revision: int = 0
    satellite_projection_activation_ids: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )


def _new_id(kind: str) -> str:
    return f"{kind}_{secrets.token_hex(16)}"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new(path: Path, data: bytes, *, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    temporary = path.parent / f".{path.name}-{secrets.token_hex(16)}.tmp"
    try:
        _write_new(temporary, data, mode=mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise GenerationIntegrityError(f"Installed artifact {path.name!r} is missing or not a regular file.")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GenerationIntegrityError(f"Installed artifact {path.name!r} is unreadable.") from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_read_bytes(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationIntegrityError(f"Installed artifact {path.name!r} is unreadable or invalid.") from exc


def _require_exact_mapping(value: Any, fields: set[str], *, artifact: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise GenerationIntegrityError(f"{artifact} has an invalid manifest shape.")
    return value


class GenerationStore:
    def __init__(
        self,
        root: Path,
        *,
        secret_root: Path | None = None,
        supported_schema_versions: frozenset[int] = frozenset({1}),
    ) -> None:
        self.root = Path(root).resolve()
        self.secret_root = self.root if secret_root is None else Path(secret_root).resolve()
        self.supported_schema_versions = supported_schema_versions

    @property
    def secret_transactions_root(self) -> Path:
        return self.secret_root / "transactions"

    @property
    def _split_standard_store(self) -> bool:
        return self.secret_root != self.root

    @property
    def _configuration_file_mode(self) -> int:
        return 0o640 if self._split_standard_store else 0o600

    def initialize(self, bundle_id: str) -> None:
        if _BUNDLE_ID.fullmatch(bundle_id) is None:
            raise GenerationStoreError("Store bundle lineage must use a canonical bundle ID.")
        config_root_mode = 0o2750 if self._split_standard_store else 0o700
        self.root.mkdir(mode=config_root_mode, parents=True, exist_ok=True)
        self.root.chmod(config_root_mode)
        for name in ("config-generations", "activations", "transactions"):
            directory = self.root / name
            directory.mkdir(mode=config_root_mode, exist_ok=True)
            directory.chmod(config_root_mode)
        self.secret_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in ("secret-generations", "secret-status", "transactions"):
            (self.secret_root / name).mkdir(mode=0o700, exist_ok=True)
        binding = self.root / "store.json"
        expected = {"format": STORE_FORMAT, "bundle_id": bundle_id}
        if binding.exists():
            actual = _require_exact_mapping(_read_json(binding), set(expected), artifact="store binding")
            if actual != expected:
                raise StoreLineageConflict("Installed store is bound to a different bundle lineage.")
            return
        _write_new(binding, _json_bytes(expected), mode=self._configuration_file_mode)
        _fsync_directory(self.root)

    def validate_initialized(self) -> str:
        """Validate the installed-store binding without creating or changing it."""
        return str(self._require_initialized()["bundle_id"])

    def install_candidate(self, inspection: CandidateInspection) -> tuple[ConfigGeneration, SecretGeneration]:
        config = self.install_config_candidate(inspection)
        if inspection.secrets is None:
            raise GenerationStoreError("Inspected candidate has no validated secret snapshot.")
        secret_generation = self.install_secrets(inspection.secrets)
        return config, secret_generation

    def install_config_candidate(self, inspection: CandidateInspection) -> ConfigGeneration:
        if (
            not inspection.report.activation_eligible
            or inspection.bundle is None
            or inspection.normalized is None
            or inspection.secrets is None
            or inspection.normalized_candidate_revision != inspection.normalized.config_revision
        ):
            raise GenerationStoreError("Only an activation-eligible inspected candidate can be installed.")
        required_secret_ids = frozenset(
            use.logical_id for use in collect_secret_references(inspection.bundle) if use.required
        )
        return self._install_config(inspection.normalized, required_secret_ids=required_secret_ids)

    def _install_config(
        self,
        normalized: NormalizedBundle,
        *,
        required_secret_ids: frozenset[str],
    ) -> ConfigGeneration:
        bundle_id = normalized.configuration.get("bundle_id")
        schema_version = normalized.configuration.get("schema_version")
        if not isinstance(bundle_id, str) or not isinstance(schema_version, int):
            raise GenerationIntegrityError("Normalized configuration lacks bundle identity or schema version.")
        self._require_lineage(bundle_id)
        self._require_compatible(schema_version)
        expected_revision = f"{CONFIG_REVISION_PREFIX}{hashlib.sha256(normalized.canonical_bytes).hexdigest()}"
        if normalized.format != CONFIG_FORMAT or normalized.config_revision != expected_revision:
            raise GenerationIntegrityError("Normalized configuration revision does not match its canonical bytes.")
        try:
            canonical_object = json.loads(normalized.canonical_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenerationIntegrityError("Normalized canonical bytes are not valid JSON.") from exc
        if canonicalize_json(canonical_object) != normalized.canonical_bytes:
            raise GenerationIntegrityError("Normalized configuration object does not match its canonical bytes.")
        if canonical_object != normalized.envelope()["configuration"]:
            raise GenerationIntegrityError("Normalized configuration object does not match its canonical bytes.")

        generation_id = _new_id("config")
        directory = self._create_generation_directory("config-generations", generation_id)
        metadata = {
            "format": CONFIG_GENERATION_FORMAT,
            "generation_id": generation_id,
            "config_format": normalized.format,
            "config_revision": normalized.config_revision,
            "bundle_id": bundle_id,
            "schema_version": schema_version,
            "required_secret_ids": sorted(required_secret_ids),
        }
        try:
            _write_new(directory / "configuration.json", normalized.canonical_bytes, mode=self._configuration_file_mode)
            _write_new(directory / "metadata.json", _json_bytes(metadata), mode=self._configuration_file_mode)
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
        except BaseException:
            self._remove_incomplete(directory)
            raise
        return self.load_config(generation_id)

    def install_secrets(self, snapshot: SecretSnapshot) -> SecretGeneration:
        self._require_initialized()
        generation_id = _new_id("secret")
        directory = self._create_generation_directory(
            "secret-generations", generation_id, base=self.secret_root
        )
        values = {logical_id: snapshot.resolve(logical_id) for logical_id in sorted(snapshot.present_ids)}
        if not all(_LOGICAL_SECRET_ID.fullmatch(key) is not None and isinstance(value, str) for key, value in values.items()):
            self._remove_incomplete(directory)
            raise GenerationIntegrityError("Secret snapshot contains invalid logical IDs or values.")
        metadata = {"format": SECRET_GENERATION_FORMAT, "generation_id": generation_id}
        status = {
            "format": SECRET_STATUS_FORMAT,
            "generation_id": generation_id,
            "state": "available",
            "raw_present": True,
            "revoked_at": None,
            "replaced_by": None,
        }
        status_path = self.secret_root / "secret-status" / f"{generation_id}.json"
        status_created = False
        try:
            _write_new(directory / "secrets.json", _json_bytes(values), mode=0o600)
            _write_new(directory / "metadata.json", _json_bytes(metadata), mode=0o600)
            _write_new(status_path, _json_bytes(status), mode=0o600)
            status_created = True
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
            _fsync_directory(status_path.parent)
        except BaseException:
            self._remove_incomplete(directory)
            if status_created:
                try:
                    status_path.unlink()
                except FileNotFoundError:
                    pass
            raise
        return self.load_secrets(generation_id)

    def create_activation(self, config_generation_id: str, secret_generation_id: str) -> ActivationGeneration:
        config = self.load_config(config_generation_id)
        secret_generation = self.load_secrets(secret_generation_id)
        if secret_generation.state != "available":
            raise SecretGenerationRevokedError("Revoked secret generation cannot be activated.")
        missing = config.required_secret_ids - secret_generation.snapshot.present_ids
        if missing:
            raise GenerationStoreError("Secret generation is missing required secrets for the configuration generation.")
        self._require_lineage(config.bundle_id)
        self._require_compatible(config.schema_version)
        generation_id = _new_id("activation")
        directory = self._create_generation_directory("activations", generation_id)
        metadata = {
            "format": ACTIVATION_GENERATION_FORMAT,
            "generation_id": generation_id,
            "config_generation_id": config_generation_id,
            "secret_generation_id": secret_generation_id,
        }
        try:
            _write_new(directory / "metadata.json", _json_bytes(metadata), mode=0o600)
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
        except BaseException:
            self._remove_incomplete(directory)
            raise
        return self.load_activation(generation_id)

    def _replace_selected_pointer(
        self,
        activation_generation_id: str,
        *,
        operation_id: str,
        selection_revision: int,
        satellite_projection_activation_ids: Mapping[str, str],
    ) -> SelectedActivation:
        selected = self._resolve_activation(activation_generation_id)
        if selected.secrets.state != "available":
            raise SecretGenerationRevokedError("Revoked secret generation cannot be selected.")
        if _SELECTION_OPERATION_ID.fullmatch(operation_id) is None:
            raise GenerationIntegrityError("Selection operation identity is invalid.")
        _previous_operation, previous_revision = self.selection_metadata()
        expected_revision = previous_revision + 1
        if not isinstance(selection_revision, int) or selection_revision != expected_revision:
            raise GenerationIntegrityError("Selection revision must increase monotonically by one.")
        projection_map = self._validate_satellite_projection_activation_ids(
            selected.config, satellite_projection_activation_ids
        )
        pointer = {
            "format": SELECTED_POINTER_FORMAT,
            "operation_id": operation_id,
            "selection_revision": selection_revision,
            "activation_generation_id": selected.activation.generation_id,
            "config_generation_id": selected.config.generation_id,
            "secret_generation_id": selected.secrets.generation_id,
            "satellite_projection_activation_ids": projection_map,
        }
        temporary = self.root / f".selected-{secrets.token_hex(16)}.tmp"
        try:
            _write_new(temporary, _json_bytes(pointer), mode=self._configuration_file_mode)
            os.replace(temporary, self.root / "selected.json")
            _fsync_directory(self.root)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return SelectedActivation(
            activation=selected.activation,
            config=selected.config,
            secrets=selected.secrets,
            selection_operation_id=operation_id,
            selection_revision=selection_revision,
            satellite_projection_activation_ids=MappingProxyType(dict(projection_map)),
        )

    def load_selected(self) -> SelectedActivation:
        pointer = _require_exact_mapping(
            _read_json(self.root / "selected.json"),
            {
                "format",
                "operation_id",
                "selection_revision",
                "activation_generation_id",
                "config_generation_id",
                "secret_generation_id",
                "satellite_projection_activation_ids",
            },
            artifact="selected pointer",
        )
        if pointer["format"] != SELECTED_POINTER_FORMAT:
            raise GenerationIntegrityError("Selected pointer format is unsupported.")
        if not isinstance(pointer["operation_id"], str) or _SELECTION_OPERATION_ID.fullmatch(pointer["operation_id"]) is None:
            raise GenerationIntegrityError("Selected pointer operation identity is invalid.")
        if not isinstance(pointer["selection_revision"], int) or pointer["selection_revision"] < 1:
            raise GenerationIntegrityError("Selected pointer revision is invalid.")
        selected = self._resolve_activation(pointer["activation_generation_id"])
        if selected.secrets.state != "available":
            raise SecretGenerationRevokedError("Selected activation references a revoked secret generation.")
        if pointer["config_generation_id"] != selected.config.generation_id:
            raise GenerationIntegrityError("Selected pointer config reference does not match its activation.")
        if pointer["secret_generation_id"] != selected.secrets.generation_id:
            raise GenerationIntegrityError("Selected pointer secret reference does not match its activation.")
        projection_map = self._validate_satellite_projection_activation_ids(
            selected.config, pointer["satellite_projection_activation_ids"]
        )
        return SelectedActivation(
            activation=selected.activation,
            config=selected.config,
            secrets=selected.secrets,
            selection_operation_id=pointer["operation_id"],
            selection_revision=pointer["selection_revision"],
            satellite_projection_activation_ids=MappingProxyType(dict(projection_map)),
        )

    def _validate_satellite_projection_activation_ids(
        self,
        config: ConfigGeneration,
        value: object,
    ) -> dict[str, str]:
        if not isinstance(value, Mapping) or not all(
            isinstance(satellite_id, str)
            and _BUNDLE_ID.fullmatch(satellite_id) is not None
            and isinstance(activation_id, str)
            and _SATELLITE_ACTIVATION_ID.fullmatch(activation_id) is not None
            for satellite_id, activation_id in value.items()
        ):
            raise GenerationIntegrityError("Selected satellite projection activation map is invalid.")
        roles = config.configuration.get("roles")
        satellites_role = roles.get("satellites.yaml") if isinstance(roles, Mapping) else None
        satellites = satellites_role.get("satellites") if isinstance(satellites_role, Mapping) else None
        if not isinstance(satellites, tuple):
            raise GenerationIntegrityError("Selected satellite inventory is invalid.")
        expected = {
            item["id"]
            for item in satellites
            if isinstance(item, Mapping) and item.get("enabled") is True and isinstance(item.get("id"), str)
        }
        if set(value) != expected:
            raise GenerationIntegrityError("Selected satellite projection activation map is incomplete.")
        from .projection_generations import SatelliteProjectionGenerationStore

        projections = SatelliteProjectionGenerationStore(self)
        result = dict(sorted(value.items()))
        for satellite_id, activation_id in result.items():
            installed = projections.load_installed(satellite_id, activation_id)
            if installed.activation.source_config_revision != config.config_revision:
                raise GenerationIntegrityError(
                    "Selected satellite projection activation targets a different Brain configuration revision."
                )
        return result

    def selection_metadata(self) -> tuple[str | None, int]:
        path = self.root / "selected.json"
        if not path.exists():
            return None, 0
        selected = self.load_selected()
        return selected.selection_operation_id, selected.selection_revision

    def load_config(self, generation_id: str) -> ConfigGeneration:
        directory = self._generation_directory("config-generations", generation_id, _CONFIG_ID)
        metadata = _require_exact_mapping(
            _read_json(directory / "metadata.json"),
            {
                "format",
                "generation_id",
                "config_format",
                "config_revision",
                "bundle_id",
                "schema_version",
                "required_secret_ids",
            },
            artifact="config generation",
        )
        if metadata["format"] != CONFIG_GENERATION_FORMAT or metadata["generation_id"] != generation_id:
            raise GenerationIntegrityError("Config generation identity does not match its directory.")
        if metadata["config_format"] != CONFIG_FORMAT or not isinstance(metadata["config_revision"], str):
            raise GenerationIntegrityError("Config generation uses an unsupported canonical format.")
        canonical_bytes = _read_bytes(directory / "configuration.json")
        revision = f"{CONFIG_REVISION_PREFIX}{hashlib.sha256(canonical_bytes).hexdigest()}"
        if revision != metadata["config_revision"]:
            raise GenerationIntegrityError("Config generation content does not match its revision.")
        configuration = _read_json(directory / "configuration.json")
        if not isinstance(configuration, dict):
            raise GenerationIntegrityError("Config generation payload must be a JSON object.")
        if canonicalize_json(configuration) != canonical_bytes:
            raise GenerationIntegrityError("Config generation is not canonical JSON.")
        if configuration.get("bundle_id") != metadata["bundle_id"]:
            raise GenerationIntegrityError("Config generation bundle identity is inconsistent.")
        if configuration.get("schema_version") != metadata["schema_version"]:
            raise GenerationIntegrityError("Config generation schema identity is inconsistent.")
        required_secret_ids = metadata["required_secret_ids"]
        if (
            not isinstance(required_secret_ids, list)
            or required_secret_ids != sorted(set(required_secret_ids))
            or not all(isinstance(item, str) and _LOGICAL_SECRET_ID.fullmatch(item) for item in required_secret_ids)
        ):
            raise GenerationIntegrityError("Config generation required-secret metadata is invalid.")
        self._require_lineage(metadata["bundle_id"])
        self._require_compatible(metadata["schema_version"])
        return ConfigGeneration(
            generation_id=generation_id,
            config_revision=metadata["config_revision"],
            bundle_id=metadata["bundle_id"],
            schema_version=metadata["schema_version"],
            required_secret_ids=frozenset(required_secret_ids),
            configuration=_freeze(configuration),
        )

    def load_secrets(self, generation_id: str) -> SecretGeneration:
        directory = self._generation_directory(
            "secret-generations", generation_id, _SECRET_GENERATION_ID, base=self.secret_root
        )
        metadata = _require_exact_mapping(
            _read_json(directory / "metadata.json"),
            {"format", "generation_id"},
            artifact="secret generation",
        )
        if metadata != {"format": SECRET_GENERATION_FORMAT, "generation_id": generation_id}:
            raise GenerationIntegrityError("Secret generation identity does not match its directory.")
        status = self._load_secret_status(generation_id)
        if not status["raw_present"]:
            raise SecretGenerationPrunedError("Secret generation raw values have been pruned.")
        values = _read_json(directory / "secrets.json")
        if not isinstance(values, dict) or not all(
            isinstance(key, str)
            and _LOGICAL_SECRET_ID.fullmatch(key) is not None
            and isinstance(value, str)
            for key, value in values.items()
        ):
            raise GenerationIntegrityError("Secret generation payload is invalid.")
        return SecretGeneration(
            generation_id=generation_id,
            snapshot=SecretSnapshot(values),
            state=status["state"],
            raw_present=status["raw_present"],
        )

    def revoke_secret_generation(self, generation_id: str, *, replaced_by: str) -> None:
        self.begin_secret_retirement(generation_id, replaced_by=replaced_by)
        self.finalize_secret_retirement(generation_id, replaced_by=replaced_by)

    def begin_secret_retirement(self, generation_id: str, *, replaced_by: str) -> None:
        current = self.load_selected()
        if current.secrets.generation_id == generation_id:
            raise GenerationStoreError("Cannot revoke the currently selected secret generation.")
        if current.secrets.generation_id != replaced_by:
            raise GenerationStoreError("Replacement must be the currently selected secret generation.")
        replacement = self.load_secrets(replaced_by)
        if replacement.state != "available":
            raise SecretGenerationRevokedError("Replacement secret generation is not activatable.")
        status = self._load_secret_status(generation_id)
        if status["state"] in {"retirement_pending", "revoked"}:
            if status["replaced_by"] != replaced_by:
                raise GenerationIntegrityError("Secret generation has conflicting revocation metadata.")
            return
        status.update(
            state="retirement_pending",
            revoked_at=None,
            replaced_by=replaced_by,
        )
        _atomic_replace(self.secret_root / "secret-status" / f"{generation_id}.json", _json_bytes(status))

    def finalize_secret_retirement(self, generation_id: str, *, replaced_by: str) -> None:
        current = self.load_selected()
        if current.secrets.generation_id != replaced_by:
            raise GenerationStoreError("Secret retirement replacement must remain selected.")
        status = self._load_secret_status(generation_id)
        if status["state"] == "revoked":
            if status["replaced_by"] != replaced_by:
                raise GenerationIntegrityError("Secret generation has conflicting revocation metadata.")
            return
        if status["state"] != "retirement_pending" or status["replaced_by"] != replaced_by:
            raise GenerationStoreError("Secret generation is not pending retirement by this replacement.")
        status.update(state="revoked", revoked_at=datetime.now(UTC).isoformat())
        _atomic_replace(self.secret_root / "secret-status" / f"{generation_id}.json", _json_bytes(status))

    def restore_pending_secret_retirement(self, generation_id: str, *, replaced_by: str) -> None:
        status = self._load_secret_status(generation_id)
        if status["state"] == "available":
            return
        if status["state"] != "retirement_pending" or status["replaced_by"] != replaced_by:
            raise GenerationStoreError("Only a matching pending secret retirement can be restored.")
        status.update(state="available", revoked_at=None, replaced_by=None)
        _atomic_replace(self.secret_root / "secret-status" / f"{generation_id}.json", _json_bytes(status))

    def prune_revoked_secret_values(self, *, retain: int = 1) -> tuple[str, ...]:
        if retain < 0:
            raise ValueError("Retained revoked secret generation count cannot be negative.")
        statuses: list[dict[str, Any]] = []
        status_directory = self.secret_root / "secret-status"
        for path in status_directory.glob("secret_*.json"):
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(self.secret_root):
                raise GenerationIntegrityError("Secret lifecycle status escapes the installed store.")
            status = self._load_secret_status(path.stem)
            if status["state"] == "revoked" and status["raw_present"]:
                statuses.append(status)
        statuses.sort(key=lambda item: str(item["revoked_at"]), reverse=True)
        pruned: list[str] = []
        for status in statuses[retain:]:
            generation_id = status["generation_id"]
            raw_path = self.secret_root / "secret-generations" / generation_id / "secrets.json"
            try:
                raw_path.unlink()
            except FileNotFoundError:
                pass
            _fsync_directory(raw_path.parent)
            status["raw_present"] = False
            _atomic_replace(status_directory / f"{generation_id}.json", _json_bytes(status))
            pruned.append(generation_id)
        return tuple(pruned)

    def secret_generation_status(self, generation_id: str) -> Mapping[str, Any]:
        return MappingProxyType(dict(self._load_secret_status(generation_id)))

    def discard_activation(self, generation_id: str) -> None:
        selected_path = self.root / "selected.json"
        if selected_path.exists() and self.load_selected().activation.generation_id == generation_id:
            raise GenerationStoreError("Cannot discard the selected activation generation.")
        directory = self._generation_directory("activations", generation_id, _ACTIVATION_ID)
        self._remove_incomplete(directory)
        _fsync_directory(directory.parent)

    def discard_secret_generation(self, generation_id: str) -> None:
        selected_path = self.root / "selected.json"
        if selected_path.exists() and self.load_selected().secrets.generation_id == generation_id:
            raise GenerationStoreError("Cannot discard the selected secret generation.")
        for activation_directory in (self.root / "activations").glob("activation_*"):
            if activation_directory.is_symlink() or not activation_directory.resolve(strict=True).is_relative_to(self.root):
                raise GenerationIntegrityError("Activation generation escapes the installed store.")
            activation = self.load_activation(activation_directory.name)
            if activation.secret_generation_id == generation_id:
                raise GenerationStoreError("Cannot discard a secret generation referenced by an activation.")
        directory = self._generation_directory(
            "secret-generations", generation_id, _SECRET_GENERATION_ID, base=self.secret_root
        )
        self._remove_incomplete(directory)
        try:
            (self.secret_root / "secret-status" / f"{generation_id}.json").unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(directory.parent)
        _fsync_directory(self.secret_root / "secret-status")

    def load_activation(self, generation_id: str) -> ActivationGeneration:
        directory = self._generation_directory("activations", generation_id, _ACTIVATION_ID)
        metadata = _require_exact_mapping(
            _read_json(directory / "metadata.json"),
            {"format", "generation_id", "config_generation_id", "secret_generation_id"},
            artifact="activation generation",
        )
        if metadata["format"] != ACTIVATION_GENERATION_FORMAT or metadata["generation_id"] != generation_id:
            raise GenerationIntegrityError("Activation generation identity does not match its directory.")
        if not isinstance(metadata["config_generation_id"], str) or not isinstance(metadata["secret_generation_id"], str):
            raise GenerationIntegrityError("Activation generation references are invalid.")
        return ActivationGeneration(
            generation_id=generation_id,
            config_generation_id=metadata["config_generation_id"],
            secret_generation_id=metadata["secret_generation_id"],
        )

    def _resolve_activation(self, generation_id: str) -> SelectedActivation:
        activation = self.load_activation(generation_id)
        config = self.load_config(activation.config_generation_id)
        secret_generation = self.load_secrets(activation.secret_generation_id)
        return SelectedActivation(activation=activation, config=config, secrets=secret_generation)

    def _load_secret_status(self, generation_id: str) -> dict[str, Any]:
        if not isinstance(generation_id, str) or _SECRET_GENERATION_ID.fullmatch(generation_id) is None:
            raise GenerationIntegrityError("Secret generation identifier is invalid.")
        status = _require_exact_mapping(
            _read_json(self.secret_root / "secret-status" / f"{generation_id}.json"),
            {"format", "generation_id", "state", "raw_present", "revoked_at", "replaced_by"},
            artifact="secret generation status",
        )
        valid = (
            status["format"] == SECRET_STATUS_FORMAT
            and status["generation_id"] == generation_id
            and status["state"] in {"available", "retirement_pending", "revoked"}
            and isinstance(status["raw_present"], bool)
            and (status["revoked_at"] is None or isinstance(status["revoked_at"], str))
            and (status["replaced_by"] is None or isinstance(status["replaced_by"], str))
        )
        if not valid:
            raise GenerationIntegrityError("Secret generation lifecycle status is invalid.")
        if status["state"] == "available" and (status["revoked_at"] is not None or status["replaced_by"] is not None):
            raise GenerationIntegrityError("Available secret generation carries revocation metadata.")
        if status["state"] == "retirement_pending" and (
            status["revoked_at"] is not None or status["replaced_by"] is None
        ):
            raise GenerationIntegrityError("Pending secret retirement has invalid lifecycle metadata.")
        if status["state"] == "revoked" and (status["revoked_at"] is None or status["replaced_by"] is None):
            raise GenerationIntegrityError("Revoked secret generation lacks revocation metadata.")
        if status["replaced_by"] is not None and _SECRET_GENERATION_ID.fullmatch(status["replaced_by"]) is None:
            raise GenerationIntegrityError("Secret generation replacement identity is invalid.")
        return status

    def _require_initialized(self) -> dict[str, Any]:
        binding = _require_exact_mapping(
            _read_json(self.root / "store.json"),
            {"format", "bundle_id"},
            artifact="store binding",
        )
        if binding["format"] != STORE_FORMAT or not isinstance(binding["bundle_id"], str):
            raise GenerationIntegrityError("Installed store binding is invalid or unsupported.")
        return binding

    def _require_lineage(self, bundle_id: str) -> None:
        binding = self._require_initialized()
        if binding["format"] != STORE_FORMAT or binding["bundle_id"] != bundle_id:
            raise StoreLineageConflict("Configuration does not match the installed store bundle lineage.")

    def _require_compatible(self, schema_version: Any) -> None:
        if not isinstance(schema_version, int) or schema_version not in self.supported_schema_versions:
            raise GenerationCompatibilityError("Configuration schema is incompatible with this Oracle runtime.")

    def _create_generation_directory(
        self,
        collection: str,
        generation_id: str,
        *,
        base: Path | None = None,
    ) -> Path:
        if _GENERATION_ID.fullmatch(generation_id) is None:
            raise GenerationStoreError("Invalid generation identifier.")
        confined_root = self.root if base is None else base
        parent = confined_root / collection
        configuration_collection = base is None and self._split_standard_store
        parent_mode = 0o2750 if configuration_collection else 0o700
        generation_mode = 0o750 if configuration_collection else 0o700
        parent.mkdir(mode=parent_mode, exist_ok=True)
        parent.chmod(parent_mode)
        if parent.is_symlink() or not parent.resolve(strict=True).is_relative_to(confined_root):
            raise GenerationIntegrityError("Generation collection escapes the installed store.")
        directory = parent / generation_id
        directory.mkdir(mode=generation_mode)
        return directory

    def _generation_directory(
        self,
        collection: str,
        generation_id: str,
        pattern: re.Pattern[str],
        *,
        base: Path | None = None,
    ) -> Path:
        if not isinstance(generation_id, str) or pattern.fullmatch(generation_id) is None:
            raise GenerationIntegrityError("Generation identifier is not path-safe or has the wrong type.")
        confined_root = self.root if base is None else base
        directory = confined_root / collection / generation_id
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or not directory.resolve(strict=True).is_relative_to(confined_root)
        ):
            raise GenerationIntegrityError("Generation directory is missing or not a confined real directory.")
        return directory

    @staticmethod
    def _remove_incomplete(directory: Path) -> None:
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
