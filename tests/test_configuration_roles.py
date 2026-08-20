from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from pydantic import ValidationError

from oracle_app.configuration import (
    BundleManifest,
    BundleRoleError,
    OPTIONAL_ROLE_PATHS,
    REQUIRED_ROLE_PATHS,
    RestrictedYamlParser,
    discover_bundle_roles,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationRoleTests(unittest.TestCase):
    def test_complete_example_uses_exact_fixed_role_registry(self) -> None:
        inventory = discover_bundle_roles(EXAMPLE_ROOT)

        self.assertEqual(set(inventory.role_paths), set(REQUIRED_ROLE_PATHS | OPTIONAL_ROLE_PATHS))
        self.assertEqual(inventory.non_authoritative_paths, ("secrets.env.example",))

    def test_bundle_manifest_example_passes_executable_schema(self) -> None:
        parsed = RestrictedYamlParser().parse((EXAMPLE_ROOT / "bundle.yaml").read_text(encoding="utf-8"))

        manifest = BundleManifest.model_validate(parsed.primitive)

        self.assertEqual(manifest.schema_version, 2)
        self.assertEqual(manifest.bundle_id, "example-home")

    def test_bundle_manifest_rejects_unknown_fields_and_invalid_identity(self) -> None:
        for payload in (
            {
                "kind": "oracle_configuration_bundle",
                "schema_version": 2,
                "bundle_id": "Example Home",
            },
            {
                "kind": "oracle_configuration_bundle",
                "schema_version": 2,
                "bundle_id": "example_home",
                "include": "other.yaml",
            },
            {
                "kind": "oracle_configuration_bundle",
                "schema_version": 3,
                "bundle_id": "example_home",
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    BundleManifest.model_validate(payload)

    def test_unknown_yaml_role_is_an_error_but_other_files_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            shutil.copytree(EXAMPLE_ROOT, root)
            (root / "notes.txt").write_text("operator note\n", encoding="utf-8")
            inventory = discover_bundle_roles(root)
            self.assertIn("notes.txt", inventory.non_authoritative_paths)

            (root / "domains" / "custom.yaml").write_text("enabled: false\n", encoding="utf-8")
            with self.assertRaises(BundleRoleError) as caught:
                discover_bundle_roles(root)
            self.assertEqual(caught.exception.code, "config.bundle.unknown_role")
            self.assertEqual(caught.exception.paths, ("domains/custom.yaml",))

    def test_missing_required_role_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            shutil.copytree(EXAMPLE_ROOT, root)
            (root / "household.yaml").unlink()

            with self.assertRaises(BundleRoleError) as caught:
                discover_bundle_roles(root)

            self.assertEqual(caught.exception.code, "config.bundle.missing_role")
            self.assertEqual(caught.exception.paths, ("household.yaml",))

    def test_network_policy_and_adapters_require_inventory_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            shutil.copytree(EXAMPLE_ROOT, root)
            (root / "domains" / "network" / "inventory.yaml").unlink()

            with self.assertRaises(BundleRoleError) as caught:
                discover_bundle_roles(root)

            self.assertEqual(caught.exception.code, "config.bundle.network_anchor")
            self.assertEqual(
                caught.exception.paths,
                ("domains/network/policy.yaml", "domains/network/adapters.yaml"),
            )


if __name__ == "__main__":
    unittest.main()
