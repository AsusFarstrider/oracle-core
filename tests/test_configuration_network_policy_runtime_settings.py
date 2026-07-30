from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from oracle_app.configuration import EffectiveConfig, NetworkPolicyRuntimeSettings, inspect_candidate


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class NetworkPolicyRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_policy_definitions_grant_no_operational_authority(self) -> None:
        settings = NetworkPolicyRuntimeSettings.from_effective_config(self._effective_config())

        self.assertEqual(dict(settings.actions), {})
        self.assertEqual(dict(settings.recoveries), {})
        self.assertEqual(dict(settings.recovery_voice_phrases), {})

    def test_enabled_action_binds_exact_inventory_target_and_typed_adapter(self) -> None:
        settings = NetworkPolicyRuntimeSettings.from_effective_config(
            self._effective_config(mode="action")
        )

        action = settings.action("example_service_restart")
        self.assertEqual(action.target.id, "example_service")  # type: ignore[union-attr]
        self.assertEqual(action.adapter.adapter_id, "example_service_control")  # type: ignore[union-attr]
        self.assertEqual(action.adapter.definition.type, "service_control")  # type: ignore[union-attr]
        self.assertTrue(action.definition.requires_confirmation)  # type: ignore[union-attr]
        with self.assertRaises(TypeError):
            settings.actions["other"] = action  # type: ignore[index]

    def test_enabled_recovery_retains_plan_and_profile_identity_and_voice_lookup(self) -> None:
        settings = NetworkPolicyRuntimeSettings.from_effective_config(
            self._effective_config(mode="recovery")
        )

        recovery = settings.recovery("example_recovery")
        self.assertEqual(recovery.definition.approval_mode, "plan")  # type: ignore[union-attr]
        self.assertEqual(recovery.definition.diagnostic_profile, "network_full_v1")  # type: ignore[union-attr]
        self.assertEqual(recovery.definition.remediation_profile, "network_full_v1")  # type: ignore[union-attr]
        self.assertIs(
            settings.recovery_for_voice_phrase("  FIX   EXAMPLE NETWORK "),
            recovery,
        )

    def test_recovery_only_policy_does_not_require_adapter_role(self) -> None:
        effective = self._effective_config(mode="recovery_without_adapters")

        settings = NetworkPolicyRuntimeSettings.from_effective_config(effective)

        self.assertIsNotNone(settings.recovery("example_recovery"))
        self.assertEqual(dict(settings.actions), {})

    def test_enabled_power_action_cannot_bind_disabled_power_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_mode(bundle, "disabled_power")

            inspection = inspect_candidate(bundle)

        self.assertFalse(inspection.report.activation_eligible)
        self.assertIn(
            ("config.reference.disabled_capability", "actions[1].target_id"),
            {(finding.code, finding.path) for finding in inspection.report.validation_findings},
        )

    def test_absent_network_roles_create_no_implicit_policy(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            NetworkPolicyRuntimeSettings.from_effective_config(effective)

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
                self._write_mode(bundle, mode)
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
    def _write_mode(bundle: Path, mode: str) -> None:
        inventory_path = bundle / "domains" / "network" / "inventory.yaml"
        inventory = __import__("yaml").safe_load(inventory_path.read_text(encoding="utf-8"))
        inventory["enabled"] = True
        policy_path = bundle / "domains" / "network" / "policy.yaml"
        policy = __import__("yaml").safe_load(policy_path.read_text(encoding="utf-8"))

        if mode == "action":
            policy["actions"][0]["enabled"] = True
        elif mode == "recovery":
            policy["recoveries"][0]["enabled"] = True
            policy["recoveries"][0]["triggers"] = {
                "ui": True,
                "voice": True,
                "global_phrases": ["fix example network"],
            }
        elif mode == "recovery_without_adapters":
            inventory["monitors"] = []
            policy["actions"] = []
            policy["recoveries"][0]["enabled"] = True
            (bundle / "domains" / "network" / "adapters.yaml").unlink()
        elif mode == "disabled_power":
            inventory["power_targets"] = [
                {
                    "id": "example_power",
                    "host_id": "example_host",
                    "enabled": False,
                    "adapter_id": "example_power_adapter",
                    "capabilities": ["power_cycle"],
                }
            ]
            adapters_path = bundle / "domains" / "network" / "adapters.yaml"
            adapters = __import__("yaml").safe_load(adapters_path.read_text(encoding="utf-8"))
            adapters["providers"]["example_power_adapter"] = {
                "type": "home_assistant_power",
                "power_target_id": "example_power",
                "entity_id": "switch.example_power",
            }
            adapters_path.write_text(json.dumps(adapters), encoding="utf-8")
            policy["actions"].append(
                {
                    "id": "cycle_example_power",
                    "target_type": "power_target",
                    "target_id": "example_power",
                    "adapter_id": "example_power_adapter",
                    "operation": "power_cycle",
                    "enabled": True,
                    "requires_confirmation": True,
                    "description": "Cycle the disabled example power target.",
                }
            )

        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        policy_path.write_text(json.dumps(policy), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
