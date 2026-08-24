from __future__ import annotations

from network_test_support import *


class NetworkControlPlanningTests(NetworkTestCase):
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
