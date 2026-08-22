from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .effective import EffectiveConfig
from .models import SatelliteCapabilities, SatelliteUiConfiguration, SatellitesConfiguration


@dataclass(frozen=True)
class SatelliteUiRuntimeEntry:
    satellite_id: str
    source_id: str
    capabilities: SatelliteCapabilities
    ui: SatelliteUiConfiguration


@dataclass(frozen=True)
class SatelliteUiRuntimeSettings:
    """Frozen Brain read-model view for enabled satellite UI definitions."""

    config_revision: str
    entries: Mapping[str, SatelliteUiRuntimeEntry]
    satellite_ids_by_source: Mapping[str, str]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> SatelliteUiRuntimeSettings:
        role = effective.role("satellites.yaml")
        if not isinstance(role, SatellitesConfiguration):
            raise TypeError("Effective satellites.yaml role does not use the executable satellites schema.")
        entries: dict[str, SatelliteUiRuntimeEntry] = {}
        by_source: dict[str, str] = {}
        for satellite in role.satellites:
            if not satellite.enabled or satellite.ui is None or not satellite.ui.enabled:
                continue
            entry = SatelliteUiRuntimeEntry(
                satellite_id=satellite.id,
                source_id=satellite.source_id,
                capabilities=satellite.capabilities,
                ui=satellite.ui,
            )
            entries[entry.satellite_id] = entry
            by_source[entry.source_id] = entry.satellite_id
        return cls(
            config_revision=effective.config_revision,
            entries=MappingProxyType(entries),
            satellite_ids_by_source=MappingProxyType(by_source),
        )

    def entry(self, value: str | None) -> SatelliteUiRuntimeEntry | None:
        requested = str(value or "").strip()
        entry = self.entries.get(requested)
        if entry is not None:
            return entry
        satellite_id = self.satellite_ids_by_source.get(requested)
        return self.entries.get(satellite_id) if satellite_id is not None else None
