from __future__ import annotations

from .domain_models import (
    NetworkAdaptersConfiguration,
    NetworkInventoryConfiguration,
    NetworkPolicyConfiguration,
    ServiceControlAdapter,
)


def active_network_adapter_ids(
    inventory: NetworkInventoryConfiguration | None,
    policy: NetworkPolicyConfiguration | None,
    adapters: NetworkAdaptersConfiguration | None,
) -> frozenset[str]:
    """Return the finite typed adapter closure reached by enabled network edges."""

    if adapters is None:
        return frozenset()
    active: set[str] = set()
    if inventory is not None and inventory.enabled:
        if inventory.internet_health_probe_adapter_id is not None:
            active.add(inventory.internet_health_probe_adapter_id)
        active.update(item.adapter_id for item in inventory.monitors)
        active.update(item.adapter_id for item in inventory.power_targets if item.enabled)
    if policy is not None:
        active.update(item.adapter_id for item in policy.actions if item.enabled)

    pending = list(active)
    while pending:
        adapter_id = pending.pop()
        adapter = adapters.providers.get(adapter_id)
        if not isinstance(adapter, ServiceControlAdapter):
            continue
        referenced = set(adapter.readiness_service_adapter_ids)
        lifecycle = adapter.lifecycle
        if lifecycle is not None:
            referenced.update(lifecycle.prepare_service_adapter_ids)
            if lifecycle.client_release is not None:
                referenced.update(lifecycle.client_release.service_adapter_ids)
            if lifecycle.storage is not None:
                referenced.add(lifecycle.storage.sharing_service_adapter_id)
        for referenced_id in referenced - active:
            active.add(referenced_id)
            pending.append(referenced_id)
    return frozenset(active)
