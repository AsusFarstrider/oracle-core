from __future__ import annotations

import subprocess
import time
from typing import Any, Callable

from .config import get_home_assistant_settings
from .network_control import service_control_ref_for_action
from .network_control_local_restart import (
    clear_pending_local_host_restart,
    stage_pending_local_host_restart,
)
from .network_control_local_service_restart import (
    clear_pending_local_service_restart,
    stage_pending_local_service_restart,
)
from .provider_bridges.home_assistant import HomeAssistantBridge, HomeAssistantBridgeError
from .provider_bridges.network_probe import NetworkProbeBridge
from .provider_bridges.router_control import execute_router_action
from .provider_bridges.service_control import (
    check_host_readiness,
    check_service_available,
    execute_service_action,
    execute_service_command,
    prepare_host_restart,
    recover_host_restart_dependents,
    recover_host_restart_services,
    rollback_host_restart_preparation,
)


def execute_network_control_action(
    *,
    action_policy: dict[str, Any],
    target: dict[str, Any] | None = None,
    service_control_settings: dict[str, Any] | None = None,
    router_control_settings: dict[str, Any] | None = None,
    network_probe_settings: dict[str, Any] | None = None,
    control_context: dict[str, Any] | None = None,
    verify_available: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    adapter = str(action_policy.get("adapter") or "").strip()
    if adapter == "service_control":
        if str(action_policy.get("action_id") or "").strip() == "restart_host":
            return _execute_host_restart_action(
                action_policy=action_policy,
                target=target or {},
                service_control_settings=service_control_settings or {},
                control_context=control_context or {},
            )
        return _execute_service_control_action(
            action_policy=action_policy,
            target=target or {},
            service_control_settings=service_control_settings or {},
            control_context=control_context or {},
            verify_available=verify_available,
        )
    if adapter == "switch_power_cycle":
        return _execute_switch_power_cycle_action(
            action_policy=action_policy,
            target=target or {},
            network_probe_settings=network_probe_settings or {},
        )
    if adapter == "router_control":
        return _execute_router_control_action(
            action_policy=action_policy,
            target=target or {},
            router_control_settings=router_control_settings or {},
        )

    execution = action_policy.get("execution") if isinstance(action_policy.get("execution"), dict) else {}
    method = str(execution.get("method") or "").strip().lower()
    if adapter != "service_restart" or method != "systemd":
        return {
            "ok": False,
            "result_status": "not_implemented",
            "error_class": "network_control_execution_not_implemented",
            "summary": "No execution adapter is implemented for this allowlist entry.",
            "steps": [
                {
                    "id": "execution_not_implemented",
                    "kind": "execution",
                    "summary": "No provider adapter is implemented for this action.",
                }
            ],
        }

    unit = str(execution.get("unit") or "").strip()
    wait_seconds = _bounded_wait_seconds(execution.get("wait_seconds"))
    restart_timeout_seconds = _bounded_restart_timeout_seconds(execution.get("restart_timeout_seconds"))
    started = time.monotonic()
    steps: list[dict[str, str]] = [
        {
            "id": "restart_sent",
            "kind": "execution",
            "summary": "Restart request was sent to the configured systemd unit.",
        }
    ]
    try:
        restart = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=restart_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        steps.append(
            {
                "id": "restart_timeout",
                "kind": "execution",
                "summary": f"Restart command timed out after {restart_timeout_seconds} second(s).",
            }
        )
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_restart_timeout",
            "summary": "Oracle stopped waiting for the configured systemd restart request.",
            "execution": {
                "method": "systemd",
                "unit": unit,
                "wait_seconds": wait_seconds,
                "restart_timeout_seconds": restart_timeout_seconds,
            },
            "steps": steps,
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_restart_failed",
            "summary": "Oracle could not send the configured systemd restart request.",
            "execution": {
                "method": "systemd",
                "unit": unit,
                "wait_seconds": wait_seconds,
                "restart_timeout_seconds": restart_timeout_seconds,
            },
            "steps": steps,
        }
    if restart.returncode != 0:
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_restart_failed",
            "summary": "Systemd did not accept the configured restart request.",
            "execution": {
                "method": "systemd",
                "unit": unit,
                "wait_seconds": wait_seconds,
                "restart_timeout_seconds": restart_timeout_seconds,
            },
            "steps": steps,
        }
    if wait_seconds:
        steps.append(
            {
                "id": "wait_after_restart_started",
                "kind": "wait",
                "summary": f"Waiting {wait_seconds} second(s) before checking service availability.",
            }
        )
        time.sleep(wait_seconds)
        steps.append(
            {
                "id": "wait_after_restart",
                "kind": "wait",
                "summary": f"Waited {wait_seconds} second(s) before checking service availability.",
            }
        )

    verification_status = "not_checked"
    if verify_available is not None:
        check = verify_available()
        verification_status = str(check.get("status") or "unknown")
        steps.append(
            {
                "id": "availability_check",
                "kind": "verification",
                "summary": str(check.get("summary") or "Availability check completed."),
            }
        )
        if verification_status != "passed":
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": "network_control_verification_failed",
                "summary": str(check.get("summary") or "Restart was sent, but availability verification did not pass."),
                "execution": {
                    "method": "systemd",
                    "unit": unit,
                    "wait_seconds": wait_seconds,
                    "restart_timeout_seconds": restart_timeout_seconds,
                    "verification_status": verification_status,
                },
                "steps": steps,
            }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": True,
        "result_status": "executed",
        "error_class": "",
        "summary": f"Restart completed and verification status is {verification_status}.",
        "execution": {
            "method": "systemd",
            "unit": unit,
            "wait_seconds": wait_seconds,
            "restart_timeout_seconds": restart_timeout_seconds,
            "verification_status": verification_status,
        },
        "steps": steps
        + [
            {
                "id": "execution_completed",
                "kind": "execution",
                "summary": f"Execution completed in {elapsed_ms} ms.",
            }
        ],
    }


