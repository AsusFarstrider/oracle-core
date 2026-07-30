from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

from oracle_app.configuration import OPTIONAL_ROLE_PATHS, REQUIRED_ROLE_PATHS


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "docs" / "reference" / "generated" / "configuration-v1.schema.json"


class ConfigurationJsonSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_checked_in_schema_matches_executable_models(self) -> None:
        subprocess.run(
            [sys.executable, "scripts/generate-config-schema.py"],
            cwd=REPO_ROOT,
            check=True,
        )

    def test_schema_covers_exact_fixed_roles_and_required_set(self) -> None:
        self.assertEqual(set(self.schema["properties"]), set(REQUIRED_ROLE_PATHS | OPTIONAL_ROLE_PATHS))
        self.assertEqual(set(self.schema["required"]), set(REQUIRED_ROLE_PATHS))
        self.assertFalse(self.schema["additionalProperties"])

    def test_each_role_declares_owner_restart_and_safety_metadata(self) -> None:
        for role_path, role_schema in self.schema["properties"].items():
            with self.subTest(role=role_path):
                self.assertEqual(role_schema["x-oracle-file-role"], role_path)
                self.assertTrue(role_schema["x-oracle-owner"])
                self.assertEqual(role_schema["x-oracle-restart-impact"], "restart_required")
                self.assertIsInstance(role_schema["x-oracle-safety-classifications"], list)
                self.assertEqual(role_schema["x-oracle-required-role"], role_path in REQUIRED_ROLE_PATHS)

    def test_logical_secret_references_and_raw_secret_input_are_distinct(self) -> None:
        access_schema_text = json.dumps(self.schema["properties"]["access.yaml"], sort_keys=True)
        self.assertIn('"x-oracle-secret-reference": true', access_schema_text)
        self.assertNotIn('"x-oracle-raw-secret": true', access_schema_text)

        secret_input = self.schema["x-oracle-service-inputs"]["secret_mutation"]
        secret_text = json.dumps(secret_input, sort_keys=True)
        self.assertIn('"writeOnly": true', secret_text)
        self.assertIn('"x-oracle-raw-secret": true', secret_text)

    def test_domain_schema_contains_typed_leaves_without_arbitrary_option_bags(self) -> None:
        domain_text = json.dumps(
            {
                path: schema
                for path, schema in self.schema["properties"].items()
                if path.startswith("domains/")
            },
            sort_keys=True,
        )
        for definition in (
            "PlexMusicProvider",
            "AudiobookshelfProvider",
            "WeeWxWeatherProvider",
            "NextcloudCalendarProvider",
            "HomeAssistantProvider",
            "UiActionStep",
            "ServiceControlAdapter",
        ):
            self.assertIn(definition, domain_text)
        self.assertIn('"x-oracle-secret-reference": true', domain_text)
        self.assertNotIn('"x-oracle-raw-secret": true', domain_text)
        for role_path, role_schema in self.schema["properties"].items():
            if not role_path.startswith("domains/"):
                continue
            with self.subTest(role=role_path):
                self._assert_pattern_maps_closed(role_schema)

    def test_brain_and_satellite_schema_contains_closed_typed_runtime_leaves(self) -> None:
        required_text = json.dumps(
            {
                path: self.schema["properties"][path]
                for path in ("brain.yaml", "satellites.yaml")
            },
            sort_keys=True,
        )
        for definition in (
            "FastWhisperProvider",
            "WhisperCppProvider",
            "PiperProvider",
            "OllamaProvider",
            "SatelliteAudioConfiguration",
            "SatelliteUiConfiguration",
            "SatelliteWakeConfiguration",
            "AlsaCaptureDevice",
        ):
            self.assertIn(definition, required_text)
        self.assertNotIn('"x-oracle-raw-secret": true', required_text)
        self._assert_pattern_maps_closed(self.schema["properties"]["brain.yaml"])
        self._assert_pattern_maps_closed(self.schema["properties"]["satellites.yaml"])

    def _assert_pattern_maps_closed(self, value) -> None:
        if isinstance(value, dict):
            if "patternProperties" in value:
                self.assertFalse(value.get("additionalProperties", True))
            for child in value.values():
                self._assert_pattern_maps_closed(child)
        elif isinstance(value, list):
            for child in value:
                self._assert_pattern_maps_closed(child)


if __name__ == "__main__":
    unittest.main()
