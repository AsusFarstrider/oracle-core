from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from oracle_satellite_projection import (
    SatelliteProjectionIntegrityError,
    SatelliteProjectionLocalStore,
    SelectedLocalSatelliteActivation,
)


class SatelliteRuntimeConfigurationError(SatelliteProjectionIntegrityError):
    pass


def load_runtime_compatibility_file(path: Path) -> dict[str, Any]:
    try:
        target = path.resolve(strict=True)
    except OSError as exc:
        raise SatelliteRuntimeConfigurationError(
            "Runtime compatibility file is unavailable."
        ) from exc
    if not target.is_file():
        raise SatelliteRuntimeConfigurationError("Runtime compatibility file is unavailable.")
    try:
        payload = json.loads(target.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SatelliteRuntimeConfigurationError("Runtime compatibility file is invalid.") from exc
    if not isinstance(payload, dict):
        raise SatelliteRuntimeConfigurationError("Runtime compatibility must be an object.")
    return payload


@dataclass(frozen=True)
class InteractionRuntimeEffectiveConfig:
    satellite_id: str
    source_id: str
    activation_id: str
    projection_revision: str
    configuration: Mapping[str, Any] = field(repr=False)
    brain_base_url: str
    brain_credential_secret_id: str
    brain_credential: str = field(repr=False)
    control_service_base_url: str
    control_service_credential_secret_id: str
    control_service_credential: str = field(repr=False)

    @property
    def resolved_secret_ids(self) -> frozenset[str]:
        return frozenset(
            (self.brain_credential_secret_id, self.control_service_credential_secret_id)
        )


@dataclass(frozen=True)
class ControlServiceEffectiveConfig:
    satellite_id: str
    source_id: str
    activation_id: str
    projection_revision: str
    configuration: Mapping[str, Any] = field(repr=False)
    api_credential_secret_id: str
    api_credential: str = field(repr=False)
    music_provider_credential_secret_id: str | None = None
    music_provider_credential: str | None = field(default=None, repr=False)

    @property
    def resolved_secret_ids(self) -> frozenset[str]:
        ids = {self.api_credential_secret_id}
        if self.music_provider_credential_secret_id is not None:
            ids.add(self.music_provider_credential_secret_id)
        return frozenset(ids)


def load_interaction_runtime_effective_config(
    store: SatelliteProjectionLocalStore,
) -> InteractionRuntimeEffectiveConfig:
    return build_interaction_runtime_effective_config(_load_selected(store))


def build_interaction_runtime_effective_config(
    selected: SelectedLocalSatelliteActivation,
) -> InteractionRuntimeEffectiveConfig:
    projection = selected.projection
    root = _mapping(projection["configuration"], "projected configuration")
    interaction = root["interaction_runtime"]
    if interaction is None:
        raise SatelliteRuntimeConfigurationError(
            "Selected projection does not install an interaction runtime."
        )
    interaction = _mapping(interaction, "projected interaction runtime")
    brain = _mapping(root["brain_client"], "projected Brain client")
    control = _mapping(interaction["control_service_client"], "projected interaction control client")
    brain_secret_id = _text(brain["credential_secret"], "Brain credential reference")
    control_secret_id = _text(control["credential_secret"], "control credential reference")

    return InteractionRuntimeEffectiveConfig(
        satellite_id=selected.satellite_id,
        source_id=_text(projection["source_id"], "source identity"),
        activation_id=selected.activation_id,
        projection_revision=selected.activation.projection_revision,
        configuration=interaction,
        brain_base_url=_text(brain["base_url"], "Brain endpoint"),
        brain_credential_secret_id=brain_secret_id,
        brain_credential=_secret(selected, brain_secret_id),
        control_service_base_url=_text(control["local_client_url"], "control-service endpoint"),
        control_service_credential_secret_id=control_secret_id,
        control_service_credential=_secret(selected, control_secret_id),
    )


def load_control_service_effective_config(
    store: SatelliteProjectionLocalStore,
) -> ControlServiceEffectiveConfig:
    return build_control_service_effective_config(_load_selected(store))


def build_control_service_effective_config(
    selected: SelectedLocalSatelliteActivation,
) -> ControlServiceEffectiveConfig:
    projection = selected.projection
    root = _mapping(projection["configuration"], "projected configuration")
    control = root["control_service"]
    if control is None:
        raise SatelliteRuntimeConfigurationError(
            "Selected projection does not install a control service."
        )
    control = _mapping(control, "projected control service")
    credential_id = _text(control["credential_secret"], "control credential reference")
    music_credential_id = None
    music_credential = None
    music = control["music"]
    if music is not None:
        provider = _mapping(_mapping(music, "projected music")["provider"], "projected music provider")
        music_credential_id = _text(
            provider["credential_secret"],
            "music credential reference",
        )
        music_credential = _secret(
            selected,
            music_credential_id,
        )
    return ControlServiceEffectiveConfig(
        satellite_id=selected.satellite_id,
        source_id=_text(projection["source_id"], "source identity"),
        activation_id=selected.activation_id,
        projection_revision=selected.activation.projection_revision,
        configuration=control,
        api_credential_secret_id=credential_id,
        api_credential=_secret(selected, credential_id),
        music_provider_credential_secret_id=music_credential_id,
        music_provider_credential=music_credential,
    )


def _load_selected(store: SatelliteProjectionLocalStore) -> SelectedLocalSatelliteActivation:
    selected = store.load_selected()
    if selected is None:  # pragma: no cover - required by the optional return type
        raise SatelliteRuntimeConfigurationError("No local satellite activation is selected.")
    return selected


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SatelliteRuntimeConfigurationError(f"{label} is invalid.")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SatelliteRuntimeConfigurationError(f"{label} is invalid.")
    return value


def _secret(selected: SelectedLocalSatelliteActivation, logical_id: str) -> str:
    value = selected.resolve_secret(logical_id)
    if not value:
        raise SatelliteRuntimeConfigurationError("Selected component secret is unavailable.")
    return value