def _execute_host_restart_action(
    *,
    action_policy: dict[str, Any],
    target: dict[str, Any],
    service_control_settings: dict[str, Any],
    control_context: dict[str, Any],
) -> dict[str, Any]:
    host_id = str(target.get("id") or "").strip()
    if not host_id:
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_host_restart_target_missing",
            "summary": "Host restart requires an Oracle host target.",
            "steps": [],
        }
    execution = action_policy.get("execution") if isinstance(action_policy.get("execution"), dict) else {}
    shutdown_timeout = _bounded_host_shutdown_timeout(execution.get("shutdown_timeout_seconds"))
    recovery_timeout = _bounded_power_recovery_timeout(execution.get("recovery_timeout_seconds"))
    recovery_poll_seconds = _bounded_power_recovery_poll(execution.get("recovery_poll_seconds"))
    readiness_timeout = _bounded_host_readiness_timeout(execution.get("readiness_timeout_seconds"))
    host_address = _first_address(target)
    lifecycle_required = action_policy.get("requires_graceful_lifecycle") is True
    lifecycle_completed: list[str] = []
    lifecycle_steps: list[dict[str, str]] = []
    if lifecycle_required:
        prepared = prepare_host_restart(
            settings=service_control_settings,
            host_id=host_id,
            timeout_seconds=readiness_timeout,
        )
        lifecycle_completed = [
            str(item)
            for item in prepared.get("completed_phase_ids") or []
            if str(item)
        ]
        if prepared.get("ok") is not True:
            return {
                "ok": False,
                "result_status": "blocked",
                "error_class": str(prepared.get("error") or "network_control_host_preparation_failed"),
                "summary": str(prepared.get("detail") or "Graceful host preparation did not pass."),
                "execution": {
                    "adapter": "service_control",
                    "lifecycle_status": "failed",
                    "lifecycle_completed_phase_ids": lifecycle_completed,
                },
                "steps": [
                    {
                        "id": "host_preparation_failed",
                        "kind": "preparation",
                        "summary": "Graceful host preparation failed. The reboot was not sent.",
                    }
                ],
            }
        lifecycle_steps.append(
            {
                "id": "host_preparation_completed",
                "kind": "preparation",
                "summary": "All mandatory graceful host preparation phases passed.",
            }
        )
    hosts = service_control_settings.get("hosts")
    host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
    local_restart = isinstance(host_entry, dict) and str(host_entry.get("transport") or "").strip() == "local"
    if local_restart:
        staged = stage_pending_local_host_restart(
            control_context=control_context,
            host_id=host_id,
            readiness_timeout_seconds=readiness_timeout,
            recovery_poll_seconds=recovery_poll_seconds,
            lifecycle_status="prepared" if lifecycle_required else "not_required",
        )
        if staged.get("ok") is not True:
            rollback = (
                rollback_host_restart_preparation(
                    settings=service_control_settings,
                    host_id=host_id,
                    completed_phase_ids=lifecycle_completed,
                    timeout_seconds=readiness_timeout,
                )
                if lifecycle_required
                else {}
            )
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": str(staged.get("error") or "network_control_local_restart_state_unavailable"),
                "summary": str(staged.get("detail") or "Local restart recovery state could not be persisted."),
                "execution": {
                    "adapter": "service_control",
                    "verification_status": "failed",
                    "lifecycle_status": (
                        "rolled_back"
                        if rollback.get("ok") is True
                        else "rollback_failed"
                        if lifecycle_required
                        else "not_required"
                    ),
                },
                "steps": lifecycle_steps,
            }
    result = execute_service_action(settings=service_control_settings, host=host_id, action="restart_host")
    if result.get("ok") is not True:
        if local_restart:
            clear_pending_local_host_restart()
        rollback = (
            rollback_host_restart_preparation(
                settings=service_control_settings,
                host_id=host_id,
                completed_phase_ids=lifecycle_completed,
                timeout_seconds=readiness_timeout,
            )
            if lifecycle_required
            else {}
        )
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": str(result.get("error") or "network_control_host_restart_failed"),
            "summary": str(result.get("detail") or "The service-control bridge did not send the host restart."),
            "execution": {
                "adapter": "service_control",
                "shutdown_timeout_seconds": shutdown_timeout,
                "recovery_timeout_seconds": recovery_timeout,
                "recovery_poll_seconds": recovery_poll_seconds,
                "readiness_timeout_seconds": readiness_timeout,
                "lifecycle_status": "rolled_back" if rollback.get("ok") is True else "rollback_failed" if lifecycle_required else "",
            },
            "steps": lifecycle_steps
            + (
                [
                    {
                        "id": "host_preparation_rolled_back",
                        "kind": "recovery",
                        "summary": "Host preparation was rolled back because the reboot request failed.",
                    }
                ]
                if lifecycle_required
                else []
            ),
        }
    steps: list[dict[str, str]] = lifecycle_steps + [
        {
            "id": "host_restart_request",
            "kind": "execution",
            "summary": "Host restart request was sent through the service-control bridge.",
        }
    ]
    if result.get("deferred") is True:
        return {
            "ok": True,
            "result_status": "executed",
            "error_class": "",
            "summary": "Local host restart was scheduled. Recovery verification is deferred.",
            "execution": {
                "adapter": "service_control",
                "deferred": True,
                "verification_status": "deferred",
                "lifecycle_status": "prepared" if lifecycle_required else "not_required",
            },
            "steps": steps
            + [
                {
                    "id": "host_recovery_deferred",
                    "kind": "verification",
                    "summary": "This Oracle process cannot verify recovery after restarting its own host.",
                }
            ],
        }
    if not host_address:
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_host_address_missing",
            "summary": "Host restart was sent, but Oracle has no address for recovery verification.",
            "steps": steps,
        }
    steps.append(
        {
            "id": "host_shutdown_wait_started",
            "kind": "wait",
            "summary": "Waiting for the host to go offline.",
        }
    )
    recovery = _wait_for_host_restart(
        host_address=host_address,
        shutdown_timeout_seconds=shutdown_timeout,
        recovery_timeout_seconds=recovery_timeout,
        poll_seconds=recovery_poll_seconds,
    )
    if recovery.get("went_offline") is not True:
        steps.append(
            {
                "id": "host_shutdown_not_observed",
                "kind": "verification",
                "summary": f"Host did not go offline within {shutdown_timeout} seconds.",
            }
        )
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_host_shutdown_not_observed",
            "summary": "Restart was sent, but Oracle did not observe the host go offline.",
            "execution": {
                "adapter": "service_control",
                "shutdown_timeout_seconds": shutdown_timeout,
                "recovery_timeout_seconds": recovery_timeout,
                "recovery_poll_seconds": recovery_poll_seconds,
                "verification_status": "failed",
            },
            "steps": steps,
        }
    steps.append(
        {
            "id": "host_shutdown_observed",
            "kind": "verification",
            "summary": "Host went offline after the restart request.",
        }
    )
    if recovery.get("recovered") is not True:
        steps.append(
            {
                "id": "host_recovery_failed",
                "kind": "verification",
                "summary": f"Host did not become reachable within {recovery_timeout} seconds.",
            }
        )
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_host_recovery_failed",
            "summary": "Host restarted but did not come back online in time.",
            "execution": {
                "adapter": "service_control",
                "shutdown_timeout_seconds": shutdown_timeout,
                "recovery_timeout_seconds": recovery_timeout,
                "recovery_poll_seconds": recovery_poll_seconds,
                "verification_status": "failed",
            },
            "steps": steps,
        }
    steps.append(
        {
            "id": "host_recovery_verified",
            "kind": "verification",
            "summary": f"Host is reachable after {recovery.get('recovery_attempts')} recovery check(s).",
        }
    )
    if lifecycle_required:
        host_service_recovery = recover_host_restart_services(
            settings=service_control_settings,
            host_id=host_id,
            timeout_seconds=readiness_timeout,
        )
        if host_service_recovery.get("ok") is not True:
            steps.append(
                {
                    "id": "host_services_recovery_failed",
                    "kind": "recovery",
                    "summary": "The host returned, but prepared host services could not be restored.",
                }
            )
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": str(
                    host_service_recovery.get("error") or "network_control_host_services_recovery_failed"
                ),
                "summary": str(
                    host_service_recovery.get("detail") or "Prepared host services did not recover."
                ),
                "execution": {
                    "adapter": "service_control",
                    "verification_status": "passed",
                    "readiness_status": "not_started",
                    "lifecycle_status": "recovery_failed",
                },
                "steps": steps,
            }
        if host_service_recovery.get("status") == "recovered":
            lifecycle_completed.append("restore_host_services")
            steps.append(
                {
                    "id": "host_services_recovered",
                    "kind": "recovery",
                    "summary": "Prepared host services were restored before readiness checks.",
                }
            )
    steps.append(
        {
            "id": "host_readiness_wait_started",
            "kind": "wait",
            "summary": "Checking expected host services and health endpoints.",
        }
    )
    readiness = _wait_for_host_readiness(
        service_control_settings=service_control_settings,
        host_id=host_id,
        timeout_seconds=readiness_timeout,
        poll_seconds=recovery_poll_seconds,
    )
    if readiness.get("ready") is not True:
        failed_check_ids = [str(item) for item in readiness.get("failed_check_ids") or [] if str(item)]
        steps.append(
            {
                "id": "host_readiness_failed",
                "kind": "verification",
                "summary": (
                    f"Host returned, but readiness checks did not pass: {', '.join(failed_check_ids)}."
                    if failed_check_ids
                    else "Host returned, but readiness checks did not pass."
                ),
            }
        )
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_host_readiness_failed",
            "summary": "Host returned online, but expected services or health checks were not ready.",
            "execution": {
                "adapter": "service_control",
                "shutdown_timeout_seconds": shutdown_timeout,
                "recovery_timeout_seconds": recovery_timeout,
                "recovery_poll_seconds": recovery_poll_seconds,
                "readiness_timeout_seconds": readiness_timeout,
                "verification_status": "failed",
                "readiness_status": "failed",
                "readiness_check_count": readiness.get("check_count", 0),
                "readiness_passed_count": readiness.get("passed_count", 0),
            },
            "steps": steps,
        }
    steps.extend(
        [
            {
                "id": "host_readiness_verified",
                "kind": "verification",
                "summary": f"All {readiness.get('check_count')} configured readiness checks passed.",
            },
        ]
    )
    if lifecycle_required:
        dependent_recovery = recover_host_restart_dependents(
            settings=service_control_settings,
            host_id=host_id,
            timeout_seconds=readiness_timeout,
        )
        if dependent_recovery.get("ok") is not True:
            steps.append(
                {
                    "id": "host_dependents_recovery_failed",
                    "kind": "recovery",
                    "summary": "The host recovered, but dependent client storage or services did not.",
                }
            )
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": str(dependent_recovery.get("error") or "network_control_host_dependents_recovery_failed"),
                "summary": str(dependent_recovery.get("detail") or "Dependent services did not recover."),
                "execution": {
                    "adapter": "service_control",
                    "verification_status": "passed",
                    "readiness_status": "passed",
                    "lifecycle_status": "recovery_failed",
                    "readiness_check_count": readiness.get("check_count", 0),
                    "readiness_passed_count": readiness.get("passed_count", 0),
                },
                "steps": steps,
            }
        if dependent_recovery.get("status") == "recovered":
            steps.append(
                {
                    "id": "host_dependents_recovered",
                    "kind": "recovery",
                    "summary": "Dependent client storage and services were restored and verified.",
                }
            )
    steps.append(
        {
            "id": "execution_completed",
            "kind": "execution",
            "summary": "Host restart, recovery, and readiness verification completed.",
        }
    )
    return {
        "ok": True,
        "result_status": "executed",
        "error_class": "",
        "summary": "Host restarted, returned online, and passed readiness checks.",
        "execution": {
            "adapter": "service_control",
            "shutdown_timeout_seconds": shutdown_timeout,
            "recovery_timeout_seconds": recovery_timeout,
            "recovery_poll_seconds": recovery_poll_seconds,
            "readiness_timeout_seconds": readiness_timeout,
            "verification_status": "passed",
            "readiness_status": "passed",
            "readiness_check_count": readiness.get("check_count", 0),
            "readiness_passed_count": readiness.get("passed_count", 0),
            "lifecycle_status": "passed" if lifecycle_required else "not_required",
            "lifecycle_completed_phase_ids": lifecycle_completed,
        },
        "steps": steps,
    }


