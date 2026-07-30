from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from oracle_app.configuration import (
    ConfigurationService,
    GenerationIntegrityError,
    GenerationStore,
    RUNTIME_CUTOVER_PATH,
    arm_runtime_cutover,
    inspect_candidate,
    load_runtime_cutover_marker,
    runtime_cutover_required,
)
from oracle_app.configuration.host_local import HOST_LOCAL_PROTOCOL_FORMAT, HostLocalDispatcher


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class RuntimeCutoverMarkerTests(unittest.TestCase):
    def test_absence_is_migration_mode_and_arm_is_durable_one_way(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._selected_store(Path(temporary) / "store")
            self.assertFalse(runtime_cutover_required(store))

            marker, created = arm_runtime_cutover(
                store,
                store.load_selected(),
                actor="host_local_cli",
                audit_event_id="audit_11111111111111111111111111111111",
            )

            self.assertTrue(created)
            self.assertTrue(runtime_cutover_required(store))
            self.assertEqual(load_runtime_cutover_marker(store), marker)
            self.assertEqual(marker.activation_generation_id, store.load_selected().activation.generation_id)
            self.assertEqual((store.root / RUNTIME_CUTOVER_PATH).stat().st_mode & 0o777, 0o600)

            repeated, created = arm_runtime_cutover(store, store.load_selected(), actor="system_mode")
            self.assertFalse(created)
            self.assertEqual(repeated, marker)

    def test_present_but_corrupt_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._selected_store(Path(temporary) / "store")
            (store.root / RUNTIME_CUTOVER_PATH).write_text("{}", encoding="utf-8")

            with self.assertRaises(GenerationIntegrityError):
                runtime_cutover_required(store)

    def test_marker_is_bound_to_store_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._selected_store(Path(temporary) / "store")
            arm_runtime_cutover(store, store.load_selected(), actor="host_local_cli")
            path = store.root / RUNTIME_CUTOVER_PATH
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["bundle_id"] = "another-home"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(GenerationIntegrityError, "lineage"):
                load_runtime_cutover_marker(store)

    def test_selection_change_before_arm_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._selected_store(Path(temporary) / "store")
            stale = store.load_selected()
            config, secrets = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))
            activation = store.create_activation(config.generation_id, secrets.generation_id)
            store._replace_selected_pointer(  # noqa: SLF001 - exact concurrency setup
                activation.generation_id,
                operation_id="selection_op_22222222222222222222222222222222",
                selection_revision=2,
                satellite_projection_activation_ids={},
            )

            with self.assertRaisesRegex(GenerationIntegrityError, "changed"):
                arm_runtime_cutover(store, stale, actor="host_local_cli")

    def test_service_operation_requires_acknowledgement_and_audits_before_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._selected_store(Path(temporary) / "store")
            service = ConfigurationService(store, authoring_mode="external_read_only")
            dispatcher = HostLocalDispatcher(service)

            rejected = dispatcher.dispatch({
                "format": HOST_LOCAL_PROTOCOL_FORMAT,
                "operation": "require_canonical_runtime",
                "acknowledge_one_way": False,
            })
            self.assertFalse(rejected["ok"])
            self.assertFalse(runtime_cutover_required(store))

            response = dispatcher.dispatch({
                "format": HOST_LOCAL_PROTOCOL_FORMAT,
                "operation": "require_canonical_runtime",
                "acknowledge_one_way": True,
            })
            self.assertTrue(response["ok"])
            result = response["result"]
            self.assertEqual(result["outcome"], "armed")
            marker = load_runtime_cutover_marker(store)
            audit = json.loads((store.root / "audit" / f"{marker.audit_event_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["operation"], "require_canonical_runtime")
            self.assertEqual(audit["outcome"], "cutover_requested")
            self.assertEqual(audit["safety_acknowledgements"], ["canonical_runtime_cutover"])
            self.assertTrue(service.status().canonical_runtime_required)

    @staticmethod
    def _selected_store(root: Path) -> GenerationStore:
        store = GenerationStore(root)
        store.initialize("example-home")
        config, secrets = store.install_candidate(inspect_candidate(EXAMPLE_ROOT))
        activation = store.create_activation(config.generation_id, secrets.generation_id)
        store._replace_selected_pointer(  # noqa: SLF001 - setup for cutover tests
            activation.generation_id,
            operation_id="selection_op_11111111111111111111111111111111",
            selection_revision=1,
            satellite_projection_activation_ids={},
        )
        return store


if __name__ == "__main__":
    unittest.main()
