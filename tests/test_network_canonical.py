from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import yaml

from fastapi import FastAPI, Request

from oracle_app.admin_network_routes import admin_network_status_canonical, admin_network_status_http
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.configuration import (
    BrainEffectiveRuntimeSettings,
    EffectiveConfig,
    inspect_candidate,
)
from oracle_app.health_routes import health_librenms_http
from oracle_app.configuration.domain_models import ServiceControlAdapter
from oracle_app.network import build_network_response, build_ui_network_health_snapshot
from oracle_app.network_runtime import CanonicalNetworkExecution
from oracle_app.network_runtime.control import _execute_host, _preconditions
from oracle_app.network_runtime.service_control import TypedServiceControl
from oracle_app.provider_bridges.network_observations import (
    NetworkMonitoringObservation,
    NetworkProbeObservation,
)
from oracle_app.provider_bridges.service_control import execute_typed_service_action


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class CanonicalNetworkExecutionTests(unittest.TestCase):
    @patch("oracle_app.admin_network_routes.safe_get_network_control_verification_snapshot", return_value={})
    def test_canonical_admin_status_preserves_power_action_on_owning_host(self, _verification) -> None:
        execution = Mock()
        execution.status_snapshot.return_value = {
            "status": "healthy",
            "hosts": [{"id": "modem_main", "display_name": "Main Modem", "status": "healthy"}],
            "services": [],
            "service_groups": [],
            "power_targets": [{"id": "modem_power", "host_id": "modem_main", "enabled": True}],
            "dependencies": [],
            "monitors": [],
            "evidence": [],
        }
        execution.control_diagnostics.return_value = {
            "actions": [{
                "id": "modem_power_cycle",
                "target_type": "power_target",
                "target_id": "modem_power",
                "action_id": "power_cycle",
                "enabled": True,
                "requires_confirmation": True,
                "adapter": "home_assistant_power",
                "provider": "home_assistant",
                "description": "Cycle modem power.",
            }],
            "counts": {"total": 1},
        }

        payload = admin_network_status_canonical(execution)

        action = payload["network"]["hosts"][0]["control_actions"][0]
        self.assertEqual(action["target_type"], "power_target")
        self.assertEqual(action["target_id"], "modem_power")

    def test_canonical_http_routes_do_not_select_legacy_network_configuration(self) -> None:
        execution = Mock()
        execution.status_snapshot.return_value = {
            "status": "healthy",
            "severity": "none",
            "freshness": "fresh",
            "generated_at": "2026-07-15T12:00:00-04:00",
            "summary": "Network is healthy.",
            "hosts": [],
            "services": [],
            "service_groups": [],
            "power_targets": [],
            "dependencies": [],
            "monitors": [],
            "evidence": [],
            "provider_observations": {},
        }
        execution.control_diagnostics.return_value = {
            "actions": [],
            "counts": {"total": 0},
        }
        execution.librenms_health.return_value = {
            "status": "ok",
            "service": "oracle-brain",
            "provider": "librenms",
            "configured": True,
            "available": True,
            "degraded": False,
            "detail": "LibreNMS API is reachable.",
            "missing_config_keys": [],
        }
        application = FastAPI()
        application.state.brain_application_composition = CanonicalBrainApplicationComposition(
            runtime=Mock(),
            core_consumers=Mock(),
            route_registry=Mock(),
            dispatch_registry=Mock(),
            projection_resolver=Mock(),
            request_source_resolver=Mock(),
            playback_target_resolver=Mock(),
            notification_execution=Mock(),
            network_execution=execution,
        )
        request = Request({"type": "http", "app": application})

        with patch("oracle_app.admin_network_routes.admin_network_status") as legacy_status, patch(
            "oracle_app.health.get_librenms_settings"
        ) as legacy_librenms:
            status = admin_network_status_http(request)
            health = health_librenms_http(request)

        self.assertEqual(status["network"]["status"], "healthy")
        self.assertTrue(health.available)
        legacy_status.assert_not_called()
        legacy_librenms.assert_not_called()

    @patch("oracle_app.network_runtime.canonical.LibreNmsBridge.get_typed_monitoring_status")
    @patch("oracle_app.network_runtime.canonical.NetworkProbeBridge.get_typed_internet_status")
    def test_canonical_voice_and_snapshot_use_only_typed_observation_edges(
        self,
        probe,
        monitoring,
    ) -> None:
        probe.return_value = NetworkProbeObservation(
            status="healthy",
            checked_at="2026-07-15T12:00:00-04:00",
            source="probe",
            detail="Direct network checks succeeded.",
        )
        monitoring.return_value = NetworkMonitoringObservation(
            status="healthy",
            checked_at="2026-07-15T12:00:00-04:00",
            source="librenms",
            detail="LibreNMS reports no active alerts.",
            services=({"service_name": "Example Service", "service_status": "0"},),
            service_count=1,
        )
        execution = self._execution()

        with patch("oracle_app.network.get_network_probe_settings") as legacy_probe, patch(
            "oracle_app.network.get_librenms_settings"
        ) as legacy_librenms:
            speech, summary = build_network_response(
                "is the network okay",
                canonical_execution=execution,
                canonical_authority=True,
            )
            ui = build_ui_network_health_snapshot(
                canonical_execution=execution,
                canonical_authority=True,
            )
            snapshot = execution.status_snapshot(force_refresh=True)

        self.assertEqual(speech, "The network looks healthy.")
        self.assertEqual(summary["status"], "healthy")
        self.assertEqual(ui["status"], "healthy")
        self.assertEqual(snapshot["services"][0]["status"], "healthy")
        self.assertEqual(snapshot["monitors"][0]["provider"], "librenms")
        legacy_probe.assert_not_called()
        legacy_librenms.assert_not_called()

    @patch("oracle_app.network_runtime.control.check_typed_service_available")
    @patch("oracle_app.network_runtime.control.execute_typed_service_action")
    def test_canonical_confirmed_control_uses_bound_typed_adapter(
        self,
        execute_service,
        check_service,
    ) -> None:
        execute_service.return_value = {"ok": True, "status": "executed"}
        check_service.return_value = {"ok": True, "status": "available"}
        execution = self._execution(enable_control=True)
        payload = {
            "target_type": "service",
            "target_id": "example_service",
            "action_id": "restart_service",
            "confirmed": True,
            "actor": "operator",
            "source": "system_mode",
        }

        admitted = execution.control_confirm(payload)
        result = execution.execute_control(
            payload,
            {
                "request_id": admitted["request_id"],
                "requested_at": admitted["requested_at"],
                "actor": "operator",
                "source": "system_mode",
                "reason": "test",
            },
        )
        completed = execution.control_confirm(payload, result=result)

        self.assertTrue(admitted["allowed"])
        self.assertEqual(completed["result_status"], "executed")
        self.assertEqual(completed["execution"]["verification_status"], "passed")
        self.assertEqual(execute_service.call_args.kwargs["adapter"].service_target, "example-service.service")

    @patch("oracle_app.provider_bridges.service_control.subprocess.run")
    def test_typed_docker_restart_preserves_companion_lifecycle_order(self, run) -> None:
        run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        adapter = ServiceControlAdapter(
            type="service_control",
            target_kind="service",
            host_id="oracle_host",
            transport="local",
            platform="linux",
            service_target="nextcloud-app",
            lifecycle_service_targets=["nextcloud-cron"],
            service_adapter="docker",
        )

        result = execute_typed_service_action(
            adapter=adapter,
            credential=None,
            operation="restart_service",
        )

        self.assertTrue(result["ok"])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], ["docker", "stop", "nextcloud-cron"])
        self.assertEqual(commands[1], ["docker", "restart", "nextcloud-app"])
        self.assertEqual(commands[2], ["docker", "start", "nextcloud-cron"])

    @patch("oracle_app.network_runtime.control._wait_reachable", return_value=True)
    @patch("oracle_app.network_runtime.control.execute_typed_service_action", return_value={"ok": True})
    @patch("oracle_app.network_runtime.control.TypedServiceControl")
    def test_graceful_host_recovery_restores_host_services_before_readiness_and_clients_last(
        self,
        service_control,
        _execute,
        _reachable,
    ) -> None:
        calls = Mock()
        service = service_control.return_value
        service.prepare.return_value = {"ok": True, "completed_phase_ids": ["stop_host_services"]}
        service.recover_host_services.side_effect = lambda *_args, **_kwargs: (
            calls.host_services(),
            {"ok": True, "completed_phase_ids": ["restore_host_services"]},
        )[1]
        service.check_readiness.side_effect = lambda *_args, **_kwargs: (
            calls.readiness(),
            {"ok": True, "check_count": 1, "passed_count": 1},
        )[1]
        service.recover_client.side_effect = lambda *_args, **_kwargs: (
            calls.client(),
            {"ok": True, "completed_phase_ids": ["restore_client_storage"]},
        )[1]
        adapter = ServiceControlAdapter(
            type="service_control",
            target_kind="host",
            host_id="storage_host",
            transport="ssh",
            platform="linux",
            address="192.0.2.20",
            user="oracle",
            password_secret="HOST_PASSWORD",
            lifecycle={"mode": "graceful"},
            readiness_http_urls=["http://192.0.2.20/health"],
        )
        action = SimpleNamespace(
            adapter=SimpleNamespace(definition=adapter, credential="secret"),
            definition=SimpleNamespace(
                requires_graceful_lifecycle=True,
                execution=SimpleNamespace(
                    shutdown_timeout_seconds=1,
                    recovery_timeout_seconds=1,
                    recovery_poll_seconds=1,
                ),
            ),
        )

        result = _execute_host(SimpleNamespace(adapters=Mock()), action, {})

        self.assertTrue(result["ok"])
        self.assertEqual(calls.mock_calls, [unittest.mock.call.host_services(), unittest.mock.call.readiness(), unittest.mock.call.client()])

    @patch(
        "oracle_app.network_runtime.control.check_typed_service_available",
        side_effect=[{"ok": False, "available": False}, {"ok": False, "available": False}],
    )
    def test_pihole_continuity_allows_restart_when_target_is_already_down(self, _check) -> None:
        adapter = ServiceControlAdapter(
            type="service_control",
            target_kind="service",
            host_id="gateway_a",
            transport="ssh",
            platform="linux",
            address="192.0.2.20",
            user="oracle",
            password_secret="HOST_PASSWORD",
            service_adapter="docker",
            service_target="dns-primary",
        )
        action = SimpleNamespace(
            adapter=SimpleNamespace(definition=adapter, credential=None),
            definition=SimpleNamespace(
                target_type="service",
                target_id="dns_primary",
                operation="restart_service",
                required_preconditions=("pihole_restart_continuity",),
            ),
        )
        peer = SimpleNamespace(
            adapter=SimpleNamespace(definition=adapter, credential=None),
            definition=SimpleNamespace(
                target_type="service",
                target_id="dns_secondary",
                operation="restart_service",
                required_preconditions=("pihole_restart_continuity",),
            ),
        )
        policy = SimpleNamespace(
            actions={"target": action, "peer": peer},
        )
        execution = SimpleNamespace(
            adapters=Mock(),
            policy=policy,
        )

        results = _preconditions(execution, action)

        self.assertEqual(results[0]["status"], "passed")
        self.assertEqual(results[0]["observed_value"]["target"], "down")

    @patch("oracle_app.network_runtime.control.check_typed_service_available", return_value={"ok": True})
    def test_pihole_continuity_derives_peer_from_policy_without_household_ids(self, _check) -> None:
        definition = ServiceControlAdapter(
            type="service_control",
            target_kind="service",
            host_id="gateway_a",
            transport="local",
            platform="linux",
            service_adapter="docker",
            service_target="dns-primary",
        )
        target = SimpleNamespace(
            adapter=SimpleNamespace(definition=definition, credential=None),
            definition=SimpleNamespace(
                target_type="service",
                target_id="dns_primary",
                operation="restart_service",
                required_preconditions=("pihole_restart_continuity",),
            ),
        )
        peer = SimpleNamespace(
            adapter=SimpleNamespace(definition=definition, credential=None),
            definition=SimpleNamespace(
                target_type="service",
                target_id="dns_backup",
                operation="restart_service",
                required_preconditions=("pihole_restart_continuity",),
            ),
        )
        policy = SimpleNamespace(
            actions={"target": target, "peer": peer},
            action_for=Mock(return_value=peer),
        )
        execution = SimpleNamespace(
            adapters=Mock(),
            policy=policy,
        )

        results = _preconditions(execution, target)

        self.assertEqual(results[0]["status"], "passed")

    @patch("oracle_app.network_runtime.control.TypedServiceControl")
    def test_host_storage_precondition_uses_selected_adapter_and_fails_closed(self, service_control) -> None:
        service_control.return_value.check_storage_safety.return_value = {
            "ok": False,
            "configured": False,
            "check_count": 0,
            "passed_count": 0,
        }
        adapter = ServiceControlAdapter(
            type="service_control",
            target_kind="host",
            host_id="storage_host",
            transport="local",
            platform="linux",
        )
        runtime_adapter = SimpleNamespace(definition=adapter, credential=None)
        action = SimpleNamespace(
            adapter=runtime_adapter,
            definition=SimpleNamespace(
                target_type="host",
                target_id="storage_host",
                operation="restart_host",
                required_preconditions=("host_storage_safe_for_restart",),
            ),
        )
        execution = SimpleNamespace(
            adapters=Mock(),
            inventory=SimpleNamespace(services={}),
            policy=SimpleNamespace(actions={"storage": action}),
        )

        [result] = _preconditions(execution, action)

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["observed_value"], "0/0")
        self.assertNotIn("storage_host", str(result))
        service_control.return_value.check_storage_safety.assert_called_once_with(runtime_adapter)

    def test_client_recovery_verifies_read_write_mount_before_starting_dependents(self) -> None:
        control = TypedServiceControl(Mock())
        host = Mock()
        profile = SimpleNamespace(
            host_id="oracle_host",
            mount_path="/mnt/storage",
            mount_service_target="mnt-storage-mount.service",
            service_adapter_ids=("plex_restart",),
        )
        control._host_for_id = Mock(return_value=host)  # type: ignore[method-assign]
        control._run = Mock(side_effect=[  # type: ignore[method-assign]
            {"ok": True},
            {"ok": True, "stdout": "/dev/md0 /mnt/storage ro"},
            {"ok": True},
            {"ok": True, "stdout": "/dev/md0 /mnt/storage rw"},
        ])
        control._set_services = Mock(return_value={"ok": True})  # type: ignore[method-assign]

        result = control._restore_client(profile, 10)

        self.assertTrue(result["ok"])
        control._set_services.assert_called_once_with(("plex_restart",), "started", 10)

    def test_partial_service_stop_is_rolled_back_before_preparation_fails(self) -> None:
        definition = ServiceControlAdapter(
            type="service_control",
            target_kind="service",
            host_id="oracle_host",
            transport="local",
            platform="linux",
            service_adapter="docker",
            service_target="nextcloud-app",
            lifecycle_service_targets=["nextcloud-cron"],
        )
        runtime = SimpleNamespace(definition=definition, credential=None)
        adapters = Mock()
        adapters.adapter.return_value = runtime
        control = TypedServiceControl(adapters)
        control._run = Mock(return_value={"ok": True})  # type: ignore[method-assign]
        control._target_has_state = Mock(side_effect=[True, False])  # type: ignore[method-assign]

        result = control._set_services(["nextcloud_restart"], "stopped", 10)

        self.assertFalse(result["ok"])
        commands = [call.args[1] for call in control._run.call_args_list]
        self.assertEqual(commands[-2], ["docker", "start", "nextcloud-app"])
        self.assertEqual(commands[-1], ["docker", "start", "nextcloud-cron"])

    def _execution(self, *, enable_control: bool = False) -> CanonicalNetworkExecution:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            inventory_path = bundle / "domains" / "network" / "inventory.yaml"
            inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
            inventory["enabled"] = True
            inventory["internet_health_probe_adapter_id"] = "internet_health"
            inventory["monitors"][0]["adapter_id"] = "librenms_service"
            inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")
            adapters_path = bundle / "domains" / "network" / "adapters.yaml"
            adapters = yaml.safe_load(adapters_path.read_text(encoding="utf-8"))
            adapters["providers"].update({
                "internet_health": {
                    "type": "direct_probe",
                    "dns_host": "example.invalid",
                },
                "librenms_service": {
                    "type": "librenms",
                    "base_url": "http://librenms.invalid",
                    "credential_secret": "LIBRENMS_TOKEN",
                    "service_name": "Example Service",
                },
            })
            adapters_path.write_text(yaml.safe_dump(adapters, sort_keys=False), encoding="utf-8")
            if enable_control:
                policy_path = bundle / "domains" / "network" / "policy.yaml"
                policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
                policy["actions"][0]["enabled"] = True
                policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
            (bundle / "secrets.env").write_text(
                "LIBRENMS_TOKEN=test-token\n",
                encoding="utf-8",
            )
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible, inspection.report)
            effective = EffectiveConfig(
                activation_generation_id="activation_11111111111111111111111111111111",
                config_generation_id="config_11111111111111111111111111111111",
                secret_generation_id="secrets_11111111111111111111111111111111",
                selection_operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids=MappingProxyType({}),
                config_revision=str(inspection.normalized_candidate_revision),
                bundle_id="example-home",
                schema_version=1,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )
            runtime = BrainEffectiveRuntimeSettings.from_effective_config(effective)
            return CanonicalNetworkExecution(
                runtime.network_inventory,  # type: ignore[arg-type]
                runtime.network_adapters,  # type: ignore[arg-type]
                runtime.network_policy,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
