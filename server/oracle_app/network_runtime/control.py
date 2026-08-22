from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from oracle_app.configuration.domain_models import (
    HomeAssistantPowerAdapter,
    RouterControlAdapter,
    ServiceControlAdapter,
)
from oracle_app.configuration.network_policy_runtime_settings import NetworkActionRuntimeSettings
from oracle_app.network_control_local_restart import stage_pending_local_host_restart
from oracle_app.network_control_local_service_restart import stage_pending_local_service_restart
from oracle_app.provider_bridges.home_assistant import HomeAssistantBridge, HomeAssistantBridgeError
from oracle_app.provider_bridges.network_probe import NetworkProbeBridge
from oracle_app.provider_bridges.plex_music import MusicBridgeError
from .platform_adapters import RouterPlatformAdapter, ServicePlatformAdapter
from .service_control import TypedServiceControl


def build_dry_run(execution: Any, payload: dict[str, Any]) -> dict[str, Any]:
    target_type = str(payload.get("target_type") or "").strip().lower()
    target_id = str(payload.get("target_id") or "").strip()
    operation = str(payload.get("action_id") or "").strip()
    base = _base(payload, target_type, target_id, operation, mode="dry_run")
    if target_type not in {"host", "service", "power_target"}:
        return _deny(base, "network_control_target_type_not_allowed", "Network control dry-run denied because the target type is not allowed.")
    target = _target(execution, target_type, target_id)
    if target is None:
        return _deny(base, "network_control_target_not_found", f"Network control dry-run denied because {target_type}:{target_id} is not in Oracle inventory.")
    action = execution.policy.action_for(target_type=target_type, target_id=target_id, operation=operation)
    if action is None:
        return _deny(base, "network_control_action_not_allowlisted", f"Network control found {target_type}:{target_id}, but {operation} is not allowlisted.")
    base.update(
        target=_safe_target(target),
        provider=_provider(action),
        adapter=action.adapter.definition.type,
        confirmation_status="required",
    )
    preconditions = _preconditions(execution, action)
    base["preconditions"] = preconditions
    lifecycle = _lifecycle(execution, action)
    base["lifecycle"] = lifecycle
    if action.definition.requires_graceful_lifecycle and lifecycle.get("configured") is not True:
        return _block(base, "network_control_lifecycle_not_configured", "Network control requires a graceful host lifecycle, but no valid lifecycle profile is configured.")
    failed = next((item for item in preconditions if item["status"] == "failed"), None)
    if failed:
        return _block(base, "network_control_precondition_failed", str(failed["summary"]))
    unavailable = next((item for item in preconditions if item["status"] in {"unknown", "unavailable"}), None)
    if unavailable:
        return _block(base, "network_control_precondition_unavailable", str(unavailable["summary"]))
    base["steps"] = list(lifecycle.get("phases") or []) + [
        {"id": "confirmation_required", "kind": "policy", "summary": "Require explicit confirmation before execution."}
    ]
    return {**base, "allowed": True, "policy_status": "allowed", "error_class": "", "summary": f"Network control dry-run allowed {operation} for {target_type}:{target_id}. Execution still requires explicit confirmation."}


