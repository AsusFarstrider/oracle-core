from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from stt import SttProvider
from tts import TtsProvider

from .configuration.bootstrap import BrainConfigurationStartup
from .configuration.brain_core_runtime_consumers import BrainCoreRuntimeConsumers
from .configuration.brain_effective_runtime_settings import BrainEffectiveRuntimeSettings
from .configuration.generations import GenerationStore
from .configuration.playback_target_resolution import CanonicalPlaybackTargetResolver
from .configuration.projection_resolution import SatelliteProjectionResolver
from .configuration.request_source_resolution import CanonicalRequestSourceResolver
from .capabilities import CapabilityRegistry
from .dispatch import build_dispatch_registry
from .handlers.registry import HandlerRegistry
from .routing import build_route_capability_registry
from .notifications.canonical import CanonicalNotificationExecution
from .audiobook_runtime.canonical import CanonicalAudiobookExecution
from .orchestration_routine_canonical import CanonicalRoutineExecution
from .music_runtime.canonical import CanonicalMusicExecution
from .information_runtime import CanonicalFactsExecution, CanonicalNewsExecution
from .calendar_runtime import CanonicalCalendarExecution
from .weather_runtime import CanonicalWeatherExecution
from .network_runtime import CanonicalNetworkExecution
from .suggestions.canonical import CanonicalSuggestionsExecution
from .runtime_paths import validate_standard_storage_settings


BRAIN_APPLICATION_COMPOSITION_STATE_KEY = "brain_application_composition"


