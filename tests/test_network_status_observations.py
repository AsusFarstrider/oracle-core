from __future__ import annotations

from network_test_support import *


class NetworkStatusObservationTests(NetworkTestCase):
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
