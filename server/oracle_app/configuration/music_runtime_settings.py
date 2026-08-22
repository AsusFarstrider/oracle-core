from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .domain_models import MusicConfiguration, MusicMatchingPolicy, PlexMusicProvider
from .effective import EffectiveConfig
from .satellite_fleet_runtime_settings import (
    SatelliteBrainEdgeSettings,
    SatelliteFleetRuntimeSettings,
)


@dataclass(frozen=True)
class MusicRuntimeSettings:
    """Frozen Brain execution settings for the optional music domain role."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    enabled: bool
    provider_id: str | None
    provider: PlexMusicProvider | None
    matching: MusicMatchingPolicy
    playback_targets: Mapping[str, SatelliteBrainEdgeSettings]
    provider_credential: str | None = field(default=None, repr=False)

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> MusicRuntimeSettings:
        role = effective.role("domains/music.yaml")
        if not isinstance(role, MusicConfiguration):
            raise TypeError("Effective music role does not use the executable music schema.")

        provider_id = None
        provider = None
        provider_credential = None
        playback_targets: dict[str, SatelliteBrainEdgeSettings] = {}
        if role.enabled:
            provider_id = role.provider
            if provider_id is None:
                raise ValueError("Enabled canonical music has no selected provider.")
            provider = role.providers[provider_id]
            provider_credential = effective.secrets.resolve(provider.credential_secret)
            if provider_credential is None:
                raise ValueError("Enabled canonical music lacks its provider credential.")

            fleet = SatelliteFleetRuntimeSettings.from_effective_config(effective)
            for source_id in role.playback.source_ids:
                target = fleet.control_target_for_source(source_id)
                capabilities = None if target is None else target.capabilities
                if target is None or capabilities is None or not capabilities.music_playback:
                    raise ValueError("Canonical music playback source lacks its Brain control target.")
                playback_targets[source_id] = target

        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            enabled=role.enabled,
            provider_id=provider_id,
            provider=provider,
            matching=role.matching,
            playback_targets=MappingProxyType(playback_targets),
            provider_credential=provider_credential,
        )

    def playback_target(self, source_id: str | None) -> SatelliteBrainEdgeSettings | None:
        return self.playback_targets.get(str(source_id or "").strip())
