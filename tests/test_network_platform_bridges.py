from __future__ import annotations

from network_test_support import *


class NetworkPlatformBridgeTests(NetworkTestCase):
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

    def test_router_control_rejects_unapproved_action(self) -> None:
        result = execute_router_action(settings={"routers": {}}, router="main", action="restart_router")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "router_control_router_not_allowed")

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

    def test_service_control_rejects_unapproved_action(self) -> None:
        result = execute_service_action(settings={"hosts": {}}, host="storage_host", action="restart_nextcloud")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "service_control_host_not_allowed")

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
