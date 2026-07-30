from __future__ import annotations

from pathlib import Path
import unittest

from pydantic import ValidationError

from oracle_app.configuration import OPTIONAL_ROLE_MODELS, RestrictedYamlParser, validate_role


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class OptionalConfigurationModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RestrictedYamlParser()

    def test_every_fixed_optional_example_passes_its_registered_schema(self) -> None:
        self.assertEqual(len(OPTIONAL_ROLE_MODELS), 11)
        for role_path in sorted(OPTIONAL_ROLE_MODELS):
            with self.subTest(role=role_path):
                parsed = self.parser.parse((EXAMPLE_ROOT / role_path).read_text(encoding="utf-8"))
                model = validate_role(role_path, parsed.primitive)
                self.assertEqual(model.model_dump(mode="json", exclude_unset=True), parsed.primitive)

    def test_selected_provider_domains_require_provider_when_enabled(self) -> None:
        for role_path in ("domains/music.yaml", "domains/audiobooks.yaml", "domains/home-assistant.yaml"):
            with self.subTest(role=role_path):
                payload = self._example_payload(role_path)
                payload["enabled"] = True
                with self.assertRaises(ValidationError):
                    validate_role(role_path, payload)

    def test_information_capabilities_are_independently_enabled(self) -> None:
        payload = self._example_payload("domains/information.yaml")
        payload["suggestions"] = {"enabled": True}
        with self.assertRaises(ValidationError):
            validate_role("domains/information.yaml", payload)

        payload["suggestions"]["provider"] = "openclaw"
        payload["suggestions"]["providers"] = {"openclaw": {"adapter": "mock"}}
        model = validate_role("domains/information.yaml", payload)
        self.assertEqual(model.suggestions.provider, "openclaw")

    def test_every_domain_example_contains_typed_leaf_configuration(self) -> None:
        expected_nonempty = {
            "domains/information.yaml": ("facts.providers", "news.providers", "news.sources"),
            "domains/music.yaml": ("providers",),
            "domains/audiobooks.yaml": ("providers",),
            "domains/weather.yaml": ("providers",),
            "domains/calendar.yaml": ("providers",),
            "domains/home-assistant.yaml": ("providers",),
            "domains/notifications.yaml": ("providers", "types", "recipient_groups"),
            "domains/routines.yaml": ("definitions",),
            "domains/network/inventory.yaml": ("hosts", "services", "monitors"),
            "domains/network/policy.yaml": ("actions",),
            "domains/network/adapters.yaml": ("providers",),
        }
        for role_path, paths in expected_nonempty.items():
            with self.subTest(role=role_path):
                model = validate_role(role_path, self._example_payload(role_path))
                dumped = model.model_dump(mode="json")
                for path in paths:
                    value = dumped
                    for part in path.split("."):
                        value = value[part]
                    self.assertTrue(value, path)

    def test_provider_backed_domains_require_typed_selected_definitions(self) -> None:
        for role_path in (
            "domains/music.yaml",
            "domains/audiobooks.yaml",
            "domains/calendar.yaml",
            "domains/home-assistant.yaml",
        ):
            with self.subTest(role=role_path):
                payload = self._example_payload(role_path)
                payload["enabled"] = True
                payload["provider"] = next(iter(payload["providers"]))
                validate_role(role_path, payload)

    def test_weather_capability_selection_is_explicit_and_typed(self) -> None:
        payload = self._example_payload("domains/weather.yaml")
        payload["enabled"] = True
        payload["current"] = {"enabled": True, "provider": "weewx"}
        model = validate_role("domains/weather.yaml", payload)
        self.assertEqual(model.current.provider, "weewx")

        payload["current"]["provider"] = "missing"
        with self.assertRaises(ValidationError):
            validate_role("domains/weather.yaml", payload)

    def test_routines_and_network_policy_reject_external_execution_details(self) -> None:
        routine = self._example_payload("domains/routines.yaml")
        routine["definitions"][0]["script"] = "/tmp/do-something"
        with self.assertRaises(ValidationError):
            validate_role("domains/routines.yaml", routine)

        policy = self._example_payload("domains/network/policy.yaml")
        policy["actions"][0]["service_name"] = "example-service.service"
        with self.assertRaises(ValidationError):
            validate_role("domains/network/policy.yaml", policy)

    def test_audiobook_provider_cannot_own_user_credentials(self) -> None:
        payload = self._example_payload("domains/audiobooks.yaml")
        payload["providers"]["audiobookshelf"]["credential_secret"] = "WRONG_OWNER"
        with self.assertRaises(ValidationError):
            validate_role("domains/audiobooks.yaml", payload)

    def test_home_assistant_automation_preserves_bounded_migration_mode(self) -> None:
        payload = self._example_payload("domains/home-assistant.yaml")
        payload["enabled"] = True
        payload["provider"] = "home_assistant"
        payload["mappings"] = {
            "door_state": {
                "kind": "event",
                "event_type": "entry_state",
                "subject": "side_entry",
                "entity_id": "binary_sensor.side_entry",
                "active_state": "on",
                "inactive_state": "off",
            }
        }
        payload["automations"] = [
            {
                "id": "door_left_open",
                "enabled": True,
                "migration_mode": "runbook",
                "event_mapping_id": "door_state",
                "notification_type": "door_open",
                "notification_delivery_enabled": True,
            }
        ]
        model = validate_role("domains/home-assistant.yaml", payload)
        self.assertEqual(model.automations[0].migration_mode, "runbook")
        payload["automations"][0]["migration_mode"] = "both"
        with self.assertRaises(ValidationError):
            validate_role("domains/home-assistant.yaml", payload)

    def test_service_control_adapter_accepts_only_typed_graceful_lifecycle(self) -> None:
        payload = self._example_payload("domains/network/adapters.yaml")
        payload["providers"]["example_host"] = {
            "type": "service_control",
            "target_kind": "host",
            "host_id": "example_host",
            "transport": "local",
            "platform": "linux",
            "lifecycle": {
                "mode": "graceful",
                "prepare_service_adapter_ids": ["example_service_control"],
            },
        }
        model = validate_role("domains/network/adapters.yaml", payload)
        self.assertEqual(model.providers["example_host"].lifecycle.mode, "graceful")
        payload["providers"]["example_host"]["lifecycle"]["command"] = "shutdown now"
        with self.assertRaises(ValidationError):
            validate_role("domains/network/adapters.yaml", payload)

    def test_service_control_lifecycle_targets_are_bounded_to_docker(self) -> None:
        payload = self._example_payload("domains/network/adapters.yaml")
        adapter = payload["providers"]["example_service_control"]
        adapter["lifecycle_service_targets"] = ["example-sidecar"]
        adapter["service_adapter"] = "docker"
        model = validate_role("domains/network/adapters.yaml", payload)
        self.assertEqual(
            model.providers["example_service_control"].lifecycle_service_targets,
            ["example-sidecar"],
        )
        adapter["service_adapter"] = "systemd"
        with self.assertRaises(ValidationError):
            validate_role("domains/network/adapters.yaml", payload)

    def test_service_control_targets_reject_remote_shell_vocabulary(self) -> None:
        payload = self._example_payload("domains/network/adapters.yaml")
        adapter = payload["providers"]["example_service_control"]
        for native_adapter, target in (
            ("systemd", "example.service; reboot"),
            ("docker", "example && reboot"),
            ("windows_scheduled_task", "Example'; reboot"),
        ):
            with self.subTest(adapter=native_adapter):
                adapter["service_adapter"] = native_adapter
                adapter["service_target"] = target
                with self.assertRaises(ValidationError):
                    validate_role("domains/network/adapters.yaml", payload)

    def test_enabled_routine_uses_only_typed_oracle_native_steps(self) -> None:
        payload = {
            "enabled": True,
            "definitions": [
                {
                    "id": "bedtime",
                    "display_name": "Bedtime",
                    "description": "Wait for a bounded duration.",
                    "enabled": True,
                    "user_id": "resident_one",
                    "source_ids": ["living_room_voice"],
                    "triggers": {"ui": True, "voice": False},
                    "inputs": {
                        "sleep_minutes": {
                            "type": "integer",
                            "default": 20,
                            "minimum": 1,
                            "maximum": 240,
                        }
                    },
                    "steps": [
                        {
                            "id": "wait",
                            "type": "wait",
                            "label": "Wait",
                            "duration_input": "sleep_minutes",
                            "duration_unit": "minutes",
                            "required": True,
                            "max_lateness_seconds": 3600,
                            "on_failure": "stop",
                        }
                    ],
                }
            ],
        }
        model = validate_role("domains/routines.yaml", payload)
        self.assertEqual(model.definitions[0].steps[0].type, "wait")

    def test_credential_free_urls_reject_embedded_or_query_credentials(self) -> None:
        payload = self._example_payload("domains/music.yaml")
        for url in ("http://user:password@plex.invalid", "https://plex.invalid/api?token=secret"):
            with self.subTest(url=url):
                payload["providers"]["plex"]["base_url"] = url
                with self.assertRaises(ValidationError):
                    validate_role("domains/music.yaml", payload)

    def test_unknown_top_level_fields_are_rejected_for_every_optional_role(self) -> None:
        for role_path in sorted(OPTIONAL_ROLE_MODELS):
            with self.subTest(role=role_path):
                payload = self._example_payload(role_path)
                payload["unexpected"] = True
                with self.assertRaises(ValidationError):
                    validate_role(role_path, payload)

    def _example_payload(self, role_path: str) -> dict[str, object]:
        return self.parser.parse((EXAMPLE_ROOT / role_path).read_text(encoding="utf-8")).primitive


if __name__ == "__main__":
    unittest.main()
