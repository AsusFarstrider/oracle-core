from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .domain_models import LibreNmsAdapter, NetworkAction, NetworkPolicyConfiguration, NetworkRecovery
from .effective import EffectiveConfig
from .network_adapter_runtime_settings import (
    NetworkAdapterRuntimeSettings,
    NetworkAdaptersRuntimeSettings,
)
from .network_inventory_runtime_settings import (
    NetworkInventoryRuntimeSettings,
    NetworkPowerTargetRuntimeSettings,
    NetworkTarget,
)


NetworkActionTarget = NetworkTarget | NetworkPowerTargetRuntimeSettings


@dataclass(frozen=True)
class NetworkActionRuntimeSettings:
    definition: NetworkAction
    target: NetworkActionTarget
    adapter: NetworkAdapterRuntimeSettings


@dataclass(frozen=True)
class NetworkRecoveryRuntimeSettings:
    definition: NetworkRecovery


@dataclass(frozen=True)
class NetworkPolicyRuntimeSettings:
    """Frozen enabled policy identities; construction grants no execution authority."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    actions: Mapping[str, NetworkActionRuntimeSettings]
    recoveries: Mapping[str, NetworkRecoveryRuntimeSettings]
    recovery_voice_phrases: Mapping[str, NetworkRecoveryRuntimeSettings]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> NetworkPolicyRuntimeSettings:
        role = effective.role("domains/network/policy.yaml")
        if not isinstance(role, NetworkPolicyConfiguration):
            raise TypeError("Effective network policy role does not use the executable policy schema.")

        inventory = NetworkInventoryRuntimeSettings.from_effective_config(effective)
        adapters = (
            NetworkAdaptersRuntimeSettings.from_effective_config(effective)
            if any(definition.enabled for definition in role.actions)
            else None
        )
        actions: dict[str, NetworkActionRuntimeSettings] = {}
        recoveries: dict[str, NetworkRecoveryRuntimeSettings] = {}
        phrases: dict[str, NetworkRecoveryRuntimeSettings] = {}

        for definition in role.actions:
            if not definition.enabled:
                continue
            target: NetworkActionTarget | None
            if definition.target_type == "power_target":
                target = inventory.power_target(definition.target_id)
            else:
                target = inventory.target(definition.target_type, definition.target_id)
            adapter = None if adapters is None else adapters.adapter(definition.adapter_id)
            if target is None or adapter is None:
                raise ValueError("Enabled canonical network action has an unresolved runtime binding.")
            actions[definition.id] = NetworkActionRuntimeSettings(definition, target, adapter)

        for definition in role.recoveries:
            if not definition.enabled:
                continue
            if definition.id == "restart_network_anyway":
                sequence_actions = [actions.get(step.action_policy_id) for step in definition.sequence]
                if any(action is None for action in sequence_actions):
                    raise ValueError("Restart-anyway has an unavailable configured action.")
                modem, router, dns, edge = sequence_actions
                if (
                    modem.target.host.role != "modem"
                    or router.target.role != "router"
                    or inventory.hosts[dns.target.host_id].role != "oracle_brain"
                    or dns.target.kind != "dns"
                    or edge.target.role != "edge_gateway"
                    or modem.target.host.id == router.target.id
                    or dns.target.host_id == edge.target.id
                ):
                    raise ValueError("Restart-anyway action targets do not match the standard household roles.")
                evidence_ids = [
                    item
                    for step in definition.sequence for item in step.readiness_evidence_ids
                ] + list(definition.final_evidence_ids)
                for evidence_id in evidence_ids:
                    if evidence_id.startswith("librenms.monitor."):
                        monitor = inventory.monitors.get(evidence_id.removeprefix("librenms.monitor."))
                        adapter = None if monitor is None or adapters is None else adapters.adapter(monitor.definition.adapter_id)
                        if adapter is None or not isinstance(adapter.definition, LibreNmsAdapter):
                            raise ValueError("Restart-anyway references an unavailable LibreNMS monitor.")
                    elif evidence_id not in {"probe.internet", "probe.dns", "probe.http"}:
                        raise ValueError("Restart-anyway references an unsupported probe evidence ID.")
                if not {"probe.internet", "probe.dns"}.issubset(definition.final_evidence_ids):
                    raise ValueError("Restart-anyway final checks require Internet and DNS evidence.")
            runtime = NetworkRecoveryRuntimeSettings(definition)
            recoveries[definition.id] = runtime
            if definition.triggers.voice:
                for phrase in definition.triggers.global_phrases:
                    phrases[_normalize_phrase(phrase)] = runtime

        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            actions=MappingProxyType(actions),
            recoveries=MappingProxyType(recoveries),
            recovery_voice_phrases=MappingProxyType(phrases),
        )

    def action(self, action_id: str | None) -> NetworkActionRuntimeSettings | None:
        return self.actions.get(str(action_id or "").strip())

    def action_for(
        self,
        *,
        target_type: str,
        target_id: str,
        operation: str,
    ) -> NetworkActionRuntimeSettings | None:
        matches = [
            item
            for item in self.actions.values()
            if item.definition.target_type == target_type
            and item.definition.target_id == target_id
            and item.definition.operation == operation
        ]
        if len(matches) > 1:
            raise ValueError("Canonical network operation has multiple enabled policy owners.")
        return matches[0] if matches else None

    def recovery(self, recovery_id: str | None) -> NetworkRecoveryRuntimeSettings | None:
        return self.recoveries.get(str(recovery_id or "").strip())

    def recovery_for_voice_phrase(
        self,
        phrase: str | None,
    ) -> NetworkRecoveryRuntimeSettings | None:
        return self.recovery_voice_phrases.get(_normalize_phrase(phrase))


def _normalize_phrase(value: str | None) -> str:
    return " ".join(str(value or "").casefold().split())
