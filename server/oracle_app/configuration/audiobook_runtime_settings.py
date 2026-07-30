from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .domain_models import AudiobooksConfiguration, AudiobookshelfProvider
from .effective import EffectiveConfig
from .household_runtime_settings import HouseholdRuntimeSettings
from .satellite_fleet_runtime_settings import (
    SatelliteBrainEdgeSettings,
    SatelliteFleetRuntimeSettings,
)
from .models import SatellitesConfiguration


@dataclass(frozen=True)
class AudiobookUserAccountSettings:
    user_id: str
    account_id: str
    credential_secret: str
    credential: str = field(repr=False)


@dataclass(frozen=True)
class AudiobookRuntimeSettings:
    """Frozen Brain execution settings for the optional audiobook domain role."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    enabled: bool
    provider_id: str | None
    provider: AudiobookshelfProvider | None
    default_sleep_timer_minutes: int | None
    user_accounts: Mapping[str, AudiobookUserAccountSettings]
    playback_targets: Mapping[str, SatelliteBrainEdgeSettings]
    stream_base_urls: Mapping[str, str]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> AudiobookRuntimeSettings:
        role = effective.role("domains/audiobooks.yaml")
        if not isinstance(role, AudiobooksConfiguration):
            raise TypeError("Effective audiobook role does not use the executable audiobook schema.")

        provider_id = None
        provider = None
        user_accounts: dict[str, AudiobookUserAccountSettings] = {}
        playback_targets: dict[str, SatelliteBrainEdgeSettings] = {}
        stream_base_urls: dict[str, str] = {}
        if role.enabled:
            provider_id = role.provider
            if provider_id is None:
                raise ValueError("Enabled canonical audiobooks has no selected provider.")
            provider = role.providers[provider_id]

            household = HouseholdRuntimeSettings.from_effective_config(effective)
            for user_id, user in household.users.items():
                capability = user.capabilities.audiobooks
                if not user.enabled or capability is None or not capability.enabled:
                    continue
                if capability.account_id is None or capability.credential_secret is None:
                    raise ValueError("Enabled canonical audiobook user capability is incomplete.")
                credential = effective.secrets.resolve(capability.credential_secret)
                if credential is None:
                    raise ValueError("Enabled canonical audiobook user lacks its credential.")
                user_accounts[user_id] = AudiobookUserAccountSettings(
                    user_id=user_id,
                    account_id=capability.account_id,
                    credential_secret=capability.credential_secret,
                    credential=credential,
                )

            fleet = SatelliteFleetRuntimeSettings.from_effective_config(effective)
            satellites_role = effective.role("satellites.yaml")
            if not isinstance(satellites_role, SatellitesConfiguration):
                raise TypeError("Effective satellites role does not use the executable schema.")
            for source_id in role.playback.source_ids:
                target = fleet.control_target_for_source(source_id)
                capabilities = None if target is None else target.capabilities
                if target is None or capabilities is None or not capabilities.audiobook_playback:
                    raise ValueError("Canonical audiobook playback source lacks its Brain control target.")
                satellite = next(
                    (
                        item
                        for item in satellites_role.satellites
                        if item.id == target.satellite_id
                    ),
                    None,
                )
                if satellite is None or satellite.brain_client is None or satellite.brain_client.base_url is None:
                    raise ValueError("Canonical audiobook playback source lacks its stream callback edge.")
                playback_targets[source_id] = target
                stream_base_urls[source_id] = satellite.brain_client.base_url

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
            default_sleep_timer_minutes=role.playback.default_sleep_timer_minutes,
            user_accounts=MappingProxyType(user_accounts),
            playback_targets=MappingProxyType(playback_targets),
            stream_base_urls=MappingProxyType(stream_base_urls),
        )

    def user_account(self, user_id: str | None) -> AudiobookUserAccountSettings | None:
        return self.user_accounts.get(str(user_id or "").strip())

    def playback_target(self, source_id: str | None) -> SatelliteBrainEdgeSettings | None:
        return self.playback_targets.get(str(source_id or "").strip())

    def stream_base_url(self, source_id: str | None) -> str | None:
        return self.stream_base_urls.get(str(source_id or "").strip())