def _execute_router_control_action(
    *,
    action_policy: dict[str, Any],
    target: dict[str, Any],
    router_control_settings: dict[str, Any],
) -> dict[str, Any]:
    action_id = str(action_policy.get("action_id") or "").strip()
    router_id = str(target.get("id") or "").strip()
    if action_id != "restart_router" or not router_id:
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_router_action_not_allowed",
            "summary": "The router-control adapter only supports restart_router for Oracle router hosts.",
            "steps": [],
        }
    execution = action_policy.get("execution") if isinstance(action_policy.get("execution"), dict) else {}
    shutdown_timeout = _bounded_host_shutdown_timeout(execution.get("shutdown_timeout_seconds"))
    recovery_timeout = _bounded_power_recovery_timeout(execution.get("recovery_timeout_seconds"))
    recovery_poll_seconds = _bounded_power_recovery_poll(execution.get("recovery_poll_seconds"))
    host_address = _first_address(target)
    steps: list[dict[str, str]] = []
    result = execute_router_action(settings=router_control_settings, router=router_id, action=action_id)
    if result.get("ok") is not True:
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": str(result.get("error") or "network_control_router_control_failed"),
            "summary": str(result.get("detail") or "Router-control bridge did not complete the request."),
            "execution": {
                "adapter": "router_control",
                "shutdown_timeout_seconds": shutdown_timeout,
                "recovery_timeout_seconds": recovery_timeout,
                "recovery_poll_seconds": recovery_poll_seconds,
            },
            "steps": steps,
        }
    steps.append(
        {
            "id": "router_restart_request",
            "kind": "execution",
            "summary": "Router restart request was sent through the router-control bridge.",
        }
    )
    if host_address:
        steps.append(
            {
                "id": "router_shutdown_wait_started",
                "kind": "wait",
                "summary": "Waiting for the router to go offline.",
            }
        )
        recovery = _wait_for_host_restart(
            host_address=host_address,
            shutdown_timeout_seconds=shutdown_timeout,
            recovery_timeout_seconds=recovery_timeout,
            poll_seconds=recovery_poll_seconds,
        )
        if recovery.get("went_offline") is not True:
            steps.append(
                {
                    "id": "router_shutdown_not_observed",
                    "kind": "verification",
                    "summary": f"Router did not go offline within {shutdown_timeout} seconds.",
                }
            )
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": "network_control_router_shutdown_not_observed",
                "summary": "Restart was sent, but Oracle did not observe the router go offline.",
                "execution": {
                    "adapter": "router_control",
                    "shutdown_timeout_seconds": shutdown_timeout,
                    "recovery_timeout_seconds": recovery_timeout,
                    "recovery_poll_seconds": recovery_poll_seconds,
                    "verification_status": "failed",
                },
                "steps": steps,
            }
        steps.extend(
            [
                {
                    "id": "router_shutdown_observed",
                    "kind": "verification",
                    "summary": "Router went offline after the restart request.",
                },
                {
                    "id": "router_recovery_wait_started",
                    "kind": "wait",
                    "summary": "Waiting for the router to come back online.",
                },
            ]
        )
        if recovery.get("recovered") is not True:
            steps.append(
                {
                    "id": "router_recovery_failed",
                    "kind": "verification",
                    "summary": f"Router did not become reachable within {recovery_timeout} seconds.",
                }
            )
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": "network_control_router_recovery_failed",
                "summary": "Router restart was sent, but the router did not come back online in time.",
                "execution": {
                    "adapter": "router_control",
                    "shutdown_timeout_seconds": shutdown_timeout,
                    "recovery_timeout_seconds": recovery_timeout,
                    "recovery_poll_seconds": recovery_poll_seconds,
                    "verification_status": "failed",
                },
                "steps": steps,
            }
        steps.append(
            {
                "id": "router_recovery_verified",
                "kind": "verification",
                "summary": f"Router is reachable after {recovery.get('recovery_attempts')} recovery check(s).",
            }
        )
    return {
        "ok": True,
        "result_status": "executed",
        "error_class": "",
        "summary": "Router restart completed and the router is reachable.",
        "execution": {
            "adapter": "router_control",
            "shutdown_timeout_seconds": shutdown_timeout,
            "recovery_timeout_seconds": recovery_timeout,
            "recovery_poll_seconds": recovery_poll_seconds,
            "shutdown_observed": True if host_address else False,
            "verification_status": "passed" if host_address else "not_checked",
        },
        "steps": steps
        + [
            {
                "id": "execution_completed",
                "kind": "execution",
                "summary": "Execution completed.",
            }
        ],
    }


