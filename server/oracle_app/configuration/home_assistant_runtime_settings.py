from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .domain_models import (
    HomeAssistantAutomation,
    HomeAssistantConfiguration,
    HomeAssistantEventMapping,
    HomeAssistantMapping,
    HomeAssistantProvider,
    HomeAssistantViews,
)
from .effective import EffectiveConfig


@dataclass(frozen=True)
class HomeAssistantAutomationRuntimeSettings:
    definition: HomeAssistantAutomation
    event_mapping: HomeAssistantEventMapping


@dataclass(frozen=True)
class HomeAssistantRuntimeSettings:
    """Frozen Brain execution settings for the optional Home Assistant role."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    enabled: bool
    provider_id: str | None
    base_url: str | None
    timeout_seconds: int | None
    snapshot_root: str | None
    mappings: Mapping[str, HomeAssistantMapping]
    views: HomeAssistantViews
    automations: Mapping[str, HomeAssistantAutomationRuntimeSettings]
    credential_secret: str | None
    event_ingress_secret: str | None
    credential: str | None = field(default=None, repr=False)
    event_ingress_credential: str | None = field(default=None, repr=False)

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> HomeAssistantRuntimeSettings:
        role = effective.role("domains/home-assistant.yaml")
        if not isinstance(role, HomeAssistantConfiguration):
            raise TypeError(
                "Effective Home Assistant role does not use the executable Home Assistant schema."
            )

        provider_id = None
        base_url = None
        timeout_seconds = None
        snapshot_root = None
        credential_secret = None
        credential = None
        event_ingress_secret = None
        event_ingress_credential = None
        mappings: dict[str, HomeAssistantMapping] = {}
        automations: dict[str, HomeAssistantAutomationRuntimeSettings] = {}
        if role.enabled:
            provider_id = role.provider
            if provider_id is None:
                raise ValueError("Enabled canonical Home Assistant has no selected provider.")
            provider = role.providers[provider_id]
            if not isinstance(provider, HomeAssistantProvider):
                raise TypeError("Canonical Home Assistant does not select a typed provider.")
            base_url = str(provider.base_url)
            timeout_seconds = provider.timeout_seconds
            snapshot_root = provider.snapshot_root
            credential_secret = provider.credential_secret
            credential = effective.secrets.resolve(credential_secret)
            if credential is None:
                raise ValueError("Enabled canonical Home Assistant lacks its provider credential.")
            mappings = dict(role.mappings)

            enabled_automations = [automation for automation in role.automations if automation.enabled]
            if enabled_automations:
                event_ingress_secret = provider.event_ingress_secret
                if event_ingress_secret is None:
                    raise ValueError(
                        "Enabled canonical Home Assistant automations lack an event-ingress secret."
                    )
                event_ingress_credential = effective.secrets.resolve(event_ingress_secret)
                if event_ingress_credential is None:
                    raise ValueError(
                        "Enabled canonical Home Assistant automations lack their event-ingress credential."
                    )
            for automation in enabled_automations:
                event_mapping = role.mappings[automation.event_mapping_id]
                if not isinstance(event_mapping, HomeAssistantEventMapping):
                    raise TypeError(
                        "Canonical Home Assistant automation does not reference an event mapping."
                    )
                automations[automation.id] = HomeAssistantAutomationRuntimeSettings(
                    definition=automation,
                    event_mapping=event_mapping,
                )

        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            enabled=role.enabled,
            provider_id=provider_id,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            snapshot_root=snapshot_root,
            mappings=MappingProxyType(mappings),
            views=role.views,
            automations=MappingProxyType(automations),
            credential_secret=credential_secret,
            event_ingress_secret=event_ingress_secret,
            credential=credential,
            event_ingress_credential=event_ingress_credential,
        )

    def mapping(self, mapping_id: str | None) -> HomeAssistantMapping | None:
        return self.mappings.get(str(mapping_id or "").strip())

    def mappings_for_kind(self, kind: str) -> tuple[HomeAssistantMapping, ...]:
        return tuple(mapping for mapping in self.mappings.values() if mapping.kind == kind)

    def automation(
        self,
        automation_id: str | None,
    ) -> HomeAssistantAutomationRuntimeSettings | None:
        return self.automations.get(str(automation_id or "").strip())
