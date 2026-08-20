from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from oracle_app.configuration import (
    BrainEffectiveRuntimeSettings,
    EffectiveConfig,
    inspect_candidate,
)


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class BrainEffectiveRuntimeSettingsTests(unittest.TestCase):
    def test_complete_snapshot_constructs_every_present_fixed_role(self) -> None:
        effective = self._effective_config()

        settings = BrainEffectiveRuntimeSettings.from_effective_config(effective)

        self.assertIs(settings.effective_config, effective)
        self.assertEqual(settings.brain.config_revision, effective.config_revision)
        self.assertEqual(settings.household.config_revision, effective.config_revision)
        self.assertEqual(settings.access.config_revision, effective.config_revision)
        self.assertEqual(settings.satellites.config_revision, effective.config_revision)
        self.assertEqual(settings.satellite_ui.config_revision, effective.config_revision)
        for name in (
            "information",
            "music",
            "audiobooks",
            "weather",
            "calendar",
            "home_assistant",
            "notifications",
            "routines",
            "network_inventory",
            "network_adapters",
            "network_policy",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(settings, name))
        with self.assertRaises(FrozenInstanceError):
            settings.information = None  # type: ignore[misc]

    def test_absent_optional_roles_remain_absent_without_defaults(self) -> None:
        settings = BrainEffectiveRuntimeSettings.from_effective_config(
            self._effective_config(include_optional_roles=False)
        )

        self.assertIsNotNone(settings.brain)
        self.assertIsNotNone(settings.household)
        self.assertIsNotNone(settings.access)
        self.assertIsNotNone(settings.satellites)
        self.assertIsNotNone(settings.satellite_ui)
        self.assertIsNone(settings.information)
        self.assertIsNone(settings.music)
        self.assertIsNone(settings.audiobooks)
        self.assertIsNone(settings.weather)
        self.assertIsNone(settings.calendar)
        self.assertIsNone(settings.home_assistant)
        self.assertIsNone(settings.notifications)
        self.assertIsNone(settings.routines)
        self.assertIsNone(settings.network_inventory)
        self.assertIsNone(settings.network_adapters)
        self.assertIsNone(settings.network_policy)

    def _effective_config(self, *, include_optional_roles: bool = True) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            if not include_optional_roles:
                shutil.rmtree(bundle / "domains")
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
                schema_version=2,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
