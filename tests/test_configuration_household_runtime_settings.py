from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import (
    GenerationStore,
    HouseholdRuntimeSettings,
    inspect_candidate,
    load_effective_config,
)


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class HouseholdRuntimeSettingsTests(unittest.TestCase):
    def test_builds_immutable_typed_identity_indexes(self) -> None:
        effective = self._effective_config()
        settings = HouseholdRuntimeSettings.from_effective_config(effective)

        self.assertEqual(settings.household.id, "example_home")
        self.assertEqual(settings.secret_generation_id, effective.secret_generation_id)
        self.assertEqual(settings.default_user().id, "resident_one")  # type: ignore[union-attr]
        self.assertEqual(settings.resolve_user_id("Resident One"), "resident_one")
        self.assertEqual(settings.resolve_room_id("LOUNGE"), "living_room")
        self.assertIsNone(settings.resolve_mode_id("unknown"))
        with self.assertRaises(TypeError):
            settings.users["other"] = settings.users["resident_one"]  # type: ignore[index]

    def test_exposes_source_association_as_context_without_authentication_inference(self) -> None:
        effective = self._effective_config(
            {
                "sources": [
                    {
                        "id": "living_room_voice",
                        "enabled": True,
                        "type": "satellite",
                        "fixed": True,
                        "associated_room_id": "living_room",
                        "associated_user_id": "resident_one",
                    },
                    {
                        "id": "disabled_voice",
                        "enabled": False,
                        "type": "satellite",
                        "fixed": True,
                        "associated_room_id": "living_room",
                        "associated_user_id": "resident_one",
                    },
                ]
            }
        )
        settings = HouseholdRuntimeSettings.from_effective_config(effective)

        self.assertEqual(
            settings.configured_associated_user_id("living_room_voice"),
            "resident_one",
        )
        self.assertEqual(
            settings.configured_associated_room_id("living_room_voice"),
            "living_room",
        )
        self.assertIsNone(settings.configured_associated_user_id("disabled_voice"))
        self.assertIsNone(settings.configured_associated_room_id("unbound_ingress"))

    def test_disabled_identity_is_retained_but_not_resolvable(self) -> None:
        effective = self._effective_config(
            {
                "users": [
                    {
                        "id": "resident_one",
                        "enabled": False,
                        "display_name": "Resident One",
                        "aliases": ["resident"],
                        "capabilities": {},
                    }
                ],
                "defaults": {"user_id": None},
            }
        )
        settings = HouseholdRuntimeSettings.from_effective_config(effective)

        self.assertIsNotNone(settings.user("resident_one", enabled_only=False))
        self.assertIsNone(settings.user("resident_one"))
        self.assertIsNone(settings.resolve_user_id("resident"))

    def test_ambiguous_display_name_fails_safe_without_new_activation_rule(self) -> None:
        effective = self._effective_config(
            {
                "users": [
                    {
                        "id": "resident_one",
                        "enabled": True,
                        "display_name": "Resident",
                        "aliases": [],
                        "capabilities": {},
                    },
                    {
                        "id": "resident_two",
                        "enabled": True,
                        "display_name": "Resident",
                        "aliases": [],
                        "capabilities": {},
                    },
                ]
            }
        )
        settings = HouseholdRuntimeSettings.from_effective_config(effective)

        self.assertIsNone(settings.resolve_user_id("Resident"))
        self.assertEqual(settings.resolve_user_id("resident_one"), "resident_one")
        self.assertEqual(settings.resolve_user_id("resident_two"), "resident_two")

    def test_ui_escape_hatches_default_empty_and_preserve_configured_order(self) -> None:
        default_settings = HouseholdRuntimeSettings.from_effective_config(
            self._effective_config()
        )
        self.assertEqual(default_settings.ui.escape_hatches, {})

        settings = HouseholdRuntimeSettings.from_effective_config(
            self._effective_config(
                {
                    "ui": {
                        "escape_hatches": {
                            "audio": [
                                {
                                    "label": "First",
                                    "url": "https://first.example.invalid",
                                    "icon": "music_note",
                                },
                                {
                                    "label": "Second",
                                    "url": "https://second.example.invalid",
                                },
                            ]
                        }
                    }
                }
            )
        )
        self.assertEqual(
            [item.label for item in settings.ui.escape_hatches["audio"]],
            ["First", "Second"],
        )
        self.assertEqual(settings.ui.escape_hatches["audio"][1].icon, None)

    def test_ui_escape_hatches_reject_credentials_tokens_and_unknown_icons(self) -> None:
        invalid_links = [
            {"label": "Credentials", "url": "https://user:password@example.invalid"},
            {"label": "Token", "url": "https://example.invalid/path?token=secret"},
            {"label": "Icon", "url": "https://example.invalid", "icon": "provider-logo"},
        ]
        for link in invalid_links:
            with self.subTest(link=link["label"]):
                with self.assertRaises(Exception):
                    self._effective_config(
                        {"ui": {"escape_hatches": {"house": [link]}}}
                    )

    def _effective_config(self, updates: dict[str, object] | None = None):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            bundle = temporary_root / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            if updates:
                self._update_role(bundle / "household.yaml", updates)
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
    def _update_role(path: Path, updates: dict[str, object]) -> None:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        payload = yaml.load(path.read_text(encoding="utf-8"))
        payload.update(updates)
        path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
