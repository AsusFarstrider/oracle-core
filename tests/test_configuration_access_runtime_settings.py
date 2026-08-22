from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import (
    AccessRuntimeSettings,
    GenerationStore,
    inspect_candidate,
    load_effective_config,
)


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class AccessRuntimeSettingsTests(unittest.TestCase):
    def test_builds_frozen_access_policy_without_inventing_source_proof(self) -> None:
        effective = self._effective_config()

        settings = AccessRuntimeSettings.from_effective_config(effective)

        self.assertEqual(settings.activation_generation_id, effective.activation_generation_id)
        self.assertEqual(settings.secret_generation_id, effective.secret_generation_id)
        self.assertEqual(settings.operator_access.mode, "host_local_only")
        self.assertIsNone(settings.trusted_boundary)
        self.assertFalse(settings.public_health.enabled)
        self.assertEqual(dict(settings.source_credential_bindings), {})
        with self.assertRaises(TypeError):
            settings.source_credential_bindings["source"] = None  # type: ignore[index,assignment]

    def test_resolves_and_authenticates_one_enabled_non_satellite_binding(self) -> None:
        effective = self._effective_config(
            household_updates={
                "sources": [
                    {
                        "id": "wall_kiosk",
                        "enabled": True,
                        "type": "kiosk",
                        "fixed": True,
                        "associated_room_id": "living_room",
                    }
                ]
            },
            access_updates={
                "source_authentication": {
                    "credential_bindings": [
                        {
                            "source_id": "wall_kiosk",
                            "credential_secret": "WALL_KIOSK_CREDENTIAL",
                        }
                    ]
                }
            },
            secrets="WALL_KIOSK_CREDENTIAL=private-kiosk-token\n",
        )

        settings = AccessRuntimeSettings.from_effective_config(effective)
        binding = settings.source_credential_bindings["wall_kiosk"]

        self.assertTrue(binding.active)
        self.assertEqual(binding.source_type, "kiosk")
        self.assertNotIn("private-kiosk-token", repr(binding))
        self.assertNotIn("private-kiosk-token", repr(settings))
        self.assertEqual(
            settings.authenticate_source_credential("private-kiosk-token"),
            "wall_kiosk",
        )
        self.assertIsNone(settings.authenticate_source_credential("wrong-token"))
        self.assertIsNone(settings.authenticate_source_credential("not-the-token-é"))

    def test_disabled_source_binding_is_retained_but_inactive_without_secret(self) -> None:
        effective = self._effective_config(
            household_updates={
                "sources": [
                    {
                        "id": "retired_desktop",
                        "enabled": False,
                        "type": "desktop_app",
                        "fixed": False,
                    }
                ]
            },
            access_updates={
                "source_authentication": {
                    "credential_bindings": [
                        {
                            "source_id": "retired_desktop",
                            "credential_secret": "RETIRED_DESKTOP_CREDENTIAL",
                        }
                    ]
                }
            },
        )

        settings = AccessRuntimeSettings.from_effective_config(effective)
        binding = settings.source_credential_bindings["retired_desktop"]

        self.assertFalse(binding.active)
        self.assertIsNone(binding.credential)
        self.assertIsNone(settings.authenticate_source_credential("anything"))

    def test_duplicate_raw_operator_error_does_not_select_an_arbitrary_source(self) -> None:
        effective = self._effective_config(
            household_updates={
                "sources": [
                    {"id": "mobile_one", "enabled": True, "type": "mobile_app", "fixed": False},
                    {"id": "mobile_two", "enabled": True, "type": "mobile_app", "fixed": False},
                ]
            },
            access_updates={
                "source_authentication": {
                    "credential_bindings": [
                        {"source_id": "mobile_one", "credential_secret": "MOBILE_ONE_CREDENTIAL"},
                        {"source_id": "mobile_two", "credential_secret": "MOBILE_TWO_CREDENTIAL"},
                    ]
                }
            },
            secrets=(
                "MOBILE_ONE_CREDENTIAL=same-operator-value\n"
                "MOBILE_TWO_CREDENTIAL=same-operator-value\n"
            ),
        )

        settings = AccessRuntimeSettings.from_effective_config(effective)

        self.assertIsNone(settings.authenticate_source_credential("same-operator-value"))

    def _effective_config(
        self,
        *,
        household_updates: dict[str, object] | None = None,
        access_updates: dict[str, object] | None = None,
        secrets: str | None = None,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            bundle = temporary_root / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            if household_updates:
                self._update_role(bundle / "household.yaml", household_updates)
            if access_updates:
                self._update_role(bundle / "access.yaml", access_updates)
            if secrets is not None:
                (bundle / "secrets.env").write_text(secrets, encoding="utf-8")
            store = GenerationStore(temporary_root / "store")
            store.initialize("example-home")
            config, secret_generation = store.install_candidate(inspect_candidate(bundle))
            activation = store.create_activation(config.generation_id, secret_generation.generation_id)
            store._replace_selected_pointer(  # noqa: SLF001 - selected runtime fixture
                activation.generation_id,
                operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids={},
            )
            return load_effective_config(store)

    @staticmethod
    def _update_role(path: Path, updates: dict[str, object]) -> None:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        payload = yaml.load(path.read_text(encoding="utf-8"))
        payload.update(updates)
        path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
