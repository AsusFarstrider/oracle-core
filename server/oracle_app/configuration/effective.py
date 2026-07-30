from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from pydantic import ValidationError

from .generations import GenerationIntegrityError, GenerationStore
from .loader import LoadedBundle
from .model_base import ConfigurationModel
from .models import OPTIONAL_ROLE_MODELS, REQUIRED_ROLE_MODELS, validate_role
from .secrets import SecretSnapshot, collect_secret_references
from .validation import validate_cross_file_references


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class EffectiveConfig:
    """One immutable installed configuration/secret snapshot adopted by a process."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    satellite_projection_activation_ids: Mapping[str, str]
    config_revision: str
    bundle_id: str
    schema_version: int
    roles: Mapping[str, ConfigurationModel]
    secrets: SecretSnapshot

    def role(self, path: str) -> ConfigurationModel:
        try:
            return self.roles[path]
        except KeyError as exc:
            raise KeyError(f"Role {path!r} is not present in this effective configuration.") from exc


def load_effective_config(store: GenerationStore) -> EffectiveConfig:
    """Load and revalidate exactly one selected installed generation.

    This function reads only the installed generation store. It never opens the
    authored bundle and returns no object that can reload configuration later.
    """

    selected = store.load_selected()
    configuration = selected.config.configuration
    if set(configuration) != {"kind", "schema_version", "bundle_id", "roles"}:
        raise GenerationIntegrityError("Selected configuration envelope has an invalid shape.")
    if configuration["kind"] != "oracle_configuration_bundle":
        raise GenerationIntegrityError("Selected configuration kind is unsupported.")
    if configuration["schema_version"] != selected.config.schema_version:
        raise GenerationIntegrityError("Selected configuration schema identity is inconsistent.")
    if configuration["bundle_id"] != selected.config.bundle_id:
        raise GenerationIntegrityError("Selected configuration bundle identity is inconsistent.")

    raw_roles = configuration["roles"]
    if not isinstance(raw_roles, Mapping):
        raise GenerationIntegrityError("Selected configuration roles must be an object.")
    required = set(REQUIRED_ROLE_MODELS) - {"bundle.yaml"}
    allowed = required | set(OPTIONAL_ROLE_MODELS)
    if not required.issubset(raw_roles) or not set(raw_roles).issubset(allowed):
        raise GenerationIntegrityError("Selected configuration has an invalid fixed-role inventory.")

    manifest = validate_role(
        "bundle.yaml",
        {
            "kind": configuration["kind"],
            "schema_version": configuration["schema_version"],
            "bundle_id": configuration["bundle_id"],
        },
    )
    roles: dict[str, ConfigurationModel] = {"bundle.yaml": manifest}
    try:
        for path, primitive in raw_roles.items():
            if not isinstance(path, str) or not isinstance(primitive, Mapping):
                raise GenerationIntegrityError("Selected configuration role payload is invalid.")
            thawed = _thaw(primitive)
            if not isinstance(thawed, dict):
                raise GenerationIntegrityError("Selected configuration role payload is invalid.")
            model = validate_role(path, thawed)
            if model.model_dump(mode="json") != thawed:
                raise GenerationIntegrityError("Selected configuration is not a normalized typed role snapshot.")
            roles[path] = model
    except ValidationError as exc:
        raise GenerationIntegrityError("Selected configuration does not satisfy its executable schema.") from exc

    frozen_roles = MappingProxyType(roles)
    typed = LoadedBundle(
        candidate_id="candidate_00000000000000000000000000000000",
        root=Path(store.root),
        authored_revision="installed-generation",
        authored_bytes=MappingProxyType({}),
        roles=frozen_roles,
        non_authoritative_paths=(),
    )
    if validate_cross_file_references(
        household=typed.household,
        access=typed.access,
        satellites=typed.satellites,
        roles=typed.roles,
    ):
        raise GenerationIntegrityError("Selected configuration fails whole-bundle reference validation.")

    required_secret_ids = frozenset(
        use.logical_id for use in collect_secret_references(typed) if use.required
    )
    if required_secret_ids != selected.config.required_secret_ids:
        raise GenerationIntegrityError("Selected configuration required-secret metadata is inconsistent.")
    if not required_secret_ids.issubset(selected.secrets.snapshot.present_ids):
        raise GenerationIntegrityError("Selected configuration is missing required secret values.")
    if selected.selection_operation_id is None or selected.selection_revision < 1:
        raise GenerationIntegrityError("Selected configuration lacks committed selection identity.")

    return EffectiveConfig(
        activation_generation_id=selected.activation.generation_id,
        config_generation_id=selected.config.generation_id,
        secret_generation_id=selected.secrets.generation_id,
        selection_operation_id=selected.selection_operation_id,
        selection_revision=selected.selection_revision,
        satellite_projection_activation_ids=MappingProxyType(
            dict(selected.satellite_projection_activation_ids)
        ),
        config_revision=selected.config.config_revision,
        bundle_id=selected.config.bundle_id,
        schema_version=selected.config.schema_version,
        roles=frozen_roles,
        secrets=selected.secrets.snapshot,
    )
