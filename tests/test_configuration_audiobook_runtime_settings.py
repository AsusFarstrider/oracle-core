from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import MagicMock, patch

from oracle_app.audiobook_runtime.canonical import CanonicalAudiobookExecution
from oracle_app.configuration import AudiobookRuntimeSettings, EffectiveConfig, inspect_candidate
from oracle_app.configuration.domain_models import AudiobookshelfProvider
from oracle_app.health import check_audiobook_health


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"
PROJECTION_ACTIVATION_ID = "sat_activation_11111111111111111111111111111111"


class AudiobookRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_audiobooks_selects_no_provider_accounts_or_targets(self) -> None:
        settings = AudiobookRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.enabled)
        self.assertIsNone(settings.provider_id)
        self.assertIsNone(settings.provider)
        self.assertEqual(dict(settings.user_accounts), {})
        self.assertEqual(dict(settings.playback_targets), {})

    def test_maps_shared_provider_user_account_policy_and_playback_target(self) -> None:
        effective = self._effective_config(enabled=True)

        settings = AudiobookRuntimeSettings.from_effective_config(effective)
        account = settings.user_account("resident_one")
        target = settings.playback_target("living_room_voice")

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.provider_id, "audiobookshelf_primary")
        self.assertIsInstance(settings.provider, AudiobookshelfProvider)
        self.assertEqual(settings.provider.library_id, "main_library")
        self.assertEqual(settings.default_sleep_timer_minutes, 30)
        self.assertIsNotNone(account)
        self.assertEqual(account.account_id, "primary")  # type: ignore[union-attr]
        self.assertEqual(account.credential, "resident-audiobook-token")  # type: ignore[union-attr]
        self.assertIsNotNone(target)
        self.assertEqual(target.satellite_id, "living_room_satellite")  # type: ignore[union-attr]
        self.assertEqual(target.projection_activation_id, PROJECTION_ACTIVATION_ID)  # type: ignore[union-attr]
        self.assertEqual(settings.stream_base_url("living_room_voice"), "http://brain.invalid:8011")
        self.assertNotIn("resident-audiobook-token", repr(account))
        self.assertNotIn("resident-audiobook-token", repr(settings))
        self.assertNotIn("control-secret-value", repr(settings))
        with self.assertRaises(TypeError):
            settings.user_accounts["other"] = account  # type: ignore[index,assignment]

    def test_canonical_execution_uses_typed_provider_and_control_edges(self) -> None:
        settings = AudiobookRuntimeSettings.from_effective_config(self._effective_config(enabled=True))
        execution = CanonicalAudiobookExecution(
            settings,
            satellite_control_timeout_seconds=6,
        )
        provider_response = MagicMock()
        provider_response.read.return_value = b'{"book": []}'
        provider_response.__enter__.return_value = provider_response
        provider_response.__exit__.return_value = False

        with (
            patch(
                "oracle_app.provider_bridges.audiobookshelf_audiobook.get_audiobook_connection_settings",
                side_effect=AssertionError("canonical provider used V1 configuration"),
            ),
            patch(
                "oracle_app.provider_bridges.audiobookshelf_audiobook.request.urlopen",
                return_value=provider_response,
            ) as provider_open,
        ):
            self.assertEqual(execution.search_audiobooks("Example", user_id="resident_one"), [])

        provider_request = provider_open.call_args.args[0]
        self.assertTrue(provider_request.full_url.startswith("http://audiobooks.invalid/api/libraries/main_library/search"))
        self.assertEqual(provider_request.headers["Authorization"], "Bearer resident-audiobook-token")

        control_response = MagicMock()
        control_response.read.return_value = b'{"ok": true, "command_id": "command-1", "state": "playing"}'
        control_response.__enter__.return_value = control_response
        control_response.__exit__.return_value = False
        with (
            patch(
                "oracle_app.music_runtime.control.get_satellite_control_target",
                side_effect=AssertionError("canonical control used V1 configuration"),
            ),
            patch(
                "oracle_app.music_runtime.control.request.urlopen",
                return_value=control_response,
            ) as control_open,
        ):
            result = execution.execute_satellite_command(
                "living_room_voice",
                "play_longform_audio",
                {"playback_id": "playback-1"},
            )

        self.assertTrue(result["ok"])
        control_request = control_open.call_args.args[0]
        self.assertEqual(control_request.full_url, "http://living-room.invalid:8021/control")
        self.assertEqual(control_request.headers["Authorization"], "Bearer control-secret-value")

    def test_canonical_longform_urls_use_target_brain_edge_without_v1_base_url(self) -> None:
        settings = AudiobookRuntimeSettings.from_effective_config(self._effective_config(enabled=True))
        execution = CanonicalAudiobookExecution(settings, satellite_control_timeout_seconds=6)
        session = {
            "provider_session_id": "provider-session",
            "library_item_id": "book-1",
            "title": "Example Book",
            "tracks": [
                {
                    "content_url": "/api/items/book-1/file/0",
                    "mime_type": "audio/mpeg",
                }
            ],
        }

        with patch(
            "oracle_app.audiobook.get_oracle_base_url",
            side_effect=AssertionError("canonical payload used V1 base URL"),
        ):
            _playback_id, payload, _state = execution.build_longform_payload(
                session,
                source="living_room_voice",
                user_id="resident_one",
            )

        self.assertTrue(payload["tracks"][0]["url"].startswith("http://brain.invalid:8011/audiobooks/stream/"))

    def test_canonical_health_checks_each_configured_user_without_v1_settings(self) -> None:
        settings = AudiobookRuntimeSettings.from_effective_config(self._effective_config(enabled=True))
        execution = CanonicalAudiobookExecution(settings, satellite_control_timeout_seconds=6)

        with (
            patch.object(execution, "request_json", return_value={"success": True}) as request_json,
            patch(
                "oracle_app.audiobook.check_audiobook_health",
                side_effect=AssertionError("canonical health used V1 settings"),
            ),
        ):
            response = check_audiobook_health(execution, canonical_authority=True)

        self.assertEqual(response.status, "ok")
        self.assertEqual(response.configured_satellites, ["living_room_voice"])
        request_json.assert_called_once_with("/ping", method="GET", user_id="resident_one")

    def test_unknown_or_unconfigured_user_has_no_credential_fallback(self) -> None:
        settings = AudiobookRuntimeSettings.from_effective_config(self._effective_config(enabled=True))

        self.assertIsNone(settings.user_account("unknown_user"))
        self.assertIsNone(settings.user_account(None))
        self.assertIsNone(settings.playback_target("unknown_source"))

    def test_absent_optional_role_has_no_implicit_runtime_defaults(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            AudiobookRuntimeSettings.from_effective_config(effective)

    def _effective_config(
        self,
        *,
        enabled: bool = False,
        include_role: bool = True,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            role_path = bundle / "domains" / "audiobooks.yaml"
            projection_ids: dict[str, str] = {}
            if not include_role:
                role_path.unlink()
            elif enabled:
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
        household["users"][0]["capabilities"] = {
            "audiobooks": {
                "enabled": True,
                "account_id": "primary",
                "credential_secret": "RESIDENT_ONE_AUDIOBOOK_TOKEN",
            }
        }
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
                        "display": False,
                        "music_playback": False,
                        "audiobook_playback": True,
                    },
                    "brain_client": {
                        "base_url": "http://brain.invalid:8011",
                        "credential_secret": "LIVING_ROOM_BRAIN_CREDENTIAL",
                    },
                    "control_service": {
                        "base_url": "http://living-room.invalid:8021",
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
                }
            ]
        }
        (bundle / "satellites.yaml").write_text(json.dumps(satellites), encoding="utf-8")

        audiobooks = {
            "enabled": True,
            "provider": "audiobookshelf_primary",
            "providers": {
                "audiobookshelf_primary": {
                    "type": "audiobookshelf",
                    "base_url": "http://audiobooks.invalid",
                    "library_id": "main_library",
                    "timeout_seconds": 10,
                }
            },
            "playback": {
                "source_ids": ["living_room_voice"],
                "default_sleep_timer_minutes": 30,
            },
        }
        (bundle / "domains" / "audiobooks.yaml").write_text(
            json.dumps(audiobooks),
            encoding="utf-8",
        )
        (bundle / "secrets.env").write_text(
            "LIVING_ROOM_BRAIN_CREDENTIAL=brain-secret-value\n"
            "LIVING_ROOM_CONTROL_CREDENTIAL=control-secret-value\n"
            "LIVING_ROOM_ENROLLMENT_CREDENTIAL=enrollment-secret-value\n"
            "RESIDENT_ONE_AUDIOBOOK_TOKEN=resident-audiobook-token\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
