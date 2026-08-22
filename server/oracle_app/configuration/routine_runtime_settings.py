from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .audiobook_runtime_settings import AudiobookRuntimeSettings, AudiobookUserAccountSettings
from .domain_models import (
    HomeAssistantObjectMapping,
    RoutineDefinition,
    RoutineStep,
    RoutinesConfiguration,
    NotificationsConfiguration,
)
from .effective import EffectiveConfig
from .home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .household_runtime_settings import HouseholdRuntimeSettings
from .models import SourceConfiguration, UserConfiguration
from .satellite_fleet_runtime_settings import SatelliteBrainEdgeSettings


@dataclass(frozen=True)
class RoutineStepRuntimeSettings:
    definition: RoutineStep
    action_mapping: HomeAssistantObjectMapping | None = None
    state_mapping: HomeAssistantObjectMapping | None = None
    remediation_action_mapping: HomeAssistantObjectMapping | None = None
    native_remediation_action_id: str | None = None
    audiobook_user_account: AudiobookUserAccountSettings | None = None
    playback_target: SatelliteBrainEdgeSettings | None = None


@dataclass(frozen=True)
class RoutineDefinitionRuntimeSettings:
    definition: RoutineDefinition
    owner: UserConfiguration
    sources: Mapping[str, SourceConfiguration]
    steps: tuple[RoutineStepRuntimeSettings, ...]


