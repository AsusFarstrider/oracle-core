from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from oracle_app.configuration.domain_models import DirectProbeAdapter, LibreNmsAdapter
from oracle_app.configuration.network_adapter_runtime_settings import NetworkAdaptersRuntimeSettings
from oracle_app.configuration.network_inventory_runtime_settings import NetworkInventoryRuntimeSettings
from oracle_app.configuration.network_policy_runtime_settings import NetworkPolicyRuntimeSettings
from oracle_app.network_status import (
    _aggregate_freshness,
    _aggregate_status,
    _librenms_device_reference,
    _librenms_interface_detail,
    _librenms_interface_label,
    _librenms_interface_reference,
    _librenms_service_reference,
    _normalize_librenms_device_status,
    _normalize_librenms_interface_status,
    _normalize_librenms_service_status,
    _normalize_status,
    _severity_for_status,
    _summary_for_status,
)
from oracle_app.provider_bridges.librenms import LibreNmsBridge
from oracle_app.provider_bridges.network_observations import (
    NetworkMonitoringObservation,
    NetworkProbeObservation,
)
from oracle_app.provider_bridges.network_probe import NetworkProbeBridge

from .control import build_confirm, build_dry_run, diagnostics, execute


_CACHE_TTL_SECONDS = 30.0


@dataclass
class CanonicalNetworkExecution:
    """Typed canonical network observation and control dependency boundary."""

    inventory: NetworkInventoryRuntimeSettings
    adapters: NetworkAdaptersRuntimeSettings
    policy: NetworkPolicyRuntimeSettings
    music: Any | None = None
    _cache: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _cache_lock: Any = field(default_factory=threading.RLock, init=False, repr=False)

    def internet_health(self) -> NetworkProbeObservation:
        adapter_id = self.inventory.internet_health_probe_adapter_id
        runtime = self.adapters.adapter(adapter_id)
        if runtime is None or not isinstance(runtime.definition, DirectProbeAdapter):
            return NetworkProbeObservation(
                status="unknown",
                checked_at=datetime.now().astimezone().isoformat(),
                source="probe",
                detail="Network internet-health probe is not configured.",
            )
        return NetworkProbeBridge().get_typed_internet_status(adapter=runtime.definition)

    def monitoring(self) -> dict[str, NetworkMonitoringObservation]:
        observations: dict[str, NetworkMonitoringObservation] = {}
        by_connection: dict[tuple[str, str, int], NetworkMonitoringObservation] = {}
        bridge = LibreNmsBridge()
        for monitor_id, monitor in self.inventory.monitors.items():
            runtime = self.adapters.adapter(monitor.definition.adapter_id)
            if runtime is None or not isinstance(runtime.definition, LibreNmsAdapter):
                continue
            definition = runtime.definition
            key = (
                str(definition.base_url).rstrip("/"),
                str(definition.credential_secret),
                int(definition.timeout_seconds),
            )
            observation = by_connection.get(key)
            if observation is None:
                observation = bridge.get_typed_monitoring_status(
                    base_url=key[0],
                    api_token=str(runtime.credential or ""),
                    timeout_seconds=key[2],
                )
                by_connection[key] = observation
            observations[monitor_id] = observation
        return observations

    def librenms_health(self) -> dict[str, Any]:
        connections: dict[tuple[str, str, int], str] = {}
        for monitor in self.inventory.monitors.values():
            runtime = self.adapters.adapter(monitor.definition.adapter_id)
            if runtime is None or not isinstance(runtime.definition, LibreNmsAdapter):
                continue
            connections.setdefault((
                str(runtime.definition.base_url).rstrip("/"),
                str(runtime.definition.credential_secret),
                int(runtime.definition.timeout_seconds),
            ), str(runtime.credential or ""))
        if not connections:
            return {
                "status": "disabled", "service": "oracle-brain", "provider": "librenms",
                "configured": False, "available": False, "degraded": False,
                "detail": "LibreNMS is not configured.", "missing_config_keys": [],
                "checked_at": datetime.now().astimezone().isoformat(),
            }
        results = [LibreNmsBridge().check_typed_health(
            base_url=base_url,
            api_token=connections[(base_url, credential_secret, timeout)],
            timeout_seconds=timeout,
        ) for base_url, credential_secret, timeout in sorted(connections)]
        failed = next((item for item in results if item.get("available") is not True), None)
        if failed is not None:
            return failed
        result = dict(results[0])
        result["degraded"] = any(bool(item.get("degraded")) for item in results)
        result["active_alert_count"] = sum(int(item.get("active_alert_count") or 0) for item in results)
        return result

    def summary(self) -> dict[str, Any]:
        probe = self.internet_health()
        monitoring = self.monitoring()
        monitoring_statuses = [item.status for item in monitoring.values()]
        monitoring_status = _aggregate_status(monitoring_statuses)
        details = [item.detail for item in monitoring.values() if item.detail]
        problems: list[str] = list(probe.problems)
        for item in monitoring.values():
            problems.extend(item.problems)
        status = _summarize_status(probe.status, monitoring_status)
        return {
            "status": status,
            "internet": {
                "status": probe.status,
                "checked_at": probe.checked_at,
                "source": probe.source,
                "detail": probe.detail,
            },
            "monitoring": {
                "status": monitoring_status,
                "checked_at": next((item.checked_at for item in monitoring.values()), None),
                "source": "librenms",
                "detail": details[0] if details else "LibreNMS not configured.",
            },
            "problems": problems[:5],
            "actions_available": self.available_actions(),
            "generated_at": probe.checked_at or next((item.checked_at for item in monitoring.values()), ""),
        }

    def available_actions(self) -> list[dict[str, str]]:
        return [
            {
                "kind": item.adapter.definition.type,
                "target_type": item.definition.target_type,
                "target_id": item.definition.target_id,
                "action": item.definition.operation,
            }
            for item in self.policy.actions.values()
        ]

    def control_dry_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return build_dry_run(self, payload)

    def control_confirm(
        self,
        payload: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_confirm(self, payload, result=result)

    def execute_control(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        action = self.policy.action_for(
            target_type=str(payload.get("target_type") or ""),
            target_id=str(payload.get("target_id") or ""),
            operation=str(payload.get("action_id") or ""),
        )
        if action is None:
            raise ValueError("Canonical network action is not allowlisted.")
        return execute(self, action, context)

    def control_diagnostics(
        self,
        verification: dict[tuple[str, str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        return diagnostics(self, verification)

    def status_snapshot(self, *, force_refresh: bool = False) -> dict[str, Any]:
        with self._cache_lock:
            now = time.monotonic()
            cached = self._cache.get("snapshot")
            stored = float(self._cache.get("stored_monotonic") or 0.0)
            if not force_refresh and isinstance(cached, dict) and stored and now - stored <= _CACHE_TTL_SECONDS:
                return _with_cache(cached, self._cache.get("cached_at", ""), now - stored, True)
            snapshot = self._build_status_snapshot(
                probe=self.internet_health(),
                monitoring=self.monitoring(),
            )
            cached_at = datetime.now().astimezone().isoformat()
            self._cache.update(snapshot=copy.deepcopy(snapshot), stored_monotonic=now, cached_at=cached_at)
            return _with_cache(snapshot, cached_at, 0.0, False)

    def _build_status_snapshot(
        self,
        *,
        probe: NetworkProbeObservation,
        monitoring: dict[str, NetworkMonitoringObservation],
    ) -> dict[str, Any]:
        generated_at = datetime.now().astimezone().isoformat()
        evidence = _probe_evidence(probe, generated_at)
        monitors: list[dict[str, Any]] = []
        for monitor_id, binding in self.inventory.monitors.items():
            runtime = self.adapters.adapter(binding.definition.adapter_id)
            if runtime is None:
                continue
            definition = runtime.definition
            if isinstance(definition, DirectProbeAdapter):
                observation = NetworkProbeBridge().get_typed_internet_status(adapter=definition)
                status = _normalize_status(observation.status, detail=observation.detail)
                evidence_id = f"probe.monitor.{monitor_id}"
                evidence.append(_evidence(
                    evidence_id, "probe", observation.checked_at, generated_at, status,
                    observation.detail, binding.definition.target_type, binding.definition.target_id,
                    _direct_probe_kind(definition),
                ))
            elif isinstance(definition, LibreNmsAdapter):
                observation = monitoring.get(monitor_id)
                if observation is None:
                    continue
                status, detail, provider_reference = _librenms_monitor_result(definition, observation)
                evidence_id = f"librenms.monitor.{monitor_id}"
                evidence.append(_evidence(
                    evidence_id, "librenms", observation.checked_at, generated_at, status,
                    detail, binding.definition.target_type, binding.definition.target_id,
                    _librenms_kind(definition), provider_reference,
                ))
            else:
                continue
            matching = [item for item in evidence if item["id"] == evidence_id]
            monitor_status = _aggregate_status([str(item["status"]) for item in matching])
            monitors.append({
                "id": monitor_id,
                "display_name": monitor_id,
                "provider": definition.type,
                "status": monitor_status,
                "severity": _severity_for_status(monitor_status),
                "freshness": _aggregate_freshness(matching),
                "summary": _summary_for_status(monitor_status),
                "evidence_ids": [evidence_id],
                "target_type": binding.definition.target_type,
                "target_id": binding.definition.target_id,
                "kind": _adapter_kind(definition),
            })

        hosts = [_target_row(item, monitors, "host") for item in self.inventory.hosts.values()]
        services = [_target_row(item.definition, monitors, "service") for item in self.inventory.services.values()]
        service_by_id = {item["id"]: item for item in services}
        groups = []
        for item in self.inventory.service_groups.values():
            definition = item.definition
            members = [service_by_id[value] for value in definition.service_ids]
            status = _aggregate_status([str(value["status"]) for value in members])
            groups.append({
                "id": definition.id,
                "display_name": definition.display_name,
                "host_id": definition.host_id,
                "status": status,
                "severity": _severity_for_status(status),
                "freshness": _aggregate_freshness(members),
                "summary": _summary_for_status(status),
                "service_ids": list(definition.service_ids),
                "evidence_ids": sorted({e for value in members for e in value["evidence_ids"]}),
                "collapsed": definition.collapsed,
            })
        dependencies = [_internet_dependency(probe, evidence)]
        for item in self.inventory.dependencies.values():
            definition = item.definition
            dependencies.append({
                "id": definition.id,
                "display_name": definition.id,
                "status": "unknown",
                "severity": _severity_for_status("unknown"),
                "freshness": "unknown",
                "summary": _summary_for_status("unknown"),
                "evidence_ids": [],
                "from_type": definition.from_type,
                "from_id": definition.from_id,
                "to_type": definition.to_type,
                "to_id": definition.to_id,
                "relationship": definition.relationship,
            })
        librenms_monitors = [item for item in monitors if item["provider"] == "librenms"]
        status = _aggregate_status([str(item["status"]) for item in librenms_monitors] or [str(item["status"]) for item in evidence])
        return {
            "status": status,
            "severity": _severity_for_status(status),
            "freshness": _aggregate_freshness(librenms_monitors or evidence),
            "generated_at": generated_at,
            "summary": _summary_for_status(status),
            "hosts": hosts,
            "services": services,
            "service_groups": groups,
            "power_targets": [{
                "id": item.definition.id,
                "display_name": item.definition.id,
                "host_id": item.definition.host_id,
                "enabled": item.definition.enabled,
                "capabilities": list(item.definition.capabilities),
            } for item in self.inventory.power_targets.values()],
            "dependencies": dependencies,
            "monitors": monitors,
            "evidence": evidence,
            "provider_observations": _provider_observations(self.inventory, self.adapters, monitoring),
        }


def _probe_evidence(probe: NetworkProbeObservation, received_at: str) -> list[dict[str, Any]]:
    status = _normalize_status(probe.status, detail=probe.detail)
    result = [_evidence("probe.internet", "probe", probe.checked_at, received_at, status, probe.detail, "dependency", "internet")]
    for index, check in enumerate(probe.checks):
        kind = str(check.get("kind") or f"check_{index}")
        result.append(_evidence(
            f"probe.{kind}", "probe", probe.checked_at, received_at,
            _normalize_status(str(check.get("status") or "unknown"), detail=str(check.get("detail") or "")),
            str(check.get("detail") or ""), "dependency", f"internet_{kind}", kind,
        ))
    return result


def _evidence(
    evidence_id: str, provider: str, observed_at: str, received_at: str,
    status: str, detail: str, subject_type: str, subject_id: str,
    kind: str = "", provider_reference: dict[str, str] | None = None,
) -> dict[str, Any]:
    value = {
        "id": evidence_id, "provider": provider, "observed_at": observed_at,
        "received_at": received_at, "status": status, "severity": _severity_for_status(status),
        "freshness": "fresh" if observed_at else "unknown", "summary": detail or _summary_for_status(status),
        "subject_type": subject_type, "subject_id": subject_id,
    }
    if detail:
        value["detail"] = detail
    if kind:
        value["kind"] = kind
    if provider_reference:
        value["provider_reference"] = provider_reference
    return value


def _librenms_monitor_result(
    adapter: LibreNmsAdapter,
    observation: NetworkMonitoringObservation,
) -> tuple[str, str, dict[str, str]]:
    if adapter.interface_name:
        match = next((item for item in observation.interfaces if _same(item.get("if_name"), adapter.interface_name)), None)
        if match is not None:
            return _normalize_librenms_interface_status(match), _librenms_interface_detail(match), _librenms_interface_reference(match)
    elif adapter.service_id or adapter.service_name:
        match = next((item for item in observation.services if _service_matches(item, adapter)), None)
        if match is not None:
            detail = str(match.get("service_message") or match.get("service_desc") or "")
            return _normalize_librenms_service_status(match), detail, _librenms_service_reference(match)
    elif adapter.device_id or adapter.hostname:
        match = next((item for item in observation.devices if _device_matches(item, adapter)), None)
        if match is not None:
            detail = str(match.get("status_reason") or match.get("display") or match.get("hostname") or "")
            return _normalize_librenms_device_status(match), detail, _librenms_device_reference(match)
    status = _normalize_status(observation.status, detail=observation.detail)
    return status, observation.detail or _summary_for_status(status), {}


def _service_matches(item: dict[str, str], adapter: LibreNmsAdapter) -> bool:
    return (
        (adapter.service_id is None or _same(item.get("service_id"), adapter.service_id))
        and (adapter.service_name is None or any(_same(item.get(key), adapter.service_name) for key in ("service_name", "service_desc", "service_type")))
        and (adapter.device_id is None or _same(item.get("device_id"), adapter.device_id))
    )


def _device_matches(item: dict[str, str], adapter: LibreNmsAdapter) -> bool:
    return (
        (adapter.device_id is None or _same(item.get("device_id"), adapter.device_id))
        and (adapter.hostname is None or any(_same(item.get(key), adapter.hostname) for key in ("hostname", "sys_name", "display", "ip")))
    )


def _same(left: Any, right: Any) -> bool:
    return "".join(c for c in str(left or "").casefold() if c.isalnum()) == "".join(c for c in str(right or "").casefold() if c.isalnum())


def _librenms_kind(adapter: LibreNmsAdapter) -> str:
    if adapter.interface_name:
        return "interface"
    if adapter.service_id or adapter.service_name:
        return "service"
    return "device"


def _direct_probe_kind(adapter: DirectProbeAdapter) -> str:
    return "http" if adapter.http_url else "dns"


def _adapter_kind(adapter: DirectProbeAdapter | LibreNmsAdapter) -> str:
    return _direct_probe_kind(adapter) if isinstance(adapter, DirectProbeAdapter) else _librenms_kind(adapter)


def _target_row(definition: Any, monitors: list[dict[str, Any]], target_type: str) -> dict[str, Any]:
    matching = [item for item in monitors if item.get("target_type") == target_type and item.get("target_id") == definition.id]
    status = _aggregate_status([str(item["status"]) for item in matching])
    return {
        "id": definition.id, "display_name": definition.display_name, "status": status,
        "severity": _severity_for_status(status), "freshness": _aggregate_freshness(matching),
        "summary": _summary_for_status(status),
        "evidence_ids": sorted({e for item in matching for e in item["evidence_ids"]}),
        "kind": definition.kind, "role": getattr(definition, "role", ""),
        "host_id": getattr(definition, "host_id", ""), "description": definition.description or "",
    }


def _internet_dependency(probe: NetworkProbeObservation, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    matching = [item for item in evidence if str(item["id"]).startswith("probe.")]
    status = _normalize_status(probe.status, detail=probe.detail)
    return {
        "id": "internet", "display_name": "Internet", "status": status,
        "severity": _severity_for_status(status), "freshness": _aggregate_freshness(matching),
        "summary": probe.detail or _summary_for_status(status),
        "evidence_ids": [item["id"] for item in matching],
    }


def _provider_observations(
    inventory: NetworkInventoryRuntimeSettings,
    adapters: NetworkAdaptersRuntimeSettings,
    observations: dict[str, NetworkMonitoringObservation],
) -> dict[str, Any]:
    service_rows: list[dict[str, Any]] = []
    interface_rows: list[dict[str, Any]] = []
    for monitor_id, binding in inventory.monitors.items():
        runtime = adapters.adapter(binding.definition.adapter_id)
        observation = observations.get(monitor_id)
        if runtime is None or observation is None or not isinstance(runtime.definition, LibreNmsAdapter):
            continue
        adapter = runtime.definition
        if _librenms_kind(adapter) == "service":
            for item in observation.services:
                if _service_matches(item, adapter):
                    service_rows.append({**_librenms_service_reference(item), "status": _normalize_librenms_service_status(item), "matched_monitor_ids": [monitor_id]})
        elif _librenms_kind(adapter) == "interface":
            for item in observation.interfaces:
                if _same(item.get("if_name"), adapter.interface_name):
                    interface_rows.append({**_librenms_interface_reference(item), "status": _normalize_librenms_interface_status(item), "summary": _librenms_interface_detail(item), "matched_monitor_ids": [monitor_id]})
    return {"librenms_services": service_rows, "librenms_interfaces": interface_rows}


def _summarize_status(internet_status: str, monitoring_status: str) -> str:
    if internet_status == "down":
        return "down"
    if internet_status == "degraded":
        return "degraded"
    if internet_status == "healthy" and monitoring_status in {"healthy", "unknown"}:
        return "healthy"
    if monitoring_status in {"degraded", "down"}:
        return "degraded"
    if internet_status == "unknown" and monitoring_status == "healthy":
        return "pending"
    return "unknown"


def _with_cache(snapshot: dict[str, Any], cached_at: str, age: float, hit: bool) -> dict[str, Any]:
    value = copy.deepcopy(snapshot)
    value.update(cached_at=str(cached_at), cache_age_seconds=round(max(0.0, age), 3), cache_ttl_seconds=int(_CACHE_TTL_SECONDS), cache_hit=hit)
    return value
