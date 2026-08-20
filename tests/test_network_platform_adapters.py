from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from oracle_app.configuration.domain_models import RouterControlAdapter, ServiceControlAdapter
from oracle_app.network_runtime.platform_adapters import (
    PlatformActionOutcome,
    RouterPlatformAdapter,
    ServicePlatformAdapter,
    raid_array_healthy,
)
from oracle_app.network_runtime.platform_transport import CommandOutcome


class NetworkPlatformAdapterTests(unittest.TestCase):
    def test_systemd_restart_and_status_are_finite_platform_commands(self) -> None:
        platform = ServicePlatformAdapter(self._service(service_adapter="systemd", service_target="oracle.service"), None)
        platform.transport = Mock()
        platform.transport.run.side_effect = [
            CommandOutcome(True, 0),
            CommandOutcome(True, 0),
        ]

        restarted = platform.restart("restart_service")
        available = platform.available()

        self.assertIsInstance(restarted, PlatformActionOutcome)
        self.assertTrue(restarted.ok)
        self.assertTrue(available.ok)
        self.assertEqual(platform.transport.run.call_args_list[0].args[0][-3:], ["systemctl", "restart", "oracle.service"])
        self.assertEqual(platform.transport.run.call_args_list[1].args[0][-4:], ["systemctl", "is-active", "--quiet", "oracle.service"])

    def test_windows_kiosk_restart_and_edge_verification_stay_inside_adapter(self) -> None:
        platform = ServicePlatformAdapter(
            self._service(
                platform="windows",
                service_adapter="windows_scheduled_task",
                service_target="Oracle UI",
                restart_mode="restart_edge_kiosk",
                verification_mode="edge_running",
            ),
            "secret",
        )
        platform.transport = Mock()
        platform.transport.run.side_effect = [CommandOutcome(True, 0), CommandOutcome(True, 0)]

        self.assertTrue(platform.restart("restart_service").ok)
        self.assertTrue(platform.available().ok)

        restart_command = platform.transport.run.call_args_list[0].args[0][0]
        status_command = platform.transport.run.call_args_list[1].args[0][0]
        self.assertIn("schtasks.exe /Run", restart_command)
        self.assertIn("Get-Process msedge", restart_command)
        self.assertIn("Get-Process msedge", status_command)

    def test_remote_linux_host_restart_accepts_ssh_disconnect_code(self) -> None:
        definition = ServiceControlAdapter(
            type="service_control", target_kind="host", host_id="server",
            transport="ssh", platform="linux", address="192.0.2.10", user="oracle",
            password_secret="HOST_PASSWORD",
        )
        platform = ServicePlatformAdapter(definition, "secret")
        platform.transport = Mock()
        platform.transport.run.return_value = CommandOutcome(True, 255)

        outcome = platform.restart("restart_host")

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "restart_sent")
        self.assertEqual(platform.transport.run.call_args.args[0][-1], "reboot")

    def test_router_timeout_preserves_restart_sent_semantics(self) -> None:
        definition = RouterControlAdapter(
            type="router_control", host_id="router", address="192.0.2.1",
            user="admin", password_secret="ROUTER_PASSWORD", mechanism="ssh_reboot",
        )
        platform = RouterPlatformAdapter(definition, "secret")
        platform.transport = Mock()
        platform.transport.run.return_value = CommandOutcome(False, timed_out=True)

        outcome = platform.restart("restart_router")

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.status, "restart_sent")

    def test_missing_ssh_credential_preserves_fail_closed_error_classes(self) -> None:
        service = ServicePlatformAdapter(
            self._service(transport="ssh", address="192.0.2.10", user="oracle", password_secret="HOST_PASSWORD"),
            None,
        )
        router_definition = RouterControlAdapter(
            type="router_control", host_id="router", address="192.0.2.1",
            user="admin", password_secret="ROUTER_PASSWORD", mechanism="ssh_reboot",
        )

        self.assertEqual(service.restart("restart_service").error, "service_control_transport_not_configured")
        self.assertEqual(service.available().error, "service_control_transport_not_configured")
        self.assertEqual(RouterPlatformAdapter(router_definition, "").restart("restart_router").error, "router_control_credentials_missing")

    def test_raid_health_requires_active_array_without_missing_members(self) -> None:
        healthy = "md0 : active raid1 sda1[0] sdb1[1]\n  100 blocks [2/2] [UU]\n"
        degraded = "md0 : active raid1 sda1[0]\n  100 blocks [2/1] [U_]\n"
        self.assertTrue(raid_array_healthy(healthy, array_id="md0"))
        self.assertFalse(raid_array_healthy(degraded, array_id="md0"))

    def test_storage_and_mount_operations_are_adapter_owned(self) -> None:
        definition = ServiceControlAdapter(
            type="service_control", target_kind="host", host_id="storage",
            transport="local", platform="linux",
        )
        platform = ServicePlatformAdapter(definition, None)
        platform.transport = Mock()
        platform.transport.run.return_value = CommandOutcome(True, 0)

        self.assertTrue(platform.flush_writes(timeout_seconds=10))
        self.assertTrue(platform.unmount("/srv/storage", timeout_seconds=10))
        self.assertTrue(platform.stop_raid("md0", timeout_seconds=10))
        self.assertTrue(platform.assemble_raid("md0", timeout_seconds=10))
        self.assertTrue(platform.mount("/srv/storage", timeout_seconds=10))

        commands = [call.args[0][-3:] for call in platform.transport.run.call_args_list]
        self.assertIn(["mdadm", "--stop", "/dev/md0"], commands)
        self.assertIn(["mdadm", "--assemble", "/dev/md0"], commands)

    @patch("oracle_app.network_runtime.platform_adapters.subprocess.Popen")
    def test_local_deferred_host_restart_returns_typed_scheduled_outcome(self, popen) -> None:
        definition = ServiceControlAdapter(
            type="service_control", target_kind="host", host_id="oracle_host",
            transport="local", platform="linux",
        )

        outcome = ServicePlatformAdapter(definition, None).restart("restart_host")

        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.deferred)
        self.assertEqual(outcome.status, "scheduled")
        popen.assert_called_once()

    def test_domain_lifecycle_contains_no_platform_command_or_subprocess_mechanics(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "server" / "oracle_app" / "network_runtime" / "service_control.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "sshpass", "systemctl", "docker", "mdadm", "findmnt", "powershell.exe"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_canonical_control_has_no_dictionary_shaped_bridge_executor(self) -> None:
        root = Path(__file__).resolve().parents[1]
        canonical = (root / "server" / "oracle_app" / "network_runtime" / "control.py").read_text(encoding="utf-8")
        legacy_service = (root / "server" / "oracle_app" / "provider_bridges" / "service_control.py").read_text(encoding="utf-8")
        legacy_router = (root / "server" / "oracle_app" / "provider_bridges" / "router_control.py").read_text(encoding="utf-8")
        self.assertNotIn("provider_bridges.service_control", canonical)
        self.assertNotIn("provider_bridges.router_control", canonical)
        self.assertNotIn("def execute_typed_service_action", legacy_service)
        self.assertNotIn("def check_typed_service_available", legacy_service)
        self.assertNotIn("def execute_typed_router_action", legacy_router)

    @staticmethod
    def _service(**overrides) -> ServiceControlAdapter:
        values = {
            "type": "service_control",
            "target_kind": "service",
            "host_id": "oracle_host",
            "transport": "local",
            "platform": "linux",
            "service_target": "oracle.service",
            "service_adapter": "systemd",
        }
        values.update(overrides)
        if values["transport"] == "ssh":
            values.update(address="192.0.2.10", user="oracle", password_secret="HOST_PASSWORD")
        return ServiceControlAdapter(**values)


if __name__ == "__main__":
    unittest.main()
