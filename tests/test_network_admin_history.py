from __future__ import annotations

from network_test_support import *


class NetworkAdminHistoryTests(NetworkTestCase):
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
