from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .domain_models import (
    NetworkDependency,
    NetworkDevice,
    NetworkHost,
    NetworkInventoryConfiguration,
    NetworkMonitor,
    NetworkPowerTarget,
    NetworkService,
    NetworkServiceGroup,
)
from .effective import EffectiveConfig


NetworkTarget = NetworkHost | NetworkDevice | NetworkService


@dataclass(frozen=True)
class NetworkDeviceRuntimeSettings:
    definition: NetworkDevice
    host: NetworkHost | None


@dataclass(frozen=True)
class NetworkServiceRuntimeSettings:
    definition: NetworkService
    host: NetworkHost


@dataclass(frozen=True)
class NetworkServiceGroupRuntimeSettings:
    definition: NetworkServiceGroup
    host: NetworkHost
    services: tuple[NetworkService, ...]


@dataclass(frozen=True)
class NetworkMonitorRuntimeSettings:
    definition: NetworkMonitor
    target: NetworkTarget


@dataclass(frozen=True)
class NetworkDependencyRuntimeSettings:
    definition: NetworkDependency
    from_target: NetworkTarget
    to_target: NetworkTarget


@dataclass(frozen=True)
class NetworkPowerTargetRuntimeSettings:
    definition: NetworkPowerTarget
    host: NetworkHost


@dataclass(frozen=True)
class NetworkInventoryRuntimeSettings:
    """Frozen Oracle-owned topology for the optional network inventory anchor."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    enabled: bool
    internet_health_probe_adapter_id: str | None
    hosts: Mapping[str, NetworkHost]
    devices: Mapping[str, NetworkDeviceRuntimeSettings]
    services: Mapping[str, NetworkServiceRuntimeSettings]
    service_groups: Mapping[str, NetworkServiceGroupRuntimeSettings]
    monitors: Mapping[str, NetworkMonitorRuntimeSettings]
    dependencies: Mapping[str, NetworkDependencyRuntimeSettings]
    power_targets: Mapping[str, NetworkPowerTargetRuntimeSettings]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> NetworkInventoryRuntimeSettings:
        role = effective.role("domains/network/inventory.yaml")
        if not isinstance(role, NetworkInventoryConfiguration):
            raise TypeError(
                "Effective network inventory role does not use the executable inventory schema."
            )

        hosts: dict[str, NetworkHost] = {}
        devices: dict[str, NetworkDeviceRuntimeSettings] = {}
        services: dict[str, NetworkServiceRuntimeSettings] = {}
        groups: dict[str, NetworkServiceGroupRuntimeSettings] = {}
        monitors: dict[str, NetworkMonitorRuntimeSettings] = {}
        dependencies: dict[str, NetworkDependencyRuntimeSettings] = {}
        power_targets: dict[str, NetworkPowerTargetRuntimeSettings] = {}
        if role.enabled:
            hosts = {host.id: host for host in role.hosts}
            device_definitions = {device.id: device for device in role.devices}
            service_definitions = {service.id: service for service in role.services}
            targets: dict[str, Mapping[str, NetworkTarget]] = {
                "host": hosts,
                "device": device_definitions,
                "service": service_definitions,
            }
            for device in role.devices:
                host = None if device.host_id is None else hosts.get(device.host_id)
                if device.host_id is not None and host is None:
                    raise ValueError("Canonical network device references an unknown host.")
                devices[device.id] = NetworkDeviceRuntimeSettings(device, host)
            for service in role.services:
                host = hosts.get(service.host_id)
                if host is None:
                    raise ValueError("Canonical network service references an unknown host.")
                services[service.id] = NetworkServiceRuntimeSettings(service, host)
            for group in role.service_groups:
                host = hosts.get(group.host_id)
                bound_services = tuple(service_definitions[item] for item in group.service_ids)
                if host is None:
                    raise ValueError("Canonical network service group references an unknown host.")
                groups[group.id] = NetworkServiceGroupRuntimeSettings(group, host, bound_services)
            for monitor in role.monitors:
                target = targets[monitor.target_type].get(monitor.target_id)
                if target is None:
                    raise ValueError("Canonical network monitor references an unknown target.")
                monitors[monitor.id] = NetworkMonitorRuntimeSettings(monitor, target)
            for dependency in role.dependencies:
                from_target = targets[dependency.from_type].get(dependency.from_id)
                to_target = targets[dependency.to_type].get(dependency.to_id)
                if from_target is None or to_target is None:
                    raise ValueError("Canonical network dependency references an unknown endpoint.")
                dependencies[dependency.id] = NetworkDependencyRuntimeSettings(
                    dependency,
                    from_target,
                    to_target,
                )
            for power_target in role.power_targets:
                host = hosts.get(power_target.host_id)
                if host is None:
                    raise ValueError("Canonical network power target references an unknown host.")
                power_targets[power_target.id] = NetworkPowerTargetRuntimeSettings(
                    power_target,
                    host,
                )

        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            enabled=role.enabled,
            internet_health_probe_adapter_id=(
                role.internet_health_probe_adapter_id if role.enabled else None
            ),
            hosts=MappingProxyType(hosts),
            devices=MappingProxyType(devices),
            services=MappingProxyType(services),
            service_groups=MappingProxyType(groups),
            monitors=MappingProxyType(monitors),
            dependencies=MappingProxyType(dependencies),
            power_targets=MappingProxyType(power_targets),
        )

    def target(self, target_type: str, target_id: str | None) -> NetworkTarget | None:
        target_key = str(target_id or "").strip()
        if target_type == "host":
            return self.hosts.get(target_key)
        if target_type == "device":
            item = self.devices.get(target_key)
            return None if item is None else item.definition
        if target_type == "service":
            item = self.services.get(target_key)
            return None if item is None else item.definition
        return None

    def power_target(
        self,
        target_id: str | None,
        *,
        enabled_only: bool = True,
    ) -> NetworkPowerTargetRuntimeSettings | None:
        item = self.power_targets.get(str(target_id or "").strip())
        if item is None or (enabled_only and not item.definition.enabled):
            return None
        return item
