from __future__ import annotations

from dataclasses import dataclass, field

from .access_runtime_settings import AccessRuntimeSettings
from .audiobook_runtime_settings import AudiobookRuntimeSettings
from .brain_runtime_settings import BrainRuntimeSettings
from .calendar_runtime_settings import CalendarRuntimeSettings
from .effective import EffectiveConfig
from .home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .household_runtime_settings import HouseholdRuntimeSettings
from .information_runtime_settings import InformationRuntimeSettings
from .music_runtime_settings import MusicRuntimeSettings
from .network_adapter_runtime_settings import NetworkAdaptersRuntimeSettings
from .network_inventory_runtime_settings import NetworkInventoryRuntimeSettings
from .network_policy_runtime_settings import NetworkPolicyRuntimeSettings
from .notification_runtime_settings import NotificationRuntimeSettings
from .routine_runtime_settings import RoutineRuntimeSettings
from .satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from .satellite_ui_runtime_settings import SatelliteUiRuntimeSettings
from .weather_runtime_settings import WeatherRuntimeSettings


@dataclass(frozen=True)
class BrainEffectiveRuntimeSettings:
    """One immutable, complete applied configuration snapshot for the Brain."""

    effective_config: EffectiveConfig = field(repr=False)
    brain: BrainRuntimeSettings
    household: HouseholdRuntimeSettings
    access: AccessRuntimeSettings
    satellites: SatelliteFleetRuntimeSettings
    satellite_ui: SatelliteUiRuntimeSettings
    information: InformationRuntimeSettings | None
    music: MusicRuntimeSettings | None
    audiobooks: AudiobookRuntimeSettings | None
    weather: WeatherRuntimeSettings | None
    calendar: CalendarRuntimeSettings | None
    home_assistant: HomeAssistantRuntimeSettings | None
    notifications: NotificationRuntimeSettings | None
    routines: RoutineRuntimeSettings | None
    network_inventory: NetworkInventoryRuntimeSettings | None
    network_adapters: NetworkAdaptersRuntimeSettings | None
    network_policy: NetworkPolicyRuntimeSettings | None

    @classmethod
    def from_effective_config(
        cls,
        effective: EffectiveConfig,
    ) -> BrainEffectiveRuntimeSettings:
        roles = effective.roles
        return cls(
            effective_config=effective,
            brain=BrainRuntimeSettings.from_effective_config(effective),
            household=HouseholdRuntimeSettings.from_effective_config(effective),
            access=AccessRuntimeSettings.from_effective_config(effective),
            satellites=SatelliteFleetRuntimeSettings.from_effective_config(effective),
            satellite_ui=SatelliteUiRuntimeSettings.from_effective_config(effective),
            information=(
                InformationRuntimeSettings.from_effective_config(effective)
                if "domains/information.yaml" in roles
                else None
            ),
            music=(
                MusicRuntimeSettings.from_effective_config(effective)
                if "domains/music.yaml" in roles
                else None
            ),
            audiobooks=(
                AudiobookRuntimeSettings.from_effective_config(effective)
                if "domains/audiobooks.yaml" in roles
                else None
            ),
            weather=(
                WeatherRuntimeSettings.from_effective_config(effective)
                if "domains/weather.yaml" in roles
                else None
            ),
            calendar=(
                CalendarRuntimeSettings.from_effective_config(effective)
                if "domains/calendar.yaml" in roles
                else None
            ),
            home_assistant=(
                HomeAssistantRuntimeSettings.from_effective_config(effective)
                if "domains/home-assistant.yaml" in roles
                else None
            ),
            notifications=(
                NotificationRuntimeSettings.from_effective_config(effective)
                if "domains/notifications.yaml" in roles
                else None
            ),
            routines=(
                RoutineRuntimeSettings.from_effective_config(effective)
                if "domains/routines.yaml" in roles
                else None
            ),
            network_inventory=(
                NetworkInventoryRuntimeSettings.from_effective_config(effective)
                if "domains/network/inventory.yaml" in roles
                else None
            ),
            network_adapters=(
                NetworkAdaptersRuntimeSettings.from_effective_config(effective)
                if "domains/network/adapters.yaml" in roles
                else None
            ),
            network_policy=(
                NetworkPolicyRuntimeSettings.from_effective_config(effective)
                if "domains/network/policy.yaml" in roles
                else None
            ),
        )