def _execute_switch_power_cycle_action(
    *,
    action_policy: dict[str, Any],
    target: dict[str, Any],
    network_probe_settings: dict[str, Any],
) -> dict[str, Any]:
    if str(action_policy.get("action_id") or "").strip() != "power_cycle":
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_power_action_not_allowed",
            "summary": "The Home Assistant power adapter only supports power_cycle.",
            "steps": [],
        }
    entity_id = str(target.get("entity_id") or "").strip()
    if (
        target.get("enabled") is not True
        or str(target.get("provider") or "").strip() != "home_assistant"
        or "power_cycle" not in {str(item).strip() for item in target.get("capabilities") or []}
        or not entity_id.startswith("switch.")
    ):
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_power_target_invalid",
            "summary": "The Oracle power target is not enabled for Home Assistant power cycling.",
            "steps": [],
        }

    execution = action_policy.get("execution") if isinstance(action_policy.get("execution"), dict) else {}
    off_seconds = _bounded_power_off_seconds(execution.get("off_seconds"))
    verification_timeout = _bounded_power_verification_timeout(execution.get("verification_timeout_seconds"))
    recovery_timeout = _bounded_power_recovery_timeout(execution.get("recovery_timeout_seconds"))
    recovery_poll_seconds = _bounded_power_recovery_poll(execution.get("recovery_poll_seconds"))
    readiness_timeout = _bounded_host_readiness_timeout(execution.get("readiness_timeout_seconds"))
    host_address = str(target.get("host_address") or "").strip()
    host_display_name = str(target.get("host_display_name") or target.get("host_id") or "device").strip()
    steps: list[dict[str, str]] = []
    started = time.monotonic()
    try:
        base_url, token = get_home_assistant_settings()
    except (RuntimeError, ValueError):
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_home_assistant_unavailable",
            "summary": "Home Assistant control settings are unavailable.",
            "steps": steps,
        }
    bridge = HomeAssistantBridge(base_url=base_url, token=token)
    turned_off = False
    try:
        bridge.call_service(service_domain="switch", service_name="turn_off", entity_id=entity_id)
        turned_off = True
        off_state = bridge.wait_for_entity_state(entity_id, "off", timeout_seconds=verification_timeout)
        if str((off_state or {}).get("state") or "").strip().lower() != "off":
            raise HomeAssistantBridgeError("Power target did not report off.")
        steps.append({"id": "power_off_verified", "kind": "verification", "summary": "Home Assistant reported the power target off."})
        time.sleep(off_seconds)
        steps.append({"id": "power_off_wait", "kind": "wait", "summary": f"Waited {off_seconds} second(s) before restoring power."})
        bridge.call_service(service_domain="switch", service_name="turn_on", entity_id=entity_id)
        on_state = bridge.wait_for_entity_state(entity_id, "on", timeout_seconds=verification_timeout)
        if str((on_state or {}).get("state") or "").strip().lower() != "on":
            raise HomeAssistantBridgeError("Power target did not report on.")
        steps.append({"id": "power_on_verified", "kind": "verification", "summary": "Home Assistant reported the power target on."})
    except (HomeAssistantBridgeError, ValueError):
        if turned_off:
            try:
                bridge.call_service(service_domain="switch", service_name="turn_on", entity_id=entity_id)
            except HomeAssistantBridgeError:
                pass
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_power_cycle_failed",
            "summary": "Home Assistant did not complete and verify the power cycle.",
            "execution": {
                "adapter": "switch_power_cycle",
                "off_seconds": off_seconds,
                "verification_timeout_seconds": verification_timeout,
            },
            "steps": steps,
        }

    if host_address:
        steps.append(
            {
                "id": "host_recovery_wait_started",
                "kind": "wait",
                "summary": f"Power restored. Waiting for {host_display_name} to come back online.",
            }
        )
        probe = NetworkProbeBridge()
        deadline = time.monotonic() + recovery_timeout
        recovered = False
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            check = probe.check_host_reachable(host_address, timeout_seconds=2)
            if str(check.get("status") or "").strip().lower() == "healthy":
                recovered = True
                break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(recovery_poll_seconds, remaining))
        if not recovered:
            steps.append(
                {
                    "id": "host_recovery_failed",
                    "kind": "verification",
                    "summary": f"{host_display_name} did not become reachable within {recovery_timeout} seconds.",
                }
            )
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": "network_control_host_recovery_failed",
                "summary": "Power was restored, but the target host did not come back online in time.",
                "execution": {
                    "adapter": "switch_power_cycle",
                    "off_seconds": off_seconds,
                    "verification_timeout_seconds": verification_timeout,
                    "recovery_timeout_seconds": recovery_timeout,
                    "recovery_poll_seconds": recovery_poll_seconds,
                    "verification_status": "failed",
                    "power_restored": True,
                },
                "steps": steps,
            }
        steps.append(
            {
                "id": "host_recovery_verified",
                "kind": "verification",
                "summary": f"{host_display_name} is reachable after {attempts} recovery check(s).",
            }
        )

    steps.append(
        {
            "id": "power_readiness_wait_started",
            "kind": "wait",
            "summary": "Checking expected network readiness after power restoration.",
        }
    )
    readiness = _wait_for_power_readiness(
        profile=target.get("readiness") if isinstance(target.get("readiness"), dict) else {},
        internet_settings=network_probe_settings,
        timeout_seconds=readiness_timeout,
        poll_seconds=recovery_poll_seconds,
    )
    if readiness.get("ready") is not True:
        failed_check_ids = [str(item) for item in readiness.get("failed_check_ids") or [] if str(item)]
        steps.append(
            {
                "id": "power_readiness_failed",
                "kind": "verification",
                "summary": (
                    f"Power was restored, but readiness checks did not pass: {', '.join(failed_check_ids)}."
                    if failed_check_ids
                    else "Power was restored, but readiness checks did not pass."
                ),
            }
        )
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_power_readiness_failed",
            "summary": "Power was restored, but expected network readiness did not return in time.",
            "execution": {
                "adapter": "switch_power_cycle",
                "off_seconds": off_seconds,
                "verification_timeout_seconds": verification_timeout,
                "recovery_timeout_seconds": recovery_timeout,
                "recovery_poll_seconds": recovery_poll_seconds,
                "readiness_timeout_seconds": readiness_timeout,
                "verification_status": "passed",
                "readiness_status": "failed",
                "readiness_check_count": readiness.get("check_count", 0),
                "readiness_passed_count": readiness.get("passed_count", 0),
                "readiness_failed_check_ids": failed_check_ids,
                "power_restored": True,
            },
            "steps": steps,
        }
    steps.append(
        {
            "id": "power_readiness_verified",
            "kind": "verification",
            "summary": f"All {readiness.get('check_count')} configured readiness checks passed.",
        }
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    steps.append({"id": "execution_completed", "kind": "execution", "summary": f"Execution completed in {elapsed_ms} ms."})
    return {
        "ok": True,
        "result_status": "executed",
        "error_class": "",
        "summary": (
            f"Power cycle completed and {host_display_name} is reachable."
            if host_address
            else "Power cycle completed through Home Assistant and the switch returned on."
        ),
        "execution": {
            "adapter": "switch_power_cycle",
            "off_seconds": off_seconds,
            "verification_timeout_seconds": verification_timeout,
            "recovery_timeout_seconds": recovery_timeout,
            "recovery_poll_seconds": recovery_poll_seconds,
            "readiness_timeout_seconds": readiness_timeout,
            "verification_status": "passed",
            "readiness_status": "passed",
            "readiness_check_count": readiness.get("check_count", 0),
            "readiness_passed_count": readiness.get("passed_count", 0),
        },
        "steps": steps,
    }


def _bounded_power_off_seconds(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 10
    return min(120, max(5, parsed))


def _bounded_power_verification_timeout(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 8
    return min(30, max(3, parsed))


def _bounded_power_recovery_timeout(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 90
    return min(300, max(15, parsed))


def _bounded_host_shutdown_timeout(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 90
    return min(300, max(15, parsed))


def _bounded_host_readiness_timeout(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 120
    return min(300, max(15, parsed))


def _bounded_power_recovery_poll(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 5
    return min(30, max(2, parsed))


def _first_address(target: dict[str, Any]) -> str:
    addresses = target.get("addresses") if isinstance(target.get("addresses"), list) else []
    return next((str(item).strip() for item in addresses if str(item).strip()), "")


def _wait_for_host_recovery(
    *,
    host_address: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    probe = NetworkProbeBridge()
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        check = probe.check_host_reachable(host_address, timeout_seconds=2)
        if str(check.get("status") or "").strip().lower() == "healthy":
            return {"recovered": True, "attempts": attempts}
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))
    return {"recovered": False, "attempts": attempts}


def _wait_for_host_restart(
    *,
    host_address: str,
    shutdown_timeout_seconds: int,
    recovery_timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    probe = NetworkProbeBridge()
    shutdown_deadline = time.monotonic() + shutdown_timeout_seconds
    shutdown_attempts = 0
    while True:
        shutdown_attempts += 1
        result = probe.check_tcp_reachable(
            host_address,
            port=22,
            timeout_seconds=min(3, poll_seconds),
        )
        if str(result.get("status") or "").strip().lower() != "healthy":
            break
        if time.monotonic() >= shutdown_deadline:
            return {
                "went_offline": False,
                "recovered": False,
                "shutdown_attempts": shutdown_attempts,
                "recovery_attempts": 0,
            }
        time.sleep(poll_seconds)
    recovery_deadline = time.monotonic() + recovery_timeout_seconds
    recovery_attempts = 0
    recovered = False
    while time.monotonic() < recovery_deadline:
        recovery_attempts += 1
        result = probe.check_tcp_reachable(
            host_address,
            port=22,
            timeout_seconds=min(3, poll_seconds),
        )
        if str(result.get("status") or "").strip().lower() == "healthy":
            recovered = True
            break
        remaining = recovery_deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))
    return {
        "went_offline": True,
        "recovered": recovered,
        "shutdown_attempts": shutdown_attempts,
        "recovery_attempts": recovery_attempts,
    }


def _wait_for_host_readiness(
    *,
    service_control_settings: dict[str, Any],
    host_id: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_result: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_result = check_host_readiness(
            settings=service_control_settings,
            host_id=host_id,
            timeout_seconds=min(15, poll_seconds + 5),
        )
        if last_result.get("ok") is True:
            return {
                "ready": True,
                "check_count": last_result.get("check_count", 0),
                "passed_count": last_result.get("passed_count", 0),
                "failed_check_ids": [],
            }
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))
    return {
        "ready": False,
        "check_count": last_result.get("check_count", 0),
        "passed_count": last_result.get("passed_count", 0),
        "failed_check_ids": list(last_result.get("failed_check_ids") or []),
    }


def _wait_for_power_readiness(
    *,
    profile: dict[str, Any],
    internet_settings: dict[str, Any],
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    checks = profile.get("checks") if isinstance(profile.get("checks"), list) else []
    if not checks:
        return {
            "ready": False,
            "check_count": 0,
            "passed_count": 0,
            "failed_check_ids": ["readiness_not_configured"],
        }
    deadline = time.monotonic() + timeout_seconds
    bridge = NetworkProbeBridge()
    last_result: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_result = bridge.check_readiness(
            profile=profile,
            internet_settings=internet_settings,
        )
        if last_result.get("ok") is True:
            return {
                "ready": True,
                "check_count": last_result.get("check_count", 0),
                "passed_count": last_result.get("passed_count", 0),
                "failed_check_ids": [],
            }
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))
    return {
        "ready": False,
        "check_count": last_result.get("check_count", 0),
        "passed_count": last_result.get("passed_count", 0),
        "failed_check_ids": list(last_result.get("failed_check_ids") or []),
    }


def _bounded_wait_seconds(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return min(120, max(0, parsed))


def _bounded_restart_timeout_seconds(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 15
    return min(60, max(5, parsed))


def _execute_service_control_action(
    *,
    action_policy: dict[str, Any],
    target: dict[str, Any],
    service_control_settings: dict[str, Any],
    control_context: dict[str, Any],
    verify_available: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    action_id = str(action_policy.get("action_id") or "").strip()
    service_ref = service_control_ref_for_action(target=target, action_id=action_id)
    host_id = str(service_ref.get("host_id") or "").strip()
    service_name = str(service_ref.get("service_name") or "").strip()
    execution = action_policy.get("execution") if isinstance(action_policy.get("execution"), dict) else {}
    restart_timeout_seconds = _bounded_restart_timeout_seconds(execution.get("restart_timeout_seconds"))
    wait_seconds = _bounded_wait_seconds(execution.get("wait_seconds"))
    steps: list[dict[str, str]] = [
        {
            "id": "service_control_request",
            "kind": "execution",
            "summary": "Restart request was sent through the service-control bridge.",
        }
    ]
    if action_id not in {"restart_service", "restart_runtime", "restart_ui"} or not host_id or not service_name:
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": "network_control_service_control_ref_missing",
            "summary": "Oracle could not resolve a service-control reference for this target.",
            "execution": {"adapter": "service_control", "wait_seconds": wait_seconds},
            "steps": steps,
        }

    deferred_self_restart = _is_deferred_local_service_restart(
        settings=service_control_settings,
        host_id=host_id,
        service_name=service_name,
    )
    if deferred_self_restart:
        staged = stage_pending_local_service_restart(
            control_context=control_context,
            target_id=str(target.get("id") or "").strip(),
            host_id=host_id,
            service_name=service_name,
        )
        if staged.get("ok") is not True:
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": str(staged.get("error") or "network_control_local_service_restart_state_unavailable"),
                "summary": str(staged.get("detail") or "Local service restart recovery state could not be persisted."),
                "execution": {
                    "adapter": "service_control",
                    "verification_status": "failed",
                },
                "steps": steps,
            }

    started = time.monotonic()
    result = execute_service_command(
        settings=service_control_settings,
        host_id=host_id,
        service_name=service_name,
        command=action_id,
        timeout_seconds=restart_timeout_seconds,
    )
    if result.get("ok") is not True:
        if deferred_self_restart:
            clear_pending_local_service_restart()
        return {
            "ok": False,
            "result_status": "failed",
            "error_class": str(result.get("error") or "network_control_service_control_failed"),
            "summary": str(result.get("detail") or "Service-control bridge did not complete the request."),
            "execution": {
                "adapter": "service_control",
                "wait_seconds": wait_seconds,
                "restart_timeout_seconds": restart_timeout_seconds,
            },
            "steps": steps,
        }

    service_manager = str(result.get("service_manager") or "")
    deferred = result.get("deferred") is True
    if deferred:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        steps.extend(
            [
                {
                    "id": "restart_scheduled",
                    "kind": "execution",
                    "summary": "Restart was scheduled through service-control and will run after this response returns.",
                },
                {
                    "id": "availability_check_deferred",
                    "kind": "verification",
                    "summary": "Availability verification is deferred because the local service will restart this process.",
                },
                {
                    "id": "execution_completed",
                    "kind": "execution",
                    "summary": f"Execution completed in {elapsed_ms} ms.",
                },
            ]
        )
        return {
            "ok": True,
            "result_status": "executed",
            "error_class": "",
            "summary": "Restart was scheduled through service-control; verify Oracle Brain after it comes back online.",
            "execution": {
                "adapter": "service_control",
                "service_manager": service_manager,
                "wait_seconds": wait_seconds,
                "restart_timeout_seconds": restart_timeout_seconds,
                "verification_status": "deferred",
                "deferred": True,
            },
            "steps": steps,
        }
    if wait_seconds:
        steps.append(
            {
                "id": "wait_after_restart_started",
                "kind": "wait",
                "summary": f"Waiting {wait_seconds} second(s) before checking service availability.",
            }
        )
        time.sleep(wait_seconds)
        steps.append(
            {
                "id": "wait_after_restart",
                "kind": "wait",
                "summary": f"Waited {wait_seconds} second(s) before checking service availability.",
            }
        )

    verification_status = "not_checked"
    if verify_available is not None:
        check = verify_available()
        verification_status = str(check.get("status") or "unknown")
        steps.append(
            {
                "id": "availability_check",
                "kind": "verification",
                "summary": str(check.get("summary") or "Availability check completed."),
            }
        )
        if verification_status != "passed":
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": "network_control_verification_failed",
                "summary": str(check.get("summary") or "Restart was sent, but availability verification did not pass."),
                "execution": {
                    "adapter": "service_control",
                    "wait_seconds": wait_seconds,
                    "restart_timeout_seconds": restart_timeout_seconds,
                    "verification_status": verification_status,
                },
                "steps": steps,
            }
    else:
        check = check_service_available(
            settings=service_control_settings,
            host_id=host_id,
            service_name=service_name,
            command=action_id,
            timeout_seconds=restart_timeout_seconds,
        )
        verification_status = "passed" if check.get("ok") is True else "failed"
        service_manager = str(check.get("service_manager") or service_manager)
        steps.append(
            {
                "id": "availability_check",
                "kind": "verification",
                "summary": str(check.get("detail") or "Service-control availability check completed."),
            }
        )
        if verification_status != "passed":
            return {
                "ok": False,
                "result_status": "failed",
                "error_class": str(check.get("error") or "network_control_verification_failed"),
                "summary": str(check.get("detail") or "Restart was sent, but availability verification did not pass."),
                "execution": {
                    "adapter": "service_control",
                    "service_manager": service_manager,
                    "wait_seconds": wait_seconds,
                    "restart_timeout_seconds": restart_timeout_seconds,
                    "verification_status": verification_status,
                },
                "steps": steps,
            }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": True,
        "result_status": "executed",
        "error_class": "",
        "summary": f"Restart completed through service-control and verification status is {verification_status}.",
        "execution": {
            "adapter": "service_control",
            "service_manager": service_manager,
            "wait_seconds": wait_seconds,
            "restart_timeout_seconds": restart_timeout_seconds,
            "verification_status": verification_status,
        },
        "steps": steps
        + [
            {
                "id": "execution_completed",
                "kind": "execution",
                "summary": f"Execution completed in {elapsed_ms} ms.",
            }
        ],
    }


def _is_deferred_local_service_restart(
    *,
    settings: dict[str, Any],
    host_id: str,
    service_name: str,
) -> bool:
    hosts = settings.get("hosts")
    host = hosts.get(host_id) if isinstance(hosts, dict) else None
    services = host.get("services") if isinstance(host, dict) else None
    service = services.get(service_name) if isinstance(services, dict) else None
    return (
        isinstance(host, dict)
        and str(host.get("transport") or "").strip().lower() == "local"
        and isinstance(service, dict)
        and str(service.get("adapter") or "").strip().lower() == "systemd"
        and (
            service.get("restart_mode") == "deferred_self_restart"
            or service.get("deferred_self_restart") is True
        )
    )
