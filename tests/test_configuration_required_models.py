from __future__ import annotations

from pathlib import Path
import unittest

from pydantic import ValidationError

from oracle_app.configuration import REQUIRED_ROLE_MODELS, RestrictedYamlParser, validate_required_role


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class RequiredConfigurationModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RestrictedYamlParser()

    def test_all_required_example_roles_pass_executable_models(self) -> None:
        self.assertEqual(
            set(REQUIRED_ROLE_MODELS),
            {"bundle.yaml", "brain.yaml", "access.yaml", "household.yaml", "satellites.yaml"},
        )
        for role_path in sorted(REQUIRED_ROLE_MODELS):
            with self.subTest(role=role_path):
                parsed = self.parser.parse((EXAMPLE_ROOT / role_path).read_text(encoding="utf-8"))
                model = validate_required_role(role_path, parsed.primitive)
                self.assertEqual(model.model_dump(mode="json", exclude_unset=True), parsed.primitive)

    def test_brain_roles_are_independently_enabled_and_require_provider(self) -> None:
        payload = self._example_payload("brain.yaml")
        payload["speech"]["stt"] = {"enabled": True}

        with self.assertRaises(ValidationError):
            validate_required_role("brain.yaml", payload)

        payload["speech"]["stt"]["provider"] = "fast_whisper"
        payload["speech"]["stt"]["providers"] = {
            "fast_whisper": {
                "type": "fast_whisper",
                "model": "small.en",
                "threads": 8,
            }
        }
        model = validate_required_role("brain.yaml", payload)
        self.assertEqual(model.speech.stt.provider, "fast_whisper")

    def test_brain_provider_selection_requires_a_typed_definition(self) -> None:
        payload = self._example_payload("brain.yaml")
        payload["inference"]["shared_backend"]["enabled"] = True
        payload["inference"]["shared_backend"]["provider"] = "missing_backend"

        with self.assertRaises(ValidationError):
            validate_required_role("brain.yaml", payload)

    def test_brain_rejects_unbounded_or_secret_bearing_provider_urls(self) -> None:
        payload = self._example_payload("brain.yaml")
        payload["inference"]["shared_backend"]["providers"]["local_ollama"]["base_url"] = (
            "http://operator:secret@127.0.0.1:11434"
        )

        with self.assertRaises(ValidationError):
            validate_required_role("brain.yaml", payload)

    def test_access_host_local_mode_rejects_browser_mutation(self) -> None:
        payload = self._example_payload("access.yaml")
        payload["operator_access"]["browser_mutation"] = True
        payload["operator_access"]["csrf_protection"] = "boundary_proof"

        with self.assertRaises(ValidationError):
            validate_required_role("access.yaml", payload)

    def test_access_trusted_boundary_requires_matching_enabled_singleton(self) -> None:
        payload = self._example_payload("access.yaml")
        payload["operator_access"] = {
            "mode": "trusted_boundary",
            "boundary_id": "oracle_web_gateway",
            "browser_mutation": True,
            "csrf_protection": "boundary_proof",
            "host_local_cli": True,
        }
        with self.assertRaises(ValidationError):
            validate_required_role("access.yaml", payload)

        payload["trusted_boundary"] = {
            "boundary_id": "oracle_web_gateway",
            "enabled": True,
            "type": "authenticated_reverse_proxy",
            "trusted_proxy_ids": ["oracle-web-gateway"],
            "accepted_headers": ["authenticated_request"],
        }
        model = validate_required_role("access.yaml", payload)
        self.assertEqual(model.trusted_boundary.boundary_id, "oracle_web_gateway")

    def test_access_rejects_duplicate_source_or_secret_bindings(self) -> None:
        payload = self._example_payload("access.yaml")
        payload["source_authentication"] = {
            "credential_bindings": [
                {"source_id": "phone_one", "credential_secret": "PHONE_ONE_TOKEN"},
                {"source_id": "phone_one", "credential_secret": "PHONE_TWO_TOKEN"},
            ]
        }
        with self.assertRaises(ValidationError):
            validate_required_role("access.yaml", payload)

        payload["source_authentication"]["credential_bindings"][1] = {
            "source_id": "phone_two",
            "credential_secret": "PHONE_ONE_TOKEN",
        }
        with self.assertRaises(ValidationError):
            validate_required_role("access.yaml", payload)

    def test_household_requires_iana_timezone_and_fixed_room_association(self) -> None:
        payload = self._example_payload("household.yaml")
        payload["household"]["timezone"] = "Not/A_Timezone"
        with self.assertRaises(ValidationError):
            validate_required_role("household.yaml", payload)

        payload = self._example_payload("household.yaml")
        payload["sources"] = [
            {
                "id": "phone_one",
                "enabled": True,
                "type": "mobile_app",
                "fixed": False,
                "associated_room_id": "living_room",
            }
        ]
        with self.assertRaises(ValidationError):
            validate_required_role("household.yaml", payload)

    def test_enabled_satellite_requires_directional_credentials(self) -> None:
        payload = {"satellites": [{"id": "living_room_satellite", "enabled": True}]}

        with self.assertRaises(ValidationError):
            validate_required_role("satellites.yaml", payload)

    def test_satellite_leaf_models_are_platform_and_capability_checked(self) -> None:
        payload = self._example_payload("satellites.yaml")
        satellite = payload["satellites"][0]
        satellite["platform"] = "windows"
        satellite["audio"]["input"] = {"type": "alsa_arecord", "device": "hw:1,0"}
        with self.assertRaises(ValidationError):
            validate_required_role("satellites.yaml", payload)

        satellite["audio"]["input"] = {"type": "system_default"}
        satellite["enabled"] = True
        satellite["source_id"] = "example_voice"
        satellite["brain_client"] = {
            "base_url": "http://oracle-brain.example.invalid:8011",
            "credential_secret": "EXAMPLE_BRAIN_TOKEN",
        }
        satellite["control_service"] = {
            "base_url": "http://example-satellite.invalid:8021",
            "local_client_url": "http://127.0.0.1:8021",
            "credential_secret": "EXAMPLE_CONTROL_TOKEN",
        }
        satellite["enrollment"] = {"credential_secret": "EXAMPLE_ENROLLMENT_TOKEN"}
        satellite["capabilities"]["display"] = True
        with self.assertRaises(ValidationError):
            validate_required_role("satellites.yaml", payload)

        satellite["ui"] = {
            "enabled": True,
            "touch": True,
            "profile": "example_touch",
            "layout": "satellite_landscape_touch_v1",
            "pages": ["home", "audio"],
            "bottom_nav": ["home", "audio"],
        }
        model = validate_required_role("satellites.yaml", payload)
        self.assertTrue(model.satellites[0].ui.enabled)

        satellite["capabilities"]["music_playback"] = True
        del satellite["control_service"]["base_url"]
        with self.assertRaisesRegex(ValidationError, "control-service URL"):
            validate_required_role("satellites.yaml", payload)

        satellite["control_service"]["base_url"] = "http://example-satellite.invalid:8021"
        del satellite["brain_client"]["base_url"]
        with self.assertRaisesRegex(ValidationError, "satellite-to-Brain URL"):
            validate_required_role("satellites.yaml", payload)

        satellite["brain_client"]["base_url"] = "http://oracle-brain.example.invalid:8011"
        satellite["capabilities"]["voice"] = True
        del satellite["control_service"]["local_client_url"]
        with self.assertRaisesRegex(ValidationError, "local control-service client URL"):
            validate_required_role("satellites.yaml", payload)

    def test_satellite_playback_rejects_retired_plexamp_control_adapter(self) -> None:
        payload = self._example_payload("satellites.yaml")
        payload["satellites"][0]["audio"]["playback"]["adapter"] = "plexamp"
        with self.assertRaises(ValidationError):
            validate_required_role("satellites.yaml", payload)

    def test_satellite_volume_control_is_typed_and_platform_checked(self) -> None:
        payload = self._example_payload("satellites.yaml")
        satellite = payload["satellites"][0]
        satellite["audio"]["playback"]["volume_control"] = {
            "type": "alsa",
            "card": "Headphones",
            "control": "PCM",
        }
        model = validate_required_role("satellites.yaml", payload)
        self.assertEqual(model.satellites[0].audio.playback.volume_control.type, "alsa")

        satellite["platform"] = "windows"
        with self.assertRaisesRegex(ValidationError, "ALSA volume control"):
            validate_required_role("satellites.yaml", payload)

        satellite["audio"]["playback"]["volume_control"] = {
            "type": "windows_default_endpoint",
        }
        model = validate_required_role("satellites.yaml", payload)
        self.assertEqual(
            model.satellites[0].audio.playback.volume_control.type,
            "windows_default_endpoint",
        )

        satellite["platform"] = "linux"
        with self.assertRaisesRegex(ValidationError, "Windows default-endpoint"):
            validate_required_role("satellites.yaml", payload)

    def test_enabled_wake_detection_requires_one_matching_model_location(self) -> None:
        payload = self._example_payload("satellites.yaml")
        wake = payload["satellites"][0]["wake"]
        wake["enabled"] = True
        wake["model"] = {"format": "onnx", "path": "models/example.tflite"}
        with self.assertRaises(ValidationError):
            validate_required_role("satellites.yaml", payload)

        wake["model"] = {"format": "onnx", "asset_id": "example", "path": "models/example.onnx"}
        with self.assertRaises(ValidationError):
            validate_required_role("satellites.yaml", payload)

    def test_unknown_fields_are_rejected_in_every_required_role(self) -> None:
        for role_path in sorted(REQUIRED_ROLE_MODELS):
            with self.subTest(role=role_path):
                payload = self._example_payload(role_path)
                payload["unexpected"] = True
                with self.assertRaises(ValidationError):
                    validate_required_role(role_path, payload)

    def _example_payload(self, role_path: str) -> dict[str, object]:
        return self.parser.parse((EXAMPLE_ROOT / role_path).read_text(encoding="utf-8")).primitive


if __name__ == "__main__":
    unittest.main()
