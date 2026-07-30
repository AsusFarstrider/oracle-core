from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from typing import Any
from urllib import error, request

from oracle_app.configuration.domain_models import ServiceControlAdapter
from oracle_app.provider_bridges.ssh_transport import SshHostVerificationError, strict_ssh_options


_RESTART_COMMANDS = {"restart_service", "restart_runtime", "restart_ui"}
_HOST_ACTIONS = {"restart_host"}
_WINDOWS_TASK_PATTERN = re.compile(r"^[A-Za-z0-9_. -]+$")
_LINUX_BLOCK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_SYSTEMD_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9@_.-]+$")
_DOCKER_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")


def get_available_service_actions(settings: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    hosts = settings.get("hosts")
    if not isinstance(hosts, dict):
        return actions
    for host_key, host in hosts.items():
        if not isinstance(host, dict):
            continue
        if host.get("enabled") is not True:
            continue
        allowed_actions = host.get("allowed_actions")
        if not isinstance(allowed_actions, dict):
            continue
        for action_key, action in allowed_actions.items():
            if not isinstance(action, dict) or action.get("enabled") is not True:
                continue
            actions.append(
                {
                    "kind": "service_control",
                    "host": str(host_key),
                    "action": str(action_key),
                }
            )
    return actions


def execute_service_action(*, settings: dict[str, Any], host: str, action: str) -> dict[str, Any]:
    hosts = settings.get("hosts")
    if not isinstance(hosts, dict):
        return {
            "ok": False,
            "error": "service_control_not_configured",
            "detail": "No approved service-control hosts are configured.",
        }
    host_entry = hosts.get(host)
    if not isinstance(host_entry, dict):
        return {
            "ok": False,
            "error": "service_control_host_not_allowed",
            "detail": f"Host {host} is not approved for service control.",
        }
    if host_entry.get("enabled") is not True:
        return _service_control_error("service_control_host_disabled", f"Host {host} is disabled for service control.")
    allowed_actions = host_entry.get("allowed_actions")
    action_entry = allowed_actions.get(action) if isinstance(allowed_actions, dict) else None
    if not isinstance(action_entry, dict):
        return {
            "ok": False,
            "error": "service_control_action_not_allowed",
            "detail": f"Action {action} is not approved for host {host}.",
        }
    if action_entry.get("enabled") is not True:
        return _service_control_error("service_control_action_disabled", f"Action {action} is disabled for host {host}.")
    if action not in _HOST_ACTIONS:
        return _service_control_error("service_control_not_implemented", "Approved host control execution is not implemented.")

    transport = str(host_entry.get("transport") or "").strip().lower()
    platform = str(host_entry.get("platform") or "").strip().lower()
    if platform not in {"linux", "windows"}:
        return _service_control_error("service_control_platform_not_implemented", "Host-control platform is not implemented.")
    if transport == "local":
        if platform != "linux":
            return _service_control_error("service_control_transport_not_implemented", "Local host restart is not implemented.")
        return _schedule_deferred_host_restart()
    if transport != "ssh":
        return _service_control_error("service_control_transport_not_implemented", "Host-control transport is not implemented.")

    command_argv = (
        ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "reboot"]
        if platform == "linux"
        else ["shutdown.exe", "/r", "/t", "0", "/f"]
    )
    argv, stdin, environment = _host_transport_command(
        host_entry=host_entry,
        command_argv=command_argv,
    )
    if not argv:
        return _service_control_error("service_control_transport_not_configured", "Host-control transport is not fully configured.")
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": True,
            "status": "restart_sent",
            "platform": platform,
            "detail": "Host restart connection closed while the restart request was being sent.",
        }
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_command_failed", "Host restart request could not be sent.")
    if result.returncode not in {0, 255}:
        return _service_control_error("service_control_command_failed", "Host restart request returned a failure.")
    return {
        "ok": True,
        "status": "restart_sent",
        "platform": platform,
        "detail": "Host restart request was sent.",
    }


