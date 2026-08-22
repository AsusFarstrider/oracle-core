from __future__ import annotations

from datetime import datetime
from typing import Any

from .network_control_preconditions import with_inherited_host_preconditions
from uuid import uuid4


_ALLOWED_TARGET_TYPES = {"host", "service", "power_target"}


def build_network_control_dry_run(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    control_policy: dict[str, list[dict[str, Any]]] | None = None,
    request_payload: dict[str, Any],
    preconditions: list[dict[str, Any]] | None = None,
    lifecycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested_at = datetime.now().astimezone().isoformat()
    request_id = f"netctl-{uuid4()}"
    target_type = str(request_payload.get("target_type") or "").strip().lower()
    target_id = str(request_payload.get("target_id") or "").strip()
    action_id = str(request_payload.get("action_id") or "").strip()
    actor = _safe_text(request_payload.get("actor")) or "unknown"
    source = _safe_text(request_payload.get("source")) or "system_mode"
    reason = _safe_text(request_payload.get("reason"))

    base = {
        "request_id": request_id,
        "requested_at": requested_at,
        "actor": actor,
        "source": source,
        "target_type": target_type,
        "target_id": target_id,
        "action_id": action_id,
        "mode": "dry_run",
        "provider": "",
        "confirmation_status": "not_required",
        "result_status": "not_executed",
        "reason": reason,
        "steps": [],
        "target": {},
        "preconditions": list(preconditions or []),
        "lifecycle": dict(lifecycle or {}),
    }

    if target_type not in _ALLOWED_TARGET_TYPES:
        return _denied(
            base,
            error_class="network_control_target_type_not_allowed",
            summary="Network control dry-run denied because the target type is not allowed.",
        )
    if not target_id:
        return _denied(
            base,
            error_class="network_control_missing_target",
            summary="Network control dry-run denied because no target id was provided.",
        )
    if not action_id:
        return _denied(
            base,
            error_class="network_control_missing_action",
            summary="Network control dry-run denied because no action id was provided.",
        )

    target = _find_inventory_target(inventory=inventory, target_type=target_type, target_id=target_id)
    if not target:
        return _denied(
            base,
            error_class="network_control_target_not_found",
            summary=f"Network control dry-run denied because {target_type}:{target_id} is not in Oracle inventory.",
        )

    base["target"] = {
        "id": str(target.get("id") or target_id),
        "display_name": str(target.get("display_name") or target_id),
        "kind": str(target.get("kind") or ""),
        "host_id": str(target.get("host_id") or ""),
    }

    action_policy = _find_action_policy(
        control_policy=control_policy or {"actions": []},
        target_type=target_type,
        target_id=target_id,
        action_id=action_id,
    )
    if not action_policy:
        return _denied(
            base,
            error_class="network_control_action_not_allowlisted",
            summary=(
                f"Network control dry-run found {target_type}:{target_id}, but action "
                f"{action_id} is not allowlisted for execution."
            ),
        )
    base["provider"] = str(action_policy.get("provider") or "")
    base["adapter"] = str(action_policy.get("adapter") or "")
    base["confirmation_status"] = "required" if action_policy.get("requires_confirmation") is True else "not_required"

    if action_policy.get("enabled") is not True:
        return _denied(
            base,
            error_class="network_control_action_disabled",
            summary=(
                f"Network control dry-run found {target_type}:{target_id} action "
                f"{action_id}, but the allowlist entry is disabled."
            ),
        )

    if action_policy.get("requires_graceful_lifecycle") is True and base["lifecycle"].get("configured") is not True:
        return _blocked(
            base,
            error_class="network_control_lifecycle_not_configured",
            summary="Network control requires a graceful host lifecycle, but no valid lifecycle profile is configured.",
        )
    failed_precondition = _first_precondition_with_status(base["preconditions"], {"failed"})
    if failed_precondition:
        return _blocked(
            base,
            error_class="network_control_precondition_failed",
            summary=str(failed_precondition.get("summary") or "Network control dry-run blocked by a failed precondition."),
        )
    unknown_precondition = _first_precondition_with_status(base["preconditions"], {"unknown", "unavailable"})
    if unknown_precondition:
        return _blocked(
            base,
            error_class="network_control_precondition_unavailable",
            summary=str(unknown_precondition.get("summary") or "Network control dry-run blocked because a precondition could not be checked."),
        )
    base["steps"] = _build_plan_steps(action_policy, lifecycle=base["lifecycle"])
    return {
        **base,
        "allowed": True,
        "policy_status": "allowed",
        "error_class": "",
        "summary": (
            f"Network control dry-run allowed {action_id} for {target_type}:{target_id}. "
            "Execution still requires explicit confirmation."
        ),
    }


def build_network_control_confirm(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    control_policy: dict[str, list[dict[str, Any]]] | None = None,
    request_payload: dict[str, Any],
    preconditions: list[dict[str, Any]] | None = None,
    lifecycle: dict[str, Any] | None = None,
    execution_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    confirmed = request_payload.get("confirmed") is True
    dry_run = build_network_control_dry_run(
        inventory=inventory,
        control_policy=control_policy,
        request_payload=request_payload,
        preconditions=preconditions,
        lifecycle=lifecycle,
    )
    base = {
        **dry_run,
        "mode": "execute",
        "confirmed": confirmed,
    }
    if dry_run.get("allowed") is not True:
        return {
            **base,
            "confirmation_status": "confirmed" if confirmed else base.get("confirmation_status", "required"),
        }
    if not confirmed:
        return {
            **base,
            "allowed": False,
            "policy_status": "denied",
            "confirmation_status": "required",
            "result_status": "not_executed",
            "error_class": "network_control_confirmation_required",
            "summary": "Network control execution denied because explicit confirmation was not provided.",
        }
    if execution_result is not None:
        return {
            **base,
            "allowed": bool(execution_result.get("ok")),
            "policy_status": (
                "blocked"
                if str(execution_result.get("result_status") or "") == "blocked"
                else "allowed"
            ),
            "confirmation_status": "confirmed",
            "result_status": str(execution_result.get("result_status") or ("executed" if execution_result.get("ok") else "failed")),
            "error_class": str(execution_result.get("error_class") or ""),
            "summary": str(execution_result.get("summary") or ""),
            "execution": {
                key: value
                for key, value in dict(execution_result.get("execution") or {}).items()
                if key
                in {
                    "adapter",
                    "method",
                    "service_manager",
                    "unit",
                    "wait_seconds",
                    "restart_timeout_seconds",
                    "off_seconds",
                    "shutdown_timeout_seconds",
                    "recovery_timeout_seconds",
                    "recovery_poll_seconds",
                    "readiness_timeout_seconds",
                    "verification_status",
                    "readiness_status",
                    "readiness_check_count",
                    "readiness_passed_count",
                    "readiness_failed_check_ids",
                    "power_restored",
                    "shutdown_observed",
                    "local_restart_completed",
                    "boot_changed",
                    "deferred",
                    "availability_status",
                    "active_target_type",
                    "active_target_id",
                    "active_action_id",
                    "active_started_at",
                    "cooldown_seconds",
                    "cooldown_remaining_seconds",
                    "cooldown_until",
                    "lifecycle_status",
                    "lifecycle_completed_phase_ids",
                }
            },
            "steps": list(base.get("steps") or []) + list(execution_result.get("steps") or []),
        }
    return {
        **base,
        "allowed": True,
        "policy_status": "allowed",
        "confirmation_status": "confirmed",
        "result_status": "not_implemented",
        "error_class": "network_control_execution_not_implemented",
        "summary": (
            f"Network control confirmed {base.get('action_id')} for "
            f"{base.get('target_type')}:{base.get('target_id')}, but execution is not implemented yet."
        ),
        "steps": list(base.get("steps") or [])
        + [
            {
                "id": "execution_not_implemented",
                "kind": "execution",
                "summary": "Confirmed execution endpoint is present, but no provider adapter is implemented for this action yet.",
            }
        ],
    }


def find_network_control_action_policy(
    *,
    control_policy: dict[str, list[dict[str, Any]]] | None,
    target_type: str,
    target_id: str,
    action_id: str,
) -> dict[str, Any]:
    return _find_action_policy(
        control_policy=control_policy or {"actions": []},
        target_type=target_type,
        target_id=target_id,
        action_id=action_id,
    )


def find_network_control_target(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    target_type: str,
    target_id: str,
) -> dict[str, Any]:
    return _find_inventory_target(
        inventory=inventory,
        target_type=target_type,
        target_id=target_id,
    )


def build_network_control_actions_diagnostics(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    control_policy: dict[str, list[dict[str, Any]]] | None = None,
    service_control_settings: dict[str, Any] | None = None,
    router_control_settings: dict[str, Any] | None = None,
    verification_results: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    counts = {
        "total": 0,
        "verified": 0,
        "enabled_unverified": 0,
        "ready": 0,
        "disabled": 0,
        "misconfigured": 0,
    }
    effective_control_policy = control_policy or {"actions": []}
    for raw_action in effective_control_policy.get("actions") or []:
        if not isinstance(raw_action, dict):
            continue
        action_policy = with_inherited_host_preconditions(
            action_policy=raw_action,
            target_type=str(raw_action.get("target_type") or "").strip().lower(),
            target_id=str(raw_action.get("target_id") or "").strip(),
            inventory=inventory,
            control_policy=effective_control_policy,
        )
        action = _build_action_diagnostics(
            inventory=inventory,
            action_policy=action_policy,
            service_control_settings=service_control_settings or {},
            router_control_settings=router_control_settings or {},
            verification_results=verification_results or {},
        )
        actions.append(action)
        counts["total"] += 1
        status = str(action.get("status") or "misconfigured")
        if status in counts:
            counts[status] += 1
        else:
            counts["misconfigured"] += 1
        if action.get("configuration_status") == "ready":
            counts["ready"] += 1

    return {
        "summary": {
            **counts,
            "all_ready": counts["total"] > 0 and counts["ready"] == counts["total"],
            "all_verified": counts["total"] > 0 and counts["verified"] == counts["total"],
        },
        "actions": actions,
    }


def _build_action_diagnostics(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    action_policy: dict[str, Any],
    service_control_settings: dict[str, Any],
    router_control_settings: dict[str, Any],
    verification_results: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    target_type = str(action_policy.get("target_type") or "").strip().lower()
    target_id = str(action_policy.get("target_id") or "").strip()
    action_id = str(action_policy.get("action_id") or "").strip()
    adapter = str(action_policy.get("adapter") or "").strip()
    provider = str(action_policy.get("provider") or "").strip()
    enabled = action_policy.get("enabled") is True
    requires_confirmation = action_policy.get("requires_confirmation") is True
    target = _find_inventory_target(inventory=inventory, target_type=target_type, target_id=target_id)
    issues: list[dict[str, str]] = []
    service_control = _empty_service_control_diagnostics()
    router_control = _empty_router_control_diagnostics()
    power_readiness = _empty_power_readiness_diagnostics()

    if not target:
        issues.append(
            {
                "id": "inventory_target_missing",
                "severity": "error",
                "summary": "Control policy target is not present in Oracle network inventory.",
            }
        )
    if enabled and not requires_confirmation:
        issues.append(
            {
                "id": "confirmation_not_required",
                "severity": "error",
                "summary": "Enabled network control actions must require explicit confirmation.",
            }
        )
    if not enabled:
        issues.append(
            {
                "id": "action_disabled",
                "severity": "info",
                "summary": "Control policy action is present but disabled.",
            }
        )

    if adapter == "service_control" or provider == "service_control":
        service_control = _service_control_action_diagnostics(
            action_id=action_id,
            target=target,
            service_control_settings=service_control_settings,
            lifecycle_required=action_policy.get("requires_graceful_lifecycle") is True,
        )
        issues.extend(service_control["issues"])
    if adapter == "switch_power_cycle" and enabled:
        power_readiness["required"] = True
        readiness = target.get("readiness") if isinstance(target.get("readiness"), dict) else {}
        readiness_checks = [item for item in readiness.get("checks") or [] if isinstance(item, dict)]
        power_readiness["configured"] = bool(readiness_checks)
        power_readiness["check_count"] = len(readiness_checks)
        capabilities = {str(item).strip() for item in target.get("capabilities") or [] if str(item).strip()}
        if (
            target.get("enabled") is not True
            or str(target.get("provider") or "").strip() != "home_assistant"
            or not str(target.get("entity_id") or "").strip().startswith("switch.")
            or "power_cycle" not in capabilities
        ):
            issues.append(
                {
                    "id": "power_target_not_actionable",
                    "severity": "error",
                    "summary": "Oracle inventory power target is not enabled for Home Assistant power cycling.",
                }
            )
        if not readiness_checks:
            power_readiness["issues"].append(
                {
                    "id": "power_readiness_missing",
                    "severity": "error",
                    "summary": "Power target does not declare post-cycle network readiness checks.",
                }
            )
        issues.extend(power_readiness["issues"])
    if adapter == "router_control":
        router_control = _router_control_action_diagnostics(
            action_id=action_id,
            target=target,
            policy_enabled=enabled,
            router_control_settings=router_control_settings,
        )
        issues.extend(router_control["issues"])

    configuration_status = "ready"
    if any(issue.get("severity") == "error" for issue in issues):
        configuration_status = "misconfigured"
    elif not enabled:
        configuration_status = "disabled"
    verification = verification_results.get((target_type, target_id, action_id), {})
    status = (
        "misconfigured"
        if configuration_status == "misconfigured"
        else "disabled"
        if configuration_status == "disabled"
        else "verified"
        if verification
        else "enabled_unverified"
    )

    return {
        "id": str(action_policy.get("id") or "").strip(),
        "target_type": target_type,
        "target_id": target_id,
        "action_id": action_id,
        "provider": provider,
        "adapter": adapter,
        "enabled": enabled,
        "requires_confirmation": requires_confirmation,
        "required_preconditions": [
            str(item).strip()
            for item in action_policy.get("required_preconditions") or []
            if str(item).strip()
        ],
        "status": status,
        "configuration_status": configuration_status,
        "verification": {
            "verified": bool(verification),
            "request_id": str(verification.get("request_id") or ""),
            "verified_at": str(verification.get("verified_at") or ""),
            "verification_status": str(verification.get("verification_status") or ""),
        },
        "target": {
            "exists": bool(target),
            "id": str(target.get("id") or target_id) if target else target_id,
            "display_name": str(target.get("display_name") or target_id) if target else target_id,
            "host_id": str(target.get("host_id") or "") if target else "",
        },
        "service_control": service_control,
        "router_control": router_control,
        "power_readiness": power_readiness,
        "issues": issues,
    }


def _empty_power_readiness_diagnostics() -> dict[str, Any]:
    return {
        "required": False,
        "configured": False,
        "check_count": 0,
        "issues": [],
    }


def _empty_router_control_diagnostics() -> dict[str, Any]:
    return {
        "required": False,
        "router_id": "",
        "profile_configured": False,
        "profile_enabled": False,
        "action_configured": False,
        "action_enabled": False,
        "transport": "",
        "bridge_adapter": "",
        "credentials_configured": False,
        "issues": [],
    }


def _router_control_action_diagnostics(
    *,
    action_id: str,
    target: dict[str, Any],
    policy_enabled: bool,
    router_control_settings: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = _empty_router_control_diagnostics()
    diagnostics["required"] = True
    router_id = str(target.get("id") or "").strip()
    diagnostics["router_id"] = router_id
    routers = router_control_settings.get("routers") if isinstance(router_control_settings.get("routers"), dict) else {}
    profile = routers.get(router_id) if isinstance(routers, dict) else None
    diagnostics["profile_configured"] = isinstance(profile, dict)
    if not isinstance(profile, dict):
        diagnostics["issues"].append(
            {
                "id": "router_control_profile_missing",
                "severity": "error",
                "summary": "Router-control bridge profile is not configured.",
            }
        )
        return diagnostics
    diagnostics["profile_enabled"] = profile.get("enabled") is True
    diagnostics["transport"] = str(profile.get("transport") or "").strip()
    diagnostics["bridge_adapter"] = str(profile.get("adapter") or "").strip()
    diagnostics["credentials_configured"] = bool(
        str(profile.get("user") or "").strip()
        and (str(profile.get("password") or "").strip() or str(profile.get("password_env") or "").strip())
    )
    allowed_actions = profile.get("allowed_actions") if isinstance(profile.get("allowed_actions"), dict) else {}
    action = allowed_actions.get(action_id) if isinstance(allowed_actions, dict) else None
    diagnostics["action_configured"] = isinstance(action, dict)
    diagnostics["action_enabled"] = isinstance(action, dict) and action.get("enabled") is True
    if not diagnostics["action_configured"]:
        diagnostics["issues"].append(
            {
                "id": "router_control_action_missing",
                "severity": "error",
                "summary": "Router-control bridge action is not configured.",
            }
        )
    if diagnostics["transport"] != "ssh" or diagnostics["bridge_adapter"] != "ssh_reboot":
        diagnostics["issues"].append(
            {
                "id": "router_control_adapter_invalid",
                "severity": "error",
                "summary": "Router-control bridge must use the allowlisted SSH reboot adapter.",
            }
        )
    if not diagnostics["credentials_configured"]:
        diagnostics["issues"].append(
            {
                "id": "router_control_credentials_missing",
                "severity": "error",
                "summary": "Router-control bridge login is not configured.",
            }
        )
    if not diagnostics["profile_enabled"] or not diagnostics["action_enabled"]:
        diagnostics["issues"].append(
            {
                "id": "router_control_disabled",
                "severity": "error" if policy_enabled else "info",
                "summary": "Router-control bridge profile or action is disabled.",
            }
        )
    return diagnostics


def _service_control_action_diagnostics(
    *,
    action_id: str,
    target: dict[str, Any],
    service_control_settings: dict[str, Any],
    lifecycle_required: bool,
) -> dict[str, Any]:
    diagnostics = _empty_service_control_diagnostics()
    diagnostics["required"] = True
    if action_id == "restart_host":
        host_id = str(target.get("id") or "").strip()
        diagnostics["host_id"] = host_id
        hosts = service_control_settings.get("hosts") if isinstance(service_control_settings.get("hosts"), dict) else {}
        host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
        diagnostics["host_configured"] = isinstance(host_entry, dict)
        if not isinstance(host_entry, dict):
            diagnostics["issues"].append(
                {
                    "id": "service_control_host_missing",
                    "severity": "error",
                    "summary": "Service-control bridge host profile is not configured.",
                }
            )
            return diagnostics
        diagnostics["host_enabled"] = host_entry.get("enabled") is True
        diagnostics["transport"] = str(host_entry.get("transport") or "").strip()
        diagnostics["platform"] = str(host_entry.get("platform") or "").strip()
        diagnostics["bridge_adapter"] = "host_restart"
        allowed_actions = host_entry.get("allowed_actions") if isinstance(host_entry.get("allowed_actions"), dict) else {}
        action = allowed_actions.get(action_id) if isinstance(allowed_actions, dict) else None
        diagnostics["command_allowed"] = isinstance(action, dict) and action.get("enabled") is True
        readiness = action.get("readiness") if isinstance(action, dict) and isinstance(action.get("readiness"), dict) else {}
        lifecycle = action.get("lifecycle") if isinstance(action, dict) and isinstance(action.get("lifecycle"), dict) else {}
        readiness_services = [item for item in readiness.get("services") or [] if str(item).strip()]
        readiness_http_checks = [item for item in readiness.get("http_checks") or [] if isinstance(item, dict)]
        diagnostics["readiness_configured"] = bool(readiness_services or readiness_http_checks)
        diagnostics["readiness_check_count"] = len(readiness_services) + len(readiness_http_checks)
        diagnostics["lifecycle_required"] = lifecycle_required
        diagnostics["lifecycle_configured"] = lifecycle.get("mode") == "graceful"
        if host_entry.get("enabled") is not True:
            diagnostics["issues"].append(
                {
                    "id": "service_control_host_disabled",
                    "severity": "error",
                    "summary": "Service-control bridge host profile is disabled.",
                }
            )
        if diagnostics["transport"] not in {"local", "ssh"} or diagnostics["platform"] not in {"linux", "windows"}:
            diagnostics["issues"].append(
                {
                    "id": "service_control_host_restart_unsupported",
                    "severity": "error",
                    "summary": "Service-control bridge host restart requires a supported transport and platform.",
                }
            )
        if not diagnostics["command_allowed"]:
            diagnostics["issues"].append(
                {
                    "id": "service_control_command_missing",
                    "severity": "error",
                    "summary": "Service-control bridge host profile does not allow this action.",
                }
            )
        if not diagnostics["readiness_configured"]:
            diagnostics["issues"].append(
                {
                    "id": "service_control_readiness_missing",
                    "severity": "error",
                    "summary": "Service-control bridge host profile does not declare post-restart readiness checks.",
                }
            )
        if lifecycle_required and not diagnostics["lifecycle_configured"]:
            diagnostics["issues"].append(
                {
                    "id": "service_control_lifecycle_missing",
                    "severity": "error",
                    "summary": "Service-control bridge host profile does not declare a graceful restart lifecycle.",
                }
            )
        return diagnostics
    service_ref = _service_control_ref_for_action(target=target, action_id=action_id)
    host_id = str(service_ref.get("host_id") or "").strip()
    service_name = str(service_ref.get("service_name") or "").strip()
    diagnostics["host_id"] = host_id
    diagnostics["service_name"] = service_name
    if not host_id or not service_name:
        diagnostics["issues"].append(
            {
                "id": "service_control_ref_missing",
                "severity": "error",
                "summary": "Oracle inventory target does not declare a service-control bridge reference.",
            }
        )
        return diagnostics

    hosts = service_control_settings.get("hosts") if isinstance(service_control_settings.get("hosts"), dict) else {}
    host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
    diagnostics["host_configured"] = isinstance(host_entry, dict)
    if not isinstance(host_entry, dict):
        diagnostics["issues"].append(
            {
                "id": "service_control_host_missing",
                "severity": "error",
                "summary": "Service-control bridge host profile is not configured.",
            }
        )
        return diagnostics

    diagnostics["host_enabled"] = host_entry.get("enabled") is True
    diagnostics["transport"] = str(host_entry.get("transport") or "").strip()
    diagnostics["platform"] = str(host_entry.get("platform") or "").strip()
    if host_entry.get("enabled") is not True:
        diagnostics["issues"].append(
            {
                "id": "service_control_host_disabled",
                "severity": "error",
                "summary": "Service-control bridge host profile is disabled.",
            }
        )

    services = host_entry.get("services") if isinstance(host_entry.get("services"), dict) else {}
    service_entry = services.get(service_name) if isinstance(services, dict) else None
    diagnostics["service_configured"] = isinstance(service_entry, dict)
    if not isinstance(service_entry, dict):
        diagnostics["issues"].append(
            {
                "id": "service_control_service_missing",
                "severity": "error",
                "summary": "Service-control bridge service profile is not configured.",
            }
        )
        return diagnostics

    diagnostics["bridge_adapter"] = str(service_entry.get("adapter") or "").strip()
    commands = {str(item).strip() for item in service_entry.get("commands") or [] if str(item).strip()}
    diagnostics["command_allowed"] = action_id in commands
    if action_id not in commands:
        diagnostics["issues"].append(
            {
                "id": "service_control_command_missing",
                "severity": "error",
                "summary": "Service-control bridge service profile does not allow this action.",
            }
        )
    return diagnostics


def service_control_ref_for_action(*, target: dict[str, Any], action_id: str) -> dict[str, Any]:
    return _service_control_ref_for_action(target=target, action_id=action_id)


def _service_control_ref_for_action(*, target: dict[str, Any], action_id: str) -> dict[str, Any]:
    control_refs = target.get("control_refs") if isinstance(target.get("control_refs"), dict) else {}
    service_control = control_refs.get("service_control")
    if not isinstance(service_control, dict):
        return {}
    actions = service_control.get("actions")
    if isinstance(actions, dict):
        action_ref = actions.get(action_id)
        return action_ref if isinstance(action_ref, dict) else {}
    return service_control


def _empty_service_control_diagnostics() -> dict[str, Any]:
    return {
        "required": False,
        "host_id": "",
        "service_name": "",
        "host_configured": False,
        "host_enabled": False,
        "service_configured": False,
        "command_allowed": False,
        "transport": "",
        "platform": "",
        "bridge_adapter": "",
        "readiness_configured": False,
        "readiness_check_count": 0,
        "lifecycle_required": False,
        "lifecycle_configured": False,
        "issues": [],
    }


def _find_inventory_target(
    *,
    inventory: dict[str, list[dict[str, Any]]],
    target_type: str,
    target_id: str,
) -> dict[str, Any]:
    if target_type == "host":
        section = "hosts"
    elif target_type == "service":
        section = "services"
    elif target_type == "power_target":
        section = "power_targets"
    else:
        return {}
    for item in inventory.get(section) or []:
        if isinstance(item, dict) and str(item.get("id") or "") == target_id:
            return item
    return {}


def _find_action_policy(
    *,
    control_policy: dict[str, list[dict[str, Any]]],
    target_type: str,
    target_id: str,
    action_id: str,
) -> dict[str, Any]:
    for item in control_policy.get("actions") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("target_type") or "").strip().lower() != target_type:
            continue
        if str(item.get("target_id") or "").strip() != target_id:
            continue
        if str(item.get("action_id") or "").strip() != action_id:
            continue
        return item
    return {}


def _build_plan_steps(
    action_policy: dict[str, Any],
    *,
    lifecycle: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    steps = [
        {
            "id": "policy_check",
            "kind": "policy",
            "summary": "Oracle control policy allows this target and action.",
        }
    ]
    required_preconditions = [
        str(item).strip()
        for item in action_policy.get("required_preconditions") or []
        if str(item).strip()
    ]
    if required_preconditions:
        steps.append(
            {
                "id": "preconditions",
                "kind": "precondition",
                "summary": f"Required preconditions: {', '.join(required_preconditions)}.",
            }
        )
    if action_policy.get("requires_graceful_lifecycle") is True:
        phases = [
            item
            for item in (lifecycle or {}).get("phases") or []
            if isinstance(item, dict)
        ]
        steps.extend(
            {
                "id": str(item.get("id") or "lifecycle_phase"),
                "kind": str(item.get("kind") or "preparation"),
                "summary": str(item.get("summary") or "Graceful host lifecycle phase."),
            }
            for item in phases
        )
    if action_policy.get("requires_confirmation") is True:
        steps.append(
            {
                "id": "confirmation",
                "kind": "confirmation",
                "summary": "Explicit confirmation is required before execution.",
            }
        )
    provider = str(action_policy.get("provider") or "provider").strip()
    adapter = str(action_policy.get("adapter") or "adapter").strip()
    steps.append(
        {
            "id": "provider_adapter",
            "kind": "provider",
            "summary": f"Would call {provider} adapter {adapter} after confirmation.",
        }
    )
    return steps


def _denied(base: dict[str, Any], *, error_class: str, summary: str) -> dict[str, Any]:
    return {
        **base,
        "allowed": False,
        "policy_status": "denied",
        "error_class": error_class,
        "summary": summary,
    }


def _blocked(base: dict[str, Any], *, error_class: str, summary: str) -> dict[str, Any]:
    return {
        **base,
        "allowed": False,
        "policy_status": "blocked",
        "error_class": error_class,
        "summary": summary,
    }


def _first_precondition_with_status(
    preconditions: list[dict[str, Any]],
    statuses: set[str],
) -> dict[str, Any]:
    for item in preconditions:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").strip().lower() in statuses:
            return item
    return {}


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())
