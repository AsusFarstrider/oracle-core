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


class NetworkDomainTests(unittest.TestCase):
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

    @patch("oracle_app.network.get_network_probe_settings", return_value={"enabled": False})
    @patch("oracle_app.network.get_librenms_settings", return_value={"enabled": False})
    @patch("oracle_app.network.get_network_router_control_settings", return_value={})
    @patch("oracle_app.network.get_network_service_control_settings", return_value={})
    def test_network_summary_unknown_when_providers_disabled(
        self,
        _mock_service_control,
        _mock_router_control,
        _mock_librenms,
        _mock_probe,
    ) -> None:
        summary = get_network_summary()

        self.assertEqual(summary["status"], "unknown")
        self.assertEqual(summary["internet"]["status"], "unknown")
        self.assertEqual(summary["monitoring"]["status"], "unknown")
        self.assertEqual(summary["actions_available"], [])

    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "unknown",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS not configured.",
            "problems": [],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_network_summary_probe_success_maps_to_healthy(self, _mock_probe, _mock_librenms) -> None:
        summary = get_network_summary()

        self.assertEqual(summary["status"], "healthy")
        self.assertEqual(summary["internet"]["status"], "healthy")

    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "degraded",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Some direct network checks failed.",
            "problems": ["HTTP reachability failed with status 503."],
        },
    )
    def test_network_summary_probe_failure_can_map_to_degraded(self, _mock_probe, _mock_librenms) -> None:
        summary = get_network_summary()

        self.assertEqual(summary["status"], "degraded")
        self.assertIn("HTTP reachability failed with status 503.", summary["problems"])

    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "degraded",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports 1 active alert(s).",
            "problems": ["Service up/down on 192.0.2.205 is critical."],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_network_summary_degrades_when_librenms_has_active_alert(self, _mock_probe, _mock_librenms) -> None:
        summary = get_network_summary()

        self.assertEqual(summary["status"], "degraded")
        self.assertEqual(summary["monitoring"]["status"], "degraded")
        self.assertIn("Service up/down on 192.0.2.205 is critical.", summary["problems"])

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [
                {
                    "id": "oracle_host",
                    "display_name": "Oracle Server",
                    "role": "oracle_brain",
                    "addresses": ["oracle-brain.local"],
                }
            ],
            "services": [
                {
                    "id": "oracle_brain",
                    "display_name": "Oracle Brain",
                    "host_id": "oracle_host",
                }
            ],
            "service_groups": [
                {
                    "id": "oracle_runtime",
                    "display_name": "Oracle",
                    "host_id": "oracle_host",
                    "service_ids": ["oracle_brain"],
                    "collapsed": True,
                }
            ],
            "monitors": [
                {
                    "id": "oracle_brain_http",
                    "target_type": "service",
                    "target_id": "oracle_brain",
                    "source": "direct_probe",
                    "kind": "http",
                    "match": {"url": "http://oracle-brain.local:8011/health"},
                },
                {
                    "id": "oracle_host_librenms",
                    "target_type": "host",
                    "target_id": "oracle_host",
                    "source": "librenms",
                    "kind": "device",
                    "match": {"hostname": "oracle-brain.local"},
                },
            ],
            "dependencies": [
                {
                    "id": "oracle_brain_depends_on_oracle_host",
                    "from_type": "service",
                    "from_id": "oracle_brain",
                    "to_type": "host",
                    "to_id": "oracle_host",
                    "relationship": "depends_on",
                }
            ],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "degraded",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports 1 active alert(s).",
            "problems": ["Service up/down on oracle-brain.local is critical."],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
            "checks": [
                {
                    "kind": "dns",
                    "status": "healthy",
                    "detail": "DNS resolution succeeded for cloudflare.com.",
                },
                {
                    "kind": "http",
                    "status": "healthy",
                    "detail": "HTTP reachability succeeded with status 204.",
                },
            ],
        },
    )
    def test_network_status_snapshot_normalizes_provider_evidence(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        snapshot = get_network_status_snapshot()

        self.assertEqual(snapshot["status"], "degraded")
        self.assertEqual(snapshot["severity"], "warning")
        self.assertEqual(snapshot["freshness"], "fresh")
        self.assertEqual(snapshot["dependencies"][0]["id"], "internet")
        self.assertEqual(snapshot["dependencies"][0]["status"], "healthy")

        evidence_ids = {item["id"] for item in snapshot["evidence"]}
        self.assertIn("probe.internet", evidence_ids)
        self.assertIn("probe.http", evidence_ids)
        self.assertIn("librenms.monitoring", evidence_ids)
        self.assertIn("librenms.problem.0", evidence_ids)

        service = next(item for item in snapshot["services"] if item["id"] == "oracle_brain")
        self.assertEqual(service["status"], "healthy")
        self.assertIn("probe.http", service["evidence_ids"])

        group = next(item for item in snapshot["service_groups"] if item["id"] == "oracle_runtime")
        self.assertEqual(group["status"], "healthy")
        self.assertEqual(group["service_ids"], ["oracle_brain"])

        host = next(item for item in snapshot["hosts"] if item["id"] == "oracle_host")
        self.assertEqual(host["status"], "degraded")
        self.assertIn("librenms.monitor.oracle_host_librenms", host["evidence_ids"])

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [
                {"id": "test_satellite_bravo", "display_name": "Pi Satellite .204", "kind": "satellite"},
                {"id": "test_satellite_delta", "display_name": "Pi Satellite .206", "kind": "satellite"},
            ],
            "services": [],
            "service_groups": [],
            "monitors": [
                {
                    "id": "test_satellite_bravo_librenms",
                    "display_name": "Pi Satellite .204 Health",
                    "target_type": "host",
                    "target_id": "test_satellite_bravo",
                    "source": "librenms",
                    "kind": "device",
                    "match": {"ip": "192.0.2.204"},
                },
                {
                    "id": "test_satellite_delta_librenms",
                    "display_name": "Pi Satellite .206 Health",
                    "target_type": "host",
                    "target_id": "test_satellite_delta",
                    "source": "librenms",
                    "kind": "device",
                    "match": {"ip": "192.0.2.206"},
                },
            ],
            "dependencies": [],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "degraded",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports 1 active alert(s).",
            "problems": ["Device down on 192.0.2.204 is critical."],
            "alerts": [
                {
                    "description": "Device down",
                    "hostname": "192.0.2.204",
                    "severity": "critical",
                }
            ],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_librenms_monitor_evidence_drives_satellite_health(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        snapshot = get_network_status_snapshot()

        hosts = {item["id"]: item for item in snapshot["hosts"]}
        self.assertEqual(hosts["test_satellite_bravo"]["status"], "degraded")
        self.assertEqual(hosts["test_satellite_delta"]["status"], "healthy")
        self.assertIn("librenms.monitor.test_satellite_bravo_librenms", hosts["test_satellite_bravo"]["evidence_ids"])
        self.assertIn("librenms.monitor.test_satellite_delta_librenms", hosts["test_satellite_delta"]["evidence_ids"])

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [
                {"id": "desktop_satellite_109", "display_name": "Bedroom", "kind": "satellite"},
            ],
            "services": [],
            "service_groups": [],
            "monitors": [
                {
                    "id": "desktop_satellite_109_librenms",
                    "display_name": "Bedroom Health",
                    "target_type": "host",
                    "target_id": "desktop_satellite_109",
                    "source": "librenms",
                    "kind": "device",
                    "match": {"ip": "192.0.2.209"},
                },
                {
                    "id": "desktop_satellite_109_control",
                    "display_name": "Reading Room Satellite Control",
                    "target_type": "host",
                    "target_id": "desktop_satellite_109",
                    "source": "oracle_satellite_control",
                    "kind": "health",
                    "match": {"source_id": "desktop-satellite-109"},
                },
            ],
            "dependencies": [],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.network.get_music_settings",
        return_value={
            "satellites": {
                "desktop-satellite-109": {
                    "base_url": "http://192.0.2.209:8021",
                    "api_key": "test-key",
                    "timeout_seconds": 5,
                }
            }
        },
    )
    @patch("oracle_app.network.request.urlopen")
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-06-02T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
            "devices": [],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-06-02T20:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_satellite_control_monitor_evidence_drives_satellite_health_when_librenms_has_no_device(
        self,
        _mock_probe,
        _mock_librenms,
        mock_urlopen,
        _mock_music_settings,
        _mock_inventory,
    ) -> None:
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        mock_urlopen.return_value = FakeResponse()

        snapshot = get_network_status_snapshot()

        host = next(item for item in snapshot["hosts"] if item["id"] == "desktop_satellite_109")
        self.assertEqual(host["status"], "healthy")
        self.assertIn("oracle_satellite_control.monitor.desktop_satellite_109_control", host["evidence_ids"])
        monitor = next(item for item in snapshot["monitors"] if item["id"] == "desktop_satellite_109_control")
        self.assertEqual(monitor["status"], "healthy")
        self.assertEqual(monitor["evidence_ids"], ["oracle_satellite_control.monitor.desktop_satellite_109_control"])

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [
                {"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"},
            ],
            "services": [
                {"id": "plex", "display_name": "Plex", "host_id": "oracle_host"},
                {"id": "nextcloud", "display_name": "Nextcloud", "host_id": "oracle_host"},
            ],
            "service_groups": [],
            "monitors": [
                {
                    "id": "plex_librenms_service",
                    "display_name": "Plex LibreNMS Service",
                    "target_type": "service",
                    "target_id": "plex",
                    "source": "librenms",
                    "kind": "service",
                    "match": {"hostname": "oracle-brain.local", "service_name": "plex"},
                },
            ],
            "dependencies": [],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "degraded",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports 2 active alert(s).",
            "problems": ["Service up/down on oracle-brain.local is critical."],
            "alerts": [
                {
                    "description": "Service up/down",
                    "hostname": "oracle-brain.local",
                    "service_name": "plex",
                    "service_id": "77",
                    "severity": "critical",
                },
                {
                    "description": "Service up/down",
                    "hostname": "oracle-brain.local",
                    "service_name": "provider-only-service",
                    "service_id": "88",
                    "severity": "critical",
                },
            ],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_librenms_service_monitor_updates_only_curated_service(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        snapshot = get_network_status_snapshot()

        services = {item["id"]: item for item in snapshot["services"]}
        self.assertEqual(set(services), {"plex", "nextcloud"})
        self.assertEqual(services["plex"]["status"], "degraded")
        self.assertIn("librenms.monitor.plex_librenms_service", services["plex"]["evidence_ids"])
        self.assertEqual(services["nextcloud"]["status"], "unknown")
        self.assertEqual(services["nextcloud"]["evidence_ids"], [])

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"}],
            "services": [
                {"id": "plex", "display_name": "Plex", "host_id": "oracle_host"},
                {"id": "nextcloud", "display_name": "Nextcloud", "host_id": "oracle_host"},
            ],
            "service_groups": [],
            "monitors": [
                {
                    "id": "plex_librenms_service",
                    "display_name": "Plex LibreNMS Service",
                    "target_type": "service",
                    "target_id": "plex",
                    "source": "librenms",
                    "kind": "service",
                    "match": {"service_name": "plex"},
                },
                {
                    "id": "nextcloud_librenms_service",
                    "display_name": "Nextcloud LibreNMS Service",
                    "target_type": "service",
                    "target_id": "nextcloud",
                    "source": "librenms",
                    "kind": "service",
                    "match": {"service_name": "nextcloud"},
                },
            ],
            "dependencies": [],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-05-24T08:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
            "alerts": [],
            "services": [
                {
                    "service_id": "62",
                    "device_id": "1",
                    "service_ip": "192.0.2.205",
                    "service_name": "plex",
                    "service_desc": "Plex",
                    "service_status": "0",
                    "service_message": "TCP OK",
                },
                {
                    "service_id": "55",
                    "device_id": "1",
                    "service_ip": "192.0.2.205",
                    "service_name": "nextcloud",
                    "service_desc": "Nextcloud",
                    "service_status": "2",
                    "service_message": "HTTP CRITICAL",
                },
                {
                    "service_id": "99",
                    "device_id": "1",
                    "service_ip": "192.0.2.205",
                    "service_name": "provider-only",
                    "service_desc": "Provider Only",
                    "service_status": "0",
                    "service_message": "OK",
                },
            ],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-05-24T08:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_librenms_service_observations_drive_declared_service_monitors(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        snapshot = get_network_status_snapshot()
        payload = build_network_admin_payload(snapshot)

        services = {item["id"]: item for item in snapshot["services"]}
        self.assertEqual(services["plex"]["status"], "healthy")
        self.assertEqual(services["nextcloud"]["status"], "down")
        self.assertIn("librenms.monitor.plex_librenms_service", services["plex"]["evidence_ids"])
        self.assertIn("librenms.monitor.nextcloud_librenms_service", services["nextcloud"]["evidence_ids"])

        diagnostics = payload["provider_diagnostics"]["librenms_services"]
        self.assertEqual(diagnostics["total"], 3)
        self.assertEqual(diagnostics["matched"], 2)
        self.assertEqual(diagnostics["unmatched"], 1)

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "service_groups": [],
            "monitors": [
                {
                    "id": "plex_librenms_service",
                    "target_type": "service",
                    "target_id": "plex",
                    "source": "librenms",
                    "kind": "service",
                    "match": {"service_name": "plex"},
                }
            ],
            "dependencies": [],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-05-24T08:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
            "alerts": [],
            "services": [
                {
                    "service_id": "99",
                    "service_name": "provider-only",
                    "service_status": "0",
                    "service_message": "OK",
                }
            ],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-05-24T08:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_declared_service_monitor_without_matching_librenms_service_has_no_evidence(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        payload = build_network_admin_payload(get_network_status_snapshot())

        service = payload["hosts"][0]["services"][0]
        self.assertEqual(service["id"], "plex")
        self.assertEqual(service["monitor_count"], 1)
        self.assertEqual(service["evidence_count"], 0)
        self.assertEqual(service["monitoring_state"], "configured_no_evidence")

    def test_librenms_alert_normalization_keeps_service_match_fields(self) -> None:
        alert = LibreNmsBridge()._normalize_alert(  # noqa: SLF001 - contract coverage for provider payload shaping.
            {
                "alert": "Service up/down",
                "hostname": "oracle-brain.local",
                "service": "plex",
                "service_id": 77,
                "device_id": 205,
                "severity": "critical",
            }
        )

        self.assertEqual(alert["service_name"], "plex")
        self.assertEqual(alert["service_id"], "77")
        self.assertEqual(alert["device_id"], "205")

    def test_librenms_service_extraction_flattens_nested_service_payload(self) -> None:
        services = LibreNmsBridge()._extract_services(  # noqa: SLF001 - contract coverage for provider payload shaping.
            {
                "services": [
                    [
                        {
                            "service_id": 62,
                            "device_id": 1,
                            "service_ip": "192.0.2.205",
                            "service_name": "plex",
                            "service_desc": "Plex",
                            "service_status": 0,
                            "service_message": "TCP OK",
                        }
                    ]
                ]
            }
        )

        self.assertEqual(len(services), 1)
        normalized = LibreNmsBridge()._normalize_service(services[0])  # noqa: SLF001
        self.assertEqual(normalized["service_name"], "plex")
        self.assertEqual(normalized["service_status"], "0")

    def test_librenms_device_extraction_normalizes_device_payload(self) -> None:
        devices = LibreNmsBridge()._extract_devices(  # noqa: SLF001 - contract coverage for provider payload shaping.
            {
                "devices": [
                    {
                        "device_id": 3,
                        "hostname": "192.0.2.153",
                        "sysName": "mesh_node-xe75",
                        "display": "Primary Mesh Node",
                        "ip": "192.0.2.153",
                        "status": 1,
                    }
                ]
            }
        )

        self.assertEqual(len(devices), 1)
        normalized = LibreNmsBridge()._normalize_device(devices[0])  # noqa: SLF001
        self.assertEqual(normalized["device_id"], "3")
        self.assertEqual(normalized["display"], "Primary Mesh Node")
        self.assertEqual(normalized["status"], "1")

    def test_librenms_interface_extraction_normalizes_port_payload(self) -> None:
        interfaces = LibreNmsBridge()._extract_interfaces(  # noqa: SLF001 - contract coverage for provider payload shaping.
            {
                "ports": [
                    {
                        "port_id": 10,
                        "device_id": 2,
                        "ifIndex": 4,
                        "ifName": "wan",
                        "ifDescr": "eth1",
                        "ifAlias": "Internet uplink",
                        "ifOperStatus": "up",
                        "ifAdminStatus": "up",
                    }
                ]
            }
        )

        self.assertEqual(len(interfaces), 1)
        normalized = LibreNmsBridge()._normalize_interface(interfaces[0])  # noqa: SLF001
        self.assertEqual(normalized["port_id"], "10")
        self.assertEqual(normalized["device_id"], "2")
        self.assertEqual(normalized["if_index"], "4")
        self.assertEqual(normalized["if_name"], "wan")
        self.assertEqual(normalized["if_descr"], "eth1")
        self.assertEqual(normalized["if_alias"], "Internet uplink")
        self.assertEqual(normalized["if_oper_status"], "up")
        self.assertEqual(normalized["if_admin_status"], "up")

    @patch.object(LibreNmsBridge, "_fetch_interface_detail")
    def test_librenms_interface_detail_enrichment_uses_port_id(self, mock_fetch_detail) -> None:
        mock_fetch_detail.return_value = {
            "payload": {
                "port": [
                    {
                        "port_id": 399,
                        "device_id": 2,
                        "ifName": "eth1",
                        "ifOperStatus": "up",
                        "ifAdminStatus": "up",
                    }
                ]
            },
            "http_status": 200,
            "error": None,
        }

        interfaces = LibreNmsBridge()._with_interface_details(  # noqa: SLF001 - contract coverage for provider payload shaping.
            [{"port_id": 399, "ifName": "eth1"}],
            base_url="http://librenms.local",
            api_token="secret-token",
            timeout_seconds=5,
            max_detail_fetches=1,
        )

        normalized = LibreNmsBridge()._normalize_interface(interfaces[0])  # noqa: SLF001
        self.assertEqual(normalized["port_id"], "399")
        self.assertEqual(normalized["device_id"], "2")
        self.assertEqual(normalized["if_name"], "eth1")
        self.assertEqual(normalized["if_oper_status"], "up")
        self.assertEqual(normalized["if_admin_status"], "up")
        self.assertNotIn("secret-token", str(interfaces))

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [
                {
                    "id": "router_main",
                    "display_name": "Main Router",
                    "kind": "network_node",
                    "role": "router",
                },
                {
                    "id": "modem_main",
                    "display_name": "Main Modem",
                    "kind": "network_node",
                    "role": "modem",
                },
            ],
            "services": [],
            "service_groups": [],
            "monitors": [
                {
                    "id": "router_wan_librenms_interface",
                    "display_name": "Router WAN Interface",
                    "target_type": "host",
                    "target_id": "modem_main",
                    "source": "librenms",
                    "kind": "interface",
                    "match": {"device_id": "2", "if_name": "wan"},
                }
            ],
            "dependencies": [],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-06-15T08:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
            "alerts": [],
            "interfaces": [
                {
                    "port_id": "10",
                    "device_id": "2",
                    "if_index": "4",
                    "if_name": "wan",
                    "if_descr": "eth1",
                    "if_alias": "Internet uplink",
                    "if_oper_status": "down",
                    "if_admin_status": "up",
                }
            ],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-06-15T08:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_librenms_interface_observation_drives_curated_modem_health(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        snapshot = get_network_status_snapshot()
        payload = build_network_admin_payload(snapshot)

        hosts = {item["id"]: item for item in snapshot["hosts"]}
        self.assertEqual(hosts["modem_main"]["status"], "down")
        self.assertEqual(hosts["router_main"]["status"], "unknown")
        self.assertIn("librenms.monitor.router_wan_librenms_interface", hosts["modem_main"]["evidence_ids"])

        evidence = next(item for item in snapshot["evidence"] if item["id"] == "librenms.monitor.router_wan_librenms_interface")
        self.assertEqual(evidence["status"], "down")
        self.assertEqual(evidence["provider_reference"]["if_name"], "wan")
        self.assertEqual(evidence["provider_reference"]["device_id"], "2")

        diagnostics = payload["provider_diagnostics"]["librenms_interfaces"]
        self.assertEqual(diagnostics["total"], 1)
        self.assertEqual(diagnostics["matched"], 1)
        self.assertEqual(diagnostics["items"][0]["matched_monitor_ids"], ["router_wan_librenms_interface"])

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "modem_main", "display_name": "Main Modem", "kind": "network_node"}],
            "services": [],
            "service_groups": [],
            "monitors": [
                {
                    "id": "router_wan_librenms_interface",
                    "target_type": "host",
                    "target_id": "modem_main",
                    "source": "librenms",
                    "kind": "interface",
                    "match": {"device_id": "2", "if_name": "wan"},
                }
            ],
            "dependencies": [],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-06-15T08:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
            "alerts": [],
            "interfaces": [],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-06-15T08:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_declared_interface_monitor_without_librenms_ports_has_no_evidence(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        payload = build_network_admin_payload(get_network_status_snapshot())

        host = payload["hosts"][0]
        self.assertEqual(host["id"], "modem_main")
        self.assertEqual(host["monitor_count"], 1)
        self.assertEqual(host["evidence_count"], 0)
        self.assertEqual(host["monitoring_state"], "configured_no_evidence")

    def test_plex_sessions_status_counts_active_streams(self) -> None:
        status = PlexMusicBridge().extract_active_sessions_status(  # noqa: SLF001 - provider payload shaping coverage.
            """
            <MediaContainer size="1">
              <Video title="Movie Night">
                <Player title="Living Room TV" />
              </Video>
            </MediaContainer>
            """
        )

        self.assertTrue(status["available"])
        self.assertEqual(status["active_stream_count"], 1)
        self.assertEqual(status["sessions"][0]["title"], "Movie Night")
        self.assertEqual(status["sessions"][0]["player"], "Living Room TV")

    @patch(
        "oracle_app.network.get_network_inventory_settings",
        return_value={
            "hosts": [
                {
                    "id": "mesh_node_primary",
                    "display_name": "Primary Mesh Node",
                    "kind": "network_node",
                    "role": "mesh_ap",
                }
            ],
            "services": [],
            "monitors": [
                {
                    "id": "mesh_node_primary_librenms",
                    "target_type": "host",
                    "target_id": "mesh_node_primary",
                    "source": "librenms",
                    "kind": "device",
                    "match": {"ip": "192.0.2.153", "device_id": "3"},
                }
            ],
        },
    )
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-05-24T16:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
            "devices": [
                {
                    "device_id": "3",
                    "hostname": "192.0.2.153",
                    "display": "Primary Mesh Node",
                    "ip": "192.0.2.153",
                    "status": "0",
                }
            ],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-05-24T16:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    def test_librenms_device_observations_drive_host_health(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        snapshot = get_network_status_snapshot()

        host = next(item for item in snapshot["hosts"] if item["id"] == "mesh_node_primary")
        self.assertEqual(host["status"], "down")
        self.assertIn("librenms.monitor.mesh_node_primary_librenms", host["evidence_ids"])

    @patch(
        "oracle_app.admin_network_routes.get_network_status_snapshot",
        return_value={
            "status": "healthy",
            "severity": "none",
            "freshness": "fresh",
            "generated_at": "2026-05-23T12:00:00-04:00",
            "summary": "No problems are known.",
            "hosts": [
                {
                    "id": "oracle_host",
                    "display_name": "Oracle Server",
                    "status": "healthy",
                    "severity": "none",
                    "freshness": "fresh",
                    "summary": "No problems are known.",
                    "evidence_ids": [],
                }
            ],
            "services": [
                {
                    "id": "oracle_brain",
                    "display_name": "Oracle Brain",
                    "host_id": "oracle_host",
                    "status": "healthy",
                    "severity": "none",
                    "freshness": "fresh",
                    "summary": "No problems are known.",
                    "evidence_ids": [],
                },
                {
                    "id": "plex",
                    "display_name": "Plex",
                    "host_id": "oracle_host",
                    "status": "unknown",
                    "severity": "unknown",
                    "freshness": "unknown",
                    "summary": "Status is unknown.",
                    "evidence_ids": [],
                },
            ],
            "service_groups": [
                {
                    "id": "oracle_runtime",
                    "display_name": "Oracle",
                    "host_id": "oracle_host",
                    "status": "healthy",
                    "severity": "none",
                    "freshness": "fresh",
                    "summary": "No problems are known.",
                    "service_ids": ["oracle_brain"],
                    "evidence_ids": [],
                    "collapsed": True,
                }
            ],
            "dependencies": [],
            "monitors": [],
            "evidence": [],
        },
    )
    def test_admin_network_status_groups_services_under_hosts(self, _mock_snapshot) -> None:
        payload = admin_network_status()

        self.assertTrue(payload["ok"])
        network = payload["network"]
        host = network["hosts"][0]
        self.assertEqual(host["id"], "oracle_host")
        self.assertEqual(host["service_groups"][0]["id"], "oracle_runtime")
        self.assertEqual(host["services"][0]["id"], "plex")

    def test_network_admin_payload_reports_inventory_coverage(self) -> None:
        payload = build_network_admin_payload(
            {
                "status": "healthy",
                "severity": "none",
                "freshness": "fresh",
                "generated_at": "2026-05-24T08:00:00-04:00",
                "summary": "No problems are known.",
                "hosts": [
                    {
                        "id": "oracle_host",
                        "display_name": "Oracle Server",
                        "status": "healthy",
                        "severity": "none",
                        "freshness": "fresh",
                        "summary": "No problems are known.",
                        "evidence_ids": ["librenms.monitor.oracle_host_librenms"],
                    },
                    {
                        "id": "nas",
                        "display_name": "NAS",
                        "status": "unknown",
                        "severity": "unknown",
                        "freshness": "unknown",
                        "summary": "Status is unknown.",
                        "evidence_ids": [],
                    },
                ],
                "services": [
                    {
                        "id": "plex",
                        "display_name": "Plex",
                        "host_id": "oracle_host",
                        "status": "healthy",
                        "severity": "none",
                        "freshness": "fresh",
                        "summary": "No problems are known.",
                        "evidence_ids": ["librenms.monitor.plex_librenms_service"],
                    },
                    {
                        "id": "nextcloud",
                        "display_name": "Nextcloud",
                        "host_id": "oracle_host",
                        "status": "unknown",
                        "severity": "unknown",
                        "freshness": "unknown",
                        "summary": "Status is unknown.",
                        "evidence_ids": [],
                    },
                ],
                "service_groups": [],
                "dependencies": [],
                "monitors": [
                    {
                        "id": "oracle_host_librenms",
                        "display_name": "Oracle Host LibreNMS",
                        "provider": "librenms",
                        "status": "healthy",
                        "severity": "none",
                        "freshness": "fresh",
                        "summary": "No problems are known.",
                        "target_type": "host",
                        "target_id": "oracle_host",
                        "evidence_ids": ["librenms.monitor.oracle_host_librenms"],
                    },
                    {
                        "id": "plex_librenms_service",
                        "display_name": "Plex LibreNMS Service",
                        "provider": "librenms",
                        "status": "healthy",
                        "severity": "none",
                        "freshness": "fresh",
                        "summary": "No problems are known.",
                        "target_type": "service",
                        "target_id": "plex",
                        "evidence_ids": ["librenms.monitor.plex_librenms_service"],
                    },
                    {
                        "id": "nas_librenms",
                        "display_name": "NAS LibreNMS",
                        "provider": "librenms",
                        "status": "unknown",
                        "severity": "unknown",
                        "freshness": "unknown",
                        "summary": "Status is unknown.",
                        "target_type": "host",
                        "target_id": "nas",
                        "evidence_ids": [],
                    },
                ],
                "evidence": [
                    {"id": "librenms.monitor.oracle_host_librenms"},
                    {"id": "librenms.monitor.plex_librenms_service"},
                ],
            }
        )

        self.assertEqual(payload["coverage"]["hosts"]["total"], 2)
        self.assertEqual(payload["coverage"]["hosts"]["monitored"], 1)
        self.assertEqual(payload["coverage"]["hosts"]["configured_no_evidence"], 1)
        self.assertEqual(payload["coverage"]["services"]["monitored"], 1)
        self.assertEqual(payload["coverage"]["services"]["unmonitored"], 1)
        self.assertEqual(payload["coverage"]["monitors"]["without_evidence"], 1)

        host = next(item for item in payload["hosts"] if item["id"] == "nas")
        self.assertEqual(host["monitor_count"], 1)
        self.assertEqual(host["monitoring_state"], "configured_no_evidence")
        service = payload["hosts"][0]["services"][1]
        self.assertEqual(service["id"], "nextcloud")
        self.assertEqual(service["monitoring_state"], "unmonitored")

    def test_network_admin_payload_has_no_stage3_executable_fields(self) -> None:
        payload = build_network_admin_payload(
            {
                "status": "healthy",
                "severity": "none",
                "freshness": "fresh",
                "generated_at": "2026-05-24T08:00:00-04:00",
                "summary": "No problems are known.",
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "evidence_ids": []}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host", "evidence_ids": []}],
                "service_groups": [],
                "dependencies": [],
                "monitors": [],
                "evidence": [
                    {
                        "id": "librenms.monitor.plex",
                        "provider": "librenms",
                        "detail": "TCP OK",
                        "provider_reference": {"service_id": "62", "service_name": "plex"},
                    }
                ],
                "provider_observations": {
                    "librenms_services": [
                        {
                            "service_id": "62",
                            "device_id": "1",
                            "service_name": "plex",
                            "status": "healthy",
                            "matched_monitor_ids": [],
                        }
                    ]
                },
            }
        )

        forbidden_key_fragments = (
            "action",
            "command",
            "execute",
            "restart",
            "reboot",
            "self_heal",
            "remediate",
            "url",
            "token",
            "credential",
            "secret",
            "password",
        )
        for path, value in _walk_payload(payload):
            if isinstance(value, dict):
                continue
            key = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()
            self.assertFalse(
                any(fragment in key for fragment in forbidden_key_fragments),
                f"{path} must not expose executable or secret-bearing fields",
            )

    def test_network_admin_payload_attaches_safe_control_action_metadata(self) -> None:
        payload = build_network_admin_payload(
            {
                "status": "healthy",
                "severity": "none",
                "freshness": "fresh",
                "generated_at": "2026-05-24T08:00:00-04:00",
                "summary": "No problems are known.",
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "evidence_ids": []}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host", "evidence_ids": []}],
                "service_groups": [],
                "dependencies": [],
                "monitors": [],
                "evidence": [],
            },
            control_policy=_enabled_plex_restart_policy(),
        )

        service = payload["hosts"][0]["services"][0]
        self.assertEqual(service["control_actions"][0]["action_id"], "restart_service")
        self.assertTrue(service["control_actions"][0]["enabled"])
        self.assertTrue(service["control_actions"][0]["requires_confirmation"])
        self.assertNotIn("execution", service["control_actions"][0])
        self.assertNotIn("unit", service["control_actions"][0])

    def test_provider_diagnostics_do_not_create_oracle_services(self) -> None:
        payload = build_network_admin_payload(
            {
                "status": "healthy",
                "severity": "none",
                "freshness": "fresh",
                "generated_at": "2026-05-24T08:00:00-04:00",
                "summary": "No problems are known.",
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "evidence_ids": []}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host", "evidence_ids": []}],
                "service_groups": [],
                "dependencies": [],
                "monitors": [],
                "evidence": [],
                "provider_observations": {
                    "librenms_services": [
                        {
                            "service_id": "99",
                            "device_id": "1",
                            "service_name": "provider-only",
                            "service_desc": "Provider Only",
                            "status": "healthy",
                            "matched_monitor_ids": [],
                        }
                    ]
                },
            }
        )

        host_services = [
            service["id"]
            for host in payload["hosts"]
            for service in host.get("services") or []
        ]
        diagnostics = payload["provider_diagnostics"]["librenms_services"]
        self.assertEqual(host_services, ["plex"])
        self.assertEqual(diagnostics["unmatched"], 1)
        self.assertEqual(diagnostics["items"][0]["service_name"], "provider-only")
        self.assertNotIn("provider-only", host_services)

    def test_network_control_dry_run_denies_unknown_target(self) -> None:
        payload = build_network_control_dry_run(
            inventory={"hosts": [], "services": [], "power_targets": []},
            request_payload={
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
                "actor": "admin",
                "source": "system_mode",
                "reason": "test dry-run",
            },
        )

        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["mode"], "dry_run")
        self.assertEqual(payload["policy_status"], "denied")
        self.assertEqual(payload["result_status"], "not_executed")
        self.assertEqual(payload["error_class"], "network_control_target_not_found")

    def test_network_control_guard_blocks_concurrent_actions_and_applies_target_cooldown(self) -> None:
        first = acquire_network_control(
            target_type="service",
            target_id="plex",
            action_id="restart_service",
        )
        second = acquire_network_control(
            target_type="host",
            target_id="dns_host",
            action_id="restart_host",
        )

        self.assertTrue(first["acquired"])
        self.assertFalse(second["acquired"])
        self.assertEqual(second["state"]["status"], "blocked_by_active")
        self.assertEqual(second["state"]["active_target_id"], "plex")

        cooldown = release_network_control(token=first["token"], cooldown_seconds=60)

        self.assertEqual(cooldown["status"], "cooldown")
        self.assertGreater(cooldown["cooldown_remaining_seconds"], 0)
        same_target = get_network_control_availability(
            target_type="service",
            target_id="plex",
            action_id="restart_service",
        )
        other_target = get_network_control_availability(
            target_type="host",
            target_id="dns_host",
            action_id="restart_host",
        )
        self.assertEqual(same_target["status"], "cooldown")
        self.assertEqual(other_target["status"], "ready")

    @patch(
        "oracle_app.network_control_preconditions.check_service_available",
        side_effect=[
            {"ok": True, "status": "passed", "available": True},
            {"ok": False, "status": "failed", "available": False},
        ],
    )
    def test_network_control_precondition_blocks_healthy_pihole_when_alternate_is_down(self, mock_check) -> None:
        results = evaluate_network_control_preconditions(
            action_policy={"required_preconditions": ["pihole_restart_continuity"]},
            target_type="service",
            target_id="dns_primary",
            music_settings={},
            service_control_settings={"hosts": {}},
            inventory={
                "services": [
                    {"id": "dns_primary", "host_id": "gateway_a"},
                    {"id": "dns_secondary", "host_id": "gateway_b"},
                ]
            },
            control_policy={
                "actions": [
                    {
                        "target_type": "service",
                        "target_id": service_id,
                        "action_id": "restart_service",
                        "required_preconditions": ["pihole_restart_continuity"],
                    }
                    for service_id in ("dns_primary", "dns_secondary")
                ]
            },
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["observed_value"], {"target": "healthy", "alternate": "down"})
        self.assertEqual(mock_check.call_count, 2)

    @patch(
        "oracle_app.network_control_preconditions.check_service_available",
        side_effect=[
            {"ok": False, "status": "failed", "available": False},
            {"ok": False, "status": "failed", "available": False},
        ],
    )
    def test_network_control_precondition_allows_recovery_when_both_piholes_are_down(self, _mock_check) -> None:
        results = evaluate_network_control_preconditions(
            action_policy={"required_preconditions": ["pihole_restart_continuity"]},
            target_type="service",
            target_id="dns_primary",
            music_settings={},
            service_control_settings={"hosts": {}},
            inventory={
                "services": [
                    {"id": "dns_primary", "host_id": "gateway_a"},
                    {"id": "dns_secondary", "host_id": "gateway_b"},
                ]
            },
            control_policy={
                "actions": [
                    {
                        "target_type": "service",
                        "target_id": service_id,
                        "action_id": "restart_service",
                        "required_preconditions": ["pihole_restart_continuity"],
                    }
                    for service_id in ("dns_primary", "dns_secondary")
                ]
            },
        )

        self.assertEqual(results[0]["status"], "passed")
        self.assertEqual(results[0]["observed_value"], {"target": "down", "alternate": "down"})
        self.assertIn("recovery restart", results[0]["summary"])

    @patch(
        "oracle_app.network_control_preconditions.check_storage_safety",
        return_value={"ok": False, "configured": True, "check_count": 3, "passed_count": 2},
    )
    def test_network_control_precondition_blocks_unsafe_host_storage(self, _mock_check) -> None:
        results = evaluate_network_control_preconditions(
            action_policy={"required_preconditions": ["host_storage_safe_for_restart"]},
            target_type="host",
            target_id="storage_host",
            music_settings={},
            service_control_settings={"hosts": {}},
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["observed_value"], "2/3")
        self.assertNotIn("md0", str(results))
        self.assertNotIn("/srv/example-storage", str(results))
        _mock_check.assert_called_once_with(
            settings={"hosts": {}},
            host_id="storage_host",
            profile_id="host_storage_safe_for_restart",
        )

    @patch(
        "oracle_app.network_control_preconditions.check_storage_safety",
        return_value={"ok": False, "configured": False, "check_count": 0, "passed_count": 0},
    )
    def test_network_control_storage_precondition_fails_closed_without_configuration(self, _mock_check) -> None:
        [result] = evaluate_network_control_preconditions(
            action_policy={"required_preconditions": ["host_storage_safe_for_restart"]},
            target_type="host",
            target_id="example_storage_host",
            music_settings={},
            service_control_settings={"hosts": {}},
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["observed_value"], "0/0")
        self.assertNotIn("example_storage_host", str(result))

    def test_network_preconditions_match_generic_configured_restart_targets(self) -> None:
        self.assertTrue(network_control_precondition_matches_target(
            precondition_id="plex_no_active_streams",
            target_type="service",
            target_id="media_primary",
            action_id="restart_service",
        ))
        self.assertTrue(network_control_precondition_matches_target(
            precondition_id="pihole_restart_continuity",
            target_type="host",
            target_id="gateway_a",
            action_id="restart_host",
        ))
        self.assertTrue(network_control_precondition_matches_target(
            precondition_id="host_storage_safe_for_restart",
            target_type="host",
            target_id="storage_host",
            action_id="restart_host",
        ))
        self.assertFalse(network_control_precondition_matches_target(
            precondition_id="host_storage_safe_for_restart",
            target_type="service",
            target_id="storage_service",
            action_id="restart_service",
        ))

    def test_host_restart_inherits_curated_service_restart_preconditions(self) -> None:
        effective = with_inherited_host_preconditions(
            action_policy={
                "target_type": "host",
                "target_id": "oracle_host",
                "action_id": "restart_host",
                "required_preconditions": ["host_specific_check"],
            },
            target_type="host",
            target_id="oracle_host",
            inventory={
                "services": [
                    {"id": "plex", "host_id": "oracle_host"},
                    {"id": "dns_secondary", "host_id": "oracle_host"},
                    {"id": "caddy", "host_id": "dns_host"},
                ]
            },
            control_policy={
                "actions": [
                    {
                        "target_type": "service",
                        "target_id": "plex",
                        "action_id": "restart_service",
                        "required_preconditions": ["plex_no_active_streams"],
                    },
                    {
                        "target_type": "service",
                        "target_id": "dns_secondary",
                        "action_id": "restart_service",
                        "required_preconditions": ["pihole_restart_continuity"],
                    },
                    {
                        "target_type": "service",
                        "target_id": "caddy",
                        "action_id": "restart_service",
                        "required_preconditions": ["unrelated_check"],
                    },
                ]
            },
        )

        self.assertEqual(
            effective["required_preconditions"],
            ["host_specific_check", "plex_no_active_streams", "pihole_restart_continuity"],
        )

    def test_network_control_dry_run_resolves_oracle_inventory_target_without_execution(self) -> None:
        payload = build_network_control_dry_run(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"}],
                "services": [
                    {
                        "id": "plex",
                        "display_name": "Plex",
                        "host_id": "oracle_host",
                        "kind": "media",
                    }
                ],
                "power_targets": [],
            },
            request_payload={
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
                "actor": "admin",
                "source": "system_mode",
            },
        )

        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["policy_status"], "denied")
        self.assertEqual(payload["error_class"], "network_control_action_not_allowlisted")
        self.assertEqual(payload["target"]["id"], "plex")
        self.assertEqual(payload["target"]["display_name"], "Plex")
        self.assertEqual(payload["target"]["host_id"], "oracle_host")
        self.assertEqual(payload["steps"], [])

    def test_network_control_dry_run_blocks_failed_precondition(self) -> None:
        payload = build_network_control_dry_run(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
                "power_targets": [],
            },
            control_policy=_enabled_plex_restart_policy(),
            request_payload={
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
            },
            preconditions=[
                {
                    "id": "plex_no_active_streams",
                    "provider": "plex",
                    "status": "failed",
                    "observed_value": 2,
                    "summary": "Plex has 2 active stream(s), so Oracle will not restart it.",
                }
            ],
        )

        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["policy_status"], "blocked")
        self.assertEqual(payload["error_class"], "network_control_precondition_failed")
        self.assertEqual(payload["preconditions"][0]["observed_value"], 2)

    def test_network_control_dry_run_requires_configured_graceful_lifecycle(self) -> None:
        policy = {
            "actions": [
                {
                    "id": "storage_host_restart",
                    "target_type": "host",
                    "target_id": "storage_host",
                    "action_id": "restart_host",
                    "provider": "service_control",
                    "adapter": "service_control",
                    "requires_confirmation": True,
                    "requires_graceful_lifecycle": True,
                    "enabled": True,
                }
            ]
        }

        payload = build_network_control_dry_run(
            inventory={
                "hosts": [{"id": "storage_host", "display_name": "Storage Host"}],
                "services": [],
                "power_targets": [],
            },
            control_policy=policy,
            request_payload={
                "target_type": "host",
                "target_id": "storage_host",
                "action_id": "restart_host",
            },
            lifecycle={"configured": False, "phases": []},
        )

        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["error_class"], "network_control_lifecycle_not_configured")

    def test_network_control_dry_run_includes_graceful_lifecycle_phases(self) -> None:
        policy = {
            "actions": [
                {
                    "id": "dns_host_restart",
                    "target_type": "host",
                    "target_id": "dns_host",
                    "action_id": "restart_host",
                    "provider": "service_control",
                    "adapter": "service_control",
                    "requires_confirmation": True,
                    "requires_graceful_lifecycle": True,
                    "enabled": True,
                }
            ]
        }
        lifecycle = {
            "configured": True,
            "mode": "graceful",
            "phases": [
                {"id": "stop_host_services", "kind": "preparation", "summary": "Stop services."},
                {"id": "restart_host", "kind": "execution", "summary": "Restart host."},
            ],
        }

        payload = build_network_control_dry_run(
            inventory={
                "hosts": [{"id": "dns_host", "display_name": "DNS Host"}],
                "services": [],
                "power_targets": [],
            },
            control_policy=policy,
            request_payload={
                "target_type": "host",
                "target_id": "dns_host",
                "action_id": "restart_host",
            },
            lifecycle=lifecycle,
        )

        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["lifecycle"]["mode"], "graceful")
        self.assertIn("stop_host_services", {step["id"] for step in payload["steps"]})

    def test_network_control_dry_run_allows_enabled_policy_without_execution(self) -> None:
        payload = build_network_control_dry_run(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
                "power_targets": [],
            },
            control_policy=_enabled_plex_restart_policy(),
            request_payload={
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
                "actor": "admin",
                "source": "system_mode",
            },
            preconditions=[
                {
                    "id": "plex_no_active_streams",
                    "provider": "plex",
                    "status": "passed",
                    "observed_value": 0,
                    "summary": "Plex has no active streams.",
                }
            ],
        )

        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["policy_status"], "allowed")
        self.assertEqual(payload["result_status"], "not_executed")
        self.assertEqual(payload["confirmation_status"], "required")
        self.assertEqual(payload["provider"], "ssh")
        self.assertIn("explicit confirmation", payload["summary"])
        self.assertEqual([step["id"] for step in payload["steps"]], ["policy_check", "preconditions", "confirmation", "provider_adapter"])
        for _path, value in _walk_payload(payload):
            if isinstance(value, str):
                self.assertNotIn("systemctl", value)

    def test_network_control_dry_run_denies_disabled_policy(self) -> None:
        policy = _enabled_plex_restart_policy()
        policy["actions"][0]["enabled"] = False
        payload = build_network_control_dry_run(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
                "power_targets": [],
            },
            control_policy=policy,
            request_payload={
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
            },
        )

        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["policy_status"], "denied")
        self.assertEqual(payload["error_class"], "network_control_action_disabled")

    def test_network_control_confirm_requires_explicit_confirmation(self) -> None:
        payload = build_network_control_confirm(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
                "power_targets": [],
            },
            control_policy=_enabled_plex_restart_policy(),
            request_payload={
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
            },
            preconditions=[
                {
                    "id": "plex_no_active_streams",
                    "provider": "plex",
                    "status": "passed",
                    "observed_value": 0,
                    "summary": "Plex has no active streams.",
                }
            ],
        )

        self.assertFalse(payload["allowed"])
        self.assertEqual(payload["mode"], "execute")
        self.assertEqual(payload["result_status"], "not_executed")
        self.assertEqual(payload["error_class"], "network_control_confirmation_required")

    def test_network_control_confirm_rechecks_and_returns_not_implemented(self) -> None:
        payload = build_network_control_confirm(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server", "kind": "server"}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
                "power_targets": [],
            },
            control_policy=_enabled_plex_restart_policy(),
            request_payload={
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
                "confirmed": True,
            },
            preconditions=[
                {
                    "id": "plex_no_active_streams",
                    "provider": "plex",
                    "status": "passed",
                    "observed_value": 0,
                    "summary": "Plex has no active streams.",
                }
            ],
        )

        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["mode"], "execute")
        self.assertEqual(payload["confirmation_status"], "confirmed")
        self.assertEqual(payload["result_status"], "not_implemented")
        self.assertEqual(payload["error_class"], "network_control_execution_not_implemented")
        self.assertIn("execution_not_implemented", {step["id"] for step in payload["steps"]})
        for _path, value in _walk_payload(payload):
            if isinstance(value, str):
                self.assertNotIn("systemctl", value)

    def test_network_control_confirm_keeps_safe_power_recovery_metadata(self) -> None:
        payload = build_network_control_confirm(
            inventory={
                "hosts": [{"id": "mesh_node_lounge", "display_name": "Lounge Mesh Node"}],
                "services": [],
                "power_targets": [
                    {
                        "id": "mesh_node_lounge_power",
                        "display_name": "Lounge Mesh Node Power",
                        "host_id": "mesh_node_lounge",
                    }
                ],
            },
            control_policy={
                "actions": [
                    {
                        "id": "mesh_node_lounge_power_cycle",
                        "target_type": "power_target",
                        "target_id": "mesh_node_lounge_power",
                        "action_id": "power_cycle",
                        "provider": "home_assistant",
                        "adapter": "switch_power_cycle",
                        "requires_confirmation": True,
                        "enabled": True,
                    }
                ]
            },
            request_payload={
                "target_type": "power_target",
                "target_id": "mesh_node_lounge_power",
                "action_id": "power_cycle",
                "confirmed": True,
            },
            execution_result={
                "ok": False,
                "result_status": "failed",
                "error_class": "network_control_host_recovery_failed",
                "summary": "Power was restored, but the target host did not come back online in time.",
                "execution": {
                    "adapter": "switch_power_cycle",
                    "off_seconds": 10,
                    "recovery_timeout_seconds": 90,
                    "recovery_poll_seconds": 5,
                    "verification_status": "failed",
                    "power_restored": True,
                    "host_address": "192.0.2.161",
                },
                "steps": [],
            },
        )

        self.assertEqual(payload["execution"]["recovery_timeout_seconds"], 90)
        self.assertEqual(payload["execution"]["recovery_poll_seconds"], 5)
        self.assertTrue(payload["execution"]["power_restored"])
        self.assertNotIn("host_address", payload["execution"])

    def test_network_control_confirm_keeps_safe_host_readiness_metadata(self) -> None:
        payload = build_network_control_confirm(
            inventory={
                "hosts": [{"id": "dns_host", "display_name": "DNS Host"}],
                "services": [],
                "power_targets": [],
            },
            control_policy={
                "actions": [
                    {
                        "id": "dns_host_restart",
                        "target_type": "host",
                        "target_id": "dns_host",
                        "action_id": "restart_host",
                        "provider": "service_control",
                        "adapter": "service_control",
                        "requires_confirmation": True,
                        "enabled": True,
                    }
                ]
            },
            request_payload={
                "target_type": "host",
                "target_id": "dns_host",
                "action_id": "restart_host",
                "confirmed": True,
            },
            execution_result={
                "ok": True,
                "result_status": "executed",
                "summary": "Host readiness passed.",
                "execution": {
                    "adapter": "service_control",
                    "verification_status": "passed",
                    "readiness_status": "passed",
                    "readiness_timeout_seconds": 180,
                    "readiness_check_count": 4,
                    "readiness_passed_count": 4,
                    "readiness_failed_check_ids": [],
                    "failed_check_ids": ["must_not_leak"],
                },
                "steps": [],
            },
        )

        self.assertEqual(payload["execution"]["readiness_status"], "passed")
        self.assertEqual(payload["execution"]["readiness_check_count"], 4)
        self.assertEqual(payload["execution"]["readiness_passed_count"], 4)
        self.assertEqual(payload["execution"]["readiness_failed_check_ids"], [])
        self.assertNotIn("failed_check_ids", payload["execution"])

    @patch("oracle_app.network_control_execution.time.sleep", return_value=None)
    @patch("oracle_app.network_control_execution.subprocess.run")
    def test_network_control_executor_restarts_systemd_unit_and_verifies(self, mock_run, _mock_sleep) -> None:
        mock_run.return_value.returncode = 0

        result = execute_network_control_action(
            action_policy=_enabled_plex_restart_policy()["actions"][0],
            verify_available=lambda: {"status": "passed", "summary": "Plex availability check passed after restart."},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_status"], "executed")
        self.assertEqual(result["execution"]["unit"], "example-media.service")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.args[0], ["sudo", "-n", "systemctl", "restart", "example-media.service"])
        self.assertEqual(mock_run.call_args.kwargs["timeout"], 5)

    @patch("oracle_app.network_control_execution.subprocess.run")
    def test_network_control_executor_reports_systemd_failure_without_command_output(self, mock_run) -> None:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "secret-ish systemctl detail"

        result = execute_network_control_action(
            action_policy=_enabled_plex_restart_policy()["actions"][0],
            verify_available=lambda: {"status": "passed", "summary": "Plex availability check passed after restart."},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_restart_failed")
        self.assertNotIn("secret-ish", str(result))

    @patch("oracle_app.network_control_execution.subprocess.run")
    def test_network_control_executor_reports_systemd_timeout_without_command_output(self, mock_run) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(
            ["sudo", "-n", "systemctl", "restart", "example-media.service"],
            timeout=5,
            output="secret-ish systemctl output",
            stderr="secret-ish systemctl error",
        )

        result = execute_network_control_action(
            action_policy=_enabled_plex_restart_policy()["actions"][0],
            verify_available=lambda: {"status": "passed", "summary": "Plex availability check passed after restart."},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_restart_timeout")
        self.assertNotIn("secret-ish", str(result))

    @patch("oracle_app.network_control_execution.time.sleep", return_value=None)
    @patch("oracle_app.network_control_execution.execute_service_command")
    def test_network_control_executor_uses_service_control_bridge(self, mock_service_control, _mock_sleep) -> None:
        mock_service_control.return_value = {"ok": True, "status": "executed", "detail": "done"}
        policy = _enabled_plex_restart_policy()["actions"][0]
        policy["provider"] = "service_control"
        policy["adapter"] = "service_control"
        policy["execution"] = {"restart_timeout_seconds": 5, "wait_seconds": 0}

        result = execute_network_control_action(
            action_policy=policy,
            target={
                "id": "plex",
                "control_refs": {
                    "service_control": {
                        "host_id": "oracle_host",
                        "service_name": "plex",
                    }
                },
            },
            service_control_settings={"hosts": {"oracle_host": {"enabled": True, "transport": "local", "services": {}}}},
            verify_available=lambda: {"status": "passed", "summary": "Plex availability check passed after restart."},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["execution"]["adapter"], "service_control")
        mock_service_control.assert_called_once()
        self.assertEqual(mock_service_control.call_args.kwargs["host_id"], "oracle_host")
        self.assertEqual(mock_service_control.call_args.kwargs["service_name"], "plex")
        self.assertEqual(mock_service_control.call_args.kwargs["command"], "restart_service")

    @patch(
        "oracle_app.network_control_execution._wait_for_host_readiness",
        return_value={
            "ready": True,
            "check_count": 4,
            "passed_count": 4,
            "failed_check_ids": [],
        },
    )
    @patch(
        "oracle_app.network_control_execution._wait_for_host_restart",
        return_value={
            "went_offline": True,
            "recovered": True,
            "shutdown_attempts": 2,
            "recovery_attempts": 3,
        },
    )
    @patch("oracle_app.network_control_execution.execute_service_action")
    def test_network_control_executor_restarts_host_and_verifies_recovery(
        self,
        mock_service_action,
        mock_wait_for_restart,
        mock_wait_for_readiness,
    ) -> None:
        mock_service_action.return_value = {"ok": True, "status": "restart_sent"}

        result = execute_network_control_action(
            action_policy={
                "action_id": "restart_host",
                "adapter": "service_control",
                "execution": {
                    "shutdown_timeout_seconds": 90,
                    "recovery_timeout_seconds": 180,
                    "recovery_poll_seconds": 5,
                    "readiness_timeout_seconds": 120,
                },
            },
            target={"id": "dns_host", "addresses": ["192.0.2.203"]},
            service_control_settings={"hosts": {"dns_host": {}}},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["execution"]["verification_status"], "passed")
        self.assertEqual(result["execution"]["readiness_status"], "passed")
        self.assertEqual(result["execution"]["readiness_check_count"], 4)
        mock_service_action.assert_called_once_with(
            settings={"hosts": {"dns_host": {}}},
            host="dns_host",
            action="restart_host",
        )
        mock_wait_for_restart.assert_called_once_with(
            host_address="192.0.2.203",
            shutdown_timeout_seconds=90,
            recovery_timeout_seconds=180,
            poll_seconds=5,
        )
        mock_wait_for_readiness.assert_called_once_with(
            service_control_settings={"hosts": {"dns_host": {}}},
            host_id="dns_host",
            timeout_seconds=120,
            poll_seconds=5,
        )

    @patch(
        "oracle_app.network_control_execution.recover_host_restart_dependents",
        return_value={"ok": True, "status": "not_required"},
    )
    @patch(
        "oracle_app.network_control_execution._wait_for_host_readiness",
        return_value={"ready": True, "check_count": 1, "passed_count": 1, "failed_check_ids": []},
    )
    @patch(
        "oracle_app.network_control_execution.recover_host_restart_services",
        return_value={"ok": True, "status": "recovered"},
    )
    @patch(
        "oracle_app.network_control_execution._wait_for_host_restart",
        return_value={
            "went_offline": True,
            "recovered": True,
            "shutdown_attempts": 1,
            "recovery_attempts": 2,
        },
    )
    @patch("oracle_app.network_control_execution.execute_service_action")
    @patch(
        "oracle_app.network_control_execution.prepare_host_restart",
        return_value={"ok": True, "status": "prepared", "completed_phase_ids": ["stop_host_services"]},
    )
    def test_graceful_host_restart_restores_prepared_services_before_readiness(
        self,
        _mock_prepare,
        mock_service_action,
        _mock_wait_for_restart,
        mock_recover_services,
        mock_wait_for_readiness,
        _mock_recover_dependents,
    ) -> None:
        mock_service_action.return_value = {"ok": True, "status": "restart_sent"}

        result = execute_network_control_action(
            action_policy={
                "action_id": "restart_host",
                "adapter": "service_control",
                "requires_graceful_lifecycle": True,
            },
            target={"id": "storage_host", "addresses": ["192.0.2.200"]},
            service_control_settings={"hosts": {"storage_host": {}}},
        )

        self.assertTrue(result["ok"])
        step_ids = [step["id"] for step in result["steps"]]
        self.assertLess(step_ids.index("host_services_recovered"), step_ids.index("host_readiness_wait_started"))
        self.assertIn("restore_host_services", result["execution"]["lifecycle_completed_phase_ids"])
        mock_recover_services.assert_called_once()
        mock_wait_for_readiness.assert_called_once()

    @patch(
        "oracle_app.network_control_execution._wait_for_host_readiness",
        return_value={
            "ready": False,
            "check_count": 4,
            "passed_count": 3,
            "failed_check_ids": ["dns_primary"],
        },
    )
    @patch(
        "oracle_app.network_control_execution._wait_for_host_restart",
        return_value={
            "went_offline": True,
            "recovered": True,
            "shutdown_attempts": 2,
            "recovery_attempts": 3,
        },
    )
    @patch("oracle_app.network_control_execution.execute_service_action")
    def test_network_control_executor_fails_when_host_readiness_fails(
        self,
        mock_service_action,
        _mock_wait_for_restart,
        _mock_wait_for_readiness,
    ) -> None:
        mock_service_action.return_value = {"ok": True, "status": "restart_sent"}

        result = execute_network_control_action(
            action_policy={"action_id": "restart_host", "adapter": "service_control"},
            target={"id": "dns_host", "addresses": ["192.0.2.203"]},
            service_control_settings={"hosts": {"dns_host": {}}},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_host_readiness_failed")
        self.assertEqual(result["execution"]["readiness_status"], "failed")
        self.assertEqual(result["execution"]["readiness_passed_count"], 3)

    @patch("oracle_app.network_control_execution.execute_service_action")
    def test_network_control_executor_marks_local_host_restart_deferred(self, mock_service_action) -> None:
        mock_service_action.return_value = {
            "ok": True,
            "status": "scheduled",
            "deferred": True,
        }

        result = execute_network_control_action(
            action_policy={"action_id": "restart_host", "adapter": "service_control"},
            target={"id": "oracle_host", "addresses": ["192.0.2.205"]},
            service_control_settings={"hosts": {"oracle_host": {}}},
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["execution"]["deferred"])
        self.assertEqual(result["execution"]["verification_status"], "deferred")

    @patch(
        "oracle_app.network_control_execution.stage_pending_local_host_restart",
        return_value={
            "ok": False,
            "error": "network_control_local_restart_state_unavailable",
            "detail": "Oracle could not persist local restart recovery state.",
        },
    )
    @patch("oracle_app.network_control_execution.execute_service_action")
    def test_network_control_executor_blocks_local_restart_when_pending_state_cannot_be_written(
        self,
        mock_service_action,
        _mock_stage,
    ) -> None:
        result = execute_network_control_action(
            action_policy={"action_id": "restart_host", "adapter": "service_control"},
            target={"id": "oracle_host", "addresses": ["192.0.2.205"]},
            service_control_settings={
                "hosts": {
                    "oracle_host": {
                        "transport": "local",
                    }
                }
            },
            control_context={"request_id": "netctl-local-restart"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_local_restart_state_unavailable")
        mock_service_action.assert_not_called()

    def test_pending_local_restart_waits_for_linux_boot_id_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "pending.json"
            boot_id_path = Path(tmpdir) / "boot-id"
            boot_id_path.write_text("boot-a\n", encoding="utf-8")
            staged = stage_pending_local_host_restart(
                control_context={"request_id": "netctl-local-restart"},
                host_id="oracle_host",
                readiness_timeout_seconds=120,
                recovery_poll_seconds=5,
                lifecycle_status="prepared",
                state_path=state_path,
                boot_id_path=boot_id_path,
            )

            result = complete_pending_local_host_restart(
                service_control_settings={"hosts": {}},
                state_path=state_path,
                boot_id_path=boot_id_path,
            )

        self.assertTrue(staged["ok"])
        self.assertEqual(result, {"status": "pending", "reason": "boot_not_changed"})

    @patch("oracle_app.network_control_local_restart.check_host_readiness")
    def test_canonical_pending_restart_does_not_fall_back_when_network_is_absent(
        self,
        mock_legacy_readiness,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "pending.json"
            boot_id_path = Path(tmpdir) / "boot-id"
            boot_id_path.write_text("boot-a\n", encoding="utf-8")
            stage_pending_local_host_restart(
                control_context={"request_id": "netctl-local-restart"},
                host_id="oracle_host",
                readiness_timeout_seconds=120,
                recovery_poll_seconds=5,
                lifecycle_status="prepared",
                state_path=state_path,
                boot_id_path=boot_id_path,
            )
            boot_id_path.write_text("boot-b\n", encoding="utf-8")

            result = complete_pending_local_host_restart(
                canonical_authority=True,
                canonical_execution=None,
                state_path=state_path,
                boot_id_path=boot_id_path,
            )

        self.assertEqual(
            result,
            {"status": "pending", "reason": "canonical_network_unavailable"},
        )
        mock_legacy_readiness.assert_not_called()

    @patch(
        "oracle_app.network_control_local_restart.check_host_readiness",
        return_value={
            "ok": True,
            "status": "passed",
            "check_count": 12,
            "passed_count": 12,
            "failed_check_ids": [],
        },
    )
    def test_pending_local_restart_completes_after_new_boot_and_readiness(self, _mock_readiness) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            state_path = tmp_path / "pending.json"
            boot_id_path = tmp_path / "boot-id"
            db_path = tmp_path / "oracle-memory.sqlite3"
            upsert_source(
                source_id="brain",
                source_type="brain",
                display_name="Oracle Brain",
                db_path=db_path,
            )
            boot_id_path.write_text("boot-a\n", encoding="utf-8")
            stage_pending_local_host_restart(
                control_context={
                    "request_id": "netctl-local-restart",
                    "requested_at": "2026-06-10T01:00:00-04:00",
                    "actor": "system_mode",
                    "source": "system_mode",
                },
                host_id="oracle_host",
                readiness_timeout_seconds=120,
                recovery_poll_seconds=5,
                lifecycle_status="prepared",
                state_path=state_path,
                boot_id_path=boot_id_path,
            )
            boot_id_path.write_text("boot-b\n", encoding="utf-8")

            result = complete_pending_local_host_restart(
                service_control_settings={"hosts": {"oracle_host": {}}},
                state_path=state_path,
                boot_id_path=boot_id_path,
                db_path=db_path,
            )
            state_exists_after_completion = state_path.exists()
            events = query_events(
                EventQuery(
                    event_type="network_control_confirm",
                    domain="network_control",
                    limit=10,
                ),
                db_path=db_path,
            )

        self.assertEqual(result["status"], "completed")
        self.assertFalse(state_exists_after_completion)
        self.assertEqual(len(events), 1)
        final = events[0]["payload"]
        self.assertEqual(final["request_id"], "netctl-local-restart")
        self.assertEqual(final["result_status"], "executed")
        self.assertTrue(final["execution"]["local_restart_completed"])
        self.assertTrue(final["execution"]["boot_changed"])
        self.assertEqual(final["execution"]["readiness_status"], "passed")

    def test_pending_local_service_restart_waits_for_process_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            state_path = tmp_path / "pending-service.json"
            process_stat_path = tmp_path / "process-stat"
            process_stat_path.write_text(
                "10 (python) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19\n",
                encoding="utf-8",
            )
            staged = stage_pending_local_service_restart(
                control_context={"request_id": "netctl-brain-restart"},
                target_id="oracle_brain",
                host_id="oracle_host",
                service_name="oracle_brain",
                state_path=state_path,
                process_stat_path=process_stat_path,
            )

            result = complete_pending_local_service_restart(
                state_path=state_path,
                process_stat_path=process_stat_path,
            )

        self.assertTrue(staged["ok"])
        self.assertEqual(result, {"status": "pending", "reason": "process_not_changed"})

    def test_pending_local_service_restart_completes_after_new_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            state_path = tmp_path / "pending-service.json"
            process_stat_path = tmp_path / "process-stat"
            db_path = tmp_path / "oracle-memory.sqlite3"
            upsert_source(
                source_id="brain",
                source_type="brain",
                display_name="Oracle Brain",
                db_path=db_path,
            )
            process_stat_path.write_text(
                "10 (python) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19\n",
                encoding="utf-8",
            )
            stage_pending_local_service_restart(
                control_context={
                    "request_id": "netctl-brain-restart",
                    "requested_at": "2026-06-10T23:00:00-04:00",
                    "actor": "system_mode",
                    "source": "system_mode",
                },
                target_id="oracle_brain",
                host_id="oracle_host",
                service_name="oracle_brain",
                state_path=state_path,
                process_stat_path=process_stat_path,
            )
            process_stat_path.write_text(
                "20 (python) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 20\n",
                encoding="utf-8",
            )

            result = complete_pending_local_service_restart(
                state_path=state_path,
                process_stat_path=process_stat_path,
                db_path=db_path,
            )
            events = query_events(
                EventQuery(event_type="network_control_confirm", limit=10),
                db_path=db_path,
            )

        self.assertEqual(result["status"], "completed")
        self.assertFalse(state_path.exists())
        self.assertEqual(len(events), 1)
        final = events[0]["payload"]
        self.assertEqual(final["request_id"], "netctl-brain-restart")
        self.assertEqual(final["result_status"], "executed")
        self.assertTrue(final["execution"]["local_service_restart_completed"])
        self.assertTrue(final["execution"]["process_changed"])
        self.assertEqual(final["execution"]["verification_status"], "passed")

    @patch(
        "oracle_app.network_control_execution._wait_for_host_restart",
        return_value={
            "went_offline": True,
            "recovered": True,
            "shutdown_attempts": 2,
            "recovery_attempts": 3,
        },
    )
    @patch("oracle_app.network_control_execution.execute_router_action")
    def test_network_control_executor_restarts_router_and_waits_for_recovery(
        self,
        mock_router_control,
        mock_wait_for_recovery,
    ) -> None:
        mock_router_control.return_value = {"ok": True, "status": "restart_sent"}

        result = execute_network_control_action(
            action_policy={
                "action_id": "restart_router",
                "adapter": "router_control",
                "execution": {
                    "shutdown_timeout_seconds": 90,
                    "recovery_timeout_seconds": 180,
                    "recovery_poll_seconds": 5,
                },
            },
            target={"id": "router_main", "addresses": ["192.0.2.1"]},
            router_control_settings={"routers": {"router_main": {}}},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["execution"]["adapter"], "router_control")
        mock_router_control.assert_called_once_with(
            settings={"routers": {"router_main": {}}},
            router="router_main",
            action="restart_router",
        )
        mock_wait_for_recovery.assert_called_once_with(
            host_address="192.0.2.1",
            shutdown_timeout_seconds=90,
            recovery_timeout_seconds=180,
            poll_seconds=5,
        )
        self.assertEqual(result["execution"]["verification_status"], "passed")
        self.assertTrue(result["execution"]["shutdown_observed"])
        self.assertIn("router_shutdown_observed", [step["id"] for step in result["steps"]])

    @patch(
        "oracle_app.network_control_execution._wait_for_host_restart",
        return_value={
            "went_offline": True,
            "recovered": False,
            "shutdown_attempts": 2,
            "recovery_attempts": 4,
        },
    )
    @patch("oracle_app.network_control_execution.execute_router_action")
    def test_network_control_executor_reports_router_recovery_failure(
        self,
        mock_router_control,
        _mock_wait_for_recovery,
    ) -> None:
        mock_router_control.return_value = {"ok": True, "status": "restart_sent"}

        result = execute_network_control_action(
            action_policy={"action_id": "restart_router", "adapter": "router_control"},
            target={"id": "router_main", "addresses": ["192.0.2.1"]},
            router_control_settings={"routers": {"router_main": {}}},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_router_recovery_failed")
        self.assertEqual(result["steps"][-1]["id"], "router_recovery_failed")

    @patch(
        "oracle_app.network_control_execution._wait_for_host_restart",
        return_value={
            "went_offline": False,
            "recovered": False,
            "shutdown_attempts": 18,
            "recovery_attempts": 0,
        },
    )
    @patch("oracle_app.network_control_execution.execute_router_action")
    def test_network_control_executor_does_not_accept_router_that_never_goes_offline(
        self,
        mock_router_control,
        _mock_wait_for_restart,
    ) -> None:
        mock_router_control.return_value = {"ok": True, "status": "restart_sent"}

        result = execute_network_control_action(
            action_policy={
                "action_id": "restart_router",
                "adapter": "router_control",
                "execution": {"shutdown_timeout_seconds": 90},
            },
            target={"id": "router_main", "addresses": ["192.0.2.1"]},
            router_control_settings={"routers": {"router_main": {}}},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_router_shutdown_not_observed")
        self.assertEqual(result["execution"]["verification_status"], "failed")
        self.assertEqual(result["steps"][-1]["id"], "router_shutdown_not_observed")

    @patch("oracle_app.network_control_execution.time.sleep")
    @patch(
        "oracle_app.network_control_execution._wait_for_power_readiness",
        return_value={"ready": True, "check_count": 1, "passed_count": 1, "failed_check_ids": []},
    )
    @patch("oracle_app.network_control_execution.get_home_assistant_settings")
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_power_cycles_home_assistant_switch(
        self,
        mock_bridge_class,
        mock_ha_settings,
        mock_wait_for_readiness,
        mock_sleep,
    ) -> None:
        mock_ha_settings.return_value = ("http://home-assistant.local:8123", "dummy-token")
        bridge = mock_bridge_class.return_value
        bridge.wait_for_entity_state.side_effect = [
            {"state": "off"},
            {"state": "on"},
        ]

        result = execute_network_control_action(
            action_policy={
                "action_id": "power_cycle",
                "adapter": "switch_power_cycle",
                "execution": {"off_seconds": 12, "verification_timeout_seconds": 9},
            },
            target={
                "id": "mesh_node_lounge_power",
                "provider": "home_assistant",
                "entity_id": "switch.lounge_mesh_node",
                "capabilities": ["power_cycle"],
                "enabled": True,
                "readiness": {
                    "checks": [{"id": "mesh_node_reachable", "kind": "host_reachable", "address": "192.0.2.161"}]
                },
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["execution"]["verification_status"], "passed")
        self.assertEqual(result["execution"]["readiness_status"], "passed")
        self.assertEqual(result["execution"]["readiness_check_count"], 1)
        self.assertEqual(
            [call.kwargs["service_name"] for call in bridge.call_service.call_args_list],
            ["turn_off", "turn_on"],
        )
        mock_sleep.assert_called_once_with(12)
        mock_wait_for_readiness.assert_called_once()

    @patch(
        "oracle_app.network_control_execution.time.monotonic",
        side_effect=[0, 0, 0, 1, 5, 6],
    )
    @patch("oracle_app.network_control_execution.time.sleep")
    @patch(
        "oracle_app.network_control_execution._wait_for_power_readiness",
        return_value={"ready": True, "check_count": 1, "passed_count": 1, "failed_check_ids": []},
    )
    @patch("oracle_app.network_control_execution.NetworkProbeBridge")
    @patch("oracle_app.network_control_execution.get_home_assistant_settings")
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_waits_for_powered_host_recovery(
        self,
        mock_bridge_class,
        mock_ha_settings,
        mock_probe_class,
        _mock_wait_for_readiness,
        mock_sleep,
        _mock_monotonic,
    ) -> None:
        mock_ha_settings.return_value = ("http://home-assistant.local:8123", "dummy-token")
        bridge = mock_bridge_class.return_value
        bridge.wait_for_entity_state.side_effect = [{"state": "off"}, {"state": "on"}]
        mock_probe_class.return_value.check_host_reachable.side_effect = [
            {"status": "down"},
            {"status": "healthy"},
        ]

        result = execute_network_control_action(
            action_policy={
                "action_id": "power_cycle",
                "adapter": "switch_power_cycle",
                "execution": {
                    "off_seconds": 10,
                    "recovery_timeout_seconds": 90,
                    "recovery_poll_seconds": 5,
                },
            },
            target={
                "provider": "home_assistant",
                "entity_id": "switch.lounge_mesh_node",
                "capabilities": ["power_cycle"],
                "enabled": True,
                "host_id": "mesh_node_lounge",
                "host_display_name": "Lounge Mesh Node",
                "host_address": "192.0.2.161",
                "readiness": {
                    "checks": [{"id": "mesh_node_reachable", "kind": "host_reachable", "address": "192.0.2.161"}]
                },
            },
        )

        self.assertTrue(result["ok"])
        self.assertIn("Lounge Mesh Node is reachable", result["summary"])
        self.assertEqual(result["execution"]["recovery_timeout_seconds"], 90)
        self.assertEqual(
            [step["id"] for step in result["steps"][-5:]],
            [
                "host_recovery_wait_started",
                "host_recovery_verified",
                "power_readiness_wait_started",
                "power_readiness_verified",
                "execution_completed",
            ],
        )
        self.assertEqual(mock_sleep.call_args_list[-1].args, (5,))

    @patch(
        "oracle_app.network_control_execution.time.monotonic",
        side_effect=[0, 0, 0, 16, 16],
    )
    @patch("oracle_app.network_control_execution.time.sleep")
    @patch("oracle_app.network_control_execution.NetworkProbeBridge")
    @patch("oracle_app.network_control_execution.get_home_assistant_settings")
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_fails_when_powered_host_does_not_recover(
        self,
        mock_bridge_class,
        mock_ha_settings,
        mock_probe_class,
        _mock_sleep,
        _mock_monotonic,
    ) -> None:
        mock_ha_settings.return_value = ("http://home-assistant.local:8123", "dummy-token")
        bridge = mock_bridge_class.return_value
        bridge.wait_for_entity_state.side_effect = [{"state": "off"}, {"state": "on"}]
        mock_probe_class.return_value.check_host_reachable.return_value = {"status": "down"}

        result = execute_network_control_action(
            action_policy={
                "action_id": "power_cycle",
                "adapter": "switch_power_cycle",
                "execution": {"recovery_timeout_seconds": 15},
            },
            target={
                "provider": "home_assistant",
                "entity_id": "switch.lounge_mesh_node",
                "capabilities": ["power_cycle"],
                "enabled": True,
                "host_display_name": "Lounge Mesh Node",
                "host_address": "192.0.2.161",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_host_recovery_failed")
        self.assertTrue(result["execution"]["power_restored"])
        self.assertEqual(result["steps"][-1]["id"], "host_recovery_failed")

    def test_power_target_execution_resolves_oracle_host_address(self) -> None:
        target = _with_power_target_host(
            target={
                "id": "mesh_node_lounge_power",
                "host_id": "mesh_node_lounge",
                "provider": "home_assistant",
            },
            inventory={
                "hosts": [
                    {
                        "id": "mesh_node_lounge",
                        "display_name": "Lounge Mesh Node",
                        "addresses": ["192.0.2.161"],
                    }
                ]
            },
        )

        self.assertEqual(target["host_display_name"], "Lounge Mesh Node")
        self.assertEqual(target["host_address"], "192.0.2.161")

    @patch("oracle_app.provider_bridges.network_probe.subprocess.run")
    def test_network_probe_bridge_checks_host_reachability(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = NetworkProbeBridge().check_host_reachable("192.0.2.161", timeout_seconds=2)

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(
            mock_run.call_args.args[0],
            ["ping", "-c", "1", "-W", "2", "192.0.2.161"],
        )

    @patch("oracle_app.provider_bridges.network_probe.socket.create_connection")
    def test_network_probe_bridge_checks_host_tcp_reachability(self, mock_create_connection) -> None:
        mock_create_connection.return_value.__enter__.return_value = object()

        result = NetworkProbeBridge().check_tcp_reachable(
            "192.0.2.209",
            port=22,
            timeout_seconds=3,
        )

        self.assertEqual(result["status"], "healthy")
        mock_create_connection.assert_called_once_with(("192.0.2.209", 22), timeout=3)

    @patch.object(NetworkProbeBridge, "get_internet_status", return_value={"status": "healthy"})
    @patch.object(NetworkProbeBridge, "check_tcp_reachable", return_value={"status": "healthy"})
    @patch.object(NetworkProbeBridge, "check_host_reachable", return_value={"status": "healthy"})
    def test_network_probe_bridge_checks_power_readiness(
        self,
        mock_host,
        mock_tcp,
        mock_internet,
    ) -> None:
        result = NetworkProbeBridge().check_readiness(
            profile={
                "checks": [
                    {"id": "router", "kind": "host_reachable", "address": "192.0.2.1"},
                    {"id": "dns", "kind": "tcp_reachable", "address": "192.0.2.203", "port": 53},
                    {"id": "internet", "kind": "internet"},
                ]
            },
            internet_settings={"enabled": True},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["check_count"], 3)
        self.assertEqual(result["passed_count"], 3)
        self.assertEqual(result["failed_check_ids"], [])
        mock_host.assert_called_once()
        mock_tcp.assert_called_once()
        mock_internet.assert_called_once_with(settings={"enabled": True})

    @patch(
        "oracle_app.network_control_execution._wait_for_power_readiness",
        return_value={
            "ready": False,
            "check_count": 4,
            "passed_count": 3,
            "failed_check_ids": ["internet"],
        },
    )
    @patch("oracle_app.network_control_execution.get_home_assistant_settings")
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_reports_power_readiness_failure(
        self,
        mock_bridge_class,
        mock_ha_settings,
        _mock_wait_for_readiness,
    ) -> None:
        mock_ha_settings.return_value = ("http://home-assistant.local:8123", "dummy-token")
        mock_bridge_class.return_value.wait_for_entity_state.side_effect = [{"state": "off"}, {"state": "on"}]

        result = execute_network_control_action(
            action_policy={"action_id": "power_cycle", "adapter": "switch_power_cycle"},
            target={
                "provider": "home_assistant",
                "entity_id": "switch.router",
                "capabilities": ["power_cycle"],
                "enabled": True,
                "readiness": {"checks": [{"id": "internet", "kind": "internet"}]},
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_power_readiness_failed")
        self.assertTrue(result["execution"]["power_restored"])
        self.assertEqual(result["execution"]["readiness_status"], "failed")
        self.assertEqual(result["execution"]["readiness_passed_count"], 3)
        self.assertEqual(result["execution"]["readiness_failed_check_ids"], ["internet"])
        self.assertEqual(result["steps"][-1]["id"], "power_readiness_failed")

    @patch("oracle_app.network_control_execution.get_home_assistant_settings")
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_power_cycle_attempts_restore_after_failed_off_verification(
        self,
        mock_bridge_class,
        mock_ha_settings,
    ) -> None:
        mock_ha_settings.return_value = ("http://home-assistant.local:8123", "dummy-token")
        bridge = mock_bridge_class.return_value
        bridge.wait_for_entity_state.return_value = {"state": "on"}

        result = execute_network_control_action(
            action_policy={"action_id": "power_cycle", "adapter": "switch_power_cycle"},
            target={
                "provider": "home_assistant",
                "entity_id": "switch.lounge_mesh_node",
                "capabilities": ["power_cycle"],
                "enabled": True,
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_power_cycle_failed")
        self.assertEqual(
            [call.kwargs["service_name"] for call in bridge.call_service.call_args_list],
            ["turn_off", "turn_on"],
        )

    @patch("oracle_app.network_control_execution.time.sleep", return_value=None)
    @patch("oracle_app.network_control_execution.check_service_available")
    @patch("oracle_app.network_control_execution.execute_service_command")
    def test_network_control_executor_uses_action_specific_host_ref(
        self,
        mock_service_control,
        mock_check_available,
        _mock_sleep,
    ) -> None:
        mock_service_control.return_value = {
            "ok": True,
            "status": "executed",
            "service_manager": "systemd",
            "detail": "done",
        }
        mock_check_available.return_value = {
            "ok": True,
            "status": "passed",
            "service_manager": "systemd",
            "detail": "Service-control status check passed.",
        }

        result = execute_network_control_action(
            action_policy={
                "action_id": "restart_runtime",
                "provider": "service_control",
                "adapter": "service_control",
                "execution": {"wait_seconds": 0},
            },
            target={
                "id": "test_satellite_alpha",
                "control_refs": {
                    "service_control": {
                        "actions": {
                            "restart_runtime": {
                                "host_id": "test_satellite_alpha",
                                "service_name": "runtime",
                            }
                        }
                    }
                },
            },
            service_control_settings={
                "hosts": {
                    "test_satellite_alpha": {
                        "enabled": True,
                        "transport": "ssh",
                        "services": {},
                    }
                }
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_status"], "executed")
        self.assertEqual(mock_service_control.call_args.kwargs["host_id"], "test_satellite_alpha")
        self.assertEqual(mock_service_control.call_args.kwargs["service_name"], "runtime")
        self.assertEqual(mock_service_control.call_args.kwargs["command"], "restart_runtime")
        self.assertEqual(mock_check_available.call_args.kwargs["command"], "restart_runtime")

    @patch("oracle_app.network_control_execution.time.sleep", return_value=None)
    @patch("oracle_app.network_control_execution.check_service_available")
    @patch("oracle_app.network_control_execution.execute_service_command")
    def test_network_control_executor_verifies_service_control_bridge_status(
        self,
        mock_service_control,
        mock_check_available,
        _mock_sleep,
    ) -> None:
        mock_service_control.return_value = {
            "ok": True,
            "status": "executed",
            "service_manager": "systemd",
            "detail": "done",
        }
        mock_check_available.return_value = {
            "ok": True,
            "status": "passed",
            "service_manager": "systemd",
            "detail": "Service-control status check passed.",
        }
        policy = _enabled_plex_restart_policy()["actions"][0]
        policy["provider"] = "service_control"
        policy["adapter"] = "service_control"
        policy["execution"] = {"restart_timeout_seconds": 5, "wait_seconds": 0}

        result = execute_network_control_action(
            action_policy=policy,
            target={
                "id": "caddy",
                "control_refs": {
                    "service_control": {
                        "host_id": "dns_host",
                        "service_name": "caddy",
                    }
                },
            },
            service_control_settings={"hosts": {"dns_host": {"enabled": True, "transport": "ssh", "services": {}}}},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["execution"]["adapter"], "service_control")
        self.assertEqual(result["execution"]["service_manager"], "systemd")
        self.assertEqual(result["execution"]["verification_status"], "passed")
        self.assertIn("availability_check", {step["id"] for step in result["steps"]})
        mock_check_available.assert_called_once()

    @patch("oracle_app.network_control_execution.check_service_available")
    @patch("oracle_app.network_control_execution.execute_service_command")
    @patch(
        "oracle_app.network_control_execution.stage_pending_local_service_restart",
        return_value={"ok": True, "status": "staged"},
    )
    def test_network_control_executor_defers_self_restart_verification(
        self,
        mock_stage,
        mock_service_control,
        mock_check_available,
    ) -> None:
        mock_service_control.return_value = {
            "ok": True,
            "status": "scheduled",
            "service_manager": "systemd",
            "deferred": True,
            "detail": "scheduled",
        }
        policy = _enabled_plex_restart_policy()["actions"][0]
        policy["provider"] = "service_control"
        policy["adapter"] = "service_control"
        policy["execution"] = {"restart_timeout_seconds": 5, "wait_seconds": 0}

        result = execute_network_control_action(
            action_policy=policy,
            target={
                "id": "oracle_brain",
                "control_refs": {
                    "service_control": {
                        "host_id": "oracle_host",
                        "service_name": "oracle_brain",
                    }
                },
            },
            service_control_settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "local",
                        "services": {
                            "oracle_brain": {
                                "adapter": "systemd",
                                "restart_mode": "deferred_self_restart",
                            }
                        },
                    }
                }
            },
            control_context={"request_id": "netctl-brain-restart"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result_status"], "executed")
        self.assertTrue(result["execution"]["deferred"])
        self.assertEqual(result["execution"]["verification_status"], "deferred")
        self.assertIn("availability_check_deferred", {step["id"] for step in result["steps"]})
        mock_stage.assert_called_once()
        mock_check_available.assert_not_called()

    @patch("oracle_app.network_control_execution.execute_service_command")
    @patch(
        "oracle_app.network_control_execution.stage_pending_local_service_restart",
        return_value={
            "ok": False,
            "error": "network_control_local_service_restart_state_unavailable",
            "detail": "state unavailable",
        },
    )
    def test_network_control_executor_does_not_schedule_self_restart_without_state(
        self,
        _mock_stage,
        mock_service_control,
    ) -> None:
        result = execute_network_control_action(
            action_policy={
                "action_id": "restart_service",
                "provider": "service_control",
                "adapter": "service_control",
            },
            target={
                "id": "oracle_brain",
                "control_refs": {
                    "service_control": {
                        "host_id": "oracle_host",
                        "service_name": "oracle_brain",
                    }
                },
            },
            service_control_settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "local",
                        "services": {
                            "oracle_brain": {
                                "adapter": "systemd",
                                "restart_mode": "deferred_self_restart",
                            }
                        },
                    }
                }
            },
            control_context={"request_id": "netctl-brain-restart"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_local_service_restart_state_unavailable")
        mock_service_control.assert_not_called()

    def test_network_control_actions_diagnostics_reports_ready_service_control_action(self) -> None:
        policy = _enabled_plex_restart_policy()
        policy["actions"][0]["provider"] = "service_control"
        policy["actions"][0]["adapter"] = "service_control"

        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
                "services": [
                    {
                        "id": "plex",
                        "display_name": "Plex",
                        "host_id": "oracle_host",
                        "control_refs": {
                            "service_control": {
                                "host_id": "oracle_host",
                                "service_name": "plex",
                            }
                        },
                    }
                ],
                "power_targets": [],
            },
            control_policy=policy,
            service_control_settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "platform": "linux",
                        "services": {
                            "plex": {
                                "adapter": "systemd",
                                "target": "example-media.service",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
        )

        self.assertEqual(payload["summary"]["ready"], 1)
        self.assertTrue(payload["summary"]["all_ready"])
        action = payload["actions"][0]
        self.assertEqual(action["status"], "enabled_unverified")
        self.assertEqual(action["configuration_status"], "ready")
        self.assertTrue(action["target"]["exists"])
        self.assertTrue(action["service_control"]["host_configured"])
        self.assertTrue(action["service_control"]["service_configured"])
        self.assertTrue(action["service_control"]["command_allowed"])
        self.assertEqual(action["service_control"]["transport"], "ssh")
        self.assertNotIn("example-media.service", str(action))

    def test_network_control_actions_diagnostics_reports_power_readiness_coverage(self) -> None:
        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [{"id": "router_main", "display_name": "Router"}],
                "services": [],
                "power_targets": [
                    {
                        "id": "router_main_power",
                        "display_name": "Router Power",
                        "host_id": "router_main",
                        "provider": "home_assistant",
                        "entity_id": "switch.router",
                        "capabilities": ["power_cycle"],
                        "enabled": True,
                        "readiness": {
                            "checks": [
                                {"id": "router", "kind": "host_reachable", "address": "192.0.2.1"},
                                {"id": "internet", "kind": "internet"},
                            ]
                        },
                    }
                ],
            },
            control_policy={
                "actions": [
                    {
                        "id": "router_power_cycle",
                        "target_type": "power_target",
                        "target_id": "router_main_power",
                        "action_id": "power_cycle",
                        "provider": "home_assistant",
                        "adapter": "switch_power_cycle",
                        "requires_confirmation": True,
                        "enabled": True,
                    }
                ]
            },
        )

        self.assertEqual(payload["summary"]["ready"], 1)
        action = payload["actions"][0]
        self.assertTrue(action["power_readiness"]["required"])
        self.assertTrue(action["power_readiness"]["configured"])
        self.assertEqual(action["power_readiness"]["check_count"], 2)
        self.assertNotIn("192.0.2.1", str(action["power_readiness"]))

    def test_network_control_actions_diagnostics_rejects_missing_power_readiness(self) -> None:
        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [{"id": "router_main", "display_name": "Router"}],
                "services": [],
                "power_targets": [
                    {
                        "id": "router_main_power",
                        "host_id": "router_main",
                        "provider": "home_assistant",
                        "entity_id": "switch.router",
                        "capabilities": ["power_cycle"],
                        "enabled": True,
                    }
                ],
            },
            control_policy={
                "actions": [
                    {
                        "id": "router_power_cycle",
                        "target_type": "power_target",
                        "target_id": "router_main_power",
                        "action_id": "power_cycle",
                        "provider": "home_assistant",
                        "adapter": "switch_power_cycle",
                        "requires_confirmation": True,
                        "enabled": True,
                    }
                ]
            },
        )

        self.assertEqual(payload["summary"]["misconfigured"], 1)
        self.assertIn("power_readiness_missing", {item["id"] for item in payload["actions"][0]["issues"]})

    def test_network_control_actions_diagnostics_resolves_host_action_service_control_ref(self) -> None:
        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [
                    {
                        "id": "test_satellite_alpha",
                        "display_name": "Wall Display",
                        "control_refs": {
                            "service_control": {
                                "actions": {
                                    "restart_runtime": {
                                        "host_id": "test_satellite_alpha",
                                        "service_name": "runtime",
                                    }
                                }
                            }
                        },
                    }
                ],
                "services": [],
                "power_targets": [],
            },
            control_policy={
                "actions": [
                    {
                        "id": "wall_display_runtime_restart",
                        "target_type": "host",
                        "target_id": "test_satellite_alpha",
                        "action_id": "restart_runtime",
                        "provider": "service_control",
                        "adapter": "service_control",
                        "requires_confirmation": True,
                        "enabled": True,
                    }
                ]
            },
            service_control_settings={
                "hosts": {
                    "test_satellite_alpha": {
                        "enabled": True,
                        "transport": "ssh",
                        "platform": "linux",
                        "services": {
                            "runtime": {
                                "adapter": "systemd",
                                "target": "oracle-satellite.service",
                                "commands": ["restart_runtime"],
                            }
                        },
                    }
                }
            },
        )

        self.assertEqual(payload["summary"]["ready"], 1)
        action = payload["actions"][0]
        self.assertEqual(action["target_type"], "host")
        self.assertEqual(action["action_id"], "restart_runtime")
        self.assertEqual(action["service_control"]["service_name"], "runtime")
        self.assertTrue(action["service_control"]["command_allowed"])
        self.assertNotIn("oracle-satellite.service", str(action))

    def test_network_control_actions_diagnostics_reports_ready_host_restart(self) -> None:
        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [{"id": "dns_host", "display_name": "DNS Host"}],
                "services": [],
                "power_targets": [],
            },
            control_policy={
                "actions": [
                    {
                        "id": "dns_host_restart",
                        "target_type": "host",
                        "target_id": "dns_host",
                        "action_id": "restart_host",
                        "provider": "service_control",
                        "adapter": "service_control",
                        "requires_confirmation": True,
                        "enabled": True,
                    }
                ]
            },
            service_control_settings={
                "hosts": {
                    "dns_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "platform": "linux",
                        "allowed_actions": {
                            "restart_host": {
                                "enabled": True,
                                "readiness": {"services": ["caddy"]},
                            }
                        },
                    }
                }
            },
        )

        self.assertEqual(payload["summary"]["ready"], 1)
        action = payload["actions"][0]
        self.assertEqual(action["status"], "enabled_unverified")
        self.assertEqual(action["configuration_status"], "ready")
        self.assertEqual(action["service_control"]["bridge_adapter"], "host_restart")
        self.assertTrue(action["service_control"]["command_allowed"])
        self.assertTrue(action["service_control"]["readiness_configured"])
        self.assertEqual(action["service_control"]["readiness_check_count"], 1)

    def test_network_control_actions_diagnostics_reports_missing_bridge_service(self) -> None:
        policy = _enabled_plex_restart_policy()
        policy["actions"][0]["provider"] = "service_control"
        policy["actions"][0]["adapter"] = "service_control"

        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
                "services": [
                    {
                        "id": "plex",
                        "display_name": "Plex",
                        "host_id": "oracle_host",
                        "control_refs": {
                            "service_control": {
                                "host_id": "oracle_host",
                                "service_name": "plex",
                            }
                        },
                    }
                ],
                "power_targets": [],
            },
            control_policy=policy,
            service_control_settings={"hosts": {"oracle_host": {"enabled": True, "transport": "ssh", "services": {}}}},
        )

        self.assertEqual(payload["summary"]["misconfigured"], 1)
        action = payload["actions"][0]
        self.assertEqual(action["status"], "misconfigured")
        self.assertIn("service_control_service_missing", {issue["id"] for issue in action["issues"]})

    def test_network_control_actions_diagnostics_classifies_durable_success_as_verified(self) -> None:
        policy = _enabled_plex_restart_policy()
        policy["actions"][0]["provider"] = "service_control"
        policy["actions"][0]["adapter"] = "service_control"
        verification = {
            ("service", "plex", "restart_service"): {
                "request_id": "netctl-verified",
                "verified_at": "2026-06-09T12:00:00+00:00",
                "verification_status": "passed",
            }
        }

        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
                "services": [
                    {
                        "id": "plex",
                        "display_name": "Plex",
                        "host_id": "oracle_host",
                        "control_refs": {
                            "service_control": {
                                "host_id": "oracle_host",
                                "service_name": "plex",
                            }
                        },
                    }
                ],
                "power_targets": [],
            },
            control_policy=policy,
            service_control_settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "platform": "linux",
                        "services": {
                            "plex": {
                                "adapter": "systemd",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            verification_results=verification,
        )

        self.assertEqual(payload["summary"]["verified"], 1)
        self.assertEqual(payload["summary"]["enabled_unverified"], 0)
        self.assertTrue(payload["summary"]["all_verified"])
        action = payload["actions"][0]
        self.assertEqual(action["status"], "verified")
        self.assertEqual(action["configuration_status"], "ready")
        self.assertEqual(action["verification"]["request_id"], "netctl-verified")

    def test_network_control_verification_history_requires_passed_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "oracle-memory.sqlite3"
            upsert_source(
                source_id="brain",
                source_type="brain",
                display_name="Oracle Brain",
                db_path=db_path,
            )
            outcomes = [
                ("netctl-passed", "executed", "passed", "2026-06-09T01:00:00+00:00"),
                ("netctl-deferred", "executed", "deferred", "2026-06-09T02:00:00+00:00"),
                ("netctl-failed", "failed", "failed", "2026-06-09T03:00:00+00:00"),
                ("netctl-interrupted", "interrupted", "unknown", "2026-06-09T04:00:00+00:00"),
            ]
            for request_id, result_status, verification_status, observed_at in outcomes:
                record_event(
                    "network_control_confirm",
                    observed_at=observed_at,
                    source_id="brain",
                    correlation_id=request_id,
                    domain="network_control",
                    status=result_status,
                    payload={
                        "request_id": request_id,
                        "target_type": "service",
                        "target_id": "plex",
                        "action_id": "restart_service",
                        "result_status": result_status,
                        "execution": {"verification_status": verification_status},
                    },
                    db_path=db_path,
                )

            verification = get_network_control_verification_snapshot(db_path=db_path)

        self.assertEqual(
            verification[("service", "plex", "restart_service")],
            {
                "request_id": "netctl-passed",
                "verified_at": "2026-06-09T01:00:00+00:00",
                "verification_status": "passed",
            },
        )

    def test_network_control_verification_history_requires_router_shutdown_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "oracle-memory.sqlite3"
            upsert_source(
                source_id="brain",
                source_type="brain",
                display_name="Oracle Brain",
                db_path=db_path,
            )
            for request_id, shutdown_observed, observed_at in [
                ("netctl-old-router", None, "2026-06-09T01:00:00+00:00"),
                ("netctl-router-cycle", True, "2026-06-09T02:00:00+00:00"),
            ]:
                execution = {"verification_status": "passed"}
                if shutdown_observed is not None:
                    execution["shutdown_observed"] = shutdown_observed
                record_event(
                    "network_control_confirm",
                    observed_at=observed_at,
                    source_id="brain",
                    correlation_id=request_id,
                    domain="network_control",
                    status="executed",
                    payload={
                        "request_id": request_id,
                        "target_type": "host",
                        "target_id": "router_main",
                        "action_id": "restart_router",
                        "result_status": "executed",
                        "execution": execution,
                    },
                    db_path=db_path,
                )

            verification = get_network_control_verification_snapshot(db_path=db_path)

        self.assertEqual(
            verification[("host", "router_main", "restart_router")]["request_id"],
            "netctl-router-cycle",
        )

    def test_network_control_actions_diagnostics_keeps_disabled_action_non_ready(self) -> None:
        policy = _enabled_plex_restart_policy()
        policy["actions"][0]["enabled"] = False

        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
                "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
                "power_targets": [],
            },
            control_policy=policy,
            service_control_settings={},
        )

        self.assertEqual(payload["summary"]["disabled"], 1)
        self.assertFalse(payload["summary"]["all_ready"])
        self.assertEqual(payload["actions"][0]["status"], "disabled")

    def test_network_control_actions_diagnostics_reports_disabled_router_foundation_safely(self) -> None:
        payload = build_network_control_actions_diagnostics(
            inventory={
                "hosts": [{"id": "router_main", "display_name": "Main Router"}],
                "services": [],
                "power_targets": [],
            },
            control_policy={
                "actions": [
                    {
                        "id": "router_restart",
                        "target_type": "host",
                        "target_id": "router_main",
                        "action_id": "restart_router",
                        "provider": "router_control",
                        "adapter": "router_control",
                        "requires_confirmation": True,
                        "enabled": False,
                    }
                ]
            },
            router_control_settings={
                "routers": {
                    "router_main": {
                        "address": "192.0.2.1",
                        "transport": "ssh",
                        "adapter": "ssh_reboot",
                        "user": "root",
                        "password": "dummy-secret",
                        "enabled": False,
                        "allowed_actions": {"restart_router": {"enabled": False}},
                    }
                }
            },
        )

        self.assertEqual(payload["summary"]["disabled"], 1)
        self.assertEqual(payload["summary"]["misconfigured"], 0)
        action = payload["actions"][0]
        self.assertEqual(action["status"], "disabled")
        self.assertTrue(action["router_control"]["credentials_configured"])
        self.assertNotIn("dummy-secret", str(payload))
        self.assertNotIn("192.0.2.1", str(payload))

    def test_network_admin_payload_attaches_host_control_actions(self) -> None:
        payload = build_network_admin_payload(
            {
                "status": "healthy",
                "severity": "none",
                "freshness": "fresh",
                "generated_at": "2026-05-24T08:00:00-04:00",
                "summary": "No problems are known.",
                "hosts": [{"id": "test_satellite_alpha", "display_name": "Wall Display", "evidence_ids": []}],
                "services": [],
                "service_groups": [],
                "dependencies": [],
                "monitors": [],
                "evidence": [],
            },
            control_policy={
                "actions": [
                    {
                        "id": "wall_display_runtime_restart",
                        "target_type": "host",
                        "target_id": "test_satellite_alpha",
                        "action_id": "restart_runtime",
                        "provider": "service_control",
                        "adapter": "service_control",
                        "requires_confirmation": True,
                        "enabled": False,
                    }
                ]
            },
        )

        host = payload["hosts"][0]
        self.assertEqual(host["control_actions"][0]["action_id"], "restart_runtime")
        self.assertFalse(host["control_actions"][0]["enabled"])
        self.assertNotIn("execution", host["control_actions"][0])

    def test_network_admin_payload_attaches_power_target_action_to_host(self) -> None:
        payload = build_network_admin_payload(
            {
                "status": "healthy",
                "severity": "none",
                "freshness": "fresh",
                "hosts": [{"id": "mesh_node_lounge", "display_name": "Lounge Mesh Node"}],
                "services": [],
                "service_groups": [],
                "power_targets": [
                    {
                        "id": "mesh_node_lounge_power",
                        "display_name": "Lounge Mesh Node Power",
                        "host_id": "mesh_node_lounge",
                        "enabled": True,
                        "capabilities": ["power_cycle"],
                    }
                ],
                "dependencies": [],
                "monitors": [],
                "evidence": [],
            },
            control_policy={
                "actions": [
                    {
                        "id": "mesh_node_lounge_power_cycle",
                        "target_type": "power_target",
                        "target_id": "mesh_node_lounge_power",
                        "action_id": "power_cycle",
                        "provider": "home_assistant",
                        "adapter": "switch_power_cycle",
                        "requires_confirmation": True,
                        "enabled": True,
                    }
                ]
            },
        )

        action = payload["hosts"][0]["control_actions"][0]
        self.assertEqual(action["target_type"], "power_target")
        self.assertEqual(action["target_id"], "mesh_node_lounge_power")
        self.assertEqual(action["action_id"], "power_cycle")

    @patch(
        "oracle_app.admin_network_routes.safe_get_network_control_verification_snapshot",
        return_value={
            ("service", "plex", "restart_service"): {
                "request_id": "netctl-verified",
                "verified_at": "2026-06-09T12:00:00+00:00",
                "verification_status": "passed",
            }
        },
    )
    @patch("oracle_app.admin_network_routes.get_network_service_control_settings")
    @patch("oracle_app.admin_network_routes.get_network_control_policy_settings")
    @patch("oracle_app.admin_network_routes.get_network_inventory_settings")
    def test_admin_network_control_actions_endpoint_returns_coverage(
        self,
        mock_inventory,
        mock_policy,
        mock_service_control,
        _mock_verification,
    ) -> None:
        policy = _enabled_plex_restart_policy()
        policy["actions"][0]["provider"] = "service_control"
        policy["actions"][0]["adapter"] = "service_control"
        mock_inventory.return_value = {
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
            "services": [
                {
                    "id": "plex",
                    "display_name": "Plex",
                    "host_id": "oracle_host",
                    "control_refs": {
                        "service_control": {
                            "host_id": "oracle_host",
                            "service_name": "plex",
                        }
                    },
                }
            ],
            "power_targets": [],
        }
        mock_policy.return_value = policy
        mock_service_control.return_value = {
            "hosts": {
                "oracle_host": {
                    "enabled": True,
                    "transport": "ssh",
                    "services": {"plex": {"adapter": "systemd", "commands": ["restart_service"]}},
                }
            }
        }

        payload = admin_network_control_actions()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["diagnostics"]["summary"]["ready"], 1)
        self.assertEqual(payload["diagnostics"]["summary"]["verified"], 1)

    @patch("oracle_app.admin_network_routes.execute_network_control_action")
    @patch("oracle_app.admin_network_routes.safe_record_event", return_value=True)
    @patch("oracle_app.admin_network_routes.get_network_control_policy_settings", return_value=_enabled_plex_restart_policy())
    @patch(
        "oracle_app.admin_network_routes.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [],
        },
    )
    def test_admin_network_control_dry_run_never_executes_action(
        self,
        _mock_inventory,
        _mock_policy,
        _mock_audit,
        mock_execute,
    ) -> None:
        payload = admin_network_control_dry_run(
            {
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
            }
        )

        self.assertTrue(payload["ok"])
        mock_execute.assert_not_called()

    @patch(
        "oracle_app.admin_network_routes.safe_record_event",
        return_value=True,
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_control_policy_settings",
        return_value={"actions": []},
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [],
        },
    )
    def test_admin_network_control_dry_run_endpoint_returns_plan_shape(self, _mock_inventory, _mock_policy, _mock_audit) -> None:
        payload = admin_network_control_dry_run(
            {
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
            }
        )

        self.assertTrue(payload["ok"])
        control = payload["control"]
        self.assertEqual(control["mode"], "dry_run")
        self.assertEqual(control["target"]["id"], "plex")
        self.assertFalse(control["allowed"])
        _mock_audit.assert_called_once()
        self.assertEqual(_mock_audit.call_args.args[0], "network_control_dry_run")
        self.assertEqual(get_network_control_results_snapshot(), {})

    @patch(
        "oracle_app.admin_network_routes.safe_record_event",
        return_value=True,
    )
    @patch(
        "oracle_app.network_control_preconditions.PlexMusicBridge.get_active_sessions_status",
        return_value={"provider": "plex", "available": True, "active_stream_count": 2, "sessions": []},
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_control_policy_settings",
        return_value=_enabled_plex_restart_policy(),
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [],
        },
    )
    def test_admin_network_control_dry_run_blocks_plex_restart_when_streams_are_active(
        self,
        _mock_inventory,
        _mock_policy,
        _mock_sessions,
        _mock_audit,
    ) -> None:
        payload = admin_network_control_dry_run(
            {
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
            }
        )

        control = payload["control"]
        self.assertFalse(control["allowed"])
        self.assertEqual(control["policy_status"], "blocked")
        self.assertEqual(control["error_class"], "network_control_precondition_failed")
        self.assertEqual(control["preconditions"][0]["id"], "plex_no_active_streams")
        self.assertEqual(control["preconditions"][0]["observed_value"], 2)
        _mock_audit.assert_called_once()

    @patch(
        "oracle_app.admin_network_routes.safe_record_event",
        return_value=True,
    )
    @patch(
        "oracle_app.network_control_preconditions.PlexMusicBridge.get_active_sessions_status",
        return_value={"provider": "plex", "available": True, "active_stream_count": 2, "sessions": []},
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_control_policy_settings",
        return_value={
            "actions": [
                {
                    "id": "plex_restart",
                    "target_type": "service",
                    "target_id": "plex",
                    "action_id": "restart_service",
                    "provider": "service_control",
                    "adapter": "service_control",
                    "requires_confirmation": True,
                    "required_preconditions": ["plex_no_active_streams"],
                    "enabled": True,
                },
                {
                    "id": "oracle_host_restart",
                    "target_type": "host",
                    "target_id": "oracle_host",
                    "action_id": "restart_host",
                    "provider": "service_control",
                    "adapter": "service_control",
                    "requires_confirmation": True,
                    "enabled": True,
                },
            ]
        },
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [],
        },
    )
    def test_admin_network_control_dry_run_blocks_host_restart_on_inherited_plex_precondition(
        self,
        _mock_inventory,
        _mock_policy,
        _mock_sessions,
        _mock_audit,
    ) -> None:
        payload = admin_network_control_dry_run(
            {
                "target_type": "host",
                "target_id": "oracle_host",
                "action_id": "restart_host",
            }
        )

        control = payload["control"]
        self.assertFalse(control["allowed"])
        self.assertEqual(control["policy_status"], "blocked")
        self.assertEqual(control["error_class"], "network_control_precondition_failed")
        self.assertEqual(control["preconditions"][0]["id"], "plex_no_active_streams")
        self.assertEqual(control["preconditions"][0]["observed_value"], 2)

    @patch(
        "oracle_app.admin_network_routes.safe_record_event",
        return_value=True,
    )
    @patch(
        "oracle_app.admin_network_routes.execute_network_control_action",
        return_value={
            "ok": True,
            "result_status": "executed",
            "error_class": "",
            "summary": "Restart completed and verification status is passed.",
            "execution": {
                "method": "systemd",
                "unit": "example-media.service",
                "wait_seconds": 0,
                "verification_status": "passed",
            },
            "steps": [{"id": "restart_sent", "kind": "execution", "summary": "Restart request was sent."}],
        },
    )
    @patch(
        "oracle_app.network_control_preconditions.PlexMusicBridge.get_active_sessions_status",
        return_value={"provider": "plex", "available": True, "active_stream_count": 0, "sessions": []},
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_control_policy_settings",
        return_value=_enabled_plex_restart_policy(),
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [],
        },
    )
    def test_admin_network_control_confirm_executes_allowlisted_adapter_and_audits(
        self,
        _mock_inventory,
        _mock_policy,
        _mock_sessions,
        mock_execute,
        mock_audit,
    ) -> None:
        payload = admin_network_control_confirm(
            {
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
                "confirmed": True,
            }
        )

        control = payload["control"]
        self.assertTrue(control["allowed"])
        self.assertEqual(control["mode"], "execute")
        self.assertEqual(control["confirmation_status"], "confirmed")
        self.assertEqual(control["result_status"], "executed")
        self.assertEqual(control["execution"]["availability_status"], "cooldown")
        self.assertEqual(control["execution"]["cooldown_seconds"], 60)
        self.assertTrue(control["execution"]["cooldown_until"])
        mock_execute.assert_called_once()
        self.assertEqual(mock_audit.call_count, 2)
        self.assertEqual(mock_audit.call_args_list[0].args[0], "network_control_started")
        self.assertEqual(mock_audit.call_args_list[1].args[0], "network_control_confirm")
        self.assertEqual(
            mock_audit.call_args_list[0].kwargs["payload"]["request_id"],
            mock_audit.call_args_list[1].kwargs["payload"]["request_id"],
        )
        self.assertEqual(
            control["request_id"],
            mock_audit.call_args_list[0].kwargs["payload"]["request_id"],
        )
        self.assertEqual(
            mock_audit.call_args_list[0].kwargs["correlation_id"],
            control["request_id"],
        )
        self.assertEqual(
            mock_audit.call_args_list[1].kwargs["correlation_id"],
            control["request_id"],
        )
        audit_kwargs = mock_audit.call_args_list[1].kwargs
        self.assertEqual(audit_kwargs["domain"], "network_control")
        self.assertEqual(audit_kwargs["status"], "executed")
        self.assertEqual(audit_kwargs["payload"]["target_id"], "plex")
        self.assertEqual(audit_kwargs["payload"]["action_id"], "restart_service")
        self.assertEqual(audit_kwargs["payload"]["summary"], "Restart completed and verification status is passed.")
        self.assertEqual(audit_kwargs["payload"]["execution"]["verification_status"], "passed")
        results = get_network_control_results_snapshot()
        self.assertIn(("service", "plex", "restart_service"), results)
        self.assertEqual(results[("service", "plex", "restart_service")]["result_status"], "executed")
        self.assertNotIn("example-media.service", str(results))

    @patch("oracle_app.admin_network_routes.safe_record_event", return_value=True)
    @patch(
        "oracle_app.admin_network_routes.execute_network_control_action",
        return_value={
            "ok": True,
            "result_status": "executed",
            "error_class": "",
            "summary": "Host restarted gracefully.",
            "execution": {"lifecycle_status": "passed", "verification_status": "passed"},
            "steps": [{"id": "execution_completed", "kind": "execution", "summary": "Completed."}],
        },
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_service_control_settings",
        return_value={
            "hosts": {
                "dns_host": {
                    "enabled": True,
                    "allowed_actions": {
                        "restart_host": {
                            "lifecycle": {
                                "mode": "graceful",
                                "prepare_services": ["caddy"],
                            }
                        }
                    },
                    "services": {"caddy": {"adapter": "systemd", "target": "caddy.service"}},
                }
            }
        },
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_control_policy_settings",
        return_value={
            "actions": [
                {
                    "id": "dns_host_restart",
                    "target_type": "host",
                    "target_id": "dns_host",
                    "action_id": "restart_host",
                    "provider": "service_control",
                    "adapter": "service_control",
                    "requires_confirmation": True,
                    "requires_graceful_lifecycle": True,
                    "enabled": True,
                }
            ]
        },
    )
    @patch(
        "oracle_app.admin_network_routes.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "dns_host", "display_name": "DNS Host", "addresses": ["192.0.2.203"]}],
            "services": [],
            "power_targets": [],
        },
    )
    def test_admin_network_control_confirm_preserves_lifecycle_in_completed_result(
        self,
        _mock_inventory,
        _mock_policy,
        _mock_service_control,
        _mock_execute,
        _mock_audit,
    ) -> None:
        payload = admin_network_control_confirm(
            {
                "target_type": "host",
                "target_id": "dns_host",
                "action_id": "restart_host",
                "confirmed": True,
            }
        )

        control = payload["control"]
        self.assertTrue(control["allowed"])
        self.assertEqual(control["result_status"], "executed")
        self.assertEqual(control["lifecycle"]["mode"], "graceful")
        self.assertEqual(control["execution"]["lifecycle_status"], "passed")

    @patch("oracle_app.admin_network_routes.execute_network_control_action")
    @patch("oracle_app.admin_network_routes.safe_record_event", return_value=True)
    @patch("oracle_app.network_control_preconditions.PlexMusicBridge.get_active_sessions_status", return_value={"active_stream_count": 0})
    @patch("oracle_app.admin_network_routes.get_network_control_policy_settings", return_value=_enabled_plex_restart_policy())
    @patch(
        "oracle_app.admin_network_routes.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [],
        },
    )
    def test_admin_network_control_confirm_blocks_while_another_action_is_running(
        self,
        _mock_inventory,
        _mock_policy,
        _mock_sessions,
        mock_audit,
        mock_execute,
    ) -> None:
        lease = acquire_network_control(
            target_type="host",
            target_id="dns_host",
            action_id="restart_host",
        )
        self.assertTrue(lease["acquired"])

        payload = admin_network_control_confirm(
            {
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
                "confirmed": True,
            }
        )

        control = payload["control"]
        self.assertFalse(control["allowed"])
        self.assertEqual(control["policy_status"], "blocked")
        self.assertEqual(control["result_status"], "blocked")
        self.assertEqual(control["error_class"], "network_control_action_in_progress")
        self.assertEqual(control["execution"]["active_target_id"], "dns_host")
        mock_execute.assert_not_called()
        self.assertEqual(mock_audit.call_count, 1)
        self.assertEqual(mock_audit.call_args.args[0], "network_control_confirm")

    @patch("oracle_app.admin_network_routes.execute_network_control_action", side_effect=RuntimeError("secret detail"))
    @patch("oracle_app.admin_network_routes.safe_record_event", return_value=True)
    @patch("oracle_app.network_control_preconditions.PlexMusicBridge.get_active_sessions_status", return_value={"active_stream_count": 0})
    @patch("oracle_app.admin_network_routes.get_network_control_policy_settings", return_value=_enabled_plex_restart_policy())
    @patch(
        "oracle_app.admin_network_routes.get_network_inventory_settings",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Server"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [],
        },
    )
    def test_admin_network_control_confirm_releases_guard_after_unexpected_adapter_failure(
        self,
        _mock_inventory,
        _mock_policy,
        _mock_sessions,
        mock_audit,
        _mock_execute,
    ) -> None:
        payload = admin_network_control_confirm(
            {
                "target_type": "service",
                "target_id": "plex",
                "action_id": "restart_service",
                "confirmed": True,
            }
        )

        control = payload["control"]
        self.assertEqual(control["result_status"], "failed")
        self.assertEqual(control["error_class"], "network_control_execution_failed")
        self.assertNotIn("secret detail", str(control))
        self.assertEqual(control["execution"]["availability_status"], "cooldown")
        self.assertEqual(
            get_network_control_availability(
                target_type="service",
                target_id="plex",
                action_id="restart_service",
            )["status"],
            "cooldown",
        )
        self.assertEqual(mock_audit.call_count, 2)

    def test_network_admin_payload_attaches_last_control_result_to_matching_action_only(self) -> None:
        result = record_network_control_result(
            {
                "request_id": "netctl-test",
                "requested_at": "2026-06-02T20:13:41-04:00",
                "actor": "system_mode",
                "source": "system_mode",
                "target_type": "service",
                "target_id": "caddy",
                "action_id": "restart_service",
                "mode": "execute",
                "provider": "service_control",
                "adapter": "service_control",
                "policy_status": "allowed",
                "confirmation_status": "confirmed",
                "result_status": "executed",
                "error_class": "",
                "summary": "Restart completed through service-control and verification status is passed.",
                "execution": {
                    "adapter": "service_control",
                    "service_manager": "systemd",
                    "verification_status": "passed",
                    "readiness_status": "passed",
                    "readiness_check_count": 4,
                    "readiness_passed_count": 4,
                    "unit": "caddy.service",
                    "stdout": "secret-ish output",
                },
            }
        )

        payload = build_network_admin_payload(
            {
                "status": "healthy",
                "severity": "none",
                "freshness": "fresh",
                "hosts": [{"id": "dns_host", "display_name": "DNS Host", "status": "healthy", "evidence_ids": []}],
                "services": [
                    {"id": "caddy", "display_name": "Caddy", "host_id": "dns_host", "status": "healthy", "evidence_ids": []},
                    {
                        "id": "cloudflare_tunnel",
                        "display_name": "Cloudflare Tunnel",
                        "host_id": "dns_host",
                        "status": "healthy",
                        "evidence_ids": [],
                    },
                ],
                "service_groups": [],
                "monitors": [],
                "evidence": [],
            },
            control_policy={
                "actions": [
                    {
                        "id": "caddy_restart",
                        "target_type": "service",
                        "target_id": "caddy",
                        "action_id": "restart_service",
                        "provider": "service_control",
                        "adapter": "service_control",
                        "requires_confirmation": True,
                        "enabled": True,
                    },
                    {
                        "id": "cloudflare_tunnel_restart",
                        "target_type": "service",
                        "target_id": "cloudflare_tunnel",
                        "action_id": "restart_service",
                        "provider": "service_control",
                        "adapter": "service_control",
                        "requires_confirmation": True,
                        "enabled": True,
                    },
                ]
            },
            control_results={("service", "caddy", "restart_service"): result},
            control_availability={
                ("service", "caddy", "restart_service"): {
                    "status": "cooldown",
                    "cooldown_remaining_seconds": 42,
                    "cooldown_until": "2026-06-08T18:00:00-04:00",
                    "provider_detail": "must not leak",
                },
                ("service", "cloudflare_tunnel", "restart_service"): {
                    "status": "blocked_by_active",
                    "active_target_type": "service",
                    "active_target_id": "caddy",
                    "active_action_id": "restart_service",
                },
            },
        )

        services = {
            service["id"]: service
            for host in payload["hosts"]
            for service in host["services"]
        }
        self.assertEqual(services["caddy"]["last_control_result"]["result_status"], "executed")
        self.assertEqual(services["caddy"]["last_control_result"]["execution"]["readiness_status"], "passed")
        self.assertEqual(services["caddy"]["last_control_result"]["execution"]["readiness_check_count"], 4)
        self.assertEqual(services["caddy"]["control_actions"][0]["last_control_result"]["result_status"], "executed")
        self.assertEqual(services["caddy"]["control_actions"][0]["availability"]["status"], "cooldown")
        self.assertEqual(
            services["cloudflare_tunnel"]["control_actions"][0]["availability"]["status"],
            "blocked_by_active",
        )
        self.assertNotIn("last_control_result", services["cloudflare_tunnel"])
        self.assertNotIn("last_control_result", services["cloudflare_tunnel"]["control_actions"][0])
        self.assertNotIn("caddy.service", str(payload))
        self.assertNotIn("secret-ish", str(payload))
        self.assertNotIn("must not leak", str(payload))

    def test_network_control_results_restore_latest_sanitized_final_event_from_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "oracle-memory.sqlite3"
            base_control = {
                "request_id": "netctl-old",
                "requested_at": "2026-06-08T20:00:00-04:00",
                "actor": "system_mode",
                "source": "system_mode",
                "target_type": "host",
                "target_id": "storage_host",
                "action_id": "restart_host",
                "mode": "execute",
                "provider": "service_control",
                "adapter": "service_control",
                "policy_status": "allowed",
                "confirmation_status": "confirmed",
                "result_status": "failed",
                "error_class": "network_control_host_recovery_failed",
                "summary": "Older result.",
                "execution": {
                    "verification_status": "failed",
                    "lifecycle_status": "rolled_back",
                    "lifecycle_completed_phase_ids": ["release_client_storage"],
                    "unit": "secret.service",
                    "stdout": "secret output",
                },
                "steps": [
                    {
                        "id": "host_preparation_rolled_back",
                        "kind": "rollback",
                        "summary": "Preparation rollback completed.",
                        "command": "secret command",
                    }
                ],
            }
            latest_control = {
                **base_control,
                "request_id": "netctl-latest",
                "result_status": "executed",
                "error_class": "",
                "summary": "Latest result.",
                "execution": {
                    **base_control["execution"],
                    "verification_status": "passed",
                    "lifecycle_status": "passed",
                    "lifecycle_completed_phase_ids": [
                        "release_client_storage",
                        "stop_host_services",
                        "close_host_storage",
                    ],
                },
            }
            record_event(
                "network_control_confirm",
                observed_at="2026-06-09T00:00:00+00:00",
                domain="network_control",
                status="failed",
                correlation_id="netctl-old",
                payload=build_network_control_audit_payload(base_control),
                db_path=db_path,
            )
            record_event(
                "network_control_confirm",
                observed_at="2026-06-09T01:00:00+00:00",
                domain="network_control",
                status="executed",
                correlation_id="netctl-latest",
                payload=build_network_control_audit_payload(latest_control),
                db_path=db_path,
            )

            clear_network_control_results()
            restored_count = restore_network_control_results_from_memory(db_path=db_path)

        self.assertEqual(restored_count, 1)
        restored = get_network_control_results_snapshot()[("host", "storage_host", "restart_host")]
        self.assertEqual(restored["request_id"], "netctl-latest")
        self.assertEqual(restored["recorded_at"], "2026-06-09T01:00:00+00:00")
        self.assertEqual(restored["result_status"], "executed")
        self.assertEqual(restored["execution"]["lifecycle_status"], "passed")
        self.assertEqual(
            restored["execution"]["lifecycle_completed_phase_ids"],
            ["release_client_storage", "stop_host_services", "close_host_storage"],
        )
        self.assertNotIn("secret.service", str(restored))
        self.assertNotIn("secret output", str(restored))
        self.assertNotIn("secret command", str(restored))

    def test_network_control_reconciles_unmatched_start_once_without_retry_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "oracle-memory.sqlite3"
            upsert_source(
                source_id="brain",
                source_type="brain",
                display_name="Oracle Brain",
                db_path=db_path,
            )
            record_event(
                "network_control_started",
                observed_at="2026-06-09T01:00:00+00:00",
                source_id="brain",
                correlation_id="netctl-interrupted",
                provider="service_control",
                domain="network_control",
                status="in_progress",
                payload={
                    "request_id": "netctl-interrupted",
                    "requested_at": "2026-06-09T00:59:59+00:00",
                    "actor": "codex",
                    "source": "system_mode",
                    "target_type": "host",
                    "target_id": "storage_host",
                    "action_id": "restart_host",
                    "mode": "execute",
                    "provider": "service_control",
                    "adapter": "service_control",
                    "policy_status": "allowed",
                    "confirmation_status": "confirmed",
                    "result_status": "in_progress",
                    "summary": "Oracle acquired the network control execution lease.",
                    "execution": {
                        "availability_status": "in_progress",
                        "cooldown_seconds": 300,
                        "cooldown_until": "must-not-survive",
                    },
                    "lifecycle": {
                        "configured": True,
                        "mode": "graceful",
                        "phases": [
                            {
                                "id": "stop_host_services",
                                "kind": "preparation",
                                "summary": "Stop configured host services cleanly.",
                                "command": "secret command",
                            }
                        ],
                    },
                    "steps": [
                        {
                            "id": "stop_host_services",
                            "kind": "preparation",
                            "summary": "Stop configured host services cleanly.",
                            "command": "secret command",
                        }
                    ],
                },
                db_path=db_path,
            )

            first_count = reconcile_interrupted_network_controls(db_path=db_path)
            second_count = reconcile_interrupted_network_controls(db_path=db_path)
            final_events = list_events(db_path=db_path, event_type="network_control_confirm")
            clear_network_control_results()
            restored_count = restore_network_control_results_from_memory(db_path=db_path)

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(len(final_events), 1)
        final_event = final_events[0]
        self.assertEqual(final_event["correlation_id"], "netctl-interrupted")
        self.assertEqual(final_event["status"], "interrupted")
        self.assertEqual(final_event["payload"]["result_status"], "interrupted")
        self.assertEqual(final_event["payload"]["error_class"], "network_control_interrupted_by_restart")
        self.assertEqual(final_event["payload"]["execution"]["verification_status"], "unknown")
        self.assertEqual(final_event["payload"]["execution"]["availability_status"], "ready")
        self.assertEqual(final_event["payload"]["execution"]["lifecycle_status"], "interrupted")
        self.assertNotIn("cooldown_seconds", final_event["payload"]["execution"])
        self.assertNotIn("cooldown_until", final_event["payload"]["execution"])
        self.assertNotIn("secret command", str(final_event))
        self.assertEqual(restored_count, 1)
        restored = get_network_control_results_snapshot()[("host", "storage_host", "restart_host")]
        self.assertEqual(restored["result_status"], "interrupted")
        self.assertEqual(restored["execution"]["availability_status"], "ready")

    def test_network_control_does_not_reconcile_start_with_existing_final_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "oracle-memory.sqlite3"
            upsert_source(
                source_id="brain",
                source_type="brain",
                display_name="Oracle Brain",
                db_path=db_path,
            )
            for event_type, status in (
                ("network_control_started", "in_progress"),
                ("network_control_confirm", "executed"),
            ):
                record_event(
                    event_type,
                    source_id="brain",
                    correlation_id="netctl-complete",
                    domain="network_control",
                    status=status,
                    payload={
                        "request_id": "netctl-complete",
                        "target_type": "service",
                        "target_id": "caddy",
                        "action_id": "restart_service",
                        "result_status": status,
                    },
                    db_path=db_path,
                )

            reconciled_count = reconcile_interrupted_network_controls(db_path=db_path)
            final_events = list_events(db_path=db_path, event_type="network_control_confirm")

        self.assertEqual(reconciled_count, 0)
        self.assertEqual(len(final_events), 1)
        self.assertEqual(final_events[0]["status"], "executed")

    @patch("oracle_app.network.get_network_inventory_settings", return_value={"hosts": [], "services": [], "monitors": []})
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    @patch("oracle_app.network.time.monotonic", side_effect=[100.0, 105.0, 131.0])
    def test_network_status_snapshot_reuses_cache_inside_ttl(
        self,
        _mock_monotonic,
        mock_probe,
        mock_librenms,
        _mock_inventory,
    ) -> None:
        first = get_network_status_snapshot()
        second = get_network_status_snapshot()
        third = get_network_status_snapshot()

        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["cache_age_seconds"], 5.0)
        self.assertFalse(third["cache_hit"])
        self.assertEqual(mock_probe.call_count, 2)
        self.assertEqual(mock_librenms.call_count, 2)

    @patch("oracle_app.network.get_network_inventory_settings", return_value={"hosts": [], "services": [], "monitors": []})
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS reports no active alerts.",
            "problems": [],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "healthy",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Direct network checks succeeded.",
            "problems": [],
        },
    )
    @patch("oracle_app.network.time.monotonic", side_effect=[200.0, 201.0])
    def test_network_status_snapshot_force_refresh_bypasses_cache(
        self,
        _mock_monotonic,
        mock_probe,
        mock_librenms,
        _mock_inventory,
    ) -> None:
        first = get_network_status_snapshot()
        second = get_network_status_snapshot(force_refresh=True)

        self.assertFalse(first["cache_hit"])
        self.assertFalse(second["cache_hit"])
        self.assertEqual(mock_probe.call_count, 2)
        self.assertEqual(mock_librenms.call_count, 2)

    @patch("oracle_app.network.get_network_inventory_settings", return_value={"hosts": [], "services": [], "monitors": []})
    @patch(
        "oracle_app.network.LibreNmsBridge.get_monitoring_status",
        return_value={
            "status": "unknown",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "librenms",
            "detail": "LibreNMS not configured.",
            "problems": [],
        },
    )
    @patch(
        "oracle_app.network.NetworkProbeBridge.get_internet_status",
        return_value={
            "status": "unknown",
            "checked_at": "2026-04-23T20:00:00-04:00",
            "source": "probe",
            "detail": "Network probe is disabled.",
            "problems": [],
        },
    )
    def test_network_status_snapshot_marks_disabled_providers_unconfigured(
        self,
        _mock_probe,
        _mock_librenms,
        _mock_inventory,
    ) -> None:
        snapshot = get_network_status_snapshot()

        self.assertEqual(snapshot["status"], "unconfigured")
        statuses = {item["id"]: item["status"] for item in snapshot["evidence"]}
        self.assertEqual(statuses["probe.internet"], "unconfigured")
        self.assertEqual(statuses["librenms.monitoring"], "unconfigured")

    def test_service_control_rejects_unapproved_action(self) -> None:
        result = execute_service_action(settings={"hosts": {}}, host="storage_host", action="restart_nextcloud")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "service_control_host_not_allowed")

    @patch("oracle_app.provider_bridges.service_control.request.urlopen")
    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_checks_configured_host_readiness(self, mock_run, mock_urlopen) -> None:
        mock_run.return_value.returncode = 0
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true, "has_errors": false}'

        result = check_host_readiness(
            settings={
                "hosts": {
                    "test_satellite_alpha": {
                        "enabled": True,
                        "transport": "ssh",
                        "platform": "linux",
                        "address": "192.0.2.150",
                        "user": "operator",
                        "password": "dummy-password",
                        "allowed_actions": {
                            "restart_host": {
                                "enabled": True,
                                "readiness": {
                                    "services": ["runtime"],
                                    "http_checks": [
                                        {"id": "satellite_config", "url": "http://192.0.2.150:8022/health/config"}
                                    ],
                                },
                            }
                        },
                        "services": {
                            "runtime": {
                                "adapter": "systemd",
                                "target": "oracle-satellite.service",
                                "commands": ["restart_runtime"],
                            }
                        },
                    }
                }
            },
            host_id="test_satellite_alpha",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["check_count"], 2)
        self.assertEqual(result["passed_count"], 2)
        self.assertNotIn("oracle-satellite.service", str(result))

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_reports_failed_host_readiness_check(self, mock_run) -> None:
        mock_run.return_value.returncode = 1

        result = check_host_readiness(
            settings={
                "hosts": {
                    "dns_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "platform": "linux",
                        "address": "192.0.2.203",
                        "user": "operator",
                        "password": "dummy-password",
                        "allowed_actions": {
                            "restart_host": {
                                "enabled": True,
                                "readiness": {"services": ["dns_primary"]},
                            }
                        },
                        "services": {
                            "dns_primary": {
                                "adapter": "systemd",
                                "target": "example-filter.service",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            host_id="dns_host",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_check_ids"], ["dns_primary"])
        self.assertNotIn("example-filter.service", str(result))

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_host_readiness_requires_read_write_mount(self, mock_run) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="/srv/example-storage\n"),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]

        result = check_host_readiness(
            settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "local",
                        "platform": "linux",
                        "allowed_actions": {
                            "restart_host": {
                                "enabled": True,
                                "readiness": {"read_write_mounts": ["/srv/example-storage"]},
                            }
                        },
                        "services": {},
                    }
                }
            },
            host_id="oracle_host",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["check_count"], 1)
        self.assertEqual(result["checks"], [{"id": "mount:/srv/example-storage", "kind": "mount", "status": "passed"}])

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_host_readiness_rejects_mount_when_write_probe_fails(self, mock_run) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="/srv/example-storage\n"),
            subprocess.CompletedProcess(args=[], returncode=1),
        ]

        result = check_host_readiness(
            settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "local",
                        "platform": "linux",
                        "allowed_actions": {
                            "restart_host": {
                                "enabled": True,
                                "readiness": {"read_write_mounts": ["/srv/example-storage"]},
                            }
                        },
                        "services": {},
                    }
                }
            },
            host_id="oracle_host",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_check_ids"], ["mount:/srv/example-storage"])

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_storage_safety_checks_fixed_read_only_state(self, mock_run) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="md0 : active raid5 sda1[0] sdb1[1] sdc1[2]\n      100 blocks [3/3] [UUU]\n",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="/dev/md0 /srv/example-storage rw,relatime\n",
            ),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]

        result = check_storage_safety(
            settings={
                "hosts": {
                    "storage_host": {
                        "enabled": True,
                        "transport": "local",
                        "allowed_actions": {
                            "restart_host": {
                                "preconditions": {
                                    "host_storage_safe_for_restart": {
                                        "kind": "linux_storage",
                                        "array": "md0",
                                        "mount": "/srv/example-storage",
                                        "service": "network_storage",
                                    }
                                }
                            }
                        },
                        "services": {
                            "network_storage": {
                                "adapter": "systemd",
                                "target": "nfs-server.service",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            host_id="storage_host",
            profile_id="host_storage_safe_for_restart",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["passed_count"], 3)
        self.assertEqual(mock_run.call_args_list[0].args[0], ["cat", "/proc/mdstat"])
        self.assertEqual(
            mock_run.call_args_list[1].args[0],
            ["findmnt", "-rn", "-o", "SOURCE,TARGET,OPTIONS", "/srv/example-storage"],
        )
        self.assertEqual(
            mock_run.call_args_list[2].args[0],
            ["sudo", "-n", "systemctl", "is-active", "--quiet", "nfs-server.service"],
        )
        self.assertNotIn("md0", str(result))
        self.assertNotIn("/srv/example-storage", str(result))
        self.assertNotIn("nfs-server.service", str(result))

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_storage_safety_rejects_degraded_array(self, mock_run) -> None:
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="md0 : active raid5 sda1[0] sdb1[1]\n      100 blocks [3/2] [UU_]\n",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="/dev/md0 /srv/example-storage rw,relatime\n",
            ),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]

        result = check_storage_safety(
            settings={
                "hosts": {
                    "storage_host": {
                        "enabled": True,
                        "transport": "local",
                        "allowed_actions": {
                            "restart_host": {
                                "preconditions": {
                                    "host_storage_safe_for_restart": {
                                        "kind": "linux_storage",
                                        "array": "md0",
                                        "mount": "/srv/example-storage",
                                        "service": "network_storage",
                                    }
                                }
                            }
                        },
                        "services": {
                            "network_storage": {
                                "adapter": "systemd",
                                "target": "nfs-server.service",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            host_id="storage_host",
            profile_id="host_storage_safe_for_restart",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_check_ids"], ["raid"])

    def test_service_control_builds_safe_graceful_lifecycle_plan(self) -> None:
        settings = {
            "hosts": {
                "dns_host": {
                    "enabled": True,
                    "allowed_actions": {
                        "restart_host": {
                            "lifecycle": {
                                "mode": "graceful",
                                "prepare_services": ["caddy", "dns_primary"],
                            }
                        }
                    },
                    "services": {
                        "caddy": {"adapter": "systemd", "target": "caddy.service"},
                        "dns_primary": {"adapter": "systemd", "target": "example-filter.service"},
                    },
                }
            }
        }

        plan = get_host_restart_lifecycle_plan(settings=settings, host_id="dns_host")

        self.assertTrue(plan["configured"])
        self.assertEqual(plan["mode"], "graceful")
        self.assertEqual(
            [phase["id"] for phase in plan["phases"]],
            ["stop_host_services", "restart_host", "verify_host_recovery"],
        )
        self.assertNotIn("caddy.service", str(plan))
        self.assertNotIn("example-filter.service", str(plan))

    @patch("oracle_app.provider_bridges.service_control._configured_target_has_state", return_value=True)
    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_prepares_storage_host_storage_before_reboot(self, mock_run, _mock_state) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        settings = {
            "hosts": {
                "storage_host": {
                    "enabled": True,
                    "transport": "ssh",
                    "address": "192.0.2.200",
                    "user": "operator",
                    "password": "dummy-password",
                    "allowed_actions": {
                        "restart_host": {
                            "lifecycle": {
                                "mode": "graceful",
                                "prepare_services": ["mqtt"],
                                "client_release": {
                                    "host_id": "oracle_host",
                                    "mount": "/srv/example-storage",
                                    "mount_service": "example-storage-mount.service",
                                    "services": ["nextcloud"],
                                },
                                "storage": {
                                    "array": "md0",
                                    "mount": "/srv/example-storage",
                                    "sharing_service": "network_storage",
                                },
                            }
                        }
                    },
                    "services": {
                        "mqtt": {"adapter": "docker", "target": "mosquitto"},
                        "network_storage": {"adapter": "systemd", "target": "nfs-server.service"},
                    },
                },
                "oracle_host": {
                    "enabled": True,
                    "transport": "local",
                    "services": {
                        "nextcloud": {
                            "adapter": "docker",
                            "target": "nextcloud-app",
                            "lifecycle_targets": ["nextcloud-cron"],
                        }
                    },
                },
            }
        }

        result = prepare_host_restart(settings=settings, host_id="storage_host")

        self.assertTrue(result["ok"])
        commands = [call.args[0] for call in mock_run.call_args_list]
        flattened = [" ".join(command) for command in commands]
        self.assertLess(
            next(index for index, command in enumerate(flattened) if "docker stop nextcloud-cron" in command),
            next(index for index, command in enumerate(flattened) if "umount /srv/example-storage" in command),
        )
        self.assertLess(
            next(index for index, command in enumerate(flattened) if "systemctl stop nfs-server.service" in command),
            next(index for index, command in enumerate(flattened) if "mdadm --stop /dev/md0" in command),
        )
        self.assertNotIn("dummy-password", str(result))

    @patch("oracle_app.provider_bridges.service_control._set_configured_services_state")
    @patch("oracle_app.provider_bridges.service_control._run_fixed_host_command")
    def test_service_control_remounts_recovered_client_storage_read_write(
        self,
        mock_run,
        mock_set_state,
    ) -> None:
        mock_set_state.return_value = {"ok": True}
        mock_run.side_effect = [
            {"ok": True},
            {"ok": True, "stdout": "192.0.2.200:/ /srv/example-storage ro,nosuid,nodev,noatime\n"},
            {"ok": True},
            {"ok": True, "stdout": "192.0.2.200:/ /srv/example-storage rw,nosuid,nodev,noatime\n"},
        ]
        settings = {
            "hosts": {
                "storage_host": {
                    "allowed_actions": {
                        "restart_host": {
                            "lifecycle": {
                                "mode": "graceful",
                                "client_release": {
                                    "host_id": "oracle_host",
                                    "mount": "/srv/example-storage",
                                    "mount_service": "example-storage-mount.service",
                                    "services": ["nextcloud"],
                                },
                            }
                        }
                    },
                    "services": {},
                },
                "oracle_host": {
                    "services": {
                        "nextcloud": {"adapter": "docker", "target": "nextcloud"},
                    }
                },
            }
        }

        result = recover_host_restart_dependents(settings=settings, host_id="storage_host")

        self.assertTrue(result["ok"])
        commands = [call.kwargs["command_argv"] for call in mock_run.call_args_list]
        self.assertIn(
            [
                "sudo",
                "-S",
                "-p",
                "oracle-sudo-prompt:",
                "--",
                "mount",
                "-o",
                "remount,rw",
                "/srv/example-storage",
            ],
            commands,
        )

    @patch("oracle_app.network_control_execution.execute_service_action")
    @patch("oracle_app.network_control_execution.prepare_host_restart")
    def test_network_control_host_restart_never_reboots_after_preparation_failure(
        self,
        mock_prepare,
        mock_restart,
    ) -> None:
        mock_prepare.return_value = {
            "ok": False,
            "error": "service_control_storage_close_failed",
            "detail": "Storage remained busy and could not be unmounted.",
            "completed_phase_ids": ["release_client_storage", "stop_host_services"],
        }

        result = execute_network_control_action(
            action_policy={
                "action_id": "restart_host",
                "adapter": "service_control",
                "requires_graceful_lifecycle": True,
            },
            target={"id": "storage_host", "addresses": ["192.0.2.200"]},
            service_control_settings={"hosts": {}},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["result_status"], "blocked")
        self.assertEqual(result["error_class"], "service_control_storage_close_failed")
        mock_restart.assert_not_called()

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_restarts_linux_host_with_fixed_command(self, mock_run) -> None:
        mock_run.return_value.returncode = 0

        result = execute_service_action(
            settings={
                "hosts": {
                    "dns_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "platform": "linux",
                        "address": "192.0.2.203",
                        "user": "operator",
                        "password": "dummy-password",
                        "allowed_actions": {"restart_host": {"enabled": True}},
                    }
                }
            },
            host="dns_host",
            action="restart_host",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            mock_run.call_args.args[0],
            self._ssh_remote(
                "192.0.2.203",
                ["sudo", "-S", "-p", "oracle-sudo-prompt:", "--", "reboot"],
            ),
        )
        self.assertEqual(mock_run.call_args.kwargs["env"]["SSHPASS"], "dummy-password")
        self.assertNotIn("dummy-password", mock_run.call_args.args[0])

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_restarts_windows_host_with_fixed_command(self, mock_run) -> None:
        mock_run.return_value.returncode = 0

        result = execute_service_action(
            settings={
                "hosts": {
                    "desktop_satellite_109": {
                        "enabled": True,
                        "transport": "ssh",
                        "platform": "windows",
                        "address": "192.0.2.209",
                        "user": "operator",
                        "password": "dummy-password",
                        "allowed_actions": {"restart_host": {"enabled": True}},
                    }
                }
            },
            host="desktop_satellite_109",
            action="restart_host",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            mock_run.call_args.args[0],
            self._ssh_remote("192.0.2.209", ["shutdown.exe", "/r", "/t", "0", "/f"]),
        )

    @patch("oracle_app.provider_bridges.service_control.subprocess.Popen")
    def test_service_control_schedules_deferred_local_host_restart(self, mock_popen) -> None:
        result = execute_service_action(
            settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "local",
                        "platform": "linux",
                        "allowed_actions": {"restart_host": {"enabled": True}},
                    }
                }
            },
            host="oracle_host",
            action="restart_host",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["deferred"])
        mock_popen.assert_called_once()

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_sends_systemd_restart_through_ssh_bridge(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = execute_service_command(
            settings={
                "hosts": {
                    "dns_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.203",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "caddy": {
                                "adapter": "systemd",
                                "target": "caddy.service",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            host_id="dns_host",
            service_name="caddy",
            command="restart_service",
        )

        self.assertTrue(result["ok"])
        mock_run.assert_called_once()
        self.assertEqual(
            mock_run.call_args.args[0],
            self._ssh_remote(
                "192.0.2.203",
                [
                    "sudo", "-S", "-p", "oracle-sudo-prompt:", "--",
                    "systemctl", "restart", "caddy.service",
                ],
            ),
        )
        self.assertEqual(mock_run.call_args.kwargs["input"], "dummy-password\n")

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_sends_satellite_runtime_restart_through_ssh_bridge(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = execute_service_command(
            settings={
                "hosts": {
                    "test_satellite_alpha": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.150",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "runtime": {
                                "adapter": "systemd",
                                "target": "oracle-satellite.service",
                                "commands": ["restart_runtime"],
                            }
                        },
                    }
                }
            },
            host_id="test_satellite_alpha",
            service_name="runtime",
            command="restart_runtime",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            mock_run.call_args.args[0],
            self._ssh_remote(
                "192.0.2.150",
                [
                    "sudo", "-S", "-p", "oracle-sudo-prompt:", "--",
                    "systemctl", "restart", "oracle-satellite.service",
                ],
            ),
        )

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_restarts_windows_scheduled_task_through_ssh_bridge(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = execute_service_command(
            settings={
                "hosts": {
                    "test_windows_satellite": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.211",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "runtime": {
                                "adapter": "windows_scheduled_task",
                                "target": "OracleSurfaceSatelliteRuntime",
                                "commands": ["restart_runtime"],
                            }
                        },
                    }
                }
            },
            host_id="test_windows_satellite",
            service_name="runtime",
            command="restart_runtime",
        )

        self.assertTrue(result["ok"])
        remote_command = mock_run.call_args.args[0][-1]
        self.assertEqual(
            mock_run.call_args.args[0][:-1],
            ["sshpass", "-e", "ssh", *self._strict_ssh_options(), "operator@192.0.2.211"],
        )
        self.assertIn("powershell.exe", remote_command)
        self.assertIn("Stop-ScheduledTask", remote_command)
        self.assertIn("Start-ScheduledTask", remote_command)
        self.assertIn("OracleSurfaceSatelliteRuntime", remote_command)

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_checks_windows_ui_last_result(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = check_service_available(
            settings={
                "hosts": {
                    "test_windows_satellite": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.211",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "ui": {
                                "adapter": "windows_scheduled_task",
                                "target": "OracleSurfaceSatelliteUI",
                                "commands": ["restart_ui"],
                                "restart_mode": "restart_edge_kiosk",
                                "verification_mode": "edge_running",
                            }
                        },
                    }
                }
            },
            host_id="test_windows_satellite",
            service_name="ui",
            command="restart_ui",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["service_manager"], "windows_scheduled_task")
        self.assertIn("Get-Process msedge", mock_run.call_args.args[0][-1])
        self.assertIn("powershell.exe", mock_run.call_args.args[0][-1])

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_windows_kiosk_restart_stops_edge_before_task(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = execute_service_command(
            settings={
                "hosts": {
                    "test_windows_satellite": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.211",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "ui": {
                                "adapter": "windows_scheduled_task",
                                "target": "OracleSurfaceSatelliteUI",
                                "commands": ["restart_ui"],
                                "restart_mode": "restart_edge_kiosk",
                            }
                        },
                    }
                }
            },
            host_id="test_windows_satellite",
            service_name="ui",
            command="restart_ui",
        )

        self.assertTrue(result["ok"])
        remote_command = mock_run.call_args.args[0][-1]
        self.assertIn("Get-Process msedge", remote_command)
        self.assertIn("Stop-Process -Force", remote_command)
        self.assertIn("schtasks.exe /Run", remote_command)
        self.assertIn("/I", remote_command)
        self.assertLess(remote_command.index("Stop-Process"), remote_command.index("schtasks.exe"))

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_windows_direct_edge_task_restarts_without_stopping_task(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = execute_service_command(
            settings={
                "hosts": {
                    "desktop_satellite_109": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.209",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "ui": {
                                "adapter": "windows_scheduled_task",
                                "target": "OracleSurfaceSatelliteUI",
                                "commands": ["restart_ui"],
                                "restart_mode": "restart_edge_task",
                            }
                        },
                    }
                }
            },
            host_id="desktop_satellite_109",
            service_name="ui",
            command="restart_ui",
        )

        self.assertTrue(result["ok"])
        remote_command = mock_run.call_args.args[0][-1]
        self.assertIn("Get-Process msedge", remote_command)
        self.assertIn("Stop-Process -Force", remote_command)
        self.assertIn("Start-ScheduledTask", remote_command)
        self.assertNotIn("Stop-ScheduledTask", remote_command)
        self.assertNotIn("schtasks.exe /Run", remote_command)

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    @patch("oracle_app.provider_bridges.service_control.subprocess.Popen")
    def test_service_control_schedules_deferred_local_systemd_restart(self, mock_popen, mock_run) -> None:
        result = execute_service_command(
            settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "local",
                        "services": {
                            "oracle_brain": {
                                "adapter": "systemd",
                                "target": "oracle-brain.service",
                                "commands": ["restart_service"],
                                "restart_mode": "deferred_self_restart",
                                "deferred_delay_seconds": 3,
                            }
                        },
                    }
                }
            },
            host_id="oracle_host",
            service_name="oracle_brain",
            command="restart_service",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "scheduled")
        self.assertTrue(result["deferred"])
        self.assertEqual(result["service_manager"], "systemd")
        mock_run.assert_not_called()
        mock_popen.assert_called_once()
        argv = mock_popen.call_args.args[0]
        self.assertEqual(argv[1], "-c")
        self.assertEqual(argv[-2:], ["3", "oracle-brain.service"])

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_sends_docker_restart_through_ssh_bridge(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = execute_service_command(
            settings={
                "hosts": {
                    "storage_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.200",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "home_assistant": {
                                "adapter": "docker",
                                "target": "homeassistant",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            host_id="storage_host",
            service_name="home_assistant",
            command="restart_service",
        )

        self.assertTrue(result["ok"])
        mock_run.assert_called_once()
        self.assertEqual(
            mock_run.call_args.args[0],
            self._ssh_remote(
                "192.0.2.200",
                ["docker", "restart", "homeassistant"],
            ),
        )
        self.assertIsNone(mock_run.call_args.kwargs["input"])

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_checks_systemd_status_through_ssh_bridge(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        result = check_service_available(
            settings={
                "hosts": {
                    "dns_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.203",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "tailscale": {
                                "adapter": "systemd",
                                "target": "tailscaled.service",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            host_id="dns_host",
            service_name="tailscale",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["service_manager"], "systemd")
        self.assertEqual(
            mock_run.call_args.args[0],
            self._ssh_remote(
                "192.0.2.203",
                [
                    "sudo", "-S", "-p", "oracle-sudo-prompt:", "--",
                    "systemctl", "is-active", "--quiet", "tailscaled.service",
                ],
            ),
        )
        self.assertEqual(mock_run.call_args.kwargs["input"], "dummy-password\n")

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_distinguishes_inactive_systemd_service(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=3)

        result = check_service_available(
            settings={
                "hosts": {
                    "oracle_host": {
                        "enabled": True,
                        "transport": "local",
                        "services": {
                            "dns_secondary": {
                                "adapter": "systemd",
                                "target": "example-filter.service",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            host_id="oracle_host",
            service_name="dns_secondary",
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["available"])
        self.assertNotIn("example-filter.service", str(result))

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_service_control_checks_docker_status_through_ssh_bridge(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="true\n")

        result = check_service_available(
            settings={
                "hosts": {
                    "storage_host": {
                        "enabled": True,
                        "transport": "ssh",
                        "address": "192.0.2.200",
                        "user": "operator",
                        "password": "dummy-password",
                        "services": {
                            "home_assistant": {
                                "adapter": "docker",
                                "target": "homeassistant",
                                "commands": ["restart_service"],
                            }
                        },
                    }
                }
            },
            host_id="storage_host",
            service_name="home_assistant",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["service_manager"], "docker")
        self.assertEqual(
            mock_run.call_args.args[0],
            self._ssh_remote(
                "192.0.2.200",
                ["docker", "inspect", "-f", "{{.State.Running}}", "homeassistant"],
            ),
        )
        self.assertIsNone(mock_run.call_args.kwargs["input"])

    def test_router_control_rejects_unapproved_action(self) -> None:
        result = execute_router_action(settings={"routers": {}}, router="main", action="restart_router")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "router_control_router_not_allowed")

    def test_router_control_rejects_disabled_router(self) -> None:
        result = execute_router_action(
            settings={
                "routers": {
                    "router_main": {
                        "enabled": False,
                        "allowed_actions": {"restart_router": {"enabled": False}},
                    }
                }
            },
            router="router_main",
            action="restart_router",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "router_control_router_disabled")

    @patch.dict("os.environ", {"TEST_ROUTER_PASSWORD": "dummy-password"})
    @patch("oracle_app.provider_bridges.router_control.subprocess.run")
    def test_router_control_uses_fixed_reboot_command_without_password_in_argv(self, mock_run) -> None:
        mock_run.return_value.returncode = 0

        result = execute_router_action(
            settings={
                "routers": {
                    "router_main": {
                        "address": "router.local",
                        "transport": "ssh",
                        "adapter": "ssh_reboot",
                        "user": "router-admin",
                        "password_env": "TEST_ROUTER_PASSWORD",
                        "enabled": True,
                        "allowed_actions": {"restart_router": {"enabled": True}},
                    }
                }
            },
            router="router_main",
            action="restart_router",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            mock_run.call_args.args[0],
            [
                "sshpass",
                "-e",
                "ssh",
                *self._strict_ssh_options(),
                "router-admin@router.local",
                "reboot",
            ],
        )
        self.assertEqual(mock_run.call_args.kwargs["env"]["SSHPASS"], "dummy-password")
        self.assertNotIn("dummy-password", mock_run.call_args.args[0])

    @patch(
        "oracle_app.handlers.network.build_network_response",
        return_value=(
            "The internet appears to be down.",
            {
                "status": "down",
                "internet": {"status": "down"},
                "monitoring": {"status": "unknown"},
                "problems": ["HTTP reachability failed."],
                "actions_available": [],
                "generated_at": "2026-04-23T20:00:00-04:00",
            },
        ),
    )
    def test_voice_query_routes_to_network_and_returns_short_reply(self, _mock_response) -> None:
        route = choose_route(
            "is the internet down?",
            registry=_NEUTRAL_ROUTE_REGISTRY,
            household_settings=_NEUTRAL_RUNTIME.household,
        )
        dispatch = build_dispatch_plan(CommandRequest(text="is the internet down?"), route)
        result = execute_dispatch(dispatch, registry=build_dispatch_registry())
        reply = build_reply_text(result)

        self.assertEqual(route.target, "network")
        self.assertEqual(result.status, "executed")
        self.assertEqual(reply, "The internet appears to be down.")

    @patch(
        "oracle_app.network.get_network_summary",
        return_value={
            "status": "healthy",
            "internet": {"status": "healthy", "detail": "Direct network checks succeeded."},
            "monitoring": {"status": "unknown", "detail": "LibreNMS not configured."},
            "problems": [],
            "actions_available": [],
            "generated_at": "2026-04-23T20:00:00-04:00",
        },
    )
    def test_ui_network_health_snapshot_returns_summary_block(self, _mock_summary) -> None:
        payload = build_ui_network_health_snapshot()

        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["label"], "Network")
        self.assertEqual(payload["summary"], "The network looks healthy.")
