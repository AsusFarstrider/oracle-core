from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from oracle_app.configuration import EffectiveConfig, NetworkInventoryRuntimeSettings, inspect_candidate


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class NetworkInventoryRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_inventory_selects_no_operational_topology(self) -> None:
        settings = NetworkInventoryRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.enabled)
        self.assertIsNone(settings.internet_health_probe_adapter_id)
        self.assertEqual(dict(settings.hosts), {})
        self.assertEqual(dict(settings.services), {})
        self.assertEqual(dict(settings.monitors), {})

    def test_enabled_inventory_binds_declared_topology_without_adapter_selection(self) -> None:
        settings = NetworkInventoryRuntimeSettings.from_effective_config(
            self._effective_config(enabled=True)
        )

        self.assertTrue(settings.enabled)
        self.assertIsNone(settings.internet_health_probe_adapter_id)
        self.assertEqual(settings.devices["example_device"].host.id, "example_host")  # type: ignore[union-attr]
        self.assertEqual(settings.services["example_service"].host.id, "example_host")
        self.assertEqual(settings.service_groups["example_services"].services[0].id, "example_service")
        self.assertEqual(settings.monitors["example_service_health"].target.id, "example_service")
        dependency = settings.dependencies["service_uses_device"]
        self.assertEqual(dependency.from_target.id, "example_service")
        self.assertEqual(dependency.to_target.id, "example_device")
        self.assertIsNone(settings.power_target("example_power"))
        self.assertEqual(
            settings.power_target("example_power", enabled_only=False).host.id,  # type: ignore[union-attr]
            "example_host",
        )
        self.assertEqual(
            settings.monitors["example_service_health"].definition.adapter_id,
            "example_http_probe",
        )
        self.assertFalse(hasattr(settings.monitors["example_service_health"], "adapter"))
        with self.assertRaises(TypeError):
            settings.hosts["other"] = settings.hosts["example_host"]  # type: ignore[index]

    def test_unknown_device_host_and_dependency_endpoint_block_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_enabled_inventory(bundle)
            path = bundle / "domains" / "network" / "inventory.yaml"
            inventory = json.loads(path.read_text(encoding="utf-8"))
            inventory["devices"][0]["host_id"] = "missing_host"
            inventory["dependencies"][0]["to_id"] = "missing_device"
            path.write_text(json.dumps(inventory), encoding="utf-8")

            inspection = inspect_candidate(bundle)

        self.assertFalse(inspection.report.activation_eligible)
        paths = {finding.path for finding in inspection.report.validation_findings}
        self.assertIn("devices[0].host_id", paths)
        self.assertIn("dependencies[0].to_id", paths)

    def test_absent_network_roles_create_no_implicit_inventory(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            NetworkInventoryRuntimeSettings.from_effective_config(effective)

    def _effective_config(
        self,
        *,
        enabled: bool = False,
        include_role: bool = True,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            if not include_role:
                for name in ("inventory.yaml", "policy.yaml", "adapters.yaml"):
                    (bundle / "domains" / "network" / name).unlink()
            elif enabled:
                self._write_enabled_inventory(bundle)
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
    def _write_enabled_inventory(bundle: Path) -> None:
        inventory_path = bundle / "domains" / "network" / "inventory.yaml"
        inventory = __import__("yaml").safe_load(inventory_path.read_text(encoding="utf-8"))
        inventory["enabled"] = True
        inventory["devices"] = [
            {
                "id": "example_device",
                "display_name": "Example Device",
                "kind": "appliance",
                "host_id": "example_host",
            }
        ]
        inventory["dependencies"] = [
            {
                "id": "service_uses_device",
                "from_type": "service",
                "from_id": "example_service",
                "to_type": "device",
                "to_id": "example_device",
                "relationship": "depends_on",
            }
        ]
        inventory["power_targets"] = [
            {
                "id": "example_power",
                "host_id": "example_host",
                "enabled": False,
                "adapter_id": "example_power_adapter",
                "capabilities": ["power_cycle"],
            }
        ]
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

        adapters_path = bundle / "domains" / "network" / "adapters.yaml"
        adapters = __import__("yaml").safe_load(adapters_path.read_text(encoding="utf-8"))
        adapters["providers"]["example_power_adapter"] = {
            "type": "home_assistant_power",
            "power_target_id": "example_power",
            "entity_id": "switch.example_power",
        }
        adapters_path.write_text(json.dumps(adapters), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
