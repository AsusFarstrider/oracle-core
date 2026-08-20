from __future__ import annotations

import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "docs" / "reference" / "provider-role-registry.json"
OWNERSHIP_PATH = REPO_ROOT / "ops" / "core-promotion" / "ownership.json"


class ProviderRoleRegistryTests(unittest.TestCase):
    def test_registry_is_complete_and_self_contained(self) -> None:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(registry["schema_version"], 1)
        self.assertIn("not a runtime provider selector", registry["scope"])
        roles = registry["roles"]
        ownership = None
        if OWNERSHIP_PATH.is_file():
            ownership = {
                entry["path"]: entry["classification"]
                for entry in json.loads(OWNERSHIP_PATH.read_text(encoding="utf-8"))["entries"]
            }
        expected_ids = {
            "audiobook_catalog_session",
            "audiobook_media_proxy",
            "calendar_provider",
            "facts_provider",
            "home_assistant_camera_proxy",
            "home_assistant_domain",
            "music_catalog",
            "music_media_and_local_control",
            "network_control_execution",
            "network_monitoring_observation",
            "network_probe_observation",
            "news_provider",
            "notification_delivery",
            "satellite_playback_control",
            "shared_inference",
            "speech_stt",
            "speech_tts",
            "suggestions_advisory",
            "weather_forecast_provider",
            "weather_remote_location",
            "weather_station_provider",
        }
        self.assertEqual({role["role_id"] for role in roles}, expected_ids)
        self.assertEqual(len(roles), len(expected_ids))

        required = {
            "role_id",
            "kind",
            "owner",
            "implementations",
            "consumers",
            "health_owner",
            "errors",
            "cache_owner",
            "configuration_authority",
            "tests",
        }
        for role in roles:
            with self.subTest(role=role["role_id"]):
                self.assertEqual(set(role), required)
                for field in required - {"implementations", "consumers", "tests"}:
                    self.assertTrue(str(role[field]).strip())
                self.assertTrue(role["implementations"])
                self.assertTrue(role["consumers"])
                self.assertTrue(role["tests"])
                for path in role["implementations"] + role["tests"]:
                    self.assertTrue((REPO_ROOT / path).is_file(), path)
                if ownership is not None:
                    for path in role["tests"]:
                        self.assertEqual(ownership[path], "core_direct", path)

    def test_every_provider_bridge_implementation_has_an_explicit_role(self) -> None:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        recorded = {
            path
            for role in registry["roles"]
            for path in role["implementations"]
        }
        bridge_root = REPO_ROOT / "server" / "oracle_app" / "provider_bridges"
        exempt_support = {
            "server/oracle_app/provider_bridges/__init__.py",
            "server/oracle_app/provider_bridges/openclaw/__init__.py",
            "server/oracle_app/provider_bridges/openclaw/adapters/__init__.py",
        }
        implementations = {
            path.relative_to(REPO_ROOT).as_posix()
            for path in bridge_root.rglob("*.py")
        } - exempt_support
        self.assertEqual(implementations - recorded, set())


if __name__ == "__main__":
    unittest.main()
