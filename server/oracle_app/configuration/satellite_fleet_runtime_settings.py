from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .effective import EffectiveConfig
from .household_runtime_settings import HouseholdRuntimeSettings
from .models import SatelliteCapabilities, SatellitesConfiguration


@dataclass(frozen=True)
class SatelliteBrainEdgeSettings:
    satellite_id: str
    enabled: bool
    source_id: str | None
    platform: str | None
    capabilities: SatelliteCapabilities | None
    projection_activation_id: str | None
    control_service_base_url: str | None
    control_service_credential_secret: str | None
    control_service_credential: str | None = field(default=None, repr=False)

    @property
    def playback_capable(self) -> bool:
        capabilities = self.capabilities
        return bool(
            capabilities is not None
            and (capabilities.music_playback or capabilities.audiobook_playback)
        )


@dataclass(frozen=True)
class SatelliteFleetRuntimeSettings:
    """Frozen Brain-owned fleet identity, authentication, and control seam."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    satellites: Mapping[str, SatelliteBrainEdgeSettings]
    enabled_satellite_ids_by_source: Mapping[str, str]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> SatelliteFleetRuntimeSettings:
        role = effective.role("satellites.yaml")
        if not isinstance(role, SatellitesConfiguration):
            raise TypeError("Effective satellites.yaml role does not use the executable satellites schema.")
        household = HouseholdRuntimeSettings.from_effective_config(effective)
        satellites: dict[str, SatelliteBrainEdgeSettings] = {}
        by_source: dict[str, str] = {}
        for satellite in role.satellites:
            projection_activation_id = effective.satellite_projection_activation_ids.get(satellite.id)
            if satellite.enabled and projection_activation_id is None:
                raise ValueError("Enabled canonical satellite has no selected projection activation.")
            if not satellite.enabled and projection_activation_id is not None:
                raise ValueError("Disabled canonical satellite cannot have a selected projection activation.")

            source_id = satellite.source_id
            if satellite.enabled:
                source = household.source(source_id)
                if source is None or source.type != "satellite":
                    raise ValueError("Enabled canonical satellite has no valid household satellite source.")
                by_source[source.id] = satellite.id

            playback_capable = bool(
                satellite.enabled
                and satellite.capabilities is not None
                and (
                    satellite.capabilities.music_playback
                    or satellite.capabilities.audiobook_playback
                )
            )
            control_base_url = None
            control_secret_id = None
            control_credential = None
            if playback_capable:
                if satellite.control_service is None:
                    raise ValueError("Playback-capable canonical satellite lacks its control edge.")
                control_base_url = satellite.control_service.base_url
                control_secret_id = satellite.control_service.credential_secret
                control_credential = effective.secrets.resolve(control_secret_id)
                if control_base_url is None or control_credential is None:
                    raise ValueError("Playback-capable canonical satellite has an incomplete control edge.")

            satellites[satellite.id] = SatelliteBrainEdgeSettings(
                satellite_id=satellite.id,
                enabled=satellite.enabled,
                source_id=source_id,
                platform=satellite.platform,
                capabilities=satellite.capabilities,
                projection_activation_id=projection_activation_id,
                control_service_base_url=control_base_url,
                control_service_credential_secret=control_secret_id,
                control_service_credential=control_credential,
            )
        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            satellites=MappingProxyType(satellites),
            enabled_satellite_ids_by_source=MappingProxyType(by_source),
        )

    def satellite(
        self,
        satellite_id: str | None,
        *,
        enabled_only: bool = True,
    ) -> SatelliteBrainEdgeSettings | None:
        item = self.satellites.get(str(satellite_id or "").strip())
        return item if item is not None and (item.enabled or not enabled_only) else None

    def satellite_for_source(self, source_id: str | None) -> SatelliteBrainEdgeSettings | None:
        satellite_id = self.enabled_satellite_ids_by_source.get(str(source_id or "").strip())
        return self.satellite(satellite_id)

    def control_target_for_source(self, source_id: str | None) -> SatelliteBrainEdgeSettings | None:
        satellite = self.satellite_for_source(source_id)
        if (
            satellite is None
            or not satellite.playback_capable
            or satellite.control_service_base_url is None
            or satellite.control_service_credential is None
        ):
            return None
        return satellite
