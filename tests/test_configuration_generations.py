from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import (
    GenerationCompatibilityError,
    GenerationIntegrityError,
    GenerationStore,
    SecretSnapshot,
    StoreLineageConflict,
    inspect_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationGenerationTests(unittest.TestCase):
    def test_standard_split_store_keeps_secret_lifecycle_out_of_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = GenerationStore(root / "configuration", secret_root=root / "secrets")
            store.initialize("example-home")
            config, secret = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))
            activation = store.create_activation(config.generation_id, secret.generation_id)

            self.assertTrue((store.root / "config-generations" / config.generation_id).is_dir())
            self.assertTrue((store.root / "activations" / activation.generation_id).is_dir())
            self.assertTrue((store.secret_root / "secret-generations" / secret.generation_id).is_dir())
            self.assertTrue((store.secret_root / "secret-status" / f"{secret.generation_id}.json").is_file())
            self.assertFalse((store.root / "secret-generations").exists())
            self.assertFalse((store.root / "secret-status").exists())
            self.assertEqual(store.secret_transactions_root, root / "secrets" / "transactions")

    def test_installs_immutable_generation_chain_and_atomically_selects_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "store"
            store = GenerationStore(root)
            store.initialize("example-home")
            config, secrets = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))
            activation = store.create_activation(config.generation_id, secrets.generation_id)

            selected = self._select(store, activation.generation_id)
            reloaded = store.load_selected()

            self.assertEqual(reloaded.activation, selected.activation)
            self.assertEqual(reloaded.config, selected.config)
            self.assertEqual(reloaded.secrets.generation_id, selected.secrets.generation_id)
            self.assertEqual(reloaded.secrets.snapshot.present_ids, selected.secrets.snapshot.present_ids)
            self.assertRegex(config.generation_id, r"^config_[0-9a-f]{32}$")
            self.assertRegex(secrets.generation_id, r"^secret_[0-9a-f]{32}$")
            self.assertRegex(activation.generation_id, r"^activation_[0-9a-f]{32}$")
            pointer = json.loads((root / "selected.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer["activation_generation_id"], activation.generation_id)
            self.assertEqual(pointer["config_generation_id"], config.generation_id)
            self.assertEqual(pointer["secret_generation_id"], secrets.generation_id)
            self.assertEqual(pointer["satellite_projection_activation_ids"], {})
            self.assertRegex(pointer["operation_id"], r"^selection_op_[0-9a-f]{32}$")
            self.assertEqual(pointer["selection_revision"], 1)

            second_activation = self._activation(store)
            self._select(store, second_activation.generation_id)
            second_pointer = json.loads((root / "selected.json").read_text(encoding="utf-8"))
            self.assertEqual(second_pointer["selection_revision"], 2)
            self.assertNotEqual(second_pointer["operation_id"], pointer["operation_id"])

    def test_secret_values_exist_only_in_restricted_secret_payload(self) -> None:
        with self._store() as (root, store):
            generation = store.install_secrets(SecretSnapshot({"EXAMPLE_TOKEN": "do-not-report"}))
            directory = root / "secret-generations" / generation.generation_id

            self.assertNotIn("do-not-report", (directory / "metadata.json").read_text(encoding="utf-8"))
            if os.name != "nt":
                self.assertEqual((directory / "secrets.json").stat().st_mode & 0o777, 0o600)

    def test_config_tampering_is_detected_without_changing_selected_pointer(self) -> None:
        with self._store() as (root, store):
            first = self._activation(store)
            self._select(store, first.generation_id)
            original_pointer = (root / "selected.json").read_bytes()
            second = self._activation(store)
            config_id = store.load_activation(second.generation_id).config_generation_id
            payload = root / "config-generations" / config_id / "configuration.json"
            payload.write_bytes(payload.read_bytes() + b" ")

            with self.assertRaises(GenerationIntegrityError):
                self._select(store, second.generation_id)

            self.assertEqual((root / "selected.json").read_bytes(), original_pointer)
            self.assertEqual(store.load_selected().activation.generation_id, first.generation_id)

    def test_pointer_reference_mismatch_is_detected(self) -> None:
        with self._store() as (root, store):
            first = self._activation(store)
            second = self._activation(store)
            self._select(store, first.generation_id)
            pointer_path = root / "selected.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            pointer["config_generation_id"] = store.load_activation(second.generation_id).config_generation_id
            pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

            with self.assertRaises(GenerationIntegrityError):
                store.load_selected()

    def test_installed_artifact_symlinks_are_rejected(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with self._store() as (root, store):
            activation = self._activation(store)
            self._select(store, activation.generation_id)
            pointer = root / "selected.json"
            outside = root.parent / "outside-selected.json"
            outside.write_bytes(pointer.read_bytes())
            pointer.unlink()
            pointer.symlink_to(outside)

            with self.assertRaises(GenerationIntegrityError):
                store.load_selected()

    def test_non_object_config_payload_is_rejected_as_corruption(self) -> None:
        with self._store() as (root, store):
            generation, _secrets = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))
            payload = root / "config-generations" / generation.generation_id / "configuration.json"
            payload.write_text("[]", encoding="utf-8")

            with self.assertRaises(GenerationIntegrityError):
                store.load_config(generation.generation_id)

    def test_store_binding_rejects_different_bundle_lineage(self) -> None:
        with self._store() as (_root, store):
            with self.assertRaises(StoreLineageConflict):
                store.initialize("different-home")

    def test_store_rejects_invalid_lineage_and_secret_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                GenerationStore(Path(temporary) / "invalid").initialize("Example Home")
        with self._store() as (root, store):
            with self.assertRaises(GenerationIntegrityError):
                store.install_secrets(SecretSnapshot({"not-canonical": "value"}))
            self.assertEqual(tuple((root / "secret-generations").iterdir()), ())

    def test_malformed_pointer_identifier_fails_closed(self) -> None:
        with self._store() as (root, store):
            activation = self._activation(store)
            self._select(store, activation.generation_id)
            pointer_path = root / "selected.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            pointer["activation_generation_id"] = None
            pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

            with self.assertRaises(GenerationIntegrityError):
                store.load_selected()

    def test_activation_and_rollback_share_schema_compatibility_gate(self) -> None:
        with self._store() as (root, store):
            activation = self._activation(store)
            self._select(store, activation.generation_id)

            incompatible_runtime = GenerationStore(root, supported_schema_versions=frozenset())
            with self.assertRaises(GenerationCompatibilityError):
                incompatible_runtime.load_selected()
            with self.assertRaises(GenerationCompatibilityError):
                self._select(incompatible_runtime, activation.generation_id)

    def test_activation_blocked_candidate_cannot_become_config_generation(self) -> None:
        with self._bundle_with_required_secret(include_secret=False) as bundle_root:
            inspection = inspect_candidate(bundle_root)
            self.assertFalse(inspection.report.activation_eligible)
            with self._store() as (store_root, store):
                with self.assertRaises(ValueError):
                    store.install_candidate(inspection)
                self.assertEqual(tuple((store_root / "config-generations").iterdir()), ())

    def test_activation_pair_revalidates_required_secrets_for_rollback(self) -> None:
        with self._bundle_with_required_secret(include_secret=True) as bundle_root:
            with self._store() as (_root, store):
                config, _candidate_secrets = store.install_candidate(inspect_candidate(bundle_root))
                empty_secrets = store.install_secrets(SecretSnapshot())

                with self.assertRaises(ValueError):
                    store.create_activation(config.generation_id, empty_secrets.generation_id)

    def test_generation_ids_cannot_escape_the_store(self) -> None:
        with self._store() as (_root, store):
            with self.assertRaises(GenerationIntegrityError):
                store.load_config("../config_escape")
            with self.assertRaises(GenerationIntegrityError):
                store.load_secrets("secret_not-hex")
            with self.assertRaises(GenerationIntegrityError):
                store.load_activation("config_00000000000000000000000000000000")

    def test_loaded_configuration_is_deeply_immutable(self) -> None:
        with self._store() as (_root, store):
            generation, _secrets = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))

            with self.assertRaises(TypeError):
                generation.configuration["roles"]["brain.yaml"]["logging"]["level"] = "DEBUG"

    def _activation(self, store: GenerationStore):
        config, secret_generation = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))
        return store.create_activation(config.generation_id, secret_generation.generation_id)

    @staticmethod
    def _select(store: GenerationStore, activation_generation_id: str):
        _operation, revision = store.selection_metadata()
        return store._replace_selected_pointer(
            activation_generation_id,
            operation_id="selection_op_" + f"{revision + 1:032x}",
            selection_revision=revision + 1,
            satellite_projection_activation_ids={},
        )

    def _store(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "store"
        store = GenerationStore(root)
        store.initialize("example-home")

        class StoreContext:
            def __enter__(self_nonlocal):
                return root, store

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return StoreContext()

    def _bundle_with_required_secret(self, *, include_secret: bool):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "config"
        shutil.copytree(EXAMPLE_ROOT, root)
        household_path = root / "household.yaml"
        household_path.write_text(
            household_path.read_text(encoding="utf-8").replace(
                "sources: []",
                "sources:\n"
                "  - id: resident_phone\n"
                "    enabled: true\n"
                "    type: mobile_app\n"
                "    fixed: false",
            ),
            encoding="utf-8",
        )
        access_path = root / "access.yaml"
        access_path.write_text(
            access_path.read_text(encoding="utf-8")
            + "source_authentication:\n"
            + "  credential_bindings:\n"
            + "    - source_id: resident_phone\n"
            + "      credential_secret: RESIDENT_PHONE_TOKEN\n",
            encoding="utf-8",
        )
        if include_secret:
            (root / "secrets.env").write_text("RESIDENT_PHONE_TOKEN=secret\n", encoding="utf-8")

        class BundleContext:
            def __enter__(self_nonlocal):
                return root

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return BundleContext()


if __name__ == "__main__":
    unittest.main()
