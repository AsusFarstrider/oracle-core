from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

from oracle_app.configuration import (
    BrainCoreRuntimeConsumers,
    BrainEffectiveRuntimeSettings,
    GenerationStore,
    inspect_candidate,
    load_effective_config,
)


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class Stage7InferenceConfigurationTests(unittest.TestCase):
    def test_five_supported_provider_configurations_and_independent_orders(self) -> None:
        cases = (
            (("luna", "local_ollama"), ("local_ollama", "luna")),
            (("local_ollama", "luna"), ("luna", "local_ollama")),
            (("luna",), ("luna",)),
            (("local_ollama",), ("local_ollama",)),
            ((), ("luna",)),
            (("local_ollama",), ()),
            ((), ()),
        )
        for fallback_order, facts_order in cases:
            with self.subTest(fallback=fallback_order, facts=facts_order), self._bundle() as root:
                self._configure(root, fallback_order=fallback_order, facts_order=facts_order)
                if "luna" in fallback_order or "luna" in facts_order:
                    (root / "secrets.env").write_text("OPENAI_LUNA_API_KEY=fixture-secret\n", encoding="utf-8")

                inspection = inspect_candidate(root)

                self.assertTrue(inspection.report.activation_eligible, inspection.report)
                runtime = self._runtime(root)
                consumers = BrainCoreRuntimeConsumers.from_runtime_settings(
                    runtime.brain,
                    facts=None if runtime.information is None else runtime.information.facts,
                    secrets=runtime.effective_config.secrets,
                )
                self.assertEqual(
                    consumers.inference.settings.consumer_orders.get("fallback_router", ()),
                    fallback_order,
                )
                self.assertEqual(
                    consumers.inference.settings.consumer_orders.get("facts_summarizer", ()),
                    facts_order,
                )
                if fallback_order or facts_order:
                    self.assertTrue(consumers.inference.enabled)
                else:
                    self.assertFalse(consumers.inference.enabled)

    def test_selected_luna_missing_secret_blocks_but_dormant_luna_does_not(self) -> None:
        with self._bundle() as root:
            self._configure(root, fallback_order=("luna",), facts_order=())
            active = inspect_candidate(root)
            self.assertFalse(active.report.activation_eligible)
            self.assertEqual(
                [(item.code, item.path) for item in active.report.activation_blockers],
                [
                    (
                        "config.secret.required_missing",
                        "inference.shared_backend.providers.luna.credential_secret",
                    )
                ],
            )

        with self._bundle() as root:
            self._configure(root, fallback_order=("local_ollama",), facts_order=())
            dormant = inspect_candidate(root)
            self.assertTrue(dormant.report.activation_eligible, dormant.report)
            self.assertEqual(dormant.report.activation_blockers, ())

    def test_ambient_key_does_not_enable_or_select_cloud(self) -> None:
        with self._bundle() as root:
            (root / "secrets.env").write_text("OPENAI_LUNA_API_KEY=fixture-secret\n", encoding="utf-8")

            inspection = inspect_candidate(root)
            runtime = self._runtime(root)

            self.assertTrue(inspection.report.activation_eligible)
            self.assertFalse(runtime.brain.inference.enabled)
            self.assertNotIn("luna", runtime.brain.inference.providers)
            self.assertEqual(
                [item.code for item in inspection.report.validation_findings],
                ["config.secret.unreferenced"],
            )

    def test_unknown_disabled_and_duplicate_consumer_entries_are_rejected(self) -> None:
        invalid_orders = (("missing",), ("luna", "luna"))
        for order in invalid_orders:
            with self.subTest(order=order), self._bundle() as root:
                self._configure(root, fallback_order=order, facts_order=())
                inspection = inspect_candidate(root)
                self.assertFalse(inspection.report.activation_eligible)

        with self._bundle() as root:
            self._configure(root, fallback_order=("luna",), facts_order=(), luna_enabled=False)
            inspection = inspect_candidate(root)
            self.assertFalse(inspection.report.activation_eligible)

    def _configure(
        self,
        root: Path,
        *,
        fallback_order: tuple[str, ...],
        facts_order: tuple[str, ...],
        luna_enabled: bool = True,
    ) -> None:
        brain_path = root / "brain.yaml"
        brain = yaml.safe_load(brain_path.read_text(encoding="utf-8"))
        shared = brain["inference"]["shared_backend"]
        shared["enabled"] = bool(fallback_order or facts_order)
        shared["provider"] = "local_ollama" if "local_ollama" in fallback_order or "local_ollama" in facts_order else None
        shared["providers"]["luna"] = {
            "type": "openai_luna",
            "enabled": luna_enabled,
            "credential_secret": "OPENAI_LUNA_API_KEY",
            "timeout_seconds": 6.0,
            "max_output_tokens": 256,
        }
        shared["fallback_router"] = {
            "enabled": bool(fallback_order),
            "provider_order": list(fallback_order),
            "total_timeout_seconds": 9.0,
        }
        brain_path.write_text(yaml.safe_dump(brain, sort_keys=False), encoding="utf-8")

        information_path = root / "domains" / "information.yaml"
        information = yaml.safe_load(information_path.read_text(encoding="utf-8"))
        information["facts"]["enabled"] = bool(facts_order)
        information["facts"]["summarizer_enabled"] = bool(facts_order)
        information["facts"]["summarizer_provider_order"] = list(facts_order)
        information["facts"]["summarizer_total_timeout_seconds"] = 9
        information_path.write_text(yaml.safe_dump(information, sort_keys=False), encoding="utf-8")

    def _runtime(self, root: Path) -> BrainEffectiveRuntimeSettings:
        temporary_store = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_store.cleanup)
        store = GenerationStore(Path(temporary_store.name) / "store")
        store.initialize("example-home")
        config, secrets = store.install_candidate(inspect_candidate(root))
        activation = store.create_activation(config.generation_id, secrets.generation_id)
        store._replace_selected_pointer(  # noqa: SLF001 - selected runtime fixture
            activation.generation_id,
            operation_id="selection_op_11111111111111111111111111111111",
            selection_revision=1,
            satellite_projection_activation_ids={},
        )
        return BrainEffectiveRuntimeSettings.from_effective_config(load_effective_config(store))

    def _bundle(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "config"
        shutil.copytree(EXAMPLE_ROOT, root)

        class BundleContext:
            def __enter__(self_nonlocal):
                return root

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return BundleContext()


if __name__ == "__main__":
    unittest.main()
