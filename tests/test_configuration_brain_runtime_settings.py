from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from oracle_app.configuration import (
    BrainRuntimeSettings,
    GenerationStore,
    inspect_candidate,
    load_effective_config,
)
from oracle_app.configuration.runtime_models import (
    FastWhisperProvider,
    OllamaProvider,
    PiperProvider,
)


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class BrainRuntimeSettingsTests(unittest.TestCase):
    def test_constructs_complete_frozen_brain_owned_settings(self) -> None:
        effective = self._effective_config()

        settings = BrainRuntimeSettings.from_effective_config(effective)

        self.assertEqual(settings.activation_generation_id, effective.activation_generation_id)
        self.assertEqual(settings.selection_revision, effective.selection_revision)
        self.assertEqual(settings.config_revision, effective.config_revision)
        self.assertEqual(settings.runtime.wake_arbitration.window_ms, 1000)
        self.assertEqual(settings.logging.level, "INFO")
        self.assertEqual(settings.memory_storage.database_path, "data/oracle-memory.sqlite3")
        self.assertEqual(settings.alert_storage.state_path, "data/alerts-state.json")
        self.assertFalse(settings.stt.enabled)
        self.assertIsNone(settings.stt.provider_id)
        self.assertIsNone(settings.stt.provider)
        self.assertFalse(settings.tts.enabled)
        self.assertFalse(settings.inference.enabled)
        with self.assertRaises(AttributeError):
            settings.selection_revision = 2  # type: ignore[misc]

    def test_resolves_only_enabled_explicit_provider_selections(self) -> None:
        effective = self._effective_config(
            {
                "speech": {
                    "stt": {"enabled": True, "provider": "local_fast_whisper"},
                    "tts": {"enabled": True, "provider": "local_piper"},
                },
                "inference": {
                    "shared_backend": {
                        "enabled": True,
                        "provider": "local_ollama",
                        "fallback_router": {"model": "router-model", "timeout_seconds": 9.0},
                    }
                },
            }
        )

        settings = BrainRuntimeSettings.from_effective_config(effective)

        self.assertEqual(settings.stt.provider_id, "local_fast_whisper")
        self.assertIsInstance(settings.stt.provider, FastWhisperProvider)
        self.assertEqual(settings.stt.provider.model, "small.en")
        self.assertEqual(settings.tts.provider_id, "local_piper")
        self.assertIsInstance(settings.tts.provider, PiperProvider)
        self.assertEqual(settings.inference.provider_id, "local_ollama")
        self.assertIsInstance(settings.inference.provider, OllamaProvider)
        self.assertEqual(settings.inference.fallback_router.model, "router-model")
        self.assertEqual(settings.inference.fallback_router.timeout_seconds, 9.0)

    def _effective_config(self, updates: dict[str, object] | None = None):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            bundle = temporary_root / "bundle"
            self._copy_example_bundle(bundle)
            if updates:
                self._update_brain_role(bundle / "brain.yaml", updates)
            store = GenerationStore(temporary_root / "store")
            store.initialize("example-home")
            config, secrets = store.install_candidate(inspect_candidate(bundle))
            activation = store.create_activation(config.generation_id, secrets.generation_id)
            store._replace_selected_pointer(  # noqa: SLF001 - selected runtime fixture
                activation.generation_id,
                operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids={},
            )
            return load_effective_config(store)

    @staticmethod
    def _copy_example_bundle(destination: Path) -> None:
        import shutil

        shutil.copytree(EXAMPLE_ROOT, destination)

    @staticmethod
    def _update_brain_role(path: Path, updates: dict[str, object]) -> None:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        payload = yaml.load(path.read_text(encoding="utf-8"))

        def merge(target: dict[str, object], source: dict[str, object]) -> None:
            for key, value in source.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)  # type: ignore[index]
                else:
                    target[key] = value

        merge(payload, updates)
        rendered = json.dumps(payload)
        path.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