@dataclass(frozen=True)
class CanonicalBrainApplicationComposition:
    """Complete pre-start canonical dependencies; construction starts nothing."""

    runtime: BrainEffectiveRuntimeSettings
    core_consumers: BrainCoreRuntimeConsumers
    route_registry: CapabilityRegistry
    dispatch_registry: HandlerRegistry
    projection_resolver: SatelliteProjectionResolver
    request_source_resolver: CanonicalRequestSourceResolver
    playback_target_resolver: CanonicalPlaybackTargetResolver
    notification_execution: CanonicalNotificationExecution
    audiobook_execution: CanonicalAudiobookExecution | None = None
    routine_execution: CanonicalRoutineExecution | None = None
    music_execution: CanonicalMusicExecution | None = None
    facts_execution: CanonicalFactsExecution | None = None
    news_execution: CanonicalNewsExecution | None = None
    calendar_execution: CanonicalCalendarExecution | None = None
    weather_execution: CanonicalWeatherExecution | None = None
    network_execution: CanonicalNetworkExecution | None = None
    suggestions_execution: CanonicalSuggestionsExecution | None = None
    mode: Literal["canonical"] = "canonical"

    def stt_provider(self) -> SttProvider:
        return self.core_consumers.stt_provider

    def tts_provider(self) -> TtsProvider:
        return self.core_consumers.tts_provider

    def applied_configuration_payload(self) -> dict[str, object]:
        effective = self.runtime.effective_config
        return {
            "mode": self.mode,
            "applied_generation": {
                "activation_generation_id": effective.activation_generation_id,
                "config_generation_id": effective.config_generation_id,
                "secret_generation_id": effective.secret_generation_id,
                "config_revision": effective.config_revision,
                "selection_operation_id": effective.selection_operation_id,
                "selection_revision": effective.selection_revision,
                "satellite_projection_activation_ids": dict(
                    effective.satellite_projection_activation_ids
                ),
            },
        }

    @classmethod
    def from_startup(
        cls,
        startup: BrainConfigurationStartup,
    ) -> CanonicalBrainApplicationComposition:
        if startup.mode != "canonical":
            raise ValueError("Canonical Brain composition requires canonical startup authority.")
        if startup.effective_config is None or (
            startup.service_settings is None and startup.installation_layout is None
        ):
            raise ValueError("Canonical Brain startup lacks its installed configuration inputs.")

        runtime = BrainEffectiveRuntimeSettings.from_effective_config(startup.effective_config)
        if startup.installation_layout is not None:
            validate_standard_storage_settings(
                runtime.brain.memory_storage.database_path,
                runtime.brain.alert_storage.state_path,
            )
        core_consumers = BrainCoreRuntimeConsumers.from_runtime_settings(runtime.brain)
        if startup.installation_layout is not None:
            projection_store = GenerationStore(
                startup.installation_layout.configuration,
                secret_root=startup.installation_layout.secrets,
            )
        else:
            assert startup.service_settings is not None
            projection_store = GenerationStore(startup.service_settings.store_root)
        projection_resolver = SatelliteProjectionResolver(projection_store)
        audiobook_execution = (
            CanonicalAudiobookExecution(
                runtime.audiobooks,
                satellite_control_timeout_seconds=runtime.brain.runtime.satellite_control_timeout_seconds,
            )
            if runtime.audiobooks is not None and runtime.audiobooks.enabled
            else None
        )
        music_execution = (
            CanonicalMusicExecution(
                runtime.music,
                satellite_control_timeout_seconds=runtime.brain.runtime.satellite_control_timeout_seconds,
            )
            if runtime.music is not None and runtime.music.enabled
            else None
        )
        notification_execution = CanonicalNotificationExecution(
            settings=runtime.notifications,
            home_assistant=runtime.home_assistant,
            satellites=runtime.satellites,
        )
        routine_execution = (
            CanonicalRoutineExecution(
                settings=runtime.routines,
                home_assistant=runtime.home_assistant,
                audiobooks=audiobook_execution,
                notifications=notification_execution,
            )
            if runtime.routines is not None and runtime.routines.enabled
            else None
        )
        facts_execution = (
            CanonicalFactsExecution(runtime.information.facts, inference=core_consumers.inference)
            if runtime.information is not None
            else None
        )
        news_execution = (
            CanonicalNewsExecution(runtime.information.news)
            if runtime.information is not None
            else None
        )
        calendar_execution = (
            CanonicalCalendarExecution(runtime.calendar)
            if runtime.calendar is not None
            else None
        )
        weather_execution = (
            CanonicalWeatherExecution(runtime.weather)
            if runtime.weather is not None
            else None
        )
        network_execution = (
            CanonicalNetworkExecution(
                runtime.network_inventory,
                runtime.network_adapters,
                runtime.network_policy,
                music=music_execution,
            )
            if runtime.network_inventory is not None
            and runtime.network_inventory.enabled
            and runtime.network_adapters is not None
            and runtime.network_policy is not None
            else None
        )
        suggestions_execution = (
            CanonicalSuggestionsExecution(runtime.information.suggestions)
            if runtime.information is not None
            else None
        )
        return cls(
            runtime=runtime,
            core_consumers=core_consumers,
            route_registry=build_route_capability_registry(
                runtime.household,
                facts_enabled=False if facts_execution is None else facts_execution.settings.enabled,
                news_settings=None if news_execution is None else news_execution.settings,
                canonical_information=True,
                calendar_settings=(
                    None if calendar_execution is None else calendar_execution.settings
                ),
                canonical_calendar=True,
            ),
            dispatch_registry=build_dispatch_registry(
                inference_client=core_consumers.inference,
                household_settings=runtime.household,
                home_assistant_settings=runtime.home_assistant,
                canonical_configuration=True,
                canonical_media_targets=True,
                audiobook_execution=audiobook_execution,
                music_execution=music_execution,
                facts_execution=facts_execution,
                news_execution=news_execution,
                calendar_execution=calendar_execution,
                weather_execution=weather_execution,
                network_execution=network_execution,
            ),
            projection_resolver=projection_resolver,
            request_source_resolver=CanonicalRequestSourceResolver(
                runtime=runtime,
                projections=projection_resolver,
            ),
            playback_target_resolver=CanonicalPlaybackTargetResolver(
                fleet=runtime.satellites,
            ),
            notification_execution=notification_execution,
            audiobook_execution=audiobook_execution,
            routine_execution=routine_execution,
            music_execution=music_execution,
            facts_execution=facts_execution,
            news_execution=news_execution,
            calendar_execution=calendar_execution,
            weather_execution=weather_execution,
            network_execution=network_execution,
            suggestions_execution=suggestions_execution,
        )

BrainApplicationComposition: TypeAlias = CanonicalBrainApplicationComposition
