from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oracle_app.configuration import (
    GenerationIntegrityError,
    GenerationStore,
    inspect_candidate,
    load_effective_config,
)


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class EffectiveConfigurationTests(unittest.TestCase):
    def test_loads_one_immutable_selected_snapshot_without_authored_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._selected_store(Path(temporary) / "store")
            with patch(
                "oracle_app.configuration.loader.snapshot_candidate",
                side_effect=AssertionError("authored files must not be read"),
            ):
                effective = load_effective_config(store)

            self.assertEqual(effective.selection_revision, 1)
            self.assertEqual(effective.bundle_id, "example-home")
            self.assertIn("brain.yaml", effective.roles)
            self.assertEqual(effective.secrets.present_ids, frozenset())
            self.assertEqual(dict(effective.satellite_projection_activation_ids), {})
            with self.assertRaises(TypeError):
                effective.roles["extra.yaml"] = effective.roles["brain.yaml"]  # type: ignore[index]

    def test_snapshot_does_not_follow_later_pointer_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._selected_store(Path(temporary) / "store")
            effective = load_effective_config(store)
            original_activation = effective.activation_generation_id

            config, secrets = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))
            activation = store.create_activation(config.generation_id, secrets.generation_id)
            store._replace_selected_pointer(  # noqa: SLF001 - store transaction primitive under test
                activation.generation_id,
                operation_id="selection_op_22222222222222222222222222222222",
                selection_revision=2,
                satellite_projection_activation_ids={},
            )

            self.assertEqual(effective.activation_generation_id, original_activation)
            self.assertNotEqual(store.load_selected().activation.generation_id, original_activation)

    def test_revalidates_executable_role_schema_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "store"
            store = self._selected_store(root)
            selected = store.load_selected()
            path = root / "config-generations" / selected.config.generation_id / "configuration.json"
            configuration = json.loads(path.read_text(encoding="utf-8"))
            configuration["roles"]["brain.yaml"]["unknown_runtime_field"] = True
            self._rewrite_generation_payload(root, selected.config.generation_id, configuration)

            with self.assertRaisesRegex(GenerationIntegrityError, "executable schema"):
                load_effective_config(store)

    @staticmethod
    def _selected_store(root: Path) -> GenerationStore:
        store = GenerationStore(root)
        store.initialize("example-home")
        config, secrets = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))
        activation = store.create_activation(config.generation_id, secrets.generation_id)
        store._replace_selected_pointer(  # noqa: SLF001 - setup for runtime-adoption tests
            activation.generation_id,
            operation_id="selection_op_11111111111111111111111111111111",
            selection_revision=1,
            satellite_projection_activation_ids={},
        )
        return store

    @staticmethod
    def _rewrite_generation_payload(root: Path, generation_id: str, configuration: dict[str, object]) -> None:
        import hashlib
        import rfc8785

        payload = rfc8785.dumps(configuration)
        path = root / "config-generations" / generation_id / "configuration.json"
        path.write_bytes(payload)
        metadata_path = path.parent / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["config_revision"] = f"oracle-config-v1:sha256:{hashlib.sha256(payload).hexdigest()}"
        metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
