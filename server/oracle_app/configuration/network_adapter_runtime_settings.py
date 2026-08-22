from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .domain_models import (
    LibreNmsAdapter,
    NetworkAdapter,
    NetworkAdaptersConfiguration,
    NetworkInventoryConfiguration,
    NetworkPolicyConfiguration,
    RouterControlAdapter,
    ServiceControlAdapter,
)
from .effective import EffectiveConfig
from .home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .network_adapter_selection import active_network_adapter_ids


@dataclass(frozen=True)
class NetworkAdapterRuntimeSettings:
    adapter_id: str
    definition: NetworkAdapter
    supporting_adapter_ids: tuple[str, ...]
    credential_secret: str | None
    home_assistant: HomeAssistantRuntimeSettings | None = None
    credential: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class NetworkAdaptersRuntimeSettings:
    """Frozen typed provider edges reached by enabled canonical network configuration."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    adapters: Mapping[str, NetworkAdapterRuntimeSettings]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> NetworkAdaptersRuntimeSettings:
        role = effective.role("domains/network/adapters.yaml")
        if not isinstance(role, NetworkAdaptersConfiguration):
            raise TypeError(
                "Effective network adapters role does not use the executable adapters schema."
            )
        inventory_role = effective.role("domains/network/inventory.yaml")
        if not isinstance(inventory_role, NetworkInventoryConfiguration):
            raise TypeError("Effective network inventory does not use its executable schema.")
        policy_role = effective.roles.get("domains/network/policy.yaml")
        if policy_role is not None and not isinstance(policy_role, NetworkPolicyConfiguration):
            raise TypeError("Effective network policy does not use its executable schema.")

        active_ids = active_network_adapter_ids(inventory_role, policy_role, role)
        home_assistant: HomeAssistantRuntimeSettings | None = None
        runtime: dict[str, NetworkAdapterRuntimeSettings] = {}
        for adapter_id in sorted(active_ids):
            definition = role.providers.get(adapter_id)
            if definition is None:
                raise ValueError("Active canonical network edge references an unknown adapter.")
            credential_secret = _credential_secret(definition)
            credential = None
            if credential_secret is not None:
                credential = effective.secrets.resolve(credential_secret)
                if credential is None:
                    raise ValueError(
                        f"Active canonical network adapter {adapter_id!r} lacks its credential."
                    )
            bound_home_assistant = None
            if definition.type == "home_assistant_power":
                if home_assistant is None:
                    home_assistant = HomeAssistantRuntimeSettings.from_effective_config(effective)
                if not home_assistant.enabled:
                    raise ValueError("Active canonical power adapter requires Home Assistant.")
                bound_home_assistant = home_assistant
            runtime[adapter_id] = NetworkAdapterRuntimeSettings(
                adapter_id=adapter_id,
                definition=definition,
                supporting_adapter_ids=_supporting_adapter_ids(definition),
                credential_secret=credential_secret,
                credential=credential,
                home_assistant=bound_home_assistant,
            )
        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            adapters=MappingProxyType(runtime),
        )

    def adapter(self, adapter_id: str | None) -> NetworkAdapterRuntimeSettings | None:
        return self.adapters.get(str(adapter_id or "").strip())


def _credential_secret(definition: NetworkAdapter) -> str | None:
    if isinstance(definition, LibreNmsAdapter):
        return definition.credential_secret
    if isinstance(definition, (ServiceControlAdapter, RouterControlAdapter)):
        return definition.password_secret
    return None


def _supporting_adapter_ids(definition: NetworkAdapter) -> tuple[str, ...]:
    if not isinstance(definition, ServiceControlAdapter):
        return ()
    referenced = set(definition.readiness_service_adapter_ids)
    lifecycle = definition.lifecycle
    if lifecycle is not None:
        referenced.update(lifecycle.prepare_service_adapter_ids)
        if lifecycle.client_release is not None:
            referenced.update(lifecycle.client_release.service_adapter_ids)
        if lifecycle.storage is not None:
            referenced.add(lifecycle.storage.sharing_service_adapter_id)
    return tuple(sorted(referenced))