@dataclass(frozen=True)
class RoutineRuntimeSettings:
    """Frozen Brain execution settings for the optional composite-routines role."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    enabled: bool
    definitions: Mapping[str, RoutineDefinitionRuntimeSettings]
    global_voice_phrases: Mapping[str, str]
    source_voice_phrases: Mapping[str, Mapping[str, str]]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> RoutineRuntimeSettings:
        role = effective.role("domains/routines.yaml")
        if not isinstance(role, RoutinesConfiguration):
            raise TypeError("Effective routines role does not use the executable routines schema.")

        household = HouseholdRuntimeSettings.from_effective_config(effective)
        home_assistant: HomeAssistantRuntimeSettings | None = None
        audiobooks: AudiobookRuntimeSettings | None = None
        notifications: NotificationsConfiguration | None = None
        definitions: dict[str, RoutineDefinitionRuntimeSettings] = {}
        global_phrases: dict[str, str] = {}
        source_phrases: dict[str, dict[str, str]] = {}
        if role.enabled:
            for definition in role.definitions:
                if not definition.enabled:
                    continue
                owner = household.user(definition.user_id)
                if owner is None:
                    raise ValueError("Enabled canonical routine lacks its owning user.")
                sources: dict[str, SourceConfiguration] = {}
                for source_id in definition.source_ids:
                    source = household.source(source_id)
                    if source is None:
                        raise ValueError("Enabled canonical routine lacks one of its sources.")
                    sources[source_id] = source

                bound_steps: list[RoutineStepRuntimeSettings] = []
                for step in definition.steps:
                    if step.type == "timer_sound" and step.source_id not in sources:
                        raise ValueError("Enabled canonical timer-sound routine step requires a source owned by the routine.")
                    if step.type == "notification":
                        if notifications is None:
                            notification_role = effective.role("domains/notifications.yaml")
                            if not isinstance(notification_role, NotificationsConfiguration):
                                raise TypeError("Effective notifications role does not use its executable schema.")
                            notifications = notification_role
                        notification_id = step.notification_id
                        if not notifications.enabled or not any(
                            item.enabled and item.id == notification_id for item in notifications.types
                        ):
                            raise ValueError("Enabled canonical routine requires its notification type.")
                    if step.type in {"ui_action", "state_check"} or (
                        getattr(step, "remediation_action_id", None) not in {None, "stop_audiobook"}
                    ):
                        if home_assistant is None:
                            home_assistant = HomeAssistantRuntimeSettings.from_effective_config(effective)
                        if not home_assistant.enabled:
                            raise ValueError("Enabled canonical routine requires Home Assistant.")
                    if step.type in {"audiobook_start", "sleep_timer", "playback_check"}:
                        if audiobooks is None:
                            audiobooks = AudiobookRuntimeSettings.from_effective_config(effective)
                        if not audiobooks.enabled:
                            raise ValueError("Enabled canonical routine requires audiobooks.")
                    bound_steps.append(_bind_step(step, home_assistant, audiobooks))

                definitions[definition.id] = RoutineDefinitionRuntimeSettings(
                    definition=definition,
                    owner=owner,
                    sources=MappingProxyType(sources),
                    steps=tuple(bound_steps),
                )
                for phrase in definition.triggers.global_phrases:
                    global_phrases[_normalized_phrase(phrase)] = definition.id
                for source_id in definition.source_ids:
                    index = source_phrases.setdefault(source_id, {})
                    for phrase in definition.triggers.source_phrases:
                        index[_normalized_phrase(phrase)] = definition.id

        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            enabled=role.enabled,
            definitions=MappingProxyType(definitions),
            global_voice_phrases=MappingProxyType(global_phrases),
            source_voice_phrases=MappingProxyType(
                {source_id: MappingProxyType(index) for source_id, index in source_phrases.items()}
            ),
        )

    def definition(self, routine_id: str | None) -> RoutineDefinitionRuntimeSettings | None:
        return self.definitions.get(str(routine_id or "").strip())

    def resolve_voice_trigger(
        self,
        phrase: str | None,
        *,
        source_id: str | None,
    ) -> RoutineDefinitionRuntimeSettings | None:
        normalized = _normalized_phrase(phrase or "")
        routine_id = self.global_voice_phrases.get(normalized)
        if routine_id is None:
            routine_id = self.source_voice_phrases.get(str(source_id or "").strip(), {}).get(
                normalized
            )
        return self.definitions.get(routine_id) if routine_id is not None else None


def _bind_step(
    step: RoutineStep,
    home_assistant: HomeAssistantRuntimeSettings | None,
    audiobooks: AudiobookRuntimeSettings | None,
) -> RoutineStepRuntimeSettings:
    action_mapping = None
    state_mapping = None
    remediation_mapping = None
    native_remediation = None
    account = None
    target = None
    if step.type == "ui_action":
        action_mapping = _object_mapping(home_assistant, step.action_id, "action")
    if step.type == "state_check":
        state_mapping = _object_mapping(home_assistant, step.check_id, "entity")
    remediation_id = getattr(step, "remediation_action_id", None)
    if remediation_id == "stop_audiobook":
        native_remediation = remediation_id
    elif remediation_id is not None:
        remediation_mapping = _object_mapping(home_assistant, remediation_id, "action")
    if step.type == "audiobook_start":
        if audiobooks is None:
            raise ValueError("Canonical audiobook routine step lacks its domain settings.")
        account = audiobooks.user_account(step.user_id)
        target = audiobooks.playback_target(step.source_id)
        if account is None or target is None:
            raise ValueError("Canonical audiobook routine step lacks its account or playback target.")
    elif step.type in {"sleep_timer", "playback_check"}:
        if audiobooks is None:
            raise ValueError("Canonical playback routine step lacks its domain settings.")
        target = audiobooks.playback_target(step.source_id)
        if target is None:
            raise ValueError("Canonical playback routine step lacks its playback target.")
    return RoutineStepRuntimeSettings(
        definition=step,
        action_mapping=action_mapping,
        state_mapping=state_mapping,
        remediation_action_mapping=remediation_mapping,
        native_remediation_action_id=native_remediation,
        audiobook_user_account=account,
        playback_target=target,
    )


def _object_mapping(
    settings: HomeAssistantRuntimeSettings | None,
    mapping_id: str,
    expected_kind: str,
) -> HomeAssistantObjectMapping:
    mapping = None if settings is None else settings.mapping(mapping_id)
    if not isinstance(mapping, HomeAssistantObjectMapping) or mapping.kind != expected_kind:
        raise ValueError(
            f"Canonical routine mapping {mapping_id!r} is not an enabled {expected_kind} mapping."
        )
    return mapping


def _normalized_phrase(value: str) -> str:
    return " ".join(value.casefold().split())
