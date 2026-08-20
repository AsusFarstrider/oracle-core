from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import secrets
from typing import Any

from .generations import (
    GenerationIntegrityError,
    GenerationStore,
    GenerationStoreError,
    _fsync_directory,
    _read_bytes,
    _read_json,
    _require_exact_mapping,
    _write_new,
)
from .normalization import canonicalize_json
from .projections import (
    PROJECTION_REVISION_PREFIX,
    GeneratedSatelliteProjection,
    SatelliteProjection,
)
from .secrets import SecretSnapshot


PROJECTION_GENERATION_FORMAT = "oracle-satellite-projection-generation-v1"
PROJECTION_SECRET_GENERATION_FORMAT = "oracle-satellite-projection-secret-generation-v1"
PROJECTION_ACTIVATION_FORMAT = "oracle-satellite-projection-activation-v1"

_SATELLITE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
_PROJECTION_GENERATION_ID = re.compile(r"^sat_projection_[0-9a-f]{32}$")
_SECRET_GENERATION_ID = re.compile(r"^sat_secret_[0-9a-f]{32}$")
_ACTIVATION_ID = re.compile(r"^sat_activation_[0-9a-f]{32}$")
_LOGICAL_SECRET_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CONFIG_REVISION = re.compile(r"^oracle-config-v2:sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class SatelliteProjectionGeneration:
    generation_id: str
    satellite_id: str
    projection_revision: str
    required_secret_ids: frozenset[str]
    projection: SatelliteProjection


@dataclass(frozen=True)
class SatelliteProjectionSecretGeneration:
    generation_id: str
    satellite_id: str
    snapshot: SecretSnapshot


@dataclass(frozen=True)
class SatelliteProjectionActivation:
    generation_id: str
    satellite_id: str
    projection_generation_id: str
    secret_generation_id: str
    source_config_revision: str


@dataclass(frozen=True)
class InstalledSatelliteProjection:
    activation: SatelliteProjectionActivation
    projection: SatelliteProjectionGeneration
    secrets: SatelliteProjectionSecretGeneration


class SatelliteProjectionGenerationStore:
    """Persist immutable Brain-side desired artifacts for one satellite projection."""

    def __init__(self, store: GenerationStore) -> None:
        store.validate_initialized()
        self.store = store
        self.root = Path(store.root) / "projections"

    def install(self, generated: GeneratedSatelliteProjection) -> InstalledSatelliteProjection:
        projection = self.find_projection(generated) or self.install_projection(generated)
        local_secrets = self.find_secrets(generated.projection.satellite_id, generated.secrets) or self.install_secrets(
            generated.projection.satellite_id, generated.secrets
        )
        activation = self.create_activation(
            generated.projection.satellite_id,
            projection.generation_id,
            local_secrets.generation_id,
            source_config_revision=generated.source_config_revision,
        )
        return InstalledSatelliteProjection(activation, projection, local_secrets)

    def install_projection(self, generated: GeneratedSatelliteProjection) -> SatelliteProjectionGeneration:
        satellite_id = generated.projection.satellite_id
        self._validate_generated(generated)
        generation_id = self._new_id("sat_projection")
        directory = self._create_directory(satellite_id, "projection-generations", generation_id)
        metadata = {
            "format": PROJECTION_GENERATION_FORMAT,
            "generation_id": generation_id,
            "satellite_id": satellite_id,
            "projection_revision": generated.projection.projection_revision,
            "required_secret_ids": sorted(generated.required_secret_ids),
        }
        try:
            _write_new(directory / "projection.json", generated.canonical_bytes, mode=0o600)
            _write_new(directory / "metadata.json", self._json_bytes(metadata), mode=0o600)
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
        except BaseException:
            self._remove_incomplete(directory)
            raise
        return self.load_projection(satellite_id, generation_id)

    def install_secrets(
        self,
        satellite_id: str,
        snapshot: SecretSnapshot,
    ) -> SatelliteProjectionSecretGeneration:
        self._satellite_root(satellite_id)
        values = {logical_id: snapshot.resolve(logical_id) for logical_id in sorted(snapshot.present_ids)}
        if not all(
            _LOGICAL_SECRET_ID.fullmatch(logical_id) is not None and isinstance(value, str)
            for logical_id, value in values.items()
        ):
            raise GenerationIntegrityError("Satellite projection secret snapshot is invalid.")
        generation_id = self._new_id("sat_secret")
        directory = self._create_directory(satellite_id, "secret-generations", generation_id)
        metadata = {
            "format": PROJECTION_SECRET_GENERATION_FORMAT,
            "generation_id": generation_id,
            "satellite_id": satellite_id,
            "logical_secret_ids": sorted(values),
        }
        try:
            _write_new(directory / "secrets.json", self._json_bytes(values), mode=0o600)
            _write_new(directory / "metadata.json", self._json_bytes(metadata), mode=0o600)
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
        except BaseException:
            self._remove_incomplete(directory)
            raise
        return self.load_secrets(satellite_id, generation_id)

    def create_activation(
        self,
        satellite_id: str,
        projection_generation_id: str,
        secret_generation_id: str,
        *,
        source_config_revision: str,
    ) -> SatelliteProjectionActivation:
        projection = self.load_projection(satellite_id, projection_generation_id)
        local_secrets = self.load_secrets(satellite_id, secret_generation_id)
        if local_secrets.snapshot.present_ids != projection.required_secret_ids:
            raise GenerationStoreError(
                "Satellite projection activation requires exactly its minimal local secret set."
            )
        if not isinstance(source_config_revision, str) or _CONFIG_REVISION.fullmatch(source_config_revision) is None:
            raise GenerationStoreError("Satellite projection activation source configuration revision is invalid.")
        generation_id = self._new_id("sat_activation")
        directory = self._create_directory(satellite_id, "activations", generation_id)
        metadata = {
            "format": PROJECTION_ACTIVATION_FORMAT,
            "generation_id": generation_id,
            "satellite_id": satellite_id,
            "projection_generation_id": projection_generation_id,
            "secret_generation_id": secret_generation_id,
            "source_config_revision": source_config_revision,
        }
        try:
            _write_new(directory / "metadata.json", self._json_bytes(metadata), mode=0o600)
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
        except BaseException:
            self._remove_incomplete(directory)
            raise
        return self.load_activation(satellite_id, generation_id)

    def load_installed(self, satellite_id: str, activation_id: str) -> InstalledSatelliteProjection:
        activation = self.load_activation(satellite_id, activation_id)
        projection = self.load_projection(satellite_id, activation.projection_generation_id)
        local_secrets = self.load_secrets(satellite_id, activation.secret_generation_id)
        if local_secrets.snapshot.present_ids != projection.required_secret_ids:
            raise GenerationIntegrityError("Satellite projection activation secret coverage is inconsistent.")
        return InstalledSatelliteProjection(activation, projection, local_secrets)

    def load_projection(self, satellite_id: str, generation_id: str) -> SatelliteProjectionGeneration:
        directory = self._generation_directory(
            satellite_id,
            "projection-generations",
            generation_id,
            _PROJECTION_GENERATION_ID,
        )
        metadata = _require_exact_mapping(
            _read_json(directory / "metadata.json"),
            {
                "format",
                "generation_id",
                "satellite_id",
                "projection_revision",
                "required_secret_ids",
            },
            artifact="satellite projection generation",
        )
        if (
            metadata["format"] != PROJECTION_GENERATION_FORMAT
            or metadata["generation_id"] != generation_id
            or metadata["satellite_id"] != satellite_id
        ):
            raise GenerationIntegrityError("Satellite projection generation identity is inconsistent.")
        required_ids = metadata["required_secret_ids"]
        if (
            not isinstance(required_ids, list)
            or required_ids != sorted(set(required_ids))
            or not all(isinstance(item, str) and _LOGICAL_SECRET_ID.fullmatch(item) for item in required_ids)
        ):
            raise GenerationIntegrityError("Satellite projection required-secret metadata is invalid.")
        canonical_bytes = _read_bytes(directory / "projection.json")
        try:
            primitive = json.loads(canonical_bytes)
            projection = SatelliteProjection.model_validate(primitive)
        except Exception as exc:
            raise GenerationIntegrityError("Satellite projection payload is invalid.") from exc
        if canonicalize_json(projection.model_dump(mode="json")) != canonical_bytes:
            raise GenerationIntegrityError("Satellite projection payload is not canonical JSON.")
        self._validate_projection_revision(projection)
        if frozenset(required_ids) != self._required_secret_ids(projection):
            raise GenerationIntegrityError("Satellite projection required-secret metadata is incomplete.")
        if (
            projection.satellite_id != satellite_id
            or projection.projection_revision != metadata["projection_revision"]
        ):
            raise GenerationIntegrityError("Satellite projection metadata does not match its payload.")
        return SatelliteProjectionGeneration(
            generation_id,
            satellite_id,
            projection.projection_revision,
            frozenset(required_ids),
            projection,
        )

    def load_secrets(self, satellite_id: str, generation_id: str) -> SatelliteProjectionSecretGeneration:
        directory = self._generation_directory(
            satellite_id,
            "secret-generations",
            generation_id,
            _SECRET_GENERATION_ID,
        )
        metadata = _require_exact_mapping(
            _read_json(directory / "metadata.json"),
            {"format", "generation_id", "satellite_id", "logical_secret_ids"},
            artifact="satellite projection secret generation",
        )
        if (
            metadata["format"] != PROJECTION_SECRET_GENERATION_FORMAT
            or metadata["generation_id"] != generation_id
            or metadata["satellite_id"] != satellite_id
        ):
            raise GenerationIntegrityError("Satellite projection secret generation identity is inconsistent.")
        values = _read_json(directory / "secrets.json")
        if not isinstance(values, dict) or not all(
            isinstance(key, str)
            and _LOGICAL_SECRET_ID.fullmatch(key) is not None
            and isinstance(value, str)
            for key, value in values.items()
        ):
            raise GenerationIntegrityError("Satellite projection secret payload is invalid.")
        if metadata["logical_secret_ids"] != sorted(values):
            raise GenerationIntegrityError("Satellite projection secret metadata does not match its payload.")
        return SatelliteProjectionSecretGeneration(generation_id, satellite_id, SecretSnapshot(values))

    def load_activation(self, satellite_id: str, generation_id: str) -> SatelliteProjectionActivation:
        directory = self._generation_directory(
            satellite_id,
            "activations",
            generation_id,
            _ACTIVATION_ID,
        )
        metadata = _require_exact_mapping(
            _read_json(directory / "metadata.json"),
            {
                "format",
                "generation_id",
                "satellite_id",
                "projection_generation_id",
                "secret_generation_id",
                "source_config_revision",
            },
            artifact="satellite projection activation",
        )
        if (
            metadata["format"] != PROJECTION_ACTIVATION_FORMAT
            or metadata["generation_id"] != generation_id
            or metadata["satellite_id"] != satellite_id
            or not isinstance(metadata["projection_generation_id"], str)
            or not isinstance(metadata["secret_generation_id"], str)
            or not isinstance(metadata["source_config_revision"], str)
            or _CONFIG_REVISION.fullmatch(metadata["source_config_revision"]) is None
        ):
            raise GenerationIntegrityError("Satellite projection activation is invalid.")
        return SatelliteProjectionActivation(
            generation_id,
            satellite_id,
            metadata["projection_generation_id"],
            metadata["secret_generation_id"],
            metadata["source_config_revision"],
        )

    def find_projection(self, generated: GeneratedSatelliteProjection) -> SatelliteProjectionGeneration | None:
        satellite_id = generated.projection.satellite_id
        parent = self._satellite_root(satellite_id) / "projection-generations"
        if not parent.exists():
            return None
        for directory in sorted(parent.glob("sat_projection_*")):
            projection = self.load_projection(satellite_id, directory.name)
            if projection.projection_revision == generated.projection.projection_revision:
                if canonicalize_json(projection.projection.model_dump(mode="json")) != generated.canonical_bytes:
                    raise GenerationIntegrityError("Equal satellite projection revisions have unequal payloads.")
                return projection
        return None

    def find_secrets(
        self, satellite_id: str, snapshot: SecretSnapshot
    ) -> SatelliteProjectionSecretGeneration | None:
        parent = self._satellite_root(satellite_id) / "secret-generations"
        if not parent.exists():
            return None
        for directory in sorted(parent.glob("sat_secret_*")):
            generation = self.load_secrets(satellite_id, directory.name)
            if generation.snapshot._matches(snapshot):
                return generation
        return None

    def _validate_generated(self, generated: GeneratedSatelliteProjection) -> None:
        projection = generated.projection
        if projection.satellite_id != projection.satellite_id.strip():
            raise GenerationIntegrityError("Satellite projection identity is invalid.")
        self._satellite_root(projection.satellite_id)
        if canonicalize_json(projection.model_dump(mode="json")) != generated.canonical_bytes:
            raise GenerationIntegrityError("Generated satellite projection bytes do not match its model.")
        self._validate_projection_revision(projection)
        if generated.secrets.present_ids != generated.required_secret_ids:
            raise GenerationIntegrityError("Generated satellite projection secrets are not minimal and complete.")

    @staticmethod
    def _validate_projection_revision(projection: SatelliteProjection) -> None:
        payload = projection.model_dump(mode="json")
        claimed = payload.pop("projection_revision")
        expected = f"{PROJECTION_REVISION_PREFIX}{hashlib.sha256(canonicalize_json(payload)).hexdigest()}"
        if claimed != expected:
            raise GenerationIntegrityError("Satellite projection content does not match its revision.")

    @staticmethod
    def _required_secret_ids(projection: SatelliteProjection) -> frozenset[str]:
        ids: set[str] = {projection.configuration.brain_client.credential_secret}
        interaction = projection.configuration.interaction_runtime
        if interaction is not None:
            ids.add(interaction.control_service_client.credential_secret)
        control = projection.configuration.control_service
        if control is not None:
            ids.add(control.credential_secret)
            if control.music is not None:
                ids.add(control.music.provider.credential_secret)
        return frozenset(ids)

    def _satellite_root(self, satellite_id: str) -> Path:
        if not isinstance(satellite_id, str) or _SATELLITE_ID.fullmatch(satellite_id) is None:
            raise GenerationIntegrityError("Satellite projection identity is invalid.")
        root_created = not self.root.exists()
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink() or not self.root.resolve(strict=True).is_relative_to(self.store.root):
            raise GenerationIntegrityError("Satellite projection store escapes the installed store.")
        if root_created:
            _fsync_directory(self.store.root)
        satellite_root = self.root / satellite_id
        satellite_created = not satellite_root.exists()
        satellite_root.mkdir(mode=0o700, exist_ok=True)
        if satellite_root.is_symlink() or not satellite_root.resolve(strict=True).is_relative_to(self.store.root):
            raise GenerationIntegrityError("Satellite projection installation escapes the installed store.")
        if satellite_created:
            _fsync_directory(self.root)
        return satellite_root

    def _create_directory(self, satellite_id: str, collection: str, generation_id: str) -> Path:
        parent = self._satellite_root(satellite_id) / collection
        parent_created = not parent.exists()
        parent.mkdir(mode=0o700, exist_ok=True)
        if parent.is_symlink() or not parent.resolve(strict=True).is_relative_to(self.store.root):
            raise GenerationIntegrityError("Satellite projection collection escapes the installed store.")
        if parent_created:
            _fsync_directory(parent.parent)
        directory = parent / generation_id
        directory.mkdir(mode=0o700)
        return directory

    def _generation_directory(
        self,
        satellite_id: str,
        collection: str,
        generation_id: str,
        pattern: re.Pattern[str],
    ) -> Path:
        if not isinstance(generation_id, str) or pattern.fullmatch(generation_id) is None:
            raise GenerationIntegrityError("Satellite projection generation identity is invalid.")
        directory = self._satellite_root(satellite_id) / collection / generation_id
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or not directory.resolve(strict=True).is_relative_to(self.store.root)
        ):
            raise GenerationIntegrityError("Satellite projection generation is missing or escapes the store.")
        return directory

    @staticmethod
    def _new_id(kind: str) -> str:
        return f"{kind}_{secrets.token_hex(16)}"

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"

    @staticmethod
    def _remove_incomplete(directory: Path) -> None:
        if not directory.exists() or directory.is_symlink():
            return
        for child in directory.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink()
        directory.rmdir()
        _fsync_directory(directory.parent)
