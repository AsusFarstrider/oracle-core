from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.admin_network_routes import (
    _with_power_target_host,
    admin_network_control_actions,
    admin_network_control_confirm,
    admin_network_control_dry_run,
    admin_network_status,
)
from oracle_app.dispatch import build_dispatch_plan, build_dispatch_registry, execute_dispatch
from oracle_app.network import (
    build_ui_network_health_snapshot,
    clear_network_status_cache,
    get_network_status_snapshot,
    get_network_summary,
)
from oracle_app.network_status import build_network_admin_payload
from oracle_app.network_control import (
    build_network_control_actions_diagnostics,
    build_network_control_confirm,
    build_network_control_dry_run,
)
from oracle_app.network_control_execution import execute_network_control_action
from oracle_app.network_control_guard import (
    acquire_network_control,
    clear_network_control_guard,
    get_network_control_availability,
    release_network_control,
)
from oracle_app.network_control_local_restart import (
    complete_pending_local_host_restart,
    stage_pending_local_host_restart,
)
from oracle_app.network_control_local_service_restart import (
    complete_pending_local_service_restart,
    stage_pending_local_service_restart,
)
from oracle_app.network_control_preconditions import (
    evaluate_network_control_preconditions,
    network_control_precondition_matches_target,
    with_inherited_host_preconditions,
)
from oracle_app.network_control_results import (
    build_network_control_audit_payload,
    clear_network_control_results,
    get_network_control_verification_snapshot,
    get_network_control_results_snapshot,
    reconcile_interrupted_network_controls,
    record_network_control_result,
    restore_network_control_results_from_memory,
)
from oracle_app.memory.events import EventQuery, list_events, query_events, record_event
from oracle_app.memory.sources import upsert_source
from oracle_app.provider_bridges.librenms import LibreNmsBridge
from oracle_app.provider_bridges.network_probe import NetworkProbeBridge
from oracle_app.provider_bridges.plex_music import PlexMusicBridge
from oracle_app.provider_bridges.router_control import execute_router_action
from oracle_app.provider_bridges.service_control import (
    check_host_readiness,
    check_service_available,
    check_storage_safety,
    execute_service_action,
    execute_service_command,
    get_host_restart_lifecycle_plan,
    prepare_host_restart,
    recover_host_restart_dependents,
)
from oracle_app.replies import build_reply_text
from oracle_app.routing import build_route_capability_registry, choose_route
from oracle_app.schemas import CommandRequest
from canonical_test_support import neutral_brain_runtime_settings


_NEUTRAL_RUNTIME = neutral_brain_runtime_settings()
_NEUTRAL_NETWORK_PROBE_SETTINGS = {"enabled": False}
_NEUTRAL_LIBRENMS_SETTINGS = {"enabled": False}
_NEUTRAL_NETWORK_INVENTORY_SETTINGS = {
    "hosts": [],
    "services": [],
    "service_groups": [],
    "power_targets": [],
    "dependencies": [],
    "monitors": [],
}
_NEUTRAL_ROUTER_CONTROL_SETTINGS = {"routers": {}}
_NEUTRAL_SERVICE_CONTROL_SETTINGS = {"hosts": {}}
_NEUTRAL_NETWORK_CONTROL_POLICY_SETTINGS = {"actions": []}
_NEUTRAL_MUSIC_SETTINGS = {
    "plex_configured": True,
    "plex_base_url": "http://media.example.test",
    "plex_token": "test-token",
    "satellites": {},
}
_NEUTRAL_ROUTE_REGISTRY = build_route_capability_registry(
    _NEUTRAL_RUNTIME.household,
    facts_enabled=False,
    news_settings=_NEUTRAL_RUNTIME.information.news if _NEUTRAL_RUNTIME.information else None,
    canonical_information=True,
    calendar_settings=_NEUTRAL_RUNTIME.calendar,
    canonical_calendar=True,
)


def _walk_payload(value, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_payload(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_payload(child, f"{path}[{index}]")


def _enabled_plex_restart_policy() -> dict[str, list[dict[str, object]]]:
    return {
        "actions": [
            {
                "id": "plex_restart",
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
                "provider": "ssh",
                "adapter": "service_restart",
                "requires_confirmation": True,
                "required_preconditions": ["plex_no_active_streams"],
                "enabled": True,
                "execution": {
                    "method": "systemd",
                    "unit": "example-media.service",
                    "wait_seconds": 0,
                    "restart_timeout_seconds": 5,
                },
            }
        ]
    }


class NetworkTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._ssh_tempdir = tempfile.TemporaryDirectory()
        self.known_hosts_path = Path(self._ssh_tempdir.name) / "known_hosts"
        self.known_hosts_path.write_text("example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnly\n")
        self.known_hosts_path.chmod(0o600)
        self._ssh_environment_patcher = patch.dict(
            "os.environ", {"ORACLE_SSH_KNOWN_HOSTS_FILE": str(self.known_hosts_path)}
        )
        self._ssh_environment_patcher.start()
        self._canonical_settings_patchers = [
            patch("oracle_app.network.get_network_probe_settings", return_value=_NEUTRAL_NETWORK_PROBE_SETTINGS),
            patch("oracle_app.network.get_librenms_settings", return_value=_NEUTRAL_LIBRENMS_SETTINGS),
            patch("oracle_app.network.get_network_inventory_settings", return_value=_NEUTRAL_NETWORK_INVENTORY_SETTINGS),
            patch("oracle_app.network.get_network_router_control_settings", return_value=_NEUTRAL_ROUTER_CONTROL_SETTINGS),
            patch("oracle_app.network.get_network_service_control_settings", return_value=_NEUTRAL_SERVICE_CONTROL_SETTINGS),
            patch("oracle_app.network.get_music_settings", return_value=_NEUTRAL_MUSIC_SETTINGS),
            patch("oracle_app.admin_network_routes.get_network_probe_settings", return_value=_NEUTRAL_NETWORK_PROBE_SETTINGS),
            patch("oracle_app.admin_network_routes.get_network_inventory_settings", return_value=_NEUTRAL_NETWORK_INVENTORY_SETTINGS),
            patch("oracle_app.admin_network_routes.get_network_router_control_settings", return_value=_NEUTRAL_ROUTER_CONTROL_SETTINGS),
            patch("oracle_app.admin_network_routes.get_network_service_control_settings", return_value=_NEUTRAL_SERVICE_CONTROL_SETTINGS),
            patch("oracle_app.admin_network_routes.get_network_control_policy_settings", return_value=_NEUTRAL_NETWORK_CONTROL_POLICY_SETTINGS),
            patch("oracle_app.admin_network_routes.get_music_settings", return_value=_NEUTRAL_MUSIC_SETTINGS),
        ]
        for settings_patcher in self._canonical_settings_patchers:
            settings_patcher.start()
        clear_network_status_cache()
        clear_network_control_results()
        clear_network_control_guard()

    def tearDown(self) -> None:
        for settings_patcher in reversed(self._canonical_settings_patchers):
            settings_patcher.stop()
        self._ssh_environment_patcher.stop()
        self._ssh_tempdir.cleanup()

    def _strict_ssh_options(self) -> list[str]:
        return [
            "-F", "/dev/null",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts_path}",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=8",
        ]

    def _ssh_remote(self, address: str, command: list[str]) -> list[str]:
        return [
            "sshpass", "-e", "ssh", *self._strict_ssh_options(),
            f"operator@{address}", shlex.join(command),
        ]


__all__ = [name for name in globals() if not name.startswith("__")]
