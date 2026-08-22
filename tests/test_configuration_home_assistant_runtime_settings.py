from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from oracle_app.configuration import EffectiveConfig, HomeAssistantRuntimeSettings, inspect_candidate
from oracle_app.configuration.domain_models import HomeAssistantObjectMapping


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class HomeAssistantRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_role_selects_no_provider_mapping_or_automation(self) -> None:
        settings = HomeAssistantRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.enabled)
        self.assertIsNone(settings.provider_id)
        self.assertEqual(dict(settings.mappings), {})
        self.assertEqual(dict(settings.automations), {})
        self.assertIsNone(settings.credential)
        self.assertIsNone(settings.event_ingress_credential)

    def test_interactive_bridge_resolves_api_credential_without_dormant_ingress(self) -> None:
        settings = HomeAssistantRuntimeSettings.from_effective_config(
            self._effective_config(mode="interactive")
        )

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.provider_id, "primary")
        self.assertEqual(settings.base_url, "http://home-assistant.invalid:8123")
        self.assertEqual(settings.timeout_seconds, 9)
        self.assertEqual(settings.credential, "home-assistant-token")
        self.assertIsNone(settings.event_ingress_secret)
        self.assertIsNone(settings.event_ingress_credential)
        self.assertIsInstance(settings.mapping("living_room"), HomeAssistantObjectMapping)
        self.assertEqual(tuple(item.oracle_id for item in settings.mappings_for_kind("room")), ("living_room",))
        self.assertEqual(settings.mapping("lights_on").entity_id, "script.living_room_lights_on")  # type: ignore[union-attr]
        self.assertNotIn("home-assistant-token", repr(settings))
        with self.assertRaises(TypeError):
            settings.mappings["other"] = settings.mappings["living_room"]  # type: ignore[index]

    def test_enabled_automation_resolves_ingress_and_binds_exact_event_mapping(self) -> None:
        settings = HomeAssistantRuntimeSettings.from_effective_config(
            self._effective_config(mode="automation")
        )

        automation = settings.automation("door_left_open")
        self.assertIsNotNone(automation)
        self.assertEqual(automation.event_mapping.subject, "side_entry")  # type: ignore[union-attr]
        self.assertEqual(automation.definition.notification_type, "example_notice")  # type: ignore[union-attr]
        self.assertEqual(settings.event_ingress_secret, "HOME_ASSISTANT_EVENT_TOKEN")
        self.assertEqual(settings.event_ingress_credential, "home-assistant-event-token")
        self.assertNotIn("home-assistant-event-token", repr(settings))

    def test_absent_optional_role_has_no_implicit_runtime_defaults(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            HomeAssistantRuntimeSettings.from_effective_config(effective)

    def test_typed_views_preserve_order_and_snapshot_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_enabled_role(bundle, automation=False)
            role_path = bundle / "domains" / "home-assistant.yaml"
            role = json.loads(role_path.read_text(encoding="utf-8"))
            role["providers"]["primary"]["snapshot_root"] = "/local/snapshots"
            role["mappings"].update(
                {
                    "second_light": {"kind": "entity", "oracle_id": "second", "entity_id": "light.second", "allowed_operations": ["read"]},
                    "first_light": {"kind": "entity", "oracle_id": "first", "entity_id": "light.first", "allowed_operations": ["read"]},
                    "porch_camera": {"kind": "camera", "oracle_id": "porch", "entity_id": "camera.porch", "allowed_operations": ["read"]},
                }
            )
            role["views"] = {
                "home": {"controls": [], "actions": []},
                "house": {
                    "temperatures": [],
                    "climate": [],
                    "lights": [{"mapping_id": "second_light"}, {"mapping_id": "first_light"}],
                    "cameras": [{"mapping_id": "porch_camera", "snapshot_ref": "porch/latest.jpg"}],
                    "actions": [],
                },
                "rooms": {},
            }
            role_path.write_text(json.dumps(role), encoding="utf-8")
            (bundle / "secrets.env").write_text("HOME_ASSISTANT_TOKEN=token\n", encoding="utf-8")

            inspection = inspect_candidate(bundle)

            self.assertTrue(inspection.report.activation_eligible, inspection.report)
            self.assertEqual(
                [item.mapping_id for item in inspection.bundle.roles["domains/home-assistant.yaml"].views.house.lights],
                ["second_light", "first_light"],
            )
            self.assertEqual(
                inspection.bundle.roles["domains/home-assistant.yaml"].providers["primary"].snapshot_root,
                "/local/snapshots",
            )

    def test_views_reject_unknown_mapping_room_and_missing_snapshot_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_enabled_role(bundle, automation=False)
            role_path = bundle / "domains" / "home-assistant.yaml"
            role = json.loads(role_path.read_text(encoding="utf-8"))
            role["mappings"]["porch_camera"] = {
                "kind": "camera",
                "oracle_id": "porch",
                "entity_id": "camera.porch",
                "allowed_operations": ["read"],
            }
            role["mappings"]["invented_action"] = {
                "kind": "action",
                "oracle_id": "porch",
                "entity_id": "light.porch",
                "allowed_operations": ["turn_on"],
            }
            role["views"] = {
                "home": {
                    "controls": [{"mapping_id": "missing"}],
                    "actions": [{"mapping_id": "invented_action"}],
                },
                "house": {
                    "temperatures": [],
                    "climate": [],
                    "lights": [],
                    "cameras": [{"mapping_id": "porch_camera", "snapshot_ref": "porch.jpg"}],
                    "actions": [],
                },
                "rooms": {"unknown_room": {"controls": [], "environment": []}},
            }
            role_path.write_text(json.dumps(role), encoding="utf-8")
            (bundle / "secrets.env").write_text("HOME_ASSISTANT_TOKEN=token\n", encoding="utf-8")

            inspection = inspect_candidate(bundle)
            findings = inspection.report.validation_findings

            self.assertFalse(inspection.report.activation_eligible)
            self.assertIn("views.home.controls[0].mapping_id", {item.path for item in findings})
            self.assertNotIn("views.home.actions[0].mapping_id", {item.path for item in findings})
            self.assertIn("views.rooms.unknown_room", {item.path for item in findings})
            self.assertIn("providers", {item.path for item in findings})

    def test_event_automation_rejects_ambiguous_provider_and_lifecycle_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            self._write_enabled_role(bundle, automation=True)
            role_path = bundle / "domains" / "home-assistant.yaml"
            role = json.loads(role_path.read_text(encoding="utf-8"))
            role["mappings"]["duplicate_side_entry"] = {
                "kind": "event",
                "event_type": "entry_state",
                "subject": "other_side_entry",
                "entity_id": "binary_sensor.side_entry",
                "active_state": "on",
                "inactive_state": "off",
            }
            duplicate_automation = dict(role["automations"][0])
            duplicate_automation["id"] = "second_side_entry_owner"
            role["automations"].append(duplicate_automation)
            role_path.write_text(json.dumps(role), encoding="utf-8")
            (bundle / "secrets.env").write_text(
                "HOME_ASSISTANT_TOKEN=token\nHOME_ASSISTANT_EVENT_TOKEN=event-token\n",
                encoding="utf-8",
            )

            inspection = inspect_candidate(bundle)
            codes = {item.code for item in inspection.report.validation_findings}

            self.assertFalse(inspection.report.activation_eligible)
            self.assertIn("config.identity.duplicate_provider_mapping", codes)
            self.assertIn("config.identity.duplicate_lifecycle_owner", codes)

    def _effective_config(
        self,
        *,
        mode: str | None = None,
        include_role: bool = True,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            role_path = bundle / "domains" / "home-assistant.yaml"
            if not include_role:
                role_path.unlink()
            elif mode is not None:
                self._write_enabled_role(bundle, automation=mode == "automation")
                secret_lines = ["HOME_ASSISTANT_TOKEN=home-assistant-token"]
                if mode == "automation":
                    secret_lines.append("HOME_ASSISTANT_EVENT_TOKEN=home-assistant-event-token")
                (bundle / "secrets.env").write_text("\n".join(secret_lines) + "\n", encoding="utf-8")
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible, inspection.report)
            self.assertIsNotNone(inspection.bundle)
            self.assertIsNotNone(inspection.normalized_candidate_revision)
            self.assertIsNotNone(inspection.secrets)
            return EffectiveConfig(
                activation_generation_id="activation_11111111111111111111111111111111",
                config_generation_id="config_11111111111111111111111111111111",
                secret_generation_id="secrets_11111111111111111111111111111111",
                selection_operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids=MappingProxyType({}),
                config_revision=inspection.normalized_candidate_revision,
                bundle_id="example-home",
                schema_version=2,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )

    @staticmethod
    def _write_enabled_role(bundle: Path, *, automation: bool) -> None:
        mappings = {
            "living_room": {
                "kind": "room",
                "oracle_id": "living_room",
                "entity_id": "light.living_room",
                "allowed_operations": ["turn_on", "turn_off"],
            },
            "lights_on": {
                "kind": "action",
                "oracle_id": "living_room_lights_on",
                "entity_id": "script.living_room_lights_on",
                "allowed_operations": ["run"],
            },
        }
        automations = []
        if automation:
            mappings["side_entry_state"] = {
                "kind": "event",
                "event_type": "entry_state",
                "subject": "side_entry",
                "entity_id": "binary_sensor.side_entry",
                "active_state": "on",
                "inactive_state": "off",
            }
            automations.append(
                {
                    "id": "door_left_open",
                    "enabled": True,
                    "migration_mode": "runbook",
                    "event_mapping_id": "side_entry_state",
                    "notification_type": "example_notice",
                    "notification_delivery_enabled": False,
                    "delay_seconds": 60,
                    "max_notifications": 1,
                }
            )
        role = {
            "enabled": True,
            "provider": "primary",
            "providers": {
                "primary": {
                    "type": "home_assistant",
                    "base_url": "http://home-assistant.invalid:8123",
                    "credential_secret": "HOME_ASSISTANT_TOKEN",
                    "event_ingress_secret": "HOME_ASSISTANT_EVENT_TOKEN",
                    "timeout_seconds": 9,
                }
            },
            "mappings": mappings,
            "automations": automations,
        }
        (bundle / "domains" / "home-assistant.yaml").write_text(json.dumps(role), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
