from __future__ import annotations

import os
import subprocess
from typing import Any

from oracle_app.network_runtime.platform_transport import SshHostVerificationError, strict_ssh_options


def get_available_router_actions(settings: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    routers = settings.get("routers")
    if not isinstance(routers, dict):
        return actions
    for router_key, router in routers.items():
        if not isinstance(router, dict):
            continue
        if router.get("enabled") is not True:
            continue
        allowed_actions = router.get("allowed_actions")
        if not isinstance(allowed_actions, dict):
            continue
        for action_key, action in allowed_actions.items():
            if not isinstance(action, dict) or action.get("enabled") is not True:
                continue
            actions.append(
                {
                    "kind": "router_control",
                    "router": str(router_key),
                    "action": str(action_key),
                }
            )
    return actions


def execute_router_action(*, settings: dict[str, Any], router: str, action: str) -> dict[str, Any]:
    routers = settings.get("routers")
    if not isinstance(routers, dict):
        return {
            "ok": False,
            "error": "router_control_not_configured",
            "detail": "No approved router control targets are configured.",
        }
    router_entry = routers.get(router)
    if not isinstance(router_entry, dict):
        return {
            "ok": False,
            "error": "router_control_router_not_allowed",
            "detail": f"Router {router} is not approved for router control.",
        }
    if router_entry.get("enabled") is not True:
        return {
            "ok": False,
            "error": "router_control_router_disabled",
            "detail": f"Router {router} is disabled for router control.",
        }
    allowed_actions = router_entry.get("allowed_actions")
    if not isinstance(allowed_actions, dict) or action not in allowed_actions:
        return {
            "ok": False,
            "error": "router_control_action_not_allowed",
            "detail": f"Action {action} is not approved for router {router}.",
        }
    action_entry = allowed_actions.get(action)
    if not isinstance(action_entry, dict) or action_entry.get("enabled") is not True:
        return {
            "ok": False,
            "error": "router_control_action_disabled",
            "detail": f"Action {action} is disabled for router {router}.",
        }
    adapter = str(router_entry.get("adapter") or "").strip().lower()
    if action != "restart_router" or adapter != "ssh_reboot":
        return {
            "ok": False,
            "error": "router_control_not_implemented",
            "detail": "Approved router control adapter is not implemented.",
        }
    address = str(router_entry.get("address") or "").strip()
    user = str(router_entry.get("user") or "").strip()
    password = str(router_entry.get("password") or "").strip()
    password_env = str(router_entry.get("password_env") or "").strip()
    if password_env:
        password = str(os.getenv(password_env) or "").strip()
    if not address or not user or not password:
        return {
            "ok": False,
            "error": "router_control_credentials_missing",
            "detail": "Router-control SSH settings are incomplete.",
        }
    try:
        command_environment = os.environ.copy()
        command_environment["SSHPASS"] = password
        result = subprocess.run(
            [
                "sshpass",
                "-e",
                "ssh",
                *strict_ssh_options(connect_timeout_seconds=8),
                f"{user}@{address}",
                "reboot",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=command_environment,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": True,
            "status": "restart_sent",
            "adapter": adapter,
            "detail": "Router restart connection closed while the reboot request was being sent.",
        }
    except (OSError, subprocess.SubprocessError, SshHostVerificationError):
        return {
            "ok": False,
            "error": "router_control_command_failed",
            "detail": "Router-control restart request could not be sent.",
        }
    if result.returncode not in {0, 255}:
        return {
            "ok": False,
            "error": "router_control_command_failed",
            "detail": "Router-control restart request returned a failure.",
        }
    return {
        "ok": True,
        "status": "restart_sent",
        "adapter": adapter,
        "detail": "Router restart request was sent.",
    }