def execute_service_command(
    *,
    settings: dict[str, Any],
    host_id: str,
    service_name: str,
    command: str,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    hosts = settings.get("hosts")
    if not isinstance(hosts, dict):
        return _service_control_error("service_control_not_configured", "No approved service-control hosts are configured.")
    host_entry = hosts.get(host_id)
    if not isinstance(host_entry, dict):
        return _service_control_error("service_control_host_not_allowed", f"Host {host_id} is not approved for service control.")
    if host_entry.get("enabled") is not True:
        return _service_control_error("service_control_host_disabled", f"Host {host_id} is disabled for service control.")
    transport = str(host_entry.get("transport") or "").strip().lower()
    if transport not in {"local", "ssh"}:
        return _service_control_error("service_control_transport_not_implemented", "Service-control transport is not implemented.")

    services = host_entry.get("services")
    service_entry = services.get(service_name) if isinstance(services, dict) else None
    if not isinstance(service_entry, dict):
        return _service_control_error("service_control_service_not_allowed", f"Service {service_name} is not approved on host {host_id}.")
    commands = {str(item).strip() for item in service_entry.get("commands") or [] if str(item).strip()}
    if command not in commands:
        return _service_control_error("service_control_command_not_allowed", f"Command {command} is not approved for service {service_name}.")

    adapter = str(service_entry.get("adapter") or "").strip().lower()
    target = str(service_entry.get("target") or "").strip()
    if command not in _RESTART_COMMANDS:
        return _service_control_error("service_control_command_not_implemented", f"Command {command} is not implemented.")
    command_argv = _service_command_argv(
        adapter=adapter,
        target=target,
        restart_mode=str(service_entry.get("restart_mode") or "").strip().lower(),
    )
    if not command_argv:
        return _service_control_error("service_control_adapter_not_implemented", f"Adapter {adapter} is not implemented.")
    if _should_defer_local_restart(host_entry=host_entry, service_entry=service_entry, transport=transport, adapter=adapter):
        scheduled = _schedule_deferred_systemd_restart(
            target=target,
            delay_seconds=_bounded_deferred_delay_seconds(service_entry.get("deferred_delay_seconds")),
        )
        if scheduled.get("ok") is not True:
            return scheduled
        return {
            "ok": True,
            "status": "scheduled",
            "service_manager": adapter,
            "deferred": True,
            "detail": "Service-control restart was scheduled and will run after the response returns.",
        }
    argv, stdin, environment = _transport_command_argv(host_entry=host_entry, transport=transport, command_argv=command_argv)
    if not argv:
        return _service_control_error("service_control_transport_not_configured", "Service-control transport is not fully configured.")

    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(5, min(60, int(timeout_seconds or 15))),
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return _service_control_error("service_control_command_timeout", "Service-control command timed out.")
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_command_failed", "Service-control command could not be sent.")
    if result.returncode != 0:
        return _service_control_error("service_control_command_failed", "Service-control command returned a failure.")
    return {
        "ok": True,
        "status": "executed",
        "service_manager": adapter,
        "detail": "Service-control command completed.",
    }


def execute_typed_service_action(
    *,
    adapter: ServiceControlAdapter,
    credential: str | None,
    operation: str,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    stopped_companions: list[str] = []
    if adapter.target_kind == "host":
        if operation != "restart_host":
            return _service_control_error("service_control_not_implemented", "Approved host control execution is not implemented.")
        if adapter.transport == "local":
            if adapter.platform != "linux":
                return _service_control_error("service_control_transport_not_implemented", "Local host restart is not implemented.")
            return _schedule_deferred_host_restart()
        command_argv = (
            ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "reboot"]
            if adapter.platform == "linux"
            else ["shutdown.exe", "/r", "/t", "0", "/f"]
        )
    else:
        if operation not in _RESTART_COMMANDS:
            return _service_control_error("service_control_command_not_implemented", f"Command {operation} is not implemented.")
        command_argv = _service_command_argv(
            adapter=str(adapter.service_adapter or ""),
            target=str(adapter.service_target or ""),
            restart_mode=adapter.restart_mode,
        )
        if not command_argv:
            return _service_control_error("service_control_adapter_not_implemented", "Configured service adapter is not implemented.")
        if adapter.transport == "local" and adapter.restart_mode == "deferred_self_restart":
            return _schedule_deferred_systemd_restart(
                target=str(adapter.service_target or ""),
                delay_seconds=int(adapter.deferred_delay_seconds or 2),
            )
        for target in adapter.lifecycle_service_targets:
            stopped = _run_typed_service_command(
                adapter=adapter,
                credential=credential,
                command_argv=_service_state_argv(
                    adapter=str(adapter.service_adapter or ""),
                    target=target,
                    desired_state="stopped",
                ),
                timeout_seconds=timeout_seconds,
            )
            if stopped.get("ok") is not True:
                _restore_typed_companions(
                    adapter=adapter,
                    credential=credential,
                    targets=stopped_companions,
                    timeout_seconds=timeout_seconds,
                )
                return _service_control_error(
                    "service_control_lifecycle_service_failed",
                    "A configured companion service could not be stopped.",
                )
            stopped_companions.append(target)

    argv, stdin, environment = _typed_transport_command(
        adapter=adapter,
        credential=credential,
        command_argv=command_argv,
    )
    if not argv:
        return _service_control_error("service_control_transport_not_configured", "Service-control transport is not fully configured.")
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(5, min(60, int(timeout_seconds or 15))),
            env=environment,
        )
    except subprocess.TimeoutExpired:
        _restore_typed_companions(adapter=adapter, credential=credential, targets=stopped_companions, timeout_seconds=timeout_seconds)
        if adapter.target_kind == "host":
            return {"ok": True, "status": "restart_sent", "platform": adapter.platform, "detail": "Host restart connection closed while the restart request was being sent."}
        return _service_control_error("service_control_command_timeout", "Service-control command timed out.")
    except (OSError, subprocess.SubprocessError):
        _restore_typed_companions(adapter=adapter, credential=credential, targets=stopped_companions, timeout_seconds=timeout_seconds)
        return _service_control_error("service_control_command_failed", "Service-control command could not be sent.")
    valid_codes = {0, 255} if adapter.target_kind == "host" else {0}
    if result.returncode not in valid_codes:
        _restore_typed_companions(adapter=adapter, credential=credential, targets=stopped_companions, timeout_seconds=timeout_seconds)
        return _service_control_error("service_control_command_failed", "Service-control command returned a failure.")
    restored = _restore_typed_companions(
        adapter=adapter,
        credential=credential,
        targets=stopped_companions,
        timeout_seconds=timeout_seconds,
    )
    if restored.get("ok") is not True:
        return _service_control_error(
            "service_control_lifecycle_service_failed",
            "The primary service restarted, but a companion service could not be restored.",
        )
    return {
        "ok": True,
        "status": "restart_sent" if adapter.target_kind == "host" else "executed",
        "platform": adapter.platform,
        "service_manager": adapter.service_adapter,
        "detail": "Host restart request was sent." if adapter.target_kind == "host" else "Service-control command completed.",
    }


