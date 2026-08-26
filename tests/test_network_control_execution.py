from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from network_test_support import *


class NetworkControlExecutionTests(NetworkTestCase):
    def test_canonical_pending_restart_does_not_fall_back_when_network_is_absent(self) -> None:
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
                canonical_execution=None,
                state_path=state_path,
                boot_id_path=boot_id_path,
            )

        self.assertEqual(
            result,
            {"status": "pending", "reason": "canonical_network_unavailable"},
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

    @patch(
        "oracle_app.network_control_execution.time.monotonic",
        side_effect=[0, 0, 0, 16, 16],
    )
    @patch("oracle_app.network_control_execution.time.sleep")
    @patch("oracle_app.network_control_execution.NetworkProbeBridge")
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_fails_when_powered_host_does_not_recover(
        self,
        mock_bridge_class,
        mock_probe_class,
        _mock_sleep,
        _mock_monotonic,
    ) -> None:
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
            home_assistant_connection=("http://home-assistant.local:8123", "dummy-token"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_host_recovery_failed")
        self.assertTrue(result["execution"]["power_restored"])
        self.assertEqual(result["steps"][-1]["id"], "host_recovery_failed")

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

    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_power_cycle_attempts_restore_after_failed_off_verification(
        self,
        mock_bridge_class,
    ) -> None:
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
            home_assistant_connection=("http://home-assistant.local:8123", "dummy-token"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_power_cycle_failed")
        self.assertEqual(
            [call.kwargs["service_name"] for call in bridge.call_service.call_args_list],
            ["turn_off", "turn_on"],
        )

    @patch("oracle_app.network_control_execution.time.sleep")
    @patch(
        "oracle_app.network_control_execution._wait_for_power_readiness",
        return_value={"ready": True, "check_count": 1, "passed_count": 1, "failed_check_ids": []},
    )
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_power_cycles_home_assistant_switch(
        self,
        mock_bridge_class,
        mock_wait_for_readiness,
        mock_sleep,
    ) -> None:
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
            home_assistant_connection=("http://home-assistant.local:8123", "dummy-token"),
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
        "oracle_app.network_control_execution._wait_for_power_readiness",
        return_value={
            "ready": False,
            "check_count": 4,
            "passed_count": 3,
            "failed_check_ids": ["internet"],
        },
    )
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_reports_power_readiness_failure(
        self,
        mock_bridge_class,
        _mock_wait_for_readiness,
    ) -> None:
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
            home_assistant_connection=("http://home-assistant.local:8123", "dummy-token"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "network_control_power_readiness_failed")
        self.assertTrue(result["execution"]["power_restored"])
        self.assertEqual(result["execution"]["readiness_status"], "failed")
        self.assertEqual(result["execution"]["readiness_passed_count"], 3)
        self.assertEqual(result["execution"]["readiness_failed_check_ids"], ["internet"])
        self.assertEqual(result["steps"][-1]["id"], "power_readiness_failed")

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
    @patch("oracle_app.network_control_execution.HomeAssistantBridge")
    def test_network_control_executor_waits_for_powered_host_recovery(
        self,
        mock_bridge_class,
        mock_probe_class,
        _mock_wait_for_readiness,
        mock_sleep,
        _mock_monotonic,
    ) -> None:
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
            home_assistant_connection=("http://home-assistant.local:8123", "dummy-token"),
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
        "oracle_app.network_runtime.service_control.TypedServiceControl.check_readiness",
        return_value={
            "ok": True,
            "status": "passed",
            "check_count": 12,
            "passed_count": 12,
            "failed_check_ids": [],
        },
    )
    def test_pending_local_restart_completes_after_new_boot_and_readiness(self, _mock_readiness) -> None:
        canonical_execution = SimpleNamespace(
            policy=Mock(action_for=Mock(return_value=SimpleNamespace(adapter=Mock()))),
            adapters=Mock(),
        )
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
                canonical_execution=canonical_execution,
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
                state_path=state_path,
                boot_id_path=boot_id_path,
            )

        self.assertTrue(staged["ok"])
        self.assertEqual(result, {"status": "pending", "reason": "boot_not_changed"})

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
