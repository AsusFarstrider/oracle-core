from __future__ import annotations

from pathlib import Path
import json
import shutil
import tempfile
import unittest

from oracle_app.configuration import BundleValidationError, load_bundle


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationBundleLoaderTests(unittest.TestCase):
    def test_loads_and_snapshots_complete_example_bundle(self) -> None:
        loaded = load_bundle(EXAMPLE_ROOT)

        self.assertEqual(len(loaded.roles), 16)
        self.assertEqual(loaded.household.household.id, "example_home")
        self.assertEqual(loaded.authored_bytes["bundle.yaml"], (EXAMPLE_ROOT / "bundle.yaml").read_bytes())
        self.assertEqual(loaded.non_authoritative_paths, ("secrets.env.example",))

    def test_bundle_root_and_confined_role_symlinks_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "real-config"
            shutil.copytree(EXAMPLE_ROOT, root)
            target = root / "household.data"
            (root / "household.yaml").rename(target)
            (root / "household.yaml").symlink_to(target.name)
            root_link = parent / "config"
            root_link.symlink_to(root, target_is_directory=True)

            loaded = load_bundle(root_link)

            self.assertEqual(loaded.root, root.resolve())
            self.assertIn("household.data", loaded.non_authoritative_paths)

    def test_role_symlink_cannot_escape_resolved_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "config"
            shutil.copytree(EXAMPLE_ROOT, root)
            outside = parent / "outside.yaml"
            outside.write_text((root / "household.yaml").read_text(encoding="utf-8"), encoding="utf-8")
            (root / "household.yaml").unlink()
            (root / "household.yaml").symlink_to(outside)

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.bundle.path_escape", self._codes(caught.exception))

    def test_reports_role_syntax_and_schema_errors_together(self) -> None:
        with self._bundle_copy() as root:
            (root / "brain.yaml").write_text("runtime: &shared {}\nlogging: *shared\n", encoding="utf-8")
            (root / "access.yaml").write_text("operator_access: {}\n", encoding="utf-8")

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.yaml.alias", self._codes(caught.exception))
            self.assertIn("config.schema.invalid", self._codes(caught.exception))

    def test_enabled_non_satellite_source_requires_access_binding(self) -> None:
        with self._bundle_copy() as root:
            self._append_source(
                root,
                "  - id: resident_phone\n"
                "    enabled: true\n"
                "    type: mobile_app\n"
                "    fixed: false\n",
            )

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.access.missing_source_binding", self._codes(caught.exception))

    def test_source_associations_must_reference_enabled_typed_identities(self) -> None:
        with self._bundle_copy() as root:
            self._append_source(
                root,
                "  - id: resident_phone\n"
                "    enabled: true\n"
                "    type: mobile_app\n"
                "    fixed: true\n"
                "    associated_user_id: missing_user\n"
                "    associated_room_id: missing_room\n",
            )
            self._add_access_binding(root, "resident_phone", "RESIDENT_PHONE_TOKEN")

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.reference.unknown_user", self._codes(caught.exception))
            self.assertIn("config.reference.unknown_room", self._codes(caught.exception))

    def test_enabled_satellites_require_unique_satellite_sources_and_credentials(self) -> None:
        with self._bundle_copy() as root:
            self._append_source(
                root,
                "  - id: living_room_voice\n"
                "    enabled: true\n"
                "    type: satellite\n"
                "    fixed: true\n"
                "    associated_room_id: living_room\n",
            )
            satellite = (
                "    enabled: true\n"
                "    source_id: living_room_voice\n"
                "    platform: linux\n"
                "    capabilities:\n"
                "      voice: true\n"
                "      display: false\n"
                "      music_playback: true\n"
                "      audiobook_playback: true\n"
                "    brain_client:\n"
                "      base_url: http://oracle-brain.invalid:8011\n"
                "      credential_secret: SHARED_TOKEN\n"
                "    control_service:\n"
                "      base_url: http://living-room-control.invalid:8021\n"
                "      local_client_url: http://127.0.0.1:8021\n"
                "      credential_secret: SHARED_TOKEN\n"
                "    enrollment:\n"
                "      credential_secret: ENROLLMENT_TOKEN\n"
                "    audio:\n"
                "      input:\n"
                "        type: system_default\n"
                "      interaction_output:\n"
                "        type: system_default\n"
                "      playback:\n"
                "        adapter: oracle_native\n"
                "    wake:\n"
                "      enabled: true\n"
                "      model:\n"
                "        format: onnx\n"
                "        asset_id: example_wake_model\n"
            )
            (root / "satellites.yaml").write_text(
                "satellites:\n"
                "  - id: living_room_satellite\n" + satellite +
                "  - id: second_satellite\n" + satellite,
                encoding="utf-8",
            )

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.satellite.duplicate_source", self._codes(caught.exception))
            self.assertIn("config.secret.credential_reuse", self._codes(caught.exception))

    def test_aliases_are_unique_within_typed_namespace_including_disabled_entries(self) -> None:
        with self._bundle_copy() as root:
            household = (root / "household.yaml").read_text(encoding="utf-8")
            household = household.replace(
                "defaults:\n",
                "  - id: resident_two\n"
                "    enabled: false\n"
                "    display_name: Resident Two\n"
                "    aliases:\n"
                "      - resident_one\n"
                "    capabilities: {}\n"
                "defaults:\n",
            )
            (root / "household.yaml").write_text(household, encoding="utf-8")

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.identity.alias_collision", self._codes(caught.exception))

    def test_domain_references_are_validated_across_owned_files(self) -> None:
        with self._bundle_copy() as root:
            notifications = root / "domains" / "notifications.yaml"
            notifications.write_text(
                notifications.read_text(encoding="utf-8")
                .replace("audience: []", "audience:\n      - type: source\n        id: missing_source")
                .replace("suppressed_by: []", "suppressed_by: [missing_mode]"),
                encoding="utf-8",
            )
            policy = root / "domains" / "network" / "policy.yaml"
            policy.write_text(
                policy.read_text(encoding="utf-8").replace("target_id: example_service", "target_id: missing_service"),
                encoding="utf-8",
            )

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            paths = {item.path for item in caught.exception.findings}
            self.assertIn("types[0].audience[0].id", paths)
            self.assertIn("types[0].suppressed_by[0]", paths)
            self.assertIn("actions[0].target_id", paths)

    def test_notification_suppression_requires_exact_home_assistant_mode_mapping(self) -> None:
        with self._bundle_copy() as root:
            loaded = load_bundle(root)
            household_path = root / "household.yaml"
            household = loaded.household.model_dump(mode="json")
            household["modes"] = [
                {
                    "id": "quiet",
                    "enabled": True,
                    "display_name": "Quiet Mode",
                    "aliases": [],
                }
            ]
            household_path.write_text(json.dumps(household), encoding="utf-8")

            notifications_path = root / "domains" / "notifications.yaml"
            notifications = loaded.roles["domains/notifications.yaml"].model_dump(mode="json")
            notifications["enabled"] = True
            notifications["types"][0]["enabled"] = True
            notifications["types"][0]["suppressed_by"] = ["quiet"]
            notifications_path.write_text(json.dumps(notifications), encoding="utf-8")

            home_assistant_path = root / "domains" / "home-assistant.yaml"
            home_assistant = loaded.roles["domains/home-assistant.yaml"].model_dump(mode="json")
            home_assistant["enabled"] = True
            home_assistant["provider"] = "home_assistant"
            home_assistant["mappings"] = {}
            home_assistant_path.write_text(json.dumps(home_assistant), encoding="utf-8")

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.reference.suppression_mapping", self._codes(caught.exception))

    def test_notification_audience_requires_enabled_satellite_source(self) -> None:
        with self._bundle_copy() as root:
            loaded = load_bundle(root)
            household = loaded.household.model_dump(mode="json")
            household["sources"] = [
                {
                    "id": "household_browser",
                    "enabled": True,
                    "type": "browser",
                    "fixed": False,
                    "associated_room_id": None,
                    "associated_user_id": None,
                }
            ]
            (root / "household.yaml").write_text(json.dumps(household), encoding="utf-8")
            notifications = loaded.roles["domains/notifications.yaml"].model_dump(mode="json")
            notifications["enabled"] = True
            notifications["types"][0]["enabled"] = True
            notifications["types"][0]["audience"] = [
                {"type": "source", "id": "household_browser"}
            ]
            (root / "domains" / "notifications.yaml").write_text(
                json.dumps(notifications),
                encoding="utf-8",
            )

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.reference.satellite_source", self._codes(caught.exception))

    def test_facts_summarizer_requires_shared_brain_inference(self) -> None:
        with self._bundle_copy() as root:
            information = root / "domains" / "information.yaml"
            information.write_text(
                information.read_text(encoding="utf-8")
                .replace("facts:\n  enabled: false", "facts:\n  enabled: true")
                .replace("  provider: static", "  provider: static\n  summarizer_enabled: true", 1),
                encoding="utf-8",
            )

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.reference.disabled_inference_backend", self._codes(caught.exception))

    def test_graceful_network_policy_requires_adapter_lifecycle_profile(self) -> None:
        with self._bundle_copy() as root:
            adapters = root / "domains" / "network" / "adapters.yaml"
            adapters.write_text(
                adapters.read_text(encoding="utf-8")
                + "  example_host_control:\n"
                + "    type: service_control\n"
                + "    target_kind: host\n"
                + "    host_id: example_host\n"
                + "    transport: local\n"
                + "    platform: linux\n",
                encoding="utf-8",
            )
            policy = root / "domains" / "network" / "policy.yaml"
            policy.write_text(
                policy.read_text(encoding="utf-8")
                .replace("target_type: service", "target_type: host", 1)
                .replace("target_id: example_service", "target_id: example_host", 1)
                .replace("adapter_id: example_service_control", "adapter_id: example_host_control", 1)
                .replace("operation: restart_service", "operation: restart_host", 1)
                .replace("enabled: false", "enabled: false\n    requires_graceful_lifecycle: true", 1),
                encoding="utf-8",
            )

            with self.assertRaises(BundleValidationError) as caught:
                load_bundle(root)

            self.assertIn("config.reference.lifecycle_required", self._codes(caught.exception))

    def test_satellite_component_action_uses_host_owned_service_adapter(self) -> None:
        with self._bundle_copy() as root:
            policy = root / "domains" / "network" / "policy.yaml"
            policy.write_text(
                policy.read_text(encoding="utf-8")
                .replace("target_type: service", "target_type: host", 1)
                .replace("target_id: example_service", "target_id: example_host", 1)
                .replace("operation: restart_service", "operation: restart_runtime", 1),
                encoding="utf-8",
            )
            loaded = load_bundle(root)
            self.assertEqual(
                loaded.roles["domains/network/policy.yaml"].actions[0].operation,
                "restart_runtime",
            )

    def _bundle_copy(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "config"
        shutil.copytree(EXAMPLE_ROOT, root)

        class BundleContext:
            def __enter__(self_nonlocal):
                return root

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return BundleContext()

    @staticmethod
    def _append_source(root: Path, source_yaml: str) -> None:
        path = root / "household.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("sources: []", "sources:\n" + source_yaml), encoding="utf-8")

    @staticmethod
    def _add_access_binding(root: Path, source_id: str, secret: str) -> None:
        path = root / "access.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text
            + "source_authentication:\n"
            + "  credential_bindings:\n"
            + f"    - source_id: {source_id}\n"
            + f"      credential_secret: {secret}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _codes(error: BundleValidationError) -> set[str]:
        return {finding.code for finding in error.findings}


if __name__ == "__main__":
    unittest.main()