def check_typed_service_available(
    *,
    adapter: ServiceControlAdapter,
    credential: str | None,
    timeout_seconds: int = 8,
) -> dict[str, Any]:
    if adapter.target_kind != "service":
        return _service_control_error("service_control_service_not_allowed", "Adapter does not target a service.")
    command_argv = _service_status_argv(
        adapter=str(adapter.service_adapter or ""),
        target=str(adapter.service_target or ""),
        verification_mode=str(adapter.verification_mode or ""),
    )
    if not command_argv:
        return _service_control_error("service_control_status_not_implemented", "Configured service status check is not implemented.")
    argv, stdin, environment = _typed_transport_command(
        adapter=adapter,
        credential=credential,
        command_argv=command_argv,
    )
    if not argv:
        return _service_control_error("service_control_transport_not_configured", "Service-control transport is not fully configured.")
    try:
        result = subprocess.run(
            argv, input=stdin, check=False, capture_output=True, text=True,
            timeout=max(3, min(30, int(timeout_seconds or 8))), env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_status_failed", "Configured service status check could not be completed.")
    if adapter.service_adapter == "systemd" and result.returncode == 3:
        return {"ok": False, "status": "failed", "available": False}
    if (
        adapter.service_adapter == "docker"
        and result.returncode == 0
        and str(result.stdout or "").strip().lower() != "true"
    ):
        return {"ok": False, "status": "failed", "available": False}
    if result.returncode != 0:
        return _service_control_error("service_control_status_failed", "Configured service status check did not pass.")
    return {"ok": True, "status": "available", "available": True}


def check_service_available(
    *,
    settings: dict[str, Any],
    host_id: str,
    service_name: str,
    command: str = "restart_service",
    timeout_seconds: int = 8,
) -> dict[str, Any]:
    resolved = _resolve_service_control_target(
        settings=settings,
        host_id=host_id,
        service_name=service_name,
        command=command,
    )
    if resolved.get("ok") is not True:
        return resolved

    host_entry = resolved["host_entry"]
    transport = str(resolved["transport"])
    adapter = str(resolved["adapter"])
    target = str(resolved["target"])
    command_argv = _service_status_argv(
        adapter=adapter,
        target=target,
        verification_mode=str(resolved["service_entry"].get("verification_mode") or "").strip().lower(),
    )
    if not command_argv:
        return _service_control_error("service_control_status_not_implemented", f"Status checks are not implemented for {adapter}.")
    argv, stdin, environment = _transport_command_argv(host_entry=host_entry, transport=transport, command_argv=command_argv)
    if not argv:
        return _service_control_error("service_control_transport_not_configured", "Service-control transport is not fully configured.")

    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(3, min(30, int(timeout_seconds or 8))),
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return _service_control_error("service_control_status_timeout", "Service-control status check timed out.")
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_status_failed", "Service-control status check could not be sent.")
    if adapter == "systemd" and result.returncode == 3:
        return {
            "ok": False,
            "status": "failed",
            "available": False,
            "service_manager": adapter,
            "detail": "Service-control status check confirmed the service is inactive.",
        }
    if result.returncode != 0:
        return _service_control_error("service_control_status_failed", "Service-control status check did not pass.")
    if adapter == "docker" and str(result.stdout or "").strip().lower() != "true":
        return {
            "ok": False,
            "status": "failed",
            "available": False,
            "service_manager": adapter,
            "detail": "Service-control status check confirmed the service is not running.",
        }
    return {
        "ok": True,
        "status": "passed",
        "available": True,
        "service_manager": adapter,
        "detail": "Service-control status check passed.",
    }


def check_host_readiness(
    *,
    settings: dict[str, Any],
    host_id: str,
    timeout_seconds: int = 8,
) -> dict[str, Any]:
    hosts = settings.get("hosts")
    host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
    if not isinstance(host_entry, dict):
        return _service_control_error("service_control_host_not_allowed", f"Host {host_id} is not approved for service control.")
    allowed_actions = host_entry.get("allowed_actions") if isinstance(host_entry.get("allowed_actions"), dict) else {}
    restart_action = allowed_actions.get("restart_host") if isinstance(allowed_actions, dict) else None
    readiness = restart_action.get("readiness") if isinstance(restart_action, dict) else None
    if not isinstance(readiness, dict):
        return _service_control_error("service_control_readiness_not_configured", "Host readiness checks are not configured.")

    service_names = [str(item).strip() for item in readiness.get("services") or [] if str(item).strip()]
    read_write_mounts = [
        str(item).strip()
        for item in readiness.get("read_write_mounts") or []
        if str(item).strip().startswith("/")
    ]
    http_checks = [item for item in readiness.get("http_checks") or [] if isinstance(item, dict)]
    checks: list[dict[str, str]] = []
    for service_name in service_names:
        check = _check_configured_service(
            host_entry=host_entry,
            service_name=service_name,
            timeout_seconds=timeout_seconds,
        )
        checks.append(
            {
                "id": service_name,
                "kind": "service",
                "status": "passed" if check.get("ok") is True else "failed",
            }
        )
    for raw_check in http_checks:
        check_id = str(raw_check.get("id") or "").strip()
        url = str(raw_check.get("url") or "").strip()
        check = _check_json_health(url=url, timeout_seconds=timeout_seconds)
        checks.append(
            {
                "id": check_id,
                "kind": "http",
                "status": "passed" if check.get("ok") is True else "failed",
            }
        )
    transport = str(host_entry.get("transport") or "").strip().lower()
    for mount_path in read_write_mounts:
        mount = _run_read_only_command(
            host_entry=host_entry,
            transport=transport,
            command_argv=["findmnt", "-rn", "-o", "TARGET", mount_path],
            timeout_seconds=timeout_seconds,
        )
        mount_text = str(mount.get("stdout") or "").strip()
        mount_ok = (
            mount.get("ok") is True
            and mount_text == mount_path
            and _check_mount_write_access(
                host_entry=host_entry,
                transport=transport,
                mount_path=mount_path,
                timeout_seconds=timeout_seconds,
            )
        )
        checks.append(
            {
                "id": f"mount:{mount_path}",
                "kind": "mount",
                "status": "passed" if mount_ok else "failed",
            }
        )

    failed = [item for item in checks if item["status"] != "passed"]
    return {
        "ok": bool(checks) and not failed,
        "status": "passed" if checks and not failed else "failed",
        "check_count": len(checks),
        "passed_count": len(checks) - len(failed),
        "failed_check_ids": [item["id"] for item in failed],
        "checks": checks,
        "detail": (
            "All configured host readiness checks passed."
            if checks and not failed
            else "One or more configured host readiness checks did not pass."
        ),
    }


def check_storage_safety(
    *,
    settings: dict[str, Any],
    host_id: str,
    profile_id: str,
    timeout_seconds: int = 8,
) -> dict[str, Any]:
    hosts = settings.get("hosts")
    host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
    if not isinstance(host_entry, dict) or host_entry.get("enabled") is not True:
        return _storage_safety_result(configured=False)
    allowed_actions = host_entry.get("allowed_actions") if isinstance(host_entry.get("allowed_actions"), dict) else {}
    restart_action = allowed_actions.get("restart_host") if isinstance(allowed_actions, dict) else None
    preconditions = restart_action.get("preconditions") if isinstance(restart_action, dict) else None
    profile = preconditions.get(profile_id) if isinstance(preconditions, dict) else None
    if not isinstance(profile, dict) or str(profile.get("kind") or "") != "linux_storage":
        return _storage_safety_result(configured=False)

    array_name = str(profile.get("array") or "").strip()
    mount_path = str(profile.get("mount") or "").strip()
    service_name = str(profile.get("service") or "").strip()
    if (
        not _LINUX_BLOCK_NAME_PATTERN.fullmatch(array_name)
        or not mount_path.startswith("/")
        or not service_name
    ):
        return _storage_safety_result(configured=False)

    transport = str(host_entry.get("transport") or "").strip().lower()
    mdstat = _run_read_only_command(
        host_entry=host_entry,
        transport=transport,
        command_argv=["cat", "/proc/mdstat"],
        timeout_seconds=timeout_seconds,
    )
    mount = _run_read_only_command(
        host_entry=host_entry,
        transport=transport,
        command_argv=["findmnt", "-rn", "-o", "SOURCE,TARGET,OPTIONS", mount_path],
        timeout_seconds=timeout_seconds,
    )
    service = _check_configured_service(
        host_entry=host_entry,
        service_name=service_name,
        timeout_seconds=timeout_seconds,
    )
    mdstat_text = str(mdstat.get("stdout") or "")
    raid_ok = mdstat.get("ok") is True and _raid_array_healthy(mdstat_text, array_name=array_name)
    mount_text = str(mount.get("stdout") or "").strip()
    mount_ok = (
        mount.get("ok") is True
        and _mount_target(mount_text) == mount_path
        and any(option == "rw" for option in _mount_options(mount_text))
    )
    checks = [
        {"id": "raid", "status": "passed" if raid_ok else "failed"},
        {"id": "mount", "status": "passed" if mount_ok else "failed"},
        {"id": "sharing_service", "status": "passed" if service.get("ok") is True else "failed"},
    ]
    passed_count = len([item for item in checks if item["status"] == "passed"])
    return {
        "ok": passed_count == len(checks),
        "configured": True,
        "status": "passed" if passed_count == len(checks) else "failed",
        "check_count": len(checks),
        "passed_count": passed_count,
        "failed_check_ids": [item["id"] for item in checks if item["status"] != "passed"],
        "checks": checks,
        "detail": "Storage safety checks completed.",
    }


def get_host_restart_lifecycle_plan(
    *,
    settings: dict[str, Any],
    host_id: str,
) -> dict[str, Any]:
    profile = _host_restart_lifecycle_profile(settings=settings, host_id=host_id)
    if profile is None:
        return {
            "configured": False,
            "mode": "",
            "phases": [],
            "summary": "Graceful host lifecycle is not configured.",
        }
    phases: list[dict[str, str]] = []
    client_release = profile.get("client_release")
    if isinstance(client_release, dict):
        phases.append(
            {
                "id": "release_client_storage",
                "kind": "preparation",
                "summary": "Stop dependent Oracle services and release the client storage mount.",
            }
        )
    prepare_services = _string_list(profile.get("prepare_services"))
    if prepare_services:
        phases.append(
            {
                "id": "stop_host_services",
                "kind": "preparation",
                "summary": f"Stop {len(prepare_services)} configured host service(s) cleanly.",
            }
        )
    storage = profile.get("storage")
    if isinstance(storage, dict):
        phases.append(
            {
                "id": "close_host_storage",
                "kind": "preparation",
                "summary": "Stop storage sharing, flush writes, unmount storage, and stop the RAID array.",
            }
        )
    phases.append(
        {
            "id": "restart_host",
            "kind": "execution",
            "summary": "Restart the host only after all mandatory preparation phases pass.",
        }
    )
    phases.append(
        {
            "id": "verify_host_recovery",
            "kind": "verification",
            "summary": "Verify host recovery and configured readiness checks.",
        }
    )
    if isinstance(client_release, dict):
        phases.append(
            {
                "id": "restore_client_storage",
                "kind": "recovery",
                "summary": "Restore the client storage mount and restart dependent Oracle services.",
            }
        )
    return {
        "configured": True,
        "mode": "graceful",
        "phases": phases,
        "summary": f"Graceful host lifecycle has {len(phases)} mandatory phase(s).",
    }


def prepare_host_restart(
    *,
    settings: dict[str, Any],
    host_id: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    profile = _host_restart_lifecycle_profile(settings=settings, host_id=host_id)
    if profile is None:
        return _lifecycle_result(
            ok=False,
            error="service_control_lifecycle_not_configured",
            detail="Graceful host lifecycle is not configured.",
        )
    completed: list[str] = []
    client_release = profile.get("client_release")
    if isinstance(client_release, dict):
        client_result = _release_client_storage(
            settings=settings,
            profile=client_release,
            timeout_seconds=timeout_seconds,
        )
        if client_result.get("ok") is not True:
            return client_result
        completed.append("release_client_storage")

    prepare_services = _string_list(profile.get("prepare_services"))
    if prepare_services:
        services_result = _set_configured_services_state(
            settings=settings,
            host_id=host_id,
            service_names=prepare_services,
            desired_state="stopped",
            timeout_seconds=timeout_seconds,
        )
        if services_result.get("ok") is not True:
            if isinstance(client_release, dict):
                _restore_client_storage(settings=settings, profile=client_release, timeout_seconds=timeout_seconds)
            return services_result
        completed.append("stop_host_services")

    storage = profile.get("storage")
    if isinstance(storage, dict):
        storage_result = _close_host_storage(
            settings=settings,
            host_id=host_id,
            profile=storage,
            timeout_seconds=timeout_seconds,
        )
        if storage_result.get("ok") is not True:
            _rollback_host_preparation(
                settings=settings,
                host_id=host_id,
                profile=profile,
                completed=completed,
                timeout_seconds=timeout_seconds,
            )
            return storage_result
        completed.append("close_host_storage")
    return {
        "ok": True,
        "status": "prepared",
        "completed_phase_ids": completed,
        "detail": "All configured graceful host preparation phases passed.",
    }


def rollback_host_restart_preparation(
    *,
    settings: dict[str, Any],
    host_id: str,
    completed_phase_ids: list[str],
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    profile = _host_restart_lifecycle_profile(settings=settings, host_id=host_id)
    if profile is None:
        return _lifecycle_result(
            ok=False,
            error="service_control_lifecycle_not_configured",
            detail="Graceful host lifecycle is not configured.",
        )
    return _rollback_host_preparation(
        settings=settings,
        host_id=host_id,
        profile=profile,
        completed=completed_phase_ids,
        timeout_seconds=timeout_seconds,
    )


def recover_host_restart_dependents(
    *,
    settings: dict[str, Any],
    host_id: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    profile = _host_restart_lifecycle_profile(settings=settings, host_id=host_id)
    if profile is None:
        return _lifecycle_result(
            ok=False,
            error="service_control_lifecycle_not_configured",
            detail="Graceful host lifecycle is not configured.",
        )
    client_release = profile.get("client_release")
    if not isinstance(client_release, dict):
        return {
            "ok": True,
            "status": "not_required",
            "completed_phase_ids": [],
            "detail": "No cross-host lifecycle recovery is required.",
        }
    result = _restore_client_storage(
        settings=settings,
        profile=client_release,
        timeout_seconds=timeout_seconds,
    )
    if result.get("ok") is not True:
        return result
    return {
        "ok": True,
        "status": "recovered",
        "completed_phase_ids": ["restore_client_storage"],
        "detail": "Client storage and dependent services were restored.",
    }


def recover_host_restart_services(
    *,
    settings: dict[str, Any],
    host_id: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    profile = _host_restart_lifecycle_profile(settings=settings, host_id=host_id)
    if profile is None:
        return _lifecycle_result(
            ok=False,
            error="service_control_lifecycle_not_configured",
            detail="Graceful host lifecycle is not configured.",
        )
    prepare_services = _string_list(profile.get("prepare_services"))
    if not prepare_services:
        return {
            "ok": True,
            "status": "not_required",
            "completed_phase_ids": [],
            "detail": "No prepared host services require recovery.",
        }
    result = _set_configured_services_state(
        settings=settings,
        host_id=host_id,
        service_names=prepare_services,
        desired_state="started",
        timeout_seconds=timeout_seconds,
    )
    if result.get("ok") is not True:
        return result
    return {
        "ok": True,
        "status": "recovered",
        "completed_phase_ids": ["restore_host_services"],
        "detail": "Prepared host services were restored.",
    }


def _service_control_error(error: str, detail: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "detail": detail,
    }


def _lifecycle_result(*, ok: bool, error: str, detail: str) -> dict[str, Any]:
    return {
        "ok": ok,
        "error": error,
        "detail": detail,
        "completed_phase_ids": [],
    }


def _host_restart_lifecycle_profile(
    *,
    settings: dict[str, Any],
    host_id: str,
) -> dict[str, Any] | None:
    hosts = settings.get("hosts")
    host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
    allowed_actions = host_entry.get("allowed_actions") if isinstance(host_entry, dict) else None
    restart_action = allowed_actions.get("restart_host") if isinstance(allowed_actions, dict) else None
    lifecycle = restart_action.get("lifecycle") if isinstance(restart_action, dict) else None
    if not isinstance(lifecycle, dict) or lifecycle.get("mode") != "graceful":
        return None
    services = host_entry.get("services") if isinstance(host_entry.get("services"), dict) else {}
    if any(service_name not in services for service_name in _string_list(lifecycle.get("prepare_services"))):
        return None
    storage = lifecycle.get("storage")
    if isinstance(storage, dict):
        if (
            not _LINUX_BLOCK_NAME_PATTERN.fullmatch(str(storage.get("array") or "").strip())
            or not str(storage.get("mount") or "").strip().startswith("/")
            or str(storage.get("sharing_service") or "").strip() not in services
        ):
            return None
    client_release = lifecycle.get("client_release")
    if isinstance(client_release, dict):
        hosts = settings.get("hosts")
        client_host = hosts.get(str(client_release.get("host_id") or "").strip()) if isinstance(hosts, dict) else None
        client_services = client_host.get("services") if isinstance(client_host, dict) and isinstance(client_host.get("services"), dict) else {}
        if (
            not isinstance(client_host, dict)
            or not str(client_release.get("mount") or "").strip().startswith("/")
            or not _SYSTEMD_UNIT_PATTERN.fullmatch(str(client_release.get("mount_service") or "").strip())
            or any(service_name not in client_services for service_name in _string_list(client_release.get("services")))
        ):
            return None
    return lifecycle


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()]


def _release_client_storage(
    *,
    settings: dict[str, Any],
    profile: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    client_host_id = str(profile.get("host_id") or "").strip()
    services = _string_list(profile.get("services"))
    mount_path = str(profile.get("mount") or "").strip()
    if not client_host_id or not mount_path.startswith("/"):
        return _lifecycle_result(
            ok=False,
            error="service_control_lifecycle_invalid",
            detail="Client storage lifecycle profile is invalid.",
        )
    stopped = _set_configured_services_state(
        settings=settings,
        host_id=client_host_id,
        service_names=services,
        desired_state="stopped",
        timeout_seconds=timeout_seconds,
    )
    if stopped.get("ok") is not True:
        return stopped
    unmounted = _run_fixed_host_command(
        settings=settings,
        host_id=client_host_id,
        command_argv=["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "umount", mount_path],
        timeout_seconds=timeout_seconds,
    )
    if unmounted.get("ok") is not True:
        _set_configured_services_state(
            settings=settings,
            host_id=client_host_id,
            service_names=services,
            desired_state="started",
            timeout_seconds=timeout_seconds,
        )
        return _lifecycle_result(
            ok=False,
            error="service_control_client_storage_release_failed",
            detail="Dependent services stopped, but the client storage mount could not be released.",
        )
    return {"ok": True, "detail": "Client storage was released.", "completed_phase_ids": ["release_client_storage"]}


def _restore_client_storage(
    *,
    settings: dict[str, Any],
    profile: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    client_host_id = str(profile.get("host_id") or "").strip()
    mount_path = str(profile.get("mount") or "").strip()
    mount_service = str(profile.get("mount_service") or "").strip()
    services = _string_list(profile.get("services"))
    if not client_host_id or not mount_path.startswith("/") or not mount_service:
        return _lifecycle_result(
            ok=False,
            error="service_control_lifecycle_invalid",
            detail="Client storage recovery profile is invalid.",
        )
    mounted = _run_fixed_host_command(
        settings=settings,
        host_id=client_host_id,
        command_argv=["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "systemctl", "restart", mount_service],
        timeout_seconds=timeout_seconds,
    )
    if mounted.get("ok") is not True:
        return _lifecycle_result(
            ok=False,
            error="service_control_client_storage_restore_failed",
            detail="The client storage mount could not be restored.",
        )
    mount_state = _run_fixed_host_command(
        settings=settings,
        host_id=client_host_id,
        command_argv=["findmnt", "-rn", "-o", "SOURCE,TARGET,OPTIONS", mount_path],
        timeout_seconds=timeout_seconds,
    )
    mount_text = str(mount_state.get("stdout") or "").strip()
    if (
        mount_state.get("ok") is not True
        or _mount_target(mount_text) != mount_path
        or "rw" not in _mount_options(mount_text)
    ):
        remounted = _run_fixed_host_command(
            settings=settings,
            host_id=client_host_id,
            command_argv=[
                "sudo",
                "-S",
                "-p",
                "oracle-sudo-prompt:",
                "--",
                "mount",
                "-o",
                "remount,rw",
                mount_path,
            ],
            timeout_seconds=timeout_seconds,
        )
        mount_state = _run_fixed_host_command(
            settings=settings,
            host_id=client_host_id,
            command_argv=["findmnt", "-rn", "-o", "SOURCE,TARGET,OPTIONS", mount_path],
            timeout_seconds=timeout_seconds,
        )
        mount_text = str(mount_state.get("stdout") or "").strip()
        if (
            remounted.get("ok") is not True
            or mount_state.get("ok") is not True
            or _mount_target(mount_text) != mount_path
            or "rw" not in _mount_options(mount_text)
        ):
            return _lifecycle_result(
                ok=False,
                error="service_control_client_storage_restore_failed",
                detail="The client storage mount did not recover read-write.",
            )
    started = _set_configured_services_state(
        settings=settings,
        host_id=client_host_id,
        service_names=services,
        desired_state="started",
        timeout_seconds=timeout_seconds,
    )
    if started.get("ok") is not True:
        return started
    return {"ok": True, "detail": "Client storage and services were restored.", "completed_phase_ids": ["restore_client_storage"]}


def _close_host_storage(
    *,
    settings: dict[str, Any],
    host_id: str,
    profile: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    array_name = str(profile.get("array") or "").strip()
    mount_path = str(profile.get("mount") or "").strip()
    sharing_service = str(profile.get("sharing_service") or "").strip()
    if not _LINUX_BLOCK_NAME_PATTERN.fullmatch(array_name) or not mount_path.startswith("/") or not sharing_service:
        return _lifecycle_result(
            ok=False,
            error="service_control_lifecycle_invalid",
            detail="Host storage lifecycle profile is invalid.",
        )
    stopped = _set_configured_services_state(
        settings=settings,
        host_id=host_id,
        service_names=[sharing_service],
        desired_state="stopped",
        timeout_seconds=timeout_seconds,
    )
    if stopped.get("ok") is not True:
        return stopped
    sync_result = _run_fixed_host_command(
        settings=settings,
        host_id=host_id,
        command_argv=["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "sync"],
        timeout_seconds=timeout_seconds,
    )
    if sync_result.get("ok") is not True:
        _set_configured_services_state(
            settings=settings,
            host_id=host_id,
            service_names=[sharing_service],
            desired_state="started",
            timeout_seconds=timeout_seconds,
        )
        return _lifecycle_result(
            ok=False,
            error="service_control_storage_close_failed",
            detail="Storage writes could not be flushed.",
        )
    unmounted = _run_fixed_host_command(
        settings=settings,
        host_id=host_id,
        command_argv=["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "umount", mount_path],
        timeout_seconds=timeout_seconds,
    )
    if unmounted.get("ok") is not True:
        _set_configured_services_state(
            settings=settings,
            host_id=host_id,
            service_names=[sharing_service],
            desired_state="started",
            timeout_seconds=timeout_seconds,
        )
        return _lifecycle_result(
            ok=False,
            error="service_control_storage_close_failed",
            detail="Storage remained busy and could not be unmounted.",
        )
    array_stopped = _run_fixed_host_command(
        settings=settings,
        host_id=host_id,
        command_argv=["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mdadm", "--stop", f"/dev/{array_name}"],
        timeout_seconds=timeout_seconds,
    )
    if array_stopped.get("ok") is not True:
        _run_fixed_host_command(
            settings=settings,
            host_id=host_id,
            command_argv=["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mount", mount_path],
            timeout_seconds=timeout_seconds,
        )
        _set_configured_services_state(
            settings=settings,
            host_id=host_id,
            service_names=[sharing_service],
            desired_state="started",
            timeout_seconds=timeout_seconds,
        )
        return _lifecycle_result(
            ok=False,
            error="service_control_storage_close_failed",
            detail="The RAID array could not be stopped cleanly.",
        )
    return {"ok": True, "detail": "Host storage was closed cleanly.", "completed_phase_ids": ["close_host_storage"]}


def _rollback_host_preparation(
    *,
    settings: dict[str, Any],
    host_id: str,
    profile: dict[str, Any],
    completed: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    errors: list[str] = []
    storage = profile.get("storage")
    if "close_host_storage" in completed and isinstance(storage, dict):
        array_name = str(storage.get("array") or "").strip()
        mount_path = str(storage.get("mount") or "").strip()
        sharing_service = str(storage.get("sharing_service") or "").strip()
        for command_argv in [
            ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mdadm", "--assemble", f"/dev/{array_name}"],
            ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "mount", mount_path],
        ]:
            if _run_fixed_host_command(
                settings=settings,
                host_id=host_id,
                command_argv=command_argv,
                timeout_seconds=timeout_seconds,
            ).get("ok") is not True:
                errors.append("storage")
        if _set_configured_services_state(
            settings=settings,
            host_id=host_id,
            service_names=[sharing_service],
            desired_state="started",
            timeout_seconds=timeout_seconds,
        ).get("ok") is not True:
            errors.append("sharing_service")
    prepare_services = _string_list(profile.get("prepare_services"))
    if "stop_host_services" in completed and _set_configured_services_state(
        settings=settings,
        host_id=host_id,
        service_names=prepare_services,
        desired_state="started",
        timeout_seconds=timeout_seconds,
    ).get("ok") is not True:
        errors.append("host_services")
    client_release = profile.get("client_release")
    if "release_client_storage" in completed and isinstance(client_release, dict):
        if _restore_client_storage(
            settings=settings,
            profile=client_release,
            timeout_seconds=timeout_seconds,
        ).get("ok") is not True:
            errors.append("client_storage")
    return {
        "ok": not errors,
        "status": "rolled_back" if not errors else "rollback_failed",
        "failed_phase_ids": sorted(set(errors)),
        "detail": "Host preparation rollback completed." if not errors else "Host preparation rollback did not fully complete.",
    }


def _set_configured_services_state(
    *,
    settings: dict[str, Any],
    host_id: str,
    service_names: list[str],
    desired_state: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    hosts = settings.get("hosts")
    host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
    services = host_entry.get("services") if isinstance(host_entry, dict) else None
    if not isinstance(host_entry, dict) or not isinstance(services, dict):
        return _lifecycle_result(
            ok=False,
            error="service_control_host_not_allowed",
            detail="Lifecycle host services are not configured.",
        )
    ordered_names = list(reversed(service_names)) if desired_state == "started" else service_names
    completed_targets: list[tuple[str, str]] = []
    for service_name in ordered_names:
        service_entry = services.get(service_name)
        if not isinstance(service_entry, dict):
            return _lifecycle_result(
                ok=False,
                error="service_control_service_not_allowed",
                detail="A lifecycle service is not configured.",
            )
        adapter = str(service_entry.get("adapter") or "").strip().lower()
        targets = [*_string_list(service_entry.get("lifecycle_targets")), str(service_entry.get("target") or "").strip()]
        targets = [target for target in targets if target]
        if desired_state == "started":
            targets = list(reversed(targets))
        for target in targets:
            command_argv = _service_state_argv(adapter=adapter, target=target, desired_state=desired_state)
            if not command_argv:
                return _lifecycle_result(
                    ok=False,
                    error="service_control_lifecycle_adapter_not_implemented",
                    detail="A lifecycle service adapter is not implemented.",
                )
            result = _run_fixed_host_command(
                settings=settings,
                host_id=host_id,
                command_argv=command_argv,
                timeout_seconds=timeout_seconds,
            )
            if result.get("ok") is not True:
                if desired_state == "stopped":
                    for completed_adapter, completed_target in reversed(completed_targets):
                        rollback_argv = _service_state_argv(
                            adapter=completed_adapter,
                            target=completed_target,
                            desired_state="started",
                        )
                        if rollback_argv:
                            _run_fixed_host_command(
                                settings=settings,
                                host_id=host_id,
                                command_argv=rollback_argv,
                                timeout_seconds=timeout_seconds,
                            )
                return _lifecycle_result(
                    ok=False,
                    error="service_control_lifecycle_service_failed",
                    detail=f"A configured service could not be {desired_state}.",
                )
            if not _configured_target_has_state(
                settings=settings,
                host_id=host_id,
                adapter=adapter,
                target=target,
                desired_state=desired_state,
                timeout_seconds=timeout_seconds,
            ):
                if desired_state == "stopped":
                    rollback_targets = [*completed_targets, (adapter, target)]
                    for completed_adapter, completed_target in reversed(rollback_targets):
                        rollback_argv = _service_state_argv(
                            adapter=completed_adapter,
                            target=completed_target,
                            desired_state="started",
                        )
                        if rollback_argv:
                            _run_fixed_host_command(
                                settings=settings,
                                host_id=host_id,
                                command_argv=rollback_argv,
                                timeout_seconds=timeout_seconds,
                            )
                return _lifecycle_result(
                    ok=False,
                    error="service_control_lifecycle_service_verification_failed",
                    detail=f"A configured service did not reach the {desired_state} state.",
                )
            completed_targets.append((adapter, target))
    return {
        "ok": True,
        "status": desired_state,
        "service_count": len(service_names),
        "detail": f"Configured services were {desired_state}.",
    }


def _service_state_argv(*, adapter: str, target: str, desired_state: str) -> list[str]:
    action = "start" if desired_state == "started" else "stop"
    if adapter == "systemd" and _SYSTEMD_UNIT_PATTERN.fullmatch(target):
        return ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "systemctl", action, target]
    if adapter == "docker" and _DOCKER_TARGET_PATTERN.fullmatch(target):
        return ["docker", action, target]
    return []


def _configured_target_has_state(
    *,
    settings: dict[str, Any],
    host_id: str,
    adapter: str,
    target: str,
    desired_state: str,
    timeout_seconds: int,
) -> bool:
    hosts = settings.get("hosts")
    host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
    if not isinstance(host_entry, dict):
        return False
    command_argv = _service_status_argv(adapter=adapter, target=target)
    if not command_argv:
        return False
    transport = str(host_entry.get("transport") or "").strip().lower()
    argv, stdin, environment = _transport_command_argv(
        host_entry=host_entry,
        transport=transport,
        command_argv=command_argv,
    )
    if not argv:
        return False
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(5, min(120, int(timeout_seconds or 30))),
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if adapter == "systemd":
        return result.returncode == (0 if desired_state == "started" else 3)
    if adapter == "docker":
        running = str(result.stdout or "").strip().lower() == "true"
        return result.returncode == 0 and running is (desired_state == "started")
    return False


def _run_fixed_host_command(
    *,
    settings: dict[str, Any],
    host_id: str,
    command_argv: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    hosts = settings.get("hosts")
    host_entry = hosts.get(host_id) if isinstance(hosts, dict) else None
    if not isinstance(host_entry, dict) or host_entry.get("enabled") is not True:
        return _service_control_error("service_control_host_not_allowed", "Lifecycle host is not configured.")
    transport = str(host_entry.get("transport") or "").strip().lower()
    argv, stdin, environment = _transport_command_argv(
        host_entry=host_entry,
        transport=transport,
        command_argv=command_argv,
    )
    if not argv:
        return _service_control_error("service_control_transport_not_configured", "Lifecycle transport is not configured.")
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(5, min(120, int(timeout_seconds or 30))),
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_lifecycle_command_failed", "Lifecycle operation could not be completed.")
    if result.returncode != 0:
        return _service_control_error("service_control_lifecycle_command_failed", "Lifecycle operation returned a failure.")
    return {"ok": True, "detail": "Lifecycle operation completed."}


def _storage_safety_result(*, configured: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "configured": configured,
        "status": "unavailable" if not configured else "failed",
        "check_count": 0,
        "passed_count": 0,
        "failed_check_ids": [],
        "checks": [],
        "detail": "Storage safety checks are not configured.",
    }


def _run_read_only_command(
    *,
    host_entry: dict[str, Any],
    transport: str,
    command_argv: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    argv, stdin, environment = _transport_command_argv(
        host_entry=host_entry,
        transport=transport,
        command_argv=command_argv,
    )
    if not argv:
        return _service_control_error("service_control_transport_not_configured", "Read-only check transport is not configured.")
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(3, min(30, int(timeout_seconds or 8))),
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_status_failed", "Read-only check could not be completed.")
    return {
        "ok": result.returncode == 0,
        "stdout": str(result.stdout or "") if result.returncode == 0 else "",
    }


def _check_mount_write_access(
    *,
    host_entry: dict[str, Any],
    transport: str,
    mount_path: str,
    timeout_seconds: int,
) -> bool:
    script = (
        'probe="$1/.oracle-readiness-$$"; '
        'trap \'rm -f "$probe"\' EXIT HUP INT TERM; '
        '(umask 077 && printf "oracle-readiness\\n" > "$probe") && '
        'test -s "$probe" && rm -f "$probe"'
    )
    if transport == "local":
        command_argv = ["sh", "-c", script, "oracle-readiness", mount_path]
    elif transport == "ssh":
        command_argv = [
            "sh -c "
            + shlex.quote(script)
            + " oracle-readiness "
            + shlex.quote(mount_path)
        ]
    else:
        return False
    result = _run_read_only_command(
        host_entry=host_entry,
        transport=transport,
        command_argv=command_argv,
        timeout_seconds=timeout_seconds,
    )
    return result.get("ok") is True


def _mount_options(output: str) -> list[str]:
    fields = output.split()
    if len(fields) < 3:
        return []
    return [item.strip() for item in fields[-1].split(",") if item.strip()]


def _mount_target(output: str) -> str:
    fields = output.split()
    return fields[-2] if len(fields) >= 3 else ""


def _raid_array_healthy(mdstat: str, *, array_name: str) -> bool:
    lines = mdstat.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith(f"{array_name} :"):
            continue
        header = line.strip()
        detail = " ".join(item.strip() for item in lines[index + 1 : index + 3])
        state_match = re.search(r"\[([U_]+)\]", detail)
        return " active " in f" {header} " and bool(state_match) and "_" not in state_match.group(1)
    return False


def _check_configured_service(
    *,
    host_entry: dict[str, Any],
    service_name: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    transport = str(host_entry.get("transport") or "").strip().lower()
    services = host_entry.get("services") if isinstance(host_entry.get("services"), dict) else {}
    service_entry = services.get(service_name) if isinstance(services, dict) else None
    if not isinstance(service_entry, dict):
        return _service_control_error("service_control_service_not_allowed", f"Service {service_name} is not configured.")
    adapter = str(service_entry.get("adapter") or "").strip().lower()
    target = str(service_entry.get("target") or "").strip()
    command_argv = _service_status_argv(
        adapter=adapter,
        target=target,
        verification_mode=str(service_entry.get("verification_mode") or "").strip().lower(),
    )
    if not command_argv:
        return _service_control_error("service_control_status_not_implemented", "Configured service status check is not implemented.")
    argv, stdin, environment = _transport_command_argv(host_entry=host_entry, transport=transport, command_argv=command_argv)
    if not argv:
        return _service_control_error("service_control_transport_not_configured", "Service-control transport is not fully configured.")
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(3, min(30, int(timeout_seconds or 8))),
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_status_failed", "Configured service status check could not be completed.")
    if result.returncode != 0:
        return _service_control_error("service_control_status_failed", "Configured service status check did not pass.")
    if adapter == "docker" and str(result.stdout or "").strip().lower() != "true":
        return _service_control_error("service_control_status_failed", "Configured service status check did not pass.")
    return {"ok": True}


def _check_json_health(*, url: str, timeout_seconds: int) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        return _service_control_error("service_control_health_url_invalid", "Configured health URL is invalid.")
    try:
        with request.urlopen(url, timeout=max(2, min(15, int(timeout_seconds or 8)))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (error.URLError, OSError, ValueError, json.JSONDecodeError):
        return _service_control_error("service_control_health_check_failed", "Configured health check did not pass.")
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("has_errors") is True:
        return _service_control_error("service_control_health_check_failed", "Configured health check did not pass.")
    return {"ok": True}


def _resolve_service_control_target(
    *,
    settings: dict[str, Any],
    host_id: str,
    service_name: str,
    command: str,
) -> dict[str, Any]:
    hosts = settings.get("hosts")
    if not isinstance(hosts, dict):
        return _service_control_error("service_control_not_configured", "No approved service-control hosts are configured.")
    host_entry = hosts.get(host_id)
    if not isinstance(host_entry, dict):
        return _service_control_error("service_control_host_not_allowed", f"Host {host_id} is not approved for service control.")
    if host_entry.get("enabled") is not True:
        return _service_control_error("service_control_host_disabled", f"Host {host_id} is disabled for service control.")
    transport = str(host_entry.get("transport") or "").strip().lower()
    if transport not in {"local", "ssh"}:
        return _service_control_error("service_control_transport_not_implemented", "Service-control transport is not implemented.")

    services = host_entry.get("services")
    service_entry = services.get(service_name) if isinstance(services, dict) else None
    if not isinstance(service_entry, dict):
        return _service_control_error("service_control_service_not_allowed", f"Service {service_name} is not approved on host {host_id}.")
    commands = {str(item).strip() for item in service_entry.get("commands") or [] if str(item).strip()}
    if command not in commands:
        return _service_control_error("service_control_command_not_allowed", f"Command {command} is not approved for service {service_name}.")
    adapter = str(service_entry.get("adapter") or "").strip().lower()
    target = str(service_entry.get("target") or "").strip()
    return {
        "ok": True,
        "host_entry": host_entry,
        "service_entry": service_entry,
        "transport": transport,
        "adapter": adapter,
        "target": target,
    }


def _should_defer_local_restart(
    *,
    host_entry: dict[str, Any],
    service_entry: dict[str, Any],
    transport: str,
    adapter: str,
) -> bool:
    if transport != "local" or adapter != "systemd":
        return False
    if service_entry.get("restart_mode") == "deferred_self_restart":
        return True
    return service_entry.get("deferred_self_restart") is True and host_entry.get("transport") == "local"


def _bounded_deferred_delay_seconds(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 3
    return min(30, max(1, parsed))


def _schedule_deferred_systemd_restart(*, target: str, delay_seconds: int) -> dict[str, Any]:
    if not target:
        return _service_control_error("service_control_adapter_not_implemented", "Adapter systemd is not implemented.")
    child_code = (
        "import subprocess, sys, time\n"
        "time.sleep(int(sys.argv[1]))\n"
        "subprocess.run(['sudo', '-n', 'systemctl', 'restart', sys.argv[2]], check=False)\n"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", child_code, str(delay_seconds), target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_command_failed", "Service-control command could not be scheduled.")
    return {"ok": True}


def _schedule_deferred_host_restart() -> dict[str, Any]:
    child_code = (
        "import subprocess, time\n"
        "time.sleep(3)\n"
        "subprocess.run(['sudo', '-n', 'reboot'], check=False)\n"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return _service_control_error("service_control_command_failed", "Host restart request could not be scheduled.")
    return {
        "ok": True,
        "status": "scheduled",
        "platform": "linux",
        "deferred": True,
        "detail": "Local host restart was scheduled and will run after the response returns.",
    }


def _host_transport_command(
    *,
    host_entry: dict[str, Any],
    command_argv: list[str],
) -> tuple[list[str], str | None, dict[str, str] | None]:
    address = str(host_entry.get("address") or "").strip()
    user = str(host_entry.get("user") or "").strip()
    password = _host_password(host_entry)
    if not address or not user or not password:
        return [], None, None
    environment = os.environ.copy()
    environment["SSHPASS"] = password
    try:
        ssh_options = strict_ssh_options(connect_timeout_seconds=8)
    except SshHostVerificationError:
        return [], None, None
    return (
        [
            "sshpass",
            "-e",
            "ssh",
            *ssh_options,
            f"{user}@{address}",
            shlex.join(command_argv),
        ],
        f"{password}\n" if command_argv[:1] == ["sudo"] else None,
        environment,
    )


def _service_command_argv(*, adapter: str, target: str, restart_mode: str = "") -> list[str]:
    if not target:
        return []
    if adapter == "systemd" and _SYSTEMD_UNIT_PATTERN.fullmatch(target):
        return ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "systemctl", "restart", target]
    if adapter == "docker" and _DOCKER_TARGET_PATTERN.fullmatch(target):
        return ["docker", "restart", target]
    if adapter == "windows_scheduled_task" and _WINDOWS_TASK_PATTERN.fullmatch(target):
        if restart_mode == "restart_edge_task":
            script = (
                "Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force; "
                "Start-Sleep -Seconds 2; "
                f"Start-ScheduledTask -TaskName '{target}' -ErrorAction Stop"
            )
        elif restart_mode == "restart_edge_kiosk":
            script = (
                f"$task=Get-ScheduledTask -TaskName '{target}' -ErrorAction Stop; "
                f"if ($task.State -eq 'Running') {{ Stop-ScheduledTask -TaskName '{target}' -ErrorAction Stop; "
                "Start-Sleep -Seconds 2 }; "
                "Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force; "
                f"schtasks.exe /Run /TN '{target}' /I | Out-Null; "
                "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
            )
        else:
            script = (
                f"$task=Get-ScheduledTask -TaskName '{target}' -ErrorAction Stop; "
                f"if ($task.State -eq 'Running') {{ Stop-ScheduledTask -TaskName '{target}' -ErrorAction Stop; "
                "Start-Sleep -Seconds 2 }; "
                f"Start-ScheduledTask -TaskName '{target}' -ErrorAction Stop"
            )
        return [_windows_powershell_command(script)]
    return []


def _service_status_argv(*, adapter: str, target: str, verification_mode: str = "") -> list[str]:
    if not target:
        return []
    if adapter == "systemd" and _SYSTEMD_UNIT_PATTERN.fullmatch(target):
        return ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "systemctl", "is-active", "--quiet", target]
    if adapter == "docker" and _DOCKER_TARGET_PATTERN.fullmatch(target):
        return ["docker", "inspect", "-f", "{{.State.Running}}", target]
    if adapter == "windows_scheduled_task" and _WINDOWS_TASK_PATTERN.fullmatch(target):
        if verification_mode == "edge_running":
            script = "if (-not (Get-Process msedge -ErrorAction SilentlyContinue)) { exit 1 }"
        elif verification_mode == "last_result_ok":
            script = (
                f"$result=(Get-ScheduledTaskInfo -TaskName '{target}' -ErrorAction Stop).LastTaskResult; "
                "if ($result -notin @(0,267009)) { exit 1 }"
            )
        else:
            script = (
                f"$state=(Get-ScheduledTask -TaskName '{target}' -ErrorAction Stop).State; "
                "if ($state -ne 'Running') { exit 1 }"
            )
        return [_windows_powershell_command(script)]
    return []


def _windows_powershell_command(script: str) -> str:
    escaped = script.replace('"', '\\"')
    return f'powershell.exe -NoProfile -NonInteractive -Command "{escaped}"'


def _transport_command_argv(
    *,
    host_entry: dict[str, Any],
    transport: str,
    command_argv: list[str],
) -> tuple[list[str], str | None, dict[str, str] | None]:
    if transport == "local":
        if command_argv[:1] == ["sudo"]:
            command_start = command_argv.index("--") + 1 if "--" in command_argv else 4
            return ["sudo", "-n", *command_argv[command_start:]], None, None
        return command_argv, None, None
    if transport != "ssh":
        return [], None, None

    address = str(host_entry.get("address") or "").strip()
    user = str(host_entry.get("user") or "").strip()
    password = _host_password(host_entry)
    if not address or not user or not password:
        return [], None, None
    try:
        ssh_options = strict_ssh_options(connect_timeout_seconds=8)
    except SshHostVerificationError:
        return [], None, None
    environment = os.environ.copy()
    environment["SSHPASS"] = password
    ssh_target = f"{user}@{address}"
    return [
        "sshpass",
        "-e",
        "ssh",
        *ssh_options,
        ssh_target,
        shlex.join(command_argv),
    ], f"{password}\n" if command_argv[:1] == ["sudo"] else None, environment


def _typed_transport_command(
    *,
    adapter: ServiceControlAdapter,
    credential: str | None,
    command_argv: list[str],
) -> tuple[list[str], str | None, dict[str, str] | None]:
    if adapter.transport == "local":
        if command_argv[:1] == ["sudo"]:
            command_start = command_argv.index("--") + 1 if "--" in command_argv else 4
            return ["sudo", "-n", *command_argv[command_start:]], None, None
        return command_argv, None, None
    password = str(credential or "").strip()
    if adapter.address is None or adapter.user is None or not password:
        return [], None, None
    environment = os.environ.copy()
    environment["SSHPASS"] = password
    try:
        ssh_options = strict_ssh_options(connect_timeout_seconds=8)
    except SshHostVerificationError:
        return [], None, None
    return (
        [
            "sshpass", "-e", "ssh", *ssh_options,
            f"{adapter.user}@{adapter.address}", shlex.join(command_argv),
        ],
        f"{password}\n" if command_argv[:1] == ["sudo"] else None,
        environment,
    )


def _run_typed_service_command(
    *,
    adapter: ServiceControlAdapter,
    credential: str | None,
    command_argv: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    argv, stdin, environment = _typed_transport_command(
        adapter=adapter,
        credential=credential,
        command_argv=command_argv,
    )
    if not argv:
        return {"ok": False}
    try:
        result = subprocess.run(
            argv,
            input=stdin,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(5, min(60, int(timeout_seconds or 15))),
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return {"ok": False}
    return {"ok": result.returncode == 0}


def _restore_typed_companions(
    *,
    adapter: ServiceControlAdapter,
    credential: str | None,
    targets: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    for target in reversed(targets):
        result = _run_typed_service_command(
            adapter=adapter,
            credential=credential,
            command_argv=_service_state_argv(
                adapter=str(adapter.service_adapter or ""),
                target=target,
                desired_state="started",
            ),
            timeout_seconds=timeout_seconds,
        )
        if result.get("ok") is not True:
            return {"ok": False}
    return {"ok": True}


def _host_password(host_entry: dict[str, Any]) -> str:
    password_env = str(host_entry.get("password_env") or "").strip()
    if password_env:
        value = str(os.getenv(password_env) or "").strip()
        if value:
            return value
    return str(host_entry.get("password") or "").strip()