def build_confirm(
    execution: Any,
    payload: dict[str, Any],
    *,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dry = build_dry_run(execution, payload)
    confirmed = payload.get("confirmed") is True
    base = {**dry, "mode": "execute", "confirmed": confirmed}
    if dry.get("allowed") is not True:
        return base
    if not confirmed:
        return {**base, "allowed": False, "policy_status": "denied", "confirmation_status": "required", "result_status": "not_executed", "error_class": "network_control_confirmation_required", "summary": "Network control execution denied because explicit confirmation was not provided."}
    if result is None:
        return {**base, "confirmation_status": "confirmed", "result_status": "not_implemented", "summary": "Network control is confirmed and ready for its typed adapter."}
    return {
        **base,
        "allowed": bool(result.get("ok")),
        "policy_status": "blocked" if result.get("result_status") == "blocked" else "allowed",
        "confirmation_status": "confirmed",
        "result_status": str(result.get("result_status") or ("executed" if result.get("ok") else "failed")),
        "error_class": str(result.get("error_class") or ""),
        "summary": str(result.get("summary") or ""),
        "execution": dict(result.get("execution") or {}),
        "steps": list(base.get("steps") or []) + list(result.get("steps") or []),
    }


def execute(execution: Any, action: NetworkActionRuntimeSettings, context: dict[str, Any]) -> dict[str, Any]:
    definition = action.adapter.definition
    if isinstance(definition, ServiceControlAdapter):
        if definition.target_kind == "host":
            return _execute_host(execution, action, context)
        return _execute_service(action, context)
    if isinstance(definition, RouterControlAdapter):
        return _execute_router(action)
    if isinstance(definition, HomeAssistantPowerAdapter):
        return _execute_power(execution, action)
    return _failed("network_control_execution_not_implemented", "The configured network adapter is not implemented.")


def diagnostics(execution: Any, verification: dict[tuple[str, str, str], dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    counts = {"total": 0, "verified": 0, "enabled_unverified": 0, "ready": 0, "disabled": 0, "misconfigured": 0}
    for action in execution.policy.actions.values():
        definition = action.definition
        key = (definition.target_type, definition.target_id, definition.operation)
        prior = verification.get(key, {})
        verified = prior.get("result_status") == "executed" and (prior.get("execution") or {}).get("verification_status") != "failed"
        status = "verified" if verified else "enabled_unverified"
        rows.append({
            "id": definition.id,
            "target_type": definition.target_type,
            "target_id": definition.target_id,
            "action_id": definition.operation,
            "provider": _provider(action),
            "adapter": action.adapter.definition.type,
            "description": definition.description or "",
            "enabled": True,
            "requires_confirmation": True,
            "requires_graceful_lifecycle": definition.requires_graceful_lifecycle,
            "required_preconditions": list(_effective_precondition_ids(execution, action)),
            "configuration_status": "ready",
            "status": status,
            "verification": prior,
        })
        counts["total"] += 1
        counts[status] += 1
        counts["ready"] += 1
    return {"actions": rows, "counts": counts}


def _execute_service(action: NetworkActionRuntimeSettings, context: dict[str, Any]) -> dict[str, Any]:
    adapter = action.adapter.definition
    assert isinstance(adapter, ServiceControlAdapter)
    if adapter.transport == "local" and adapter.restart_mode == "deferred_self_restart":
        staged = stage_pending_local_service_restart(
            control_context=context,
            target_id=action.definition.target_id,
            host_id=adapter.host_id,
            service_name=action.adapter.adapter_id,
        )
        if staged.get("ok") is not True:
            return _failed(str(staged.get("error") or "network_control_local_service_restart_state_unavailable"), str(staged.get("detail") or "Local service restart state could not be persisted."))
    policy = action.definition.execution
    platform = ServicePlatformAdapter(adapter, action.adapter.credential)
    result = platform.restart(action.definition.operation, timeout_seconds=int(policy.restart_timeout_seconds or 15))
    if not result.ok:
        return _failed(result.error or "network_control_service_control_failed", result.detail or "Service restart failed.")
    if result.status == "scheduled":
        return {"ok": True, "result_status": "executed", "error_class": "", "summary": "The local service restart was scheduled and will be verified after startup.", "execution": {"adapter": "service_control", "deferred": True, "verification_status": "pending"}, "steps": []}
    if policy.wait_seconds:
        time.sleep(int(policy.wait_seconds))
    check = platform.available(timeout_seconds=int(policy.restart_timeout_seconds or 15))
    if not check.ok:
        return _failed("network_control_verification_failed", "The service restart completed, but availability verification did not pass.", adapter="service_control")
    return {"ok": True, "result_status": "executed", "error_class": "", "summary": "Service restart completed and verification passed.", "execution": {"adapter": "service_control", "service_manager": adapter.service_adapter, "verification_status": "passed"}, "steps": []}


def _execute_host(execution: Any, action: NetworkActionRuntimeSettings, context: dict[str, Any]) -> dict[str, Any]:
    adapter = action.adapter.definition
    assert isinstance(adapter, ServiceControlAdapter)
    service = TypedServiceControl(execution.adapters)
    completed: list[str] = []
    if action.definition.requires_graceful_lifecycle:
        prepared = service.prepare(action.adapter)
        if prepared.get("ok") is not True:
            return _failed(str(prepared.get("error") or "network_control_host_preparation_failed"), str(prepared.get("detail") or "Host preparation failed."), adapter="service_control")
        completed = list(prepared.get("completed_phase_ids") or [])
    policy = action.definition.execution
    if adapter.transport == "local":
        staged = stage_pending_local_host_restart(
            control_context=context,
            host_id=adapter.host_id,
            readiness_timeout_seconds=int(policy.readiness_timeout_seconds or 120),
            recovery_poll_seconds=int(policy.recovery_poll_seconds or 5),
            lifecycle_status="prepared" if completed else "not_required",
        )
        if staged.get("ok") is not True:
            service.rollback(action.adapter, completed)
            return _failed(str(staged.get("error") or "network_control_local_restart_state_unavailable"), str(staged.get("detail") or "Local restart state could not be persisted."))
    sent = ServicePlatformAdapter(adapter, action.adapter.credential).restart("restart_host")
    if not sent.ok:
        service.rollback(action.adapter, completed)
        return _failed(sent.error or "network_control_host_restart_failed", sent.detail or "Host restart failed.", adapter="service_control")
    if adapter.transport == "local":
        return {"ok": True, "result_status": "executed", "error_class": "", "summary": "The local host restart was scheduled and will be verified after startup.", "execution": {"adapter": "service_control", "deferred": True, "verification_status": "pending", "lifecycle_status": "prepared" if completed else "not_required", "lifecycle_completed_phase_ids": completed}, "steps": []}
    address = str(adapter.address or "")
    if not _wait_reachable(address, healthy=False, timeout=int(policy.shutdown_timeout_seconds or 90), poll=int(policy.recovery_poll_seconds or 5)):
        return _failed("network_control_host_shutdown_not_observed", "The host restart was sent, but shutdown was not observed.", adapter="service_control")
    if not _wait_reachable(address, healthy=True, timeout=int(policy.recovery_timeout_seconds or 240), poll=int(policy.recovery_poll_seconds or 5)):
        return _failed("network_control_host_recovery_failed", "The host did not become reachable after restart.", adapter="service_control")
    host_services = service.recover_host_services(action.adapter) if completed else {"ok": True, "completed_phase_ids": []}
    if host_services.get("ok") is not True:
        return _failed("network_control_host_services_recovery_failed", "The host returned, but its prepared services did not recover.", adapter="service_control")
    readiness = service.check_readiness(action.adapter, timeout_seconds=15)
    if readiness.get("ok") is not True:
        return _failed("network_control_host_readiness_failed", "The host returned, but readiness checks did not pass.", adapter="service_control", readiness=readiness)
    client = service.recover_client(action.adapter) if completed else {"ok": True, "completed_phase_ids": []}
    if client.get("ok") is not True:
        return _failed("network_control_host_dependents_recovery_failed", "The host returned, but dependent services did not recover.", adapter="service_control")
    recovered_phases = [
        *list(host_services.get("completed_phase_ids") or []),
        *list(client.get("completed_phase_ids") or []),
    ]
    return {"ok": True, "result_status": "executed", "error_class": "", "summary": "Host restart completed and readiness passed.", "execution": {"adapter": "service_control", "verification_status": "passed", "readiness_status": "passed", "readiness_check_count": readiness["check_count"], "readiness_passed_count": readiness["passed_count"], "lifecycle_status": "passed" if completed else "not_required", "lifecycle_completed_phase_ids": completed + recovered_phases}, "steps": []}


def _execute_router(action: NetworkActionRuntimeSettings) -> dict[str, Any]:
    adapter = action.adapter.definition
    assert isinstance(adapter, RouterControlAdapter)
    result = RouterPlatformAdapter(adapter, str(action.adapter.credential or "")).restart(action.definition.operation)
    if not result.ok:
        return _failed(result.error or "network_control_router_control_failed", result.detail or "Router restart failed.", adapter="router_control")
    policy = action.definition.execution
    if not _wait_reachable(str(adapter.address), healthy=False, timeout=int(policy.shutdown_timeout_seconds or 90), poll=int(policy.recovery_poll_seconds or 5)):
        return _failed("network_control_router_shutdown_not_observed", "Router shutdown was not observed.", adapter="router_control")
    if not _wait_reachable(str(adapter.address), healthy=True, timeout=int(policy.recovery_timeout_seconds or 180), poll=int(policy.recovery_poll_seconds or 5)):
        return _failed("network_control_router_recovery_failed", "Router recovery was not observed.", adapter="router_control")
    return {"ok": True, "result_status": "executed", "error_class": "", "summary": "Router restart completed and recovery was observed.", "execution": {"adapter": "router_control", "verification_status": "passed", "shutdown_observed": True}, "steps": []}


def _execute_power(execution: Any, action: NetworkActionRuntimeSettings) -> dict[str, Any]:
    adapter = action.adapter.definition
    assert isinstance(adapter, HomeAssistantPowerAdapter)
    ha = action.adapter.home_assistant
    if ha is None or ha.base_url is None or ha.credential is None:
        return _failed("network_control_home_assistant_unavailable", "Home Assistant control settings are unavailable.")
    bridge = HomeAssistantBridge(base_url=ha.base_url, token=ha.credential, timeout_seconds=ha.timeout_seconds)
    policy = action.definition.execution
    off_seconds = int(policy.off_seconds or 10)
    verification_timeout = int(policy.verification_timeout_seconds or 8)
    turned_off = False
    try:
        bridge.call_service(service_domain="switch", service_name="turn_off", entity_id=adapter.entity_id)
        turned_off = True
        if str((bridge.wait_for_entity_state(adapter.entity_id, "off", timeout_seconds=verification_timeout) or {}).get("state") or "") != "off":
            raise HomeAssistantBridgeError("Power target did not report off.")
        time.sleep(off_seconds)
        bridge.call_service(service_domain="switch", service_name="turn_on", entity_id=adapter.entity_id)
        if str((bridge.wait_for_entity_state(adapter.entity_id, "on", timeout_seconds=verification_timeout) or {}).get("state") or "") != "on":
            raise HomeAssistantBridgeError("Power target did not report on.")
    except (HomeAssistantBridgeError, ValueError):
        if turned_off:
            try:
                bridge.call_service(service_domain="switch", service_name="turn_on", entity_id=adapter.entity_id)
            except HomeAssistantBridgeError:
                pass
        return _failed("network_control_power_cycle_failed", "Home Assistant did not complete and verify the power cycle.", adapter="switch_power_cycle")
    host_id = action.target.host.id
    host_adapter = next((item for item in execution.adapters.adapters.values() if isinstance(item.definition, ServiceControlAdapter) and item.definition.target_kind == "host" and item.definition.host_id == host_id), None)
    if host_adapter is not None and host_adapter.definition.address:
        if not _wait_reachable(str(host_adapter.definition.address), healthy=True, timeout=int(policy.recovery_timeout_seconds or 180), poll=int(policy.recovery_poll_seconds or 5)):
            return _failed("network_control_host_recovery_failed", "Power was restored, but the target host did not come back online in time.", adapter="switch_power_cycle")
        readiness = TypedServiceControl(execution.adapters).check_readiness(host_adapter, timeout_seconds=15)
        if readiness.get("ok") is not True:
            return _failed("network_control_power_readiness_failed", "Power was restored, but expected network readiness did not return in time.", adapter="switch_power_cycle", readiness=readiness)
    return {"ok": True, "result_status": "executed", "error_class": "", "summary": "Power cycle completed through Home Assistant and the switch returned on.", "execution": {"adapter": "switch_power_cycle", "off_seconds": off_seconds, "verification_status": "passed", "readiness_status": "passed", "power_restored": True}, "steps": []}


def _preconditions(execution: Any, action: NetworkActionRuntimeSettings) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    service = TypedServiceControl(execution.adapters)
    for precondition in _effective_precondition_ids(execution, action):
        if precondition == "plex_no_active_streams":
            try:
                status = execution.music.active_sessions_status() if execution.music is not None else None
                if status is None:
                    raise ValueError("Canonical music is unavailable.")
                count = int(status.get("active_stream_count") or 0)
                results.append({"id": precondition, "provider": "plex", "status": "failed" if count else "passed", "observed_value": count, "summary": f"Plex has {count} active stream(s), so Oracle will not restart it." if count else "Plex has no active streams."})
            except (MusicBridgeError, ValueError):
                results.append({"id": precondition, "provider": "plex", "status": "unavailable", "observed_value": None, "summary": "Plex active stream check is unavailable."})
        elif precondition == "pihole_restart_continuity":
            target_pihole, alternate = _continuity_service_pair(execution, action, precondition)
            if target_pihole is None or alternate is None:
                results.append({"id": precondition, "provider": "service_control", "status": "unavailable", "observed_value": None, "summary": "The target Pi-hole service cannot be resolved from canonical policy."})
                continue
            target_check = ServicePlatformAdapter(target_pihole.adapter.definition, target_pihole.adapter.credential).available()
            alternate_check = ServicePlatformAdapter(alternate.adapter.definition, alternate.adapter.credential).available()
            target_healthy = target_check.ok
            target_down = not target_check.ok and not target_check.error
            alternate_healthy = alternate_check.ok
            if alternate_healthy:
                status = "passed"
                summary = "The alternate Pi-hole is healthy, so DNS continuity is preserved."
            elif target_down:
                status = "passed"
                summary = "The target Pi-hole is already down, so restarting it does not reduce DNS continuity."
            elif target_healthy:
                status = "failed"
                summary = "The alternate Pi-hole is not healthy, so Oracle will not take the healthy Pi-hole offline."
            else:
                status = "unavailable"
                summary = "Pi-hole health could not be determined safely, so Oracle will not restart it."
            results.append({
                "id": precondition,
                "provider": "service_control",
                "status": status,
                "observed_value": {
                    "target": "healthy" if target_healthy else "down" if target_down else "unknown",
                    "alternate": "healthy" if alternate_healthy else "down" if not alternate_check.error else "unknown",
                },
                "summary": summary,
            })
        elif precondition == "host_storage_safe_for_restart":
            checked = service.check_storage_safety(action.adapter)
            results.append({"id": precondition, "provider": "service_control", "status": "passed" if checked.get("ok") is True else "failed" if checked.get("configured") else "unavailable", "observed_value": f"{checked.get('passed_count', 0)}/{checked.get('check_count', 0)}", "summary": "Host storage preflight passed for RAID, writable mount, and sharing service." if checked.get("ok") is True else "Host storage preflight did not pass, so Oracle will not restart the host."})
    return results


def _continuity_service_pair(
    execution: Any,
    action: NetworkActionRuntimeSettings,
    precondition: str,
) -> tuple[NetworkActionRuntimeSettings | None, NetworkActionRuntimeSettings | None]:
    candidates = [
        candidate
        for candidate in execution.policy.actions.values()
        if candidate.definition.target_type == "service"
        and candidate.definition.operation == "restart_service"
        and precondition in candidate.definition.required_preconditions
    ]
    target: NetworkActionRuntimeSettings | None = None
    if action.definition.target_type == "service":
        target = next(
            (candidate for candidate in candidates if candidate.definition.target_id == action.definition.target_id),
            None,
        )
    elif action.definition.target_type == "host":
        hosted = [
            candidate
            for candidate in candidates
            if execution.inventory.services[candidate.definition.target_id].definition.host_id
            == action.definition.target_id
        ]
        if len(hosted) == 1:
            target = hosted[0]
    peers = [candidate for candidate in candidates if candidate is not target]
    return target, peers[0] if len(peers) == 1 else None


def _effective_precondition_ids(
    execution: Any,
    action: NetworkActionRuntimeSettings,
) -> tuple[str, ...]:
    required = list(action.definition.required_preconditions)
    if action.definition.target_type == "host" and action.definition.operation == "restart_host":
        service_ids = {
            item.definition.id
            for item in execution.inventory.services.values()
            if item.definition.host_id == action.definition.target_id
        }
        for candidate in execution.policy.actions.values():
            if candidate.definition.target_type != "service" or candidate.definition.target_id not in service_ids:
                continue
            for precondition in candidate.definition.required_preconditions:
                if precondition not in required:
                    required.append(precondition)
    return tuple(required)


def _lifecycle(execution: Any, action: NetworkActionRuntimeSettings) -> dict[str, Any]:
    if not action.definition.requires_graceful_lifecycle:
        return {}
    return TypedServiceControl(execution.adapters).lifecycle_plan(action.adapter)


def _wait_reachable(address: str, *, healthy: bool, timeout: int, poll: int) -> bool:
    deadline = time.monotonic() + max(1, timeout)
    while time.monotonic() < deadline:
        status = NetworkProbeBridge().check_host_reachable(address, timeout_seconds=2).get("status") == "healthy"
        if status is healthy:
            return True
        time.sleep(min(max(1, poll), max(0.0, deadline - time.monotonic())))
    return False


def _target(execution: Any, target_type: str, target_id: str) -> Any | None:
    if target_type == "power_target":
        return execution.inventory.power_target(target_id)
    return execution.inventory.target(target_type, target_id)


def _safe_target(target: Any) -> dict[str, str]:
    definition = getattr(target, "definition", target)
    return {"id": definition.id, "display_name": getattr(definition, "display_name", definition.id), "kind": getattr(definition, "kind", ""), "host_id": getattr(definition, "host_id", "")}


def _base(payload: dict[str, Any], target_type: str, target_id: str, operation: str, *, mode: str) -> dict[str, Any]:
    return {"request_id": f"netctl-{uuid4()}", "requested_at": datetime.now().astimezone().isoformat(), "actor": str(payload.get("actor") or "unknown"), "source": str(payload.get("source") or "system_mode"), "target_type": target_type, "target_id": target_id, "action_id": operation, "mode": mode, "provider": "", "confirmation_status": "not_required", "result_status": "not_executed", "reason": str(payload.get("reason") or ""), "steps": [], "target": {}, "preconditions": [], "lifecycle": {}}


def _provider(action: NetworkActionRuntimeSettings) -> str:
    return {"service_control": "service_control", "router_control": "router_control", "home_assistant_power": "home_assistant"}.get(action.adapter.definition.type, action.adapter.definition.type)


def _deny(base: dict[str, Any], error: str, summary: str) -> dict[str, Any]:
    return {**base, "allowed": False, "policy_status": "denied", "error_class": error, "summary": summary}


def _block(base: dict[str, Any], error: str, summary: str) -> dict[str, Any]:
    return {**base, "allowed": False, "policy_status": "blocked", "error_class": error, "summary": summary}


def _failed(error: str, summary: str, *, adapter: str = "", readiness: dict[str, Any] | None = None) -> dict[str, Any]:
    execution = {"adapter": adapter, "verification_status": "failed"}
    if readiness is not None:
        execution.update(readiness_status="failed", readiness_check_count=readiness.get("check_count", 0), readiness_passed_count=readiness.get("passed_count", 0), readiness_failed_check_ids=readiness.get("failed_check_ids", []))
    return {"ok": False, "result_status": "failed", "error_class": error, "summary": summary, "execution": execution, "steps": []}
