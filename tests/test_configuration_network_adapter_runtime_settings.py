from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from oracle_app.configuration import EffectiveConfig, NetworkAdaptersRuntimeSettings, inspect_candidate


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class NetworkAdaptersRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_network_edges_select_no_dormant_adapter(self) -> None:
        settings = NetworkAdaptersRuntimeSettings.from_effective_config(self._effective_config())

        self.assertEqual(dict(settings.adapters), {})

    def test_enabled_monitor_selects_only_its_typed_observation_adapter(self) -> None:
        settings = NetworkAdaptersRuntimeSettings.from_effective_config(
            self._effective_config(mode="monitor")
        )

        self.assertEqual(tuple(settings.adapters), ("example_http_probe",))
        adapter = settings.adapter("example_http_probe")
        self.assertEqual(adapter.definition.type, "direct_probe")  # type: ignore[union-attr]
        self.assertIsNone(adapter.credential)  # type: ignore[union-attr]
        self.assertIsNone(settings.adapter("example_service_control"))

    def test_domain_internet_health_probe_selects_exactly_its_direct_adapter(self) -> None:
        settings = NetworkAdaptersRuntimeSettings.from_effective_config(
            self._effective_config(mode="internet_health")
        )

        self.assertEqual(
            tuple(settings.adapters),
            ("domain_internet_health", "example_http_probe"),
        )
        adapter = settings.adapter("domain_internet_health")
        self.assertEqual(adapter.definition.type, "direct_probe")  # type: ignore[union-attr]

    def test_secret_backed_observation_adapter_resolves_and_redacts_credential(self) -> None:
        settings = NetworkAdaptersRuntimeSettings.from_effective_config(
            self._effective_config(mode="librenms")
        )

        adapter = settings.adapter("librenms_primary")
        self.assertEqual(adapter.credential_secret, "LIBRENMS_TOKEN")  # type: ignore[union-attr]
        self.assertEqual(adapter.credential, "librenms-token")  # type: ignore[union-attr]
        self.assertNotIn("librenms-token", repr(settings))

    def test_active_lifecycle_closure_includes_supporting_ssh_adapter_and_secret(self) -> None:
        settings = NetworkAdaptersRuntimeSettings.from_effective_config(
            self._effective_config(mode="lifecycle")
        )

        host = settings.adapter("host_control")
        support = settings.adapter("support_service")
        self.assertEqual(host.supporting_adapter_ids, ("support_service",))  # type: ignore[union-attr]
        self.assertEqual(support.credential, "support-password")  # type: ignore[union-attr]
        self.assertNotIn("support-password", repr(settings))

    def test_missing_supporting_lifecycle_secret_blocks_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_mode(bundle, "lifecycle", include_secrets=False)

            inspection = inspect_candidate(bundle)

        self.assertFalse(inspection.report.activation_eligible)
        self.assertIn(
            "SUPPORT_PASSWORD",
            {finding.message.split("'")[1] for finding in inspection.report.activation_blockers},
        )

    def test_active_power_adapter_reuses_enabled_home_assistant_edge(self) -> None:
        settings = NetworkAdaptersRuntimeSettings.from_effective_config(
            self._effective_config(mode="power")
        )

        adapter = settings.adapter("power_adapter")
        self.assertIsNotNone(adapter.home_assistant)  # type: ignore[union-attr]
        self.assertEqual(adapter.home_assistant.credential, "home-assistant-token")  # type: ignore[union-attr]
        self.assertNotIn("home-assistant-token", repr(settings))

    def test_enabled_power_adapter_requires_enabled_home_assistant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_mode(bundle, "power", include_secrets=True)
            path = bundle / "domains" / "home-assistant.yaml"
            role = json.loads(path.read_text(encoding="utf-8"))
            role["enabled"] = False
            role["provider"] = None
            path.write_text(json.dumps(role), encoding="utf-8")

            inspection = inspect_candidate(bundle)

        self.assertFalse(inspection.report.activation_eligible)
        self.assertIn(
            "config.reference.disabled_capability",
            {finding.code for finding in inspection.report.validation_findings},
        )

    def test_absent_network_roles_create_no_implicit_adapters(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            NetworkAdaptersRuntimeSettings.from_effective_config(effective)

    def _effective_config(
        self,
        *,
        mode: str | None = None,
        include_role: bool = True,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            if not include_role:
                for name in ("inventory.yaml", "policy.yaml", "adapters.yaml"):
                    (bundle / "domains" / "network" / name).unlink()
            elif mode is not None:
                self._write_mode(bundle, mode, include_secrets=True)
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible, inspection.report)
            self.assertIsNotNone(inspection.bundle)
            self.assertIsNotNone(inspection.normalized_candidate_revision)
            self.assertIsNotNone(inspection.secrets)
            return EffectiveConfig(
                activation_generation_id="activation_11111111111111111111111111111111",
                config_generation_id="config_11111111111111111111111111111111",
                secret_generation_id="secrets_11111111111111111111111111111111",
                selection_operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids=MappingProxyType({}),
                config_revision=inspection.normalized_candidate_revision,
                bundle_id="example-home",
                schema_version=1,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )

    @staticmethod
    def _write_mode(bundle: Path, mode: str, *, include_secrets: bool) -> None:
        inventory_path = bundle / "domains" / "network" / "inventory.yaml"
        inventory = __import__("yaml").safe_load(inventory_path.read_text(encoding="utf-8"))
        inventory["enabled"] = True
        adapters_path = bundle / "domains" / "network" / "adapters.yaml"
        adapters = __import__("yaml").safe_load(adapters_path.read_text(encoding="utf-8"))
        policy_path = bundle / "domains" / "network" / "policy.yaml"
        policy = __import__("yaml").safe_load(policy_path.read_text(encoding="utf-8"))
        secrets: list[str] = []

        if mode == "librenms":
            inventory["monitors"][0]["adapter_id"] = "librenms_primary"
            adapters["providers"]["librenms_primary"] = {
                "type": "librenms",
                "base_url": "http://librenms.invalid",
                "credential_secret": "LIBRENMS_TOKEN",
                "service_name": "Example Service",
            }
            secrets.append("LIBRENMS_TOKEN=librenms-token")
        elif mode == "internet_health":
            inventory["internet_health_probe_adapter_id"] = "domain_internet_health"
            adapters["providers"]["domain_internet_health"] = {
                "type": "direct_probe",
                "dns_host": "example.invalid",
                "http_url": "http://example.invalid/generate_204",
            }
        elif mode == "lifecycle":
            adapters["providers"]["host_control"] = {
                "type": "service_control",
                "target_kind": "host",
                "host_id": "example_host",
                "transport": "local",
                "platform": "linux",
                "lifecycle": {
                    "mode": "graceful",
                    "prepare_service_adapter_ids": ["support_service"],
                },
            }
            adapters["providers"]["support_service"] = {
                "type": "service_control",
                "target_kind": "service",
                "host_id": "example_host",
                "transport": "ssh",
                "platform": "linux",
                "address": "example-host.invalid",
                "user": "oracle",
                "password_secret": "SUPPORT_PASSWORD",
                "service_target": "support.service",
                "service_adapter": "systemd",
            }
            policy["actions"].append(
                {
                    "id": "restart_example_host",
                    "target_type": "host",
                    "target_id": "example_host",
                    "adapter_id": "host_control",
                    "operation": "restart_host",
                    "enabled": True,
                    "requires_confirmation": True,
                    "requires_graceful_lifecycle": True,
                    "description": "Restart the example host through its graceful lifecycle.",
                }
            )
            secrets.append("SUPPORT_PASSWORD=support-password")
        elif mode == "power":
            inventory["power_targets"] = [
                {
                    "id": "example_power",
                    "host_id": "example_host",
                    "enabled": True,
                    "adapter_id": "power_adapter",
                    "capabilities": ["power_cycle"],
                }
            ]
            adapters["providers"]["power_adapter"] = {
                "type": "home_assistant_power",
                "power_target_id": "example_power",
                "entity_id": "switch.example_power",
            }
            ha_path = bundle / "domains" / "home-assistant.yaml"
            home_assistant = {
                "enabled": True,
                "provider": "primary",
                "providers": {
                    "primary": {
                        "type": "home_assistant",
                        "base_url": "http://home-assistant.invalid:8123",
                        "credential_secret": "HOME_ASSISTANT_TOKEN",
                    }
                },
                "mappings": {},
                "automations": [],
            }
            ha_path.write_text(json.dumps(home_assistant), encoding="utf-8")
            secrets.append("HOME_ASSISTANT_TOKEN=home-assistant-token")

        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        adapters_path.write_text(json.dumps(adapters), encoding="utf-8")
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        if include_secrets and secrets:
            (bundle / "secrets.env").write_text("\n".join(secrets) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
