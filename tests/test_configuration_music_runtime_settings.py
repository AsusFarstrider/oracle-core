from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

from oracle_app.configuration import EffectiveConfig, MusicRuntimeSettings, inspect_candidate
from oracle_app.configuration.domain_models import PlexMusicProvider
from oracle_app.music_runtime.canonical import CanonicalMusicExecution
from oracle_app.music_runtime.parsing import MusicIntent
from oracle_app.music import check_music_health


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"
PROJECTION_ACTIVATION_ID = "sat_activation_11111111111111111111111111111111"


class MusicRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_music_does_not_select_dormant_plex_definition(self) -> None:
        settings = MusicRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.enabled)
        self.assertIsNone(settings.provider_id)
        self.assertIsNone(settings.provider)
        self.assertIsNone(settings.provider_credential)
        self.assertEqual(dict(settings.playback_targets), {})

    def test_maps_selected_plex_provider_policy_and_applied_control_targets(self) -> None:
        effective = self._effective_config(enabled=True)

        settings = MusicRuntimeSettings.from_effective_config(effective)
        target = settings.playback_target("living_room_voice")

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.provider_id, "plex_primary")
        self.assertIsInstance(settings.provider, PlexMusicProvider)
        self.assertEqual(settings.provider.music_section_id, 4)
        self.assertEqual(settings.provider_credential, "plex-secret-value")
        self.assertEqual(settings.matching.maximum_candidates, 6)
        self.assertFalse(settings.matching.clarification_enabled)
        self.assertIsNotNone(target)
        self.assertEqual(target.satellite_id, "living_room_satellite")  # type: ignore[union-attr]
        self.assertEqual(target.projection_activation_id, PROJECTION_ACTIVATION_ID)  # type: ignore[union-attr]
        self.assertNotIn("plex-secret-value", repr(settings))
        self.assertNotIn("control-secret-value", repr(settings))
        with self.assertRaises(TypeError):
            settings.playback_targets["other"] = target  # type: ignore[index,assignment]

    def test_unknown_source_has_no_implicit_playback_fallback(self) -> None:
        settings = MusicRuntimeSettings.from_effective_config(self._effective_config(enabled=True))

        self.assertIsNone(settings.playback_target("unknown_source"))
        self.assertIsNone(settings.playback_target(None))

    def test_absent_optional_role_has_no_implicit_runtime_defaults(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            MusicRuntimeSettings.from_effective_config(effective)

    def test_canonical_execution_uses_typed_provider_and_control_targets(self) -> None:
        settings = MusicRuntimeSettings.from_effective_config(self._effective_config(enabled=True))
        execution = CanonicalMusicExecution(settings, satellite_control_timeout_seconds=6)
        self.assertNotIn("plex-secret-value", repr(execution.bridge._connection))
        intent = MusicIntent(
            intent="play",
            media_type="track",
            title="Test Song",
            artist=None,
            album=None,
            playlist=None,
            genre=None,
            qualifiers=[],
            mode="replace",
            original_text="Test Song",
        )

        with (
            patch(
                "oracle_app.music_runtime.canonical.search_plex_catalog",
                return_value=[{"title": "Test Song"}],
            ) as search,
            patch("oracle_app.music_runtime.canonical.execute_satellite_command", return_value={"ok": True}) as command,
            patch(
                "oracle_app.music_runtime.canonical.fetch_satellite_playback_authority",
                return_value={"sessions": [], "active_sessions": []},
            ) as authority,
            patch(
                "oracle_app.provider_bridges.plex_music.get_music_settings",
                side_effect=AssertionError("canonical music used V1 settings"),
            ),
            patch(
                "oracle_app.music_runtime.control.get_satellite_control_target",
                side_effect=AssertionError("canonical music used a V1 control target"),
            ),
        ):
            self.assertEqual(execution.search(intent), [{"title": "Test Song"}])
            self.assertEqual(execution.backend_hint("living_room_voice"), "oracle_native_music")
            execution.execute_satellite_command("living_room_voice", "pause")
            execution.fetch_playback_authority("living_room_voice")

        search.assert_called_once_with(execution.bridge, intent)
        control_target = command.call_args.kwargs["control_target"]
        self.assertEqual(control_target.base_url, "http://living-room.invalid:8021")
        self.assertEqual(control_target.credential, "control-secret-value")
        self.assertEqual(control_target.timeout_seconds, 6)
        self.assertEqual(authority.call_args.kwargs["control_target"], control_target)

    def test_canonical_execution_rejects_unadmitted_source(self) -> None:
        settings = MusicRuntimeSettings.from_effective_config(self._effective_config(enabled=True))
        execution = CanonicalMusicExecution(settings, satellite_control_timeout_seconds=6)

        with self.assertRaises(ValueError):
            execution.execute_satellite_command("unknown", "pause")

    def test_canonical_music_health_uses_typed_execution(self) -> None:
        settings = MusicRuntimeSettings.from_effective_config(self._effective_config(enabled=True))
        execution = CanonicalMusicExecution(settings, satellite_control_timeout_seconds=6)

        with patch(
            "oracle_app.music.get_music_settings",
            side_effect=AssertionError("canonical health used V1 music settings"),
        ):
            payload = check_music_health(
                music_execution=execution,
                canonical_authority=True,
            )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["configured_satellites"], ["living_room_voice"])

    def _effective_config(
        self,
        *,
        enabled: bool = False,
        include_role: bool = True,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            role_path = bundle / "domains" / "music.yaml"
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
                schema_version=2,
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
                        "display": False,
                        "music_playback": True,
                        "audiobook_playback": False,
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

        music = {
            "enabled": True,
            "provider": "plex_primary",
            "providers": {
                "plex_primary": {
                    "type": "plex",
                    "base_url": "http://plex.invalid:32400",
                    "credential_secret": "PLEX_PRIMARY_TOKEN",
                    "timeout_seconds": 8,
                    "music_section_id": 4,
                }
            },
            "matching": {
                "maximum_candidates": 6,
                "clarification_enabled": False,
            },
            "playback": {"source_ids": ["living_room_voice"]},
        }
        (bundle / "domains" / "music.yaml").write_text(json.dumps(music), encoding="utf-8")
        (bundle / "secrets.env").write_text(
            "LIVING_ROOM_BRAIN_CREDENTIAL=brain-secret-value\n"
            "LIVING_ROOM_CONTROL_CREDENTIAL=control-secret-value\n"
            "LIVING_ROOM_ENROLLMENT_CREDENTIAL=enrollment-secret-value\n"
            "PLEX_PRIMARY_TOKEN=plex-secret-value\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
