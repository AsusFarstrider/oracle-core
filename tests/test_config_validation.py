from __future__ import annotations

import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.config_validation import (
    brain_config_has_errors,
    build_control_service_config_report,
    build_control_service_runtime_report,
    build_brain_config_report,
    build_full_config_report,
    build_satellite_config_report,
    format_brain_config_report,
    format_full_config_report,
    log_brain_config_report,
)


class ConfigValidationTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT": "/var/lib/oracle/wake-captures"},
        clear=True,
    )
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_wake_capture_archive_root_is_known_sanitized_bootstrap(self, _mock_config) -> None:
        findings = build_brain_config_report()

        matching = [
            item
            for item in findings
            if item["setting"] == "ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["status"], "bootstrap_metadata")
        self.assertNotIn("/var/lib/oracle/wake-captures", json.dumps(matching))

    @patch.dict(
        "os.environ",
        {
            "ORACLE_CONFIG_AUTHORING_MODE": "managed_writable",
            "ORACLE_CONFIG_BUNDLE_ROOT": "/srv/oracle/config",
            "ORACLE_CONFIG_SOCKET_PATH": "/run/oracle/config.sock",
            "ORACLE_CONFIG_STORE_ROOT": "/var/lib/oracle/configuration",
        },
        clear=True,
    )
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_configuration_bootstrap_inputs_are_known_not_legacy_field_overrides(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertFalse(
            any(
                item["setting"].startswith("ORACLE_CONFIG_") and item["status"] == "unknown_env"
                for item in findings
            )
        )
        bootstrap = [item for item in findings if item["status"] == "bootstrap_metadata"]
        self.assertEqual(len(bootstrap), 4)
        self.assertTrue(all(item["effective_source"] == "bootstrap_env" for item in bootstrap))
        self.assertNotIn("/srv/oracle/config", json.dumps(bootstrap))

    @patch.dict(
        "os.environ",
        {
            "ORACLE_HOLIDAY_CALENDAR_ICS_URL": "https://example.invalid/holidays.ics",
            "ORACLE_USER_REGISTRY_JSON": "{}",
            "ORACLE_WAKE_ARBITRATION_LOSER_SUPPRESSION_MS": "10000",
            "ORACLE_WAKE_ARBITRATION_SCORING_STRATEGY": "audio_level_then_confidence",
            "ORACLE_WAKE_ARBITRATION_WINDOW_MS": "1000",
            "ORACLE_WEATHER_CURRENT_PROVIDER": "weewx",
            "ORACLE_WEATHER_FORECAST_PROVIDER": "nws",
            "ORACLE_WEATHER_HISTORY_DB_PATH": "/tmp/archive.sdb",
            "ORACLE_WEATHER_HISTORY_JSON_URL": "https://example.invalid/history.json",
            "ORACLE_WEATHER_HISTORY_SSH_HOST": "weather.example.invalid",
            "ORACLE_WEATHER_HISTORY_SSH_PASSWORD": "secret",
            "ORACLE_WEATHER_HISTORY_SSH_USER": "oracle",
            "ORACLE_WEATHER_HISTORY_TIMEOUT_SECONDS": "5",
        },
        clear=True,
    )
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_brain_report_accepts_all_runtime_consumed_env_names(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertFalse(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={
            "home_assistant_url": "http://home-assistant.example:8123",
            "home_assistant_token": "secret",
            "satellite_controls": {
                "satellite-alpha": {
                    "base_url": "http://satellite.example:8021",
                    "api_key": "abc",
                }
            },
        },
    )
    def test_report_warns_on_deploy_specific_local_config(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(any(item["status"] == "deprecated_local_truth" for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={"ollama_split_enabled": True},
    )
    def test_report_marks_orphaned_ollama_split_key_retired(self, _mock_config) -> None:
        findings = build_brain_config_report()

        matching = [item for item in findings if item["setting"] == "ollama_split_enabled"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["status"], "retired_no_effect")
        self.assertEqual(matching[0]["severity"], "warning")

    @patch.dict(
        "os.environ",
        {"ORACLE_HOME_ASSISTANT_URL": "http://env.example:8123"},
        clear=True,
    )
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={"home_assistant_url": "http://json.example:8123"},
    )
    def test_report_warns_on_env_vs_local_conflict(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "home_assistant_url" and item["status"] == "conflicting_sources"
                for item in findings
            )
        )

    @patch.dict("os.environ", {"ORACLE_SATELLITE_CONTROLS_JSON": "{bad json"}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_report_marks_invalid_env_json_as_error(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(item["setting"] == "satellite_controls" and item["severity"] == "error" for item in findings)
        )

    @patch.dict("os.environ", {"ORACLE_SATELLITE_CONTROLS_JSON": "[]"}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_report_marks_non_object_env_json_as_error(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "satellite_controls"
                and item["status"] == "invalid_env_json_shape"
                and item["severity"] == "error"
                for item in findings
            )
        )

    @patch.dict("os.environ", {"ORACLE_NOT_A_REAL_SETTING": "1"}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_report_warns_on_unknown_oracle_env(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict(
        "os.environ",
        {"ORACLE_NEWS_FEEDS_JSON": json.dumps({"npr": {"label": "NPR", "url": "https://feeds.npr.org/1001/rss.xml"}})},
        clear=True,
    )
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={"news_feeds": {"old": {"label": "Old", "url": "https://example.invalid"}}},
    )
    def test_report_warns_on_json_env_override_conflict(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(item["setting"] == "news_feeds" and item["status"] == "conflicting_sources" for item in findings)
        )

    @patch.dict(
        "os.environ",
        {
            "ORACLE_NETWORK_PROBE_ENABLED": "true",
            "ORACLE_NETWORK_PROBE_DNS_HOST": "cloudflare.com",
            "ORACLE_NETWORK_PROBE_HTTP_URL": "https://connectivitycheck.gstatic.com/generate_204",
            "ORACLE_NETWORK_PROBE_TIMEOUT_SECONDS": "3",
            "ORACLE_LIBRENMS_ENABLED": "false",
            "ORACLE_LIBRENMS_URL": "https://librenms.example.invalid",
            "ORACLE_LIBRENMS_TOKEN": "secret-token",
            "ORACLE_LIBRENMS_TIMEOUT_SECONDS": "5",
            "ORACLE_NETWORK_SERVICE_CONTROL_JSON": json.dumps(
                {"oracle-brain": {"address": "oracle-brain.local", "method": "systemd", "allowed_actions": {}}}
            ),
            "ORACLE_NETWORK_CONTROL_JSON": json.dumps({"actions": []}),
            "ORACLE_NETWORK_ROUTER_CONTROL_JSON": "{}",
            "ORACLE_EXTERNAL_WEB_ENABLED": "false",
            "ORACLE_EXTERNAL_WEB_AUTH_MODE": "external_forward_auth",
            "ORACLE_EXTERNAL_WEB_PUBLIC_BASE_URL": "https://oracle.example.invalid",
            "ORACLE_EXTERNAL_WEB_TRUSTED_PROXY_HEADERS": "false",
            "ORACLE_EXTERNAL_WEB_PUBLIC_HEALTH": "false",
            "ORACLE_FACTS_ENABLED": "false",
            "ORACLE_FACTS_PROVIDER": "static",
            "ORACLE_FACTS_SUMMARIZER_ENABLED": "false",
            "ORACLE_FACTS_ACK_ENABLED": "true",
            "ORACLE_FACTS_TIMEOUT_SECONDS": "8",
            "ORACLE_FACTS_CACHE_ENABLED": "false",
            "ORACLE_FACTS_CACHE_TTL_SECONDS": "86400",
            "ORACLE_FACTS_WIKIPEDIA_LANGUAGE": "en",
            "ORACLE_FACTS_WIKIPEDIA_TIMEOUT_SECONDS": "8",
        },
        clear=True,
    )
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_network_external_web_and_control_env_names_are_known_brain_settings(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertFalse(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict(
        "os.environ",
        {
            "ORACLE_EXTERNAL_ACCESS_ENABLED": "false",
            "ORACLE_EXTERNAL_ACCESS_TOKEN": "external-secret",
        },
        clear=True,
    )
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_legacy_external_access_env_names_remain_known_during_migration(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertFalse(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict("os.environ", {"ORACLE_NETWORK_SERVICE_CONTROL_JSON": "{bad json"}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_report_marks_invalid_network_service_control_json_as_error(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "network_service_control"
                and item["status"] == "invalid_env_json"
                and item["severity"] == "error"
                for item in findings
            )
        )

    @patch.dict("os.environ", {"ORACLE_NETWORK_SERVICE_CONTROL_JSON": "[]"}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_report_marks_non_object_network_service_control_json_as_error(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "network_service_control"
                and item["status"] == "invalid_env_json_shape"
                and item["severity"] == "error"
                for item in findings
            )
        )

    @patch.dict("os.environ", {"ORACLE_NETWORK_ROUTER_CONTROL_JSON": "{bad json"}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_report_marks_invalid_network_router_control_json_as_error(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "network_router_control"
                and item["status"] == "invalid_env_json"
                and item["severity"] == "error"
                for item in findings
            )
        )

    @patch.dict(
        "os.environ",
        {
            "ORACLE_LIBRENMS_TOKEN": "env-secret",
            "ORACLE_EXTERNAL_ACCESS_TOKEN": "legacy-external-secret",
        },
        clear=True,
    )
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={
            "librenms_token": "local-secret",
            "external_access_token": "local-legacy-external-secret",
        },
    )
    def test_report_redacts_librenms_and_legacy_external_access_secret_conflicts(self, _mock_config) -> None:
        findings = build_brain_config_report()

        secret_conflicts = [
            item
            for item in findings
            if item["setting"] in {"librenms_token", "external_access_token"}
            and item["status"] == "conflicting_sources"
        ]
        self.assertEqual(
            {item["setting"] for item in secret_conflicts},
            {"librenms_token", "external_access_token"},
        )
        for item in secret_conflicts:
            self.assertEqual(item["effective_value_redacted"], "<redacted>")
            self.assertEqual(
                {source["value_redacted"] for source in item["conflicting_sources"]},
                {"<redacted>"},
            )

    @patch.dict(
        "os.environ",
        {
            "ORACLE_NETWORK_SERVICE_CONTROL_JSON": json.dumps(
                {"oracle-brain": {"address": "oracle-brain.local", "method": "systemd", "allowed_actions": {}}}
            )
        },
        clear=True,
    )
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={"network_service_control": {"old": {"address": "old.local", "allowed_actions": {}}}},
    )
    def test_report_warns_on_network_control_json_env_override_conflict(self, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "network_service_control"
                and item["status"] == "conflicting_sources"
                for item in findings
            )
        )

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.load_network_inventory_config", return_value=None)
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={
            "network_inventory": {
                "hosts": [
                    {
                        "id": "oracle_host",
                        "display_name": "Oracle Host",
                    }
                ]
            }
        },
    )
    def test_report_warns_on_legacy_network_inventory_in_config_local(self, _mock_config, _mock_inventory) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "server_config_local_json"
                and item["status"] == "deprecated_local_truth"
                and "network_inventory" in item["message"]
                for item in findings
            )
        )

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    @patch(
        "oracle_app.config_validation.load_network_inventory_config",
        return_value={
            "hosts": [
                {
                    "id": "oracle_host",
                    "display_name": "Oracle Host",
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
                }
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
            "power_targets": [
                {
                    "id": "oracle_host_power",
                    "host_id": "oracle_host",
                    "provider": "home_assistant",
                    "entity_id": "switch.oracle_host_plug",
                    "enabled": False,
                }
            ],
        },
    )
    def test_report_accepts_valid_network_inventory_file(self, _mock_inventory, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertFalse(any(item["setting"].startswith("network_inventory.") for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    @patch(
        "oracle_app.config_validation.load_network_inventory_config",
        return_value={
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
    )
    def test_report_accepts_host_action_service_control_refs(self, _mock_inventory, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertFalse(any(item["setting"].startswith("network_inventory.") for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    @patch(
        "oracle_app.config_validation.load_network_inventory_config",
        return_value={
            "hosts": [
                {
                    "id": "test_satellite_alpha",
                    "display_name": "Wall Display",
                    "control_refs": {
                        "service_control": {
                            "actions": {
                                "restart_runtime": {
                                    "host_id": "test_satellite_alpha",
                                }
                            }
                        }
                    },
                }
            ],
            "services": [],
            "power_targets": [],
        },
    )
    def test_report_rejects_incomplete_host_action_service_control_refs(self, _mock_inventory, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"]
                == "network_inventory.hosts[0].control_refs.service_control.actions.restart_runtime.service_name"
                and item["status"] == "missing_required_config"
                for item in findings
            )
        )

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    @patch("oracle_app.config_validation.load_network_inventory_config", return_value=[])
    def test_report_rejects_non_object_network_inventory_file(self, _mock_inventory, _mock_config) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "network_inventory"
                and item["status"] == "invalid_config_shape"
                and item["effective_source"] == "config/network-inventory.json"
                for item in findings
            )
        )

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    @patch(
        "oracle_app.config_validation.load_network_inventory_config",
        return_value={
            "hosts": [
                {
                    "id": "oracle_host",
                    "display_name": "Oracle Host",
                },
                {
                    "id": "oracle_host",
                    "display_name": "Duplicate Oracle Host",
                },
                {
                    "id": "nas_host",
                    "display_name": "NAS Host",
                },
            ],
            "services": [
                {
                    "id": "plex",
                    "display_name": "Plex",
                    "host_id": "missing_host",
                },
                {
                    "id": "nextcloud",
                    "display_name": "Nextcloud",
                    "host_id": "nas_host",
                }
            ],
            "service_groups": [
                {
                    "id": "media",
                    "display_name": "Media",
                    "host_id": "oracle_host",
                    "service_ids": ["plex", "missing_service", "plex", "nextcloud"],
                }
            ],
            "monitors": [
                {
                    "id": "plex_monitor",
                    "target_type": "service",
                    "target_id": "missing_service",
                    "source": "librenms",
                    "kind": "service",
                }
            ],
            "power_targets": [
                {
                    "id": "oracle_host_power",
                    "host_id": "oracle_host",
                    "provider": "home_assistant",
                    "entity_id": "switch.oracle_host_plug",
                    "enabled": True,
                }
            ],
        },
    )
    def test_report_rejects_invalid_network_inventory_references_and_power_target(
        self,
        _mock_inventory,
        _mock_config,
    ) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "network_inventory.hosts.oracle_host"
                and item["status"] == "duplicate_id"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_inventory.services[0].host_id"
                and item["status"] == "unknown_reference"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_inventory.monitors[0].target_id"
                and item["status"] == "unknown_reference"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_inventory.service_groups[0].service_ids.missing_service"
                and item["status"] == "unknown_reference"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_inventory.service_groups[0].service_ids.plex"
                and item["status"] == "duplicate_id"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_inventory.service_groups[0].service_ids.nextcloud"
                and item["status"] == "host_reference_mismatch"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_inventory.power_targets[0]"
                and item["status"] == "invalid_power_target"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_inventory.power_targets[0].readiness"
                and item["status"] == "missing_required_config"
                for item in findings
            )
        )

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    @patch(
        "oracle_app.config_validation.load_network_inventory_config",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Host"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [{"id": "oracle_host_power", "host_id": "oracle_host", "provider": "home_assistant", "entity_id": "switch.example"}],
        },
    )
    @patch(
        "oracle_app.config_validation.load_network_control_config",
        return_value={
            "actions": [
                {
                    "id": "plex_restart",
                    "target_type": "service",
                    "target_id": "plex",
                    "action_id": "restart_service",
                    "provider": "ssh",
                    "adapter": "systemd_service_restart",
                    "requires_confirmation": True,
                    "required_preconditions": ["plex_no_active_streams"],
                    "execution": {"method": "systemd", "unit": "plexmediaserver.service", "wait_seconds": 10},
                    "enabled": False,
                }
            ]
        },
    )
    def test_report_accepts_valid_network_control_file(
        self,
        _mock_control,
        _mock_inventory,
        _mock_config,
    ) -> None:
        findings = build_brain_config_report()

        self.assertFalse(any(item["setting"].startswith("network_control.") for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    @patch(
        "oracle_app.config_validation.load_network_inventory_config",
        return_value={
            "hosts": [{"id": "oracle_host", "display_name": "Oracle Host"}],
            "services": [{"id": "plex", "display_name": "Plex", "host_id": "oracle_host"}],
            "power_targets": [],
        },
    )
    @patch(
        "oracle_app.config_validation.load_network_control_config",
        return_value={
            "actions": [
                {
                    "id": "bad_action",
                    "target_type": "service",
                    "target_id": "missing_service",
                    "action_id": "restart_service",
                    "provider": "ssh",
                    "adapter": "systemd_service_restart",
                    "enabled": True,
                    "required_preconditions": ["", "unknown_check", "host_storage_safe_for_restart"],
                    "execution": {
                        "method": "shell",
                        "unit": "plex restart",
                        "wait_seconds": 999,
                        "cooldown_seconds": 3601,
                    },
                    "shell_command": "systemctl restart plex",
                },
                {
                    "id": "bad_action",
                    "target_type": "router",
                    "target_id": "router",
                    "action_id": "restart_service",
                    "provider": "ssh",
                    "adapter": "systemd_service_restart",
                },
            ]
        },
    )
    def test_report_rejects_invalid_network_control_policy(
        self,
        _mock_control,
        _mock_inventory,
        _mock_config,
    ) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "network_control.actions.bad_action"
                and item["status"] == "duplicate_id"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].target_id"
                and item["status"] == "unknown_reference"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].shell_command"
                and item["status"] == "unsafe_control_config"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].requires_confirmation"
                and item["status"] == "confirmation_required"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].execution.method"
                and item["status"] == "invalid_config_value"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].execution.unit"
                and item["status"] == "invalid_config_value"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].execution.wait_seconds"
                and item["status"] == "invalid_config_value"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].execution.cooldown_seconds"
                and item["status"] == "invalid_config_value"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].required_preconditions"
                and item["status"] == "invalid_config_value"
                and "unknown_check" in item["message"]
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[0].required_preconditions"
                and item["status"] == "invalid_reference"
                and "host_storage_safe_for_restart" in item["message"]
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item["setting"] == "network_control.actions[1].target_type"
                and item["status"] == "invalid_config_value"
                for item in findings
            )
        )

    @patch.dict("os.environ", {"ORACLE_NOT_A_REAL_SETTING": "1"}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    @patch("oracle_app.config_validation.safe_record_event", return_value=True)
    def test_log_brain_config_report_emits_warning_logs(self, _mock_memory, _mock_config) -> None:
        with self.assertLogs("oracle-brain.config", level="WARNING") as captured:
            log_brain_config_report()

        self.assertIn("config_warning", "\n".join(captured.output))

    @patch.dict("os.environ", {"ORACLE_URL": "http://legacy.example:8011"}, clear=True)
    def test_satellite_report_warns_on_deprecated_legacy_brain_env(self) -> None:
        findings = build_satellite_config_report()

        self.assertTrue(
            any(item["setting"] == "ORACLE_URL" and item["status"] == "deprecated_env" for item in findings)
        )

    @patch.dict("os.environ", {"ORACLE_MUSC_CONTROL_URL": "http://127.0.0.1:8021"}, clear=True)
    def test_satellite_report_warns_on_unknown_satellite_env(self) -> None:
        findings = build_satellite_config_report()

        self.assertTrue(
            any(item["setting"] == "ORACLE_MUSC_CONTROL_URL" and item["status"] == "unknown_env" for item in findings)
        )

    @patch.dict(
        "os.environ",
        {
            "ORACLE_ERROR_TONE_COOLDOWN_SECONDS": "1",
            "ORACLE_ERROR_TONE_ENABLED": "true",
            "ORACLE_FALSE_START_SILENCE_SECONDS": "0.5",
            "ORACLE_INTERIM_ACK_ENABLED": "true",
            "ORACLE_INTERIM_ACK_POLL_INTERVAL_SECONDS": "0.1",
            "ORACLE_INTERIM_ACK_REQUEST_TIMEOUT_SECONDS": "1",
            "ORACLE_INPUT_GAIN": "1.0",
            "ORACLE_SPEECH_START_TIMEOUT_SECONDS": "1.6",
            "ORACLE_WAKE_ARBITRATION_LOSER_SUPPRESSION_MS": "10000",
            "ORACLE_WAKE_ARBITRATION_TIMEOUT_SECONDS": "3",
        },
        clear=True,
    )
    def test_satellite_report_accepts_all_runtime_consumed_env_names(self) -> None:
        findings = build_satellite_config_report()

        self.assertFalse(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict(
        "os.environ",
        {
            "ORACLE_SATELLITE_ID": "test_satellite_alpha",
            "ORACLE_SATELLITE_PROJECTION_STORE_ROOT": "/var/lib/oracle-satellite/projections",
            "ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH": "/var/lib/oracle-satellite/runtime-compatibility.json",
        },
        clear=True,
    )
    def test_satellite_report_accepts_canonical_selector_env_names(self) -> None:
        findings = build_satellite_config_report()

        self.assertFalse(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    def test_control_service_report_warns_when_api_key_missing(self) -> None:
        findings = build_control_service_config_report()

        self.assertTrue(
            any(
                item["setting"] == "ORACLE_SATELLITE_CONTROL_API_KEY"
                and item["status"] == "missing_required_env"
                for item in findings
            )
        )

    @patch.dict("os.environ", {"ORACLE_DISABLE_PLEXAMP_EXTERNAL": "true"}, clear=True)
    def test_control_service_report_accepts_disable_plexamp_external_env(self) -> None:
        findings = build_control_service_config_report()

        self.assertFalse(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict(
        "os.environ",
        {
            "ORACLE_OUTPUT_VOLUME_BACKEND": "alsa",
            "ORACLE_OUTPUT_VOLUME_CARD": "default",
            "ORACLE_OUTPUT_VOLUME_CONTROL": "Master",
        },
        clear=True,
    )
    def test_control_service_report_accepts_runtime_volume_env_names(self) -> None:
        findings = build_control_service_config_report()

        self.assertFalse(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict(
        "os.environ",
        {
            "ORACLE_SATELLITE_ID": "test_satellite_alpha",
            "ORACLE_SATELLITE_PROJECTION_STORE_ROOT": "/var/lib/oracle-satellite/projections",
            "ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH": "/var/lib/oracle-satellite/runtime-compatibility.json",
            "ORACLE_SATELLITE_CONTROL_LOG_LEVEL": "INFO",
        },
        clear=True,
    )
    def test_control_service_report_accepts_canonical_bootstrap_env_names(self) -> None:
        findings = build_control_service_config_report()

        self.assertFalse(any(item["status"] == "unknown_env" for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    @patch("oracle_app.config_validation.get_room_vocabulary", return_value=[])
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={
            "source_registry": {
                "kitchen-satellite": {
                    "source_type": "satellite",
                    "fixed": True,
                }
            }
        },
    )
    def test_report_marks_missing_fixed_default_room_as_error(
        self,
        _mock_config,
        _mock_rooms,
    ) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "source_registry.kitchen-satellite.default_room"
                and item["severity"] == "error"
                for item in findings
            )
        )

    @patch.dict("os.environ", {}, clear=True)
    @patch(
        "oracle_app.config_validation.get_room_vocabulary",
        return_value=[{"spoken_name": "kitchen", "aliases": ["kitchen"]}],
    )
    @patch(
        "oracle_app.config_validation.load_local_config",
        return_value={
            "source_registry": {
                "kitchen-satellite": {
                    "source_type": "satellite",
                    "fixed": True,
                    "default_room": "garage",
                }
            }
        },
    )
    def test_report_warns_on_unknown_source_registry_room(self, _mock_config, _mock_rooms) -> None:
        findings = build_brain_config_report()

        self.assertTrue(
            any(
                item["setting"] == "source_registry.kitchen-satellite.default_room"
                and item["severity"] == "warning"
                and item["status"] == "unknown_room_reference"
                for item in findings
            )
        )

    @patch.dict("os.environ", {"ORACLE_CONTROL_BIND": "8021"}, clear=True)
    def test_control_service_report_warns_on_unknown_env(self) -> None:
        findings = build_control_service_config_report()

        self.assertTrue(
            any(item["setting"] == "ORACLE_CONTROL_BIND" and item["status"] == "unknown_env" for item in findings)
        )

    @patch.dict("os.environ", {"ORACLE_URL": "http://legacy.example:8011"}, clear=True)
    @patch("oracle_app.config_validation.load_local_config", return_value={})
    def test_full_config_report_includes_satellite_section(self, _mock_config) -> None:
        report = build_full_config_report()
        formatted = format_full_config_report(report)

        self.assertIn("Pi satellite config check:", formatted)
        self.assertIn("ORACLE_URL", formatted)

    @patch.dict("os.environ", {}, clear=True)
    def test_control_service_runtime_report_errors_on_invalid_bind_port(self) -> None:
        findings = build_control_service_runtime_report(
            Namespace(
                api_key="secret",
                bind_host="0.0.0.0",
                bind_port=70000,
                adapter="local_playback",
                plexamp_url="http://127.0.0.1:32500",
                play_longform_audio_cmd="play",
                pause_longform_audio_cmd="pause",
                resume_longform_audio_cmd="resume",
                stop_longform_audio_cmd="stop",
                seek_longform_audio_cmd="seek {position_seconds}",
                longform_state_cmd="state",
            )
        )

        self.assertTrue(any(item["status"] == "invalid_bind_port" and item["severity"] == "error" for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    def test_control_service_runtime_report_warns_on_incomplete_longform_commands(self) -> None:
        findings = build_control_service_runtime_report(
            Namespace(
                api_key="secret",
                bind_host="0.0.0.0",
                bind_port=8021,
                adapter="local_playback",
                plexamp_url="http://127.0.0.1:32500",
                play_longform_audio_cmd="",
                pause_longform_audio_cmd="",
                resume_longform_audio_cmd="",
                stop_longform_audio_cmd="",
                seek_longform_audio_cmd="",
                longform_state_cmd="",
            )
        )

        self.assertTrue(
            any(
                item["status"] == "optional_longform_config_missing" and item["severity"] == "warning"
                for item in findings
            )
        )

    @patch.dict("os.environ", {}, clear=True)
    def test_control_service_runtime_report_accepts_legacy_plexamp_http_alias(self) -> None:
        findings = build_control_service_runtime_report(
            Namespace(
                api_key="secret",
                bind_host="0.0.0.0",
                bind_port=8021,
                adapter="plexamp_http",
                plexamp_url="http://127.0.0.1:32500",
                play_longform_audio_cmd="play",
                pause_longform_audio_cmd="pause",
                resume_longform_audio_cmd="resume",
                stop_longform_audio_cmd="stop",
                seek_longform_audio_cmd="seek {position_seconds}",
                longform_state_cmd="state",
            )
        )

        self.assertFalse(any(item["status"] == "invalid_adapter" for item in findings))

    @patch.dict("os.environ", {}, clear=True)
    def test_control_service_runtime_report_accepts_active_local_playback_adapter(self) -> None:
        findings = build_control_service_runtime_report(
            Namespace(
                api_key="secret",
                bind_host="0.0.0.0",
                bind_port=8021,
                adapter="local_playback",
                plexamp_url="http://127.0.0.1:32500",
                output_volume_backend="alsa",
                output_volume_card="default",
                output_volume_control="Master",
                play_longform_audio_cmd="play",
                pause_longform_audio_cmd="pause",
                resume_longform_audio_cmd="resume",
                stop_longform_audio_cmd="stop",
                seek_longform_audio_cmd="seek {position_seconds}",
                longform_state_cmd="state",
            )
        )

        self.assertFalse(any(item["severity"] == "error" for item in findings))

    def test_format_brain_config_report_handles_empty_findings(self) -> None:
        self.assertEqual(format_brain_config_report([]), "Brain config check: OK")

    def test_brain_config_has_errors_detects_error_findings(self) -> None:
        findings = [
            {"severity": "warning"},
            {"severity": "error"},
        ]

        self.assertTrue(brain_config_has_errors(findings))
