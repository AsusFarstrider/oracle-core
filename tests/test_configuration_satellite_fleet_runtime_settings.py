from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from oracle_app.configuration import (
    EffectiveConfig,
    SatelliteFleetRuntimeSettings,
    SatelliteUiRuntimeSettings,
    inspect_candidate,
)


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"
PROJECTION_ACTIVATION_ID = "sat_activation_11111111111111111111111111111111"


class SatelliteFleetRuntimeSettingsTests(unittest.TestCase):
    def test_retains_disabled_inventory_without_making_it_operational(self) -> None:
        effective = self._effective_config()

        settings = SatelliteFleetRuntimeSettings.from_effective_config(effective)

        disabled = settings.satellite("example_satellite", enabled_only=False)
        self.assertIsNotNone(disabled)
        self.assertFalse(disabled.enabled)  # type: ignore[union-attr]
        self.assertIsNone(settings.satellite("example_satellite"))
        self.assertEqual(dict(settings.enabled_satellite_ids_by_source), {})
        with self.assertRaises(TypeError):
            settings.satellites["other"] = disabled  # type: ignore[index,assignment]

    def test_maps_only_brain_owned_edges_for_enabled_playback_satellite(self) -> None:
        effective = self._effective_config(enabled_playback_satellite=True)

        settings = SatelliteFleetRuntimeSettings.from_effective_config(effective)
        satellite = settings.satellite("living_room_satellite")

        self.assertIsNotNone(satellite)
        self.assertEqual(satellite.source_id, "living_room_voice")  # type: ignore[union-attr]
        self.assertEqual(satellite.projection_activation_id, PROJECTION_ACTIVATION_ID)  # type: ignore[union-attr]
        self.assertEqual(satellite.control_service_base_url, "http://192.0.2.20:8021")  # type: ignore[union-attr]
        self.assertEqual(satellite.ui_trusted_peer_addresses, frozenset({"192.0.2.20"}))  # type: ignore[union-attr]
        self.assertEqual(
            settings.source_for_ui_peer("living_room_voice", "192.0.2.20"),
            "living_room_voice",
        )
        self.assertIsNone(settings.source_for_ui_peer("living_room_voice", "192.0.2.21"))
        self.assertIsNone(settings.source_for_ui_peer("unknown_source", "192.0.2.20"))
        self.assertFalse(hasattr(satellite, "brain_client_base_url"))
        self.assertFalse(hasattr(satellite, "brain_client_credential"))
        self.assertFalse(hasattr(satellite, "enrollment_credential"))
        self.assertFalse(hasattr(satellite, "local_client_url"))
        self.assertFalse(hasattr(satellite, "audio"))
        self.assertFalse(hasattr(satellite, "wake"))
        self.assertFalse(hasattr(satellite, "ui"))
        self.assertNotIn("brain-secret-value", repr(satellite))
        self.assertNotIn("control-secret-value", repr(settings))
        self.assertEqual(
            settings.control_target_for_source("living_room_voice"),
            satellite,
        )
        self.assertIsNone(settings.control_target_for_source("unknown_source"))

    def test_enabled_satellite_requires_selected_projection_activation(self) -> None:
        effective = self._effective_config(enabled_playback_satellite=True)
        missing_projection = EffectiveConfig(
            **{
                **effective.__dict__,
                "satellite_projection_activation_ids": MappingProxyType({}),
            }
        )

        with self.assertRaisesRegex(ValueError, "no selected projection activation"):
            SatelliteFleetRuntimeSettings.from_effective_config(missing_projection)

    def test_ui_view_is_separate_and_resolves_lifecycle_or_source_identity(self) -> None:
        effective = self._effective_config(enabled_playback_satellite=True)

        settings = SatelliteUiRuntimeSettings.from_effective_config(effective)

        by_id = settings.entry("living_room_satellite")
        self.assertIsNotNone(by_id)
        self.assertIs(settings.entry("living_room_voice"), by_id)
        self.assertEqual(by_id.ui.pages, ["home", "house"])  # type: ignore[union-attr]
        self.assertFalse(hasattr(by_id, "control_service_base_url"))
        with self.assertRaises(TypeError):
            settings.entries["other"] = by_id  # type: ignore[index,assignment]

    def _effective_config(self, *, enabled_playback_satellite: bool = False) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            projection_ids: dict[str, str] = {}
            if enabled_playback_satellite:
                self._write_enabled_bundle(bundle)
                projection_ids["living_room_satellite"] = PROJECTION_ACTIVATION_ID
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible)
            self.assertIsNotNone(inspection.bundle)
            self.assertIsNotNone(inspection.normalized_candidate_revision)
            self.assertIsNotNone(inspection.secrets)
            return EffectiveConfig(
                activation_generation_id="activation_11111111111111111111111111111111",
                config_generation_id="config_11111111111111111111111111111111",
                secret_generation_id="secrets_11111111111111111111111111111111",
                selection_operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids=MappingProxyType(projection_ids),
                config_revision=inspection.normalized_candidate_revision,
                bundle_id="example-home",
                schema_version=1,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )

    @staticmethod
    def _write_enabled_bundle(bundle: Path) -> None:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        household_path = bundle / "household.yaml"
        household = yaml.load(household_path.read_text(encoding="utf-8"))
        household["sources"] = [
            {
                "id": "living_room_voice",
                "enabled": True,
                "type": "satellite",
                "fixed": True,
                "associated_room_id": "living_room",
            }
        ]
        household_path.write_text(json.dumps(household), encoding="utf-8")

        satellites = {
            "satellites": [
                {
                    "id": "living_room_satellite",
                    "enabled": True,
                    "source_id": "living_room_voice",
                    "platform": "linux",
                    "capabilities": {
                        "voice": False,
                        "display": True,
                        "music_playback": True,
                        "audiobook_playback": True,
                    },
                    "brain_client": {
                        "base_url": "http://brain.invalid:8011",
                        "credential_secret": "LIVING_ROOM_BRAIN_CREDENTIAL",
                    },
                    "control_service": {
                        "base_url": "http://192.0.2.20:8021",
                        "local_client_url": "http://127.0.0.1:8021",
                        "credential_secret": "LIVING_ROOM_CONTROL_CREDENTIAL",
                    },
                    "enrollment": {
                        "credential_secret": "LIVING_ROOM_ENROLLMENT_CREDENTIAL",
                    },
                    "audio": {
                        "input": {"type": "system_default"},
                        "interaction_output": {"type": "system_default"},
                        "playback": {"adapter": "oracle_native"},
                    },
                    "ui": {
                        "enabled": True,
                        "touch": True,
                        "profile": "living_room_touch_v1",
                        "layout": "satellite_landscape_touch_v1",
                        "pages": ["home", "house"],
                        "bottom_nav": ["home", "house"],
                    },
                }
            ]
        }
        (bundle / "satellites.yaml").write_text(json.dumps(satellites), encoding="utf-8")
        (bundle / "secrets.env").write_text(
            "LIVING_ROOM_BRAIN_CREDENTIAL=brain-secret-value\n"
            "LIVING_ROOM_CONTROL_CREDENTIAL=control-secret-value\n"
            "LIVING_ROOM_ENROLLMENT_CREDENTIAL=enrollment-secret-value\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
